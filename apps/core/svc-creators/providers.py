"""Creators Home providers: who writes the scripts, who speaks them and where the audio is kept.

Each is a small class behind one method, so a hosted provider (OpenAI for scripts, ElevenLabs for voice) can replace
the open-source default by configuration. Defaults: scripts from an Ollama model on ghana (the DGX Spark), voices
from creators-tts (Kokoro) on ghana, audio in the VolumeOS asset bucket under the business's own prefix.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import wave
from typing import Any

import httpx

THINK = re.compile(r"<think>.*?</think>", re.S)
SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,400}$")


class ProviderError(RuntimeError):
    """A provider failed in a way a person should read (shown on the stage)."""


def plain_text(text: str) -> str:
    """Narration only: no reasoning blocks, markdown, headings or stage directions left in."""
    text = THINK.sub("", text)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^(#|\*\*?(scene|chapter|narrator|host)\b|\[(music|sfx|pause|b-roll)[^\]]*\])", line, re.I):
            continue
        line = re.sub(r"\*\*|__|\*(?=\S)|(?<=\S)\*", "", line)
        line = re.sub(r"\[(?:music|sfx|b-roll|pause)[^\]]*\]|\((?:music|sfx|b-roll)[^)]*\)", "", line, flags=re.I).strip()
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class OllamaWriter:
    """Writes a script as an outline, then each chapter on its own (long scripts stay coherent and on length)."""

    def __init__(self, base_url: str, model: str, timeout: float = 900.0):
        if not base_url or not model:
            raise RuntimeError("SCRIPT_LLM_URL and SCRIPT_MODEL are required")
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout

    @classmethod
    def from_env(cls) -> "OllamaWriter":
        return cls(os.environ.get("SCRIPT_LLM_URL", ""), os.environ.get("SCRIPT_MODEL", ""))

    async def _chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 4096,
                    temperature: float = 0.8) -> str:
        body: dict[str, Any] = {"model": self.model, "stream": False, "think": False,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 32768}}
        if json_mode:
            body["format"] = "json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.base_url + "/api/chat", json=body)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"the script model ({self.model}) didn't answer: {exc.__class__.__name__}") from exc
        return THINK.sub("", response.json().get("message", {}).get("content", "")).strip()

    async def outline(self, system: str, brief: str, chapters: int, target_words: int) -> list[dict[str, Any]]:
        ask = (f"{brief}\n\nPlan the video as exactly {chapters} chapters totalling about {target_words} spoken words. "
               "Chapter 1 is the opening hook (short, about 6% of the words) and the last chapter closes the video with a "
               "natural call to action. Give the chapters in between a logical progression, storytelling, concrete examples "
               "and a reason to keep watching (a pattern interrupt or open question) near each end.\n"
               'Answer as JSON: {"chapters": [{"heading": "...", "purpose": "one sentence on what this chapter does", '
               '"words": <spoken words for this chapter>}]}')
        raw = await self._chat(system, ask, json_mode=True, max_tokens=2048, temperature=0.6)
        try:
            items = json.loads(raw)["chapters"]
            plan = [{"heading": str(c["heading"]).strip()[:120] or f"Chapter {i}", "purpose": str(c.get("purpose", "")).strip()[:400],
                     "words": int(c.get("words") or 0)} for i, c in enumerate(items, start=1)]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("the script model returned an outline that couldn't be read; try again") from exc
        if not 2 <= len(plan) <= 24:
            raise ProviderError("the script model returned an outline with the wrong number of chapters; try again")
        # Keep the plan on length whatever the model proposed: scale its split to the target.
        total = sum(max(c["words"], 1) for c in plan)
        for chapter in plan:
            chapter["words"] = max(60, round(max(chapter["words"], 1) / total * target_words))
        return plan

    async def chapter(self, system: str, brief: str, plan: list[dict[str, Any]], index: int, previous: str) -> str:
        chapter = plan[index]
        listing = "\n".join(f"{i + 1}. {c['heading']} — {c['purpose']}" for i, c in enumerate(plan))
        ask = (f"{brief}\n\nThe video's chapters:\n{listing}\n\nWrite chapter {index + 1}, \"{chapter['heading']}\": "
               f"{chapter['purpose']} About {chapter['words']} words.\n"
               + (f"It follows on from the end of the previous chapter:\n\"\"\"{previous[-1200:]}\"\"\"\n" if previous else "")
               + "Write ONLY the words the narrator says: no chapter title, no headings, no markdown, no stage directions, "
                 "no [music] or (pause) notes. Natural spoken sentences, paragraphs separated by blank lines.")
        text = plain_text(await self._chat(system, ask, max_tokens=min(8192, chapter["words"] * 3), temperature=0.85))
        if len(text.split()) < max(30, chapter["words"] // 4):
            raise ProviderError(f"the script model wrote too little for chapter {index + 1}; try again")
        return text


class VoiceService:
    """creators-tts (Kokoro on ghana's GPU): the voice list, and speech for one piece of text as 16-bit WAV."""

    def __init__(self, base_url: str, token: str, timeout: float = 300.0):
        if not base_url or len(token) < 32:
            raise RuntimeError("TTS_URL and CREATORS_TTS_TOKEN are required")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    @classmethod
    def from_env(cls) -> "VoiceService | None":
        url, token = os.environ.get("TTS_URL", ""), os.environ.get("CREATORS_TTS_TOKEN", "")
        return cls(url, token) if url and token else None

    async def voices(self) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(self.base_url + "/v1/voices", headers={"Authorization": f"Bearer {self.token}"})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError("the voice service didn't answer") from exc
        return response.json()["voices"]

    async def speak(self, text: str, voice: str, speed: float) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.base_url + "/v1/synthesize", headers={"Authorization": f"Bearer {self.token}"},
                                             json={"text": text, "voice": voice, "speed": speed})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"the voice service refused the text ({exc.response.status_code})") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("the voice service didn't answer") from exc
        return response.content


def chunks_for_speech(text: str, limit: int = 1200) -> list[str]:
    """Paragraphs, and long paragraphs split at sentence ends, so each request stays well under the service's limit."""
    out: list[str] = []
    for paragraph in (p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()):
        if len(paragraph) <= limit:
            out.append(paragraph); continue
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if current and len(current) + len(sentence) + 1 > limit:
                out.append(current); current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            out.append(current)
    return out


def join_wavs(parts: list[bytes], pause_seconds: float = 0.35) -> tuple[bytes, float]:
    """One WAV from several (same format), with a short pause between paragraphs; returns it and its length."""
    if not parts:
        raise ProviderError("no audio to join")
    frames, params = [], None
    for data in parts:
        with wave.open(io.BytesIO(data)) as w:
            if params is None:
                params = w.getparams()
            elif (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (params.nchannels, params.sampwidth, params.framerate):
                raise ProviderError("the voice service returned audio in different formats")
            frames.append(w.readframes(w.getnframes()))
    assert params is not None
    silence = b"\x00" * int(params.framerate * pause_seconds) * params.sampwidth * params.nchannels
    body = silence.join(frames)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(params.nchannels); w.setsampwidth(params.sampwidth); w.setframerate(params.framerate)
        w.writeframes(body)
    return out.getvalue(), len(body) / (params.framerate * params.sampwidth * params.nchannels)


class AudioStore:
    """The asset bucket, under <tenant>/creators/<project>/...: only keys of the business's own projects are used."""

    def __init__(self, bucket: str, client: Any = None):
        if not bucket:
            raise RuntimeError("ASSET_BUCKET is required")
        if client is None:
            import boto3
            client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None)
        self.bucket, self.client = bucket, client

    @classmethod
    def from_env(cls) -> "AudioStore | None":
        bucket = os.environ.get("ASSET_BUCKET", "")
        return cls(bucket) if bucket else None

    @staticmethod
    def key(tenant: str, project_id: str, name: str) -> str:
        key = f"{tenant}/creators/{project_id}/{name}"
        if not SAFE_KEY.fullmatch(key) or ".." in key:
            raise ProviderError("unsafe storage key")
        return key

    async def put(self, key: str, data: bytes, content_type: str = "audio/wav") -> None:
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str, limit: int = 300_000_000) -> bytes:
        def read() -> bytes:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
            if obj.get("ContentLength", 0) > limit:
                raise ProviderError("stored audio is larger than expected")
            return obj["Body"].read()
        return await asyncio.to_thread(read)

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


# Scenes, characters and thumbnails (prompts for the script model) ------------------------------------------------
async def _json_chat(writer: "OllamaWriter", system: str, ask: str, max_tokens: int = 4096) -> dict[str, Any]:
    raw = await writer._chat(system, ask, json_mode=True, max_tokens=max_tokens, temperature=0.5)
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ProviderError("the script model returned something that couldn't be read; try again") from exc
    if not isinstance(value, dict):
        raise ProviderError("the script model returned something that couldn't be read; try again")
    return value


async def characters(writer: "OllamaWriter", system: str, script: str) -> list[dict[str, str]]:
    """The people (or figures) who recur in the video, each with one fixed look so every scene shows the same person."""
    ask = ("Here is a narration script:\n\"\"\"" + script[:24000] + "\"\"\"\n\nList the recurring characters a viewer would "
           "see in the visuals (at most 4; none if the video is not about specific people). For each give a short name and "
           "a fixed physical description for an image model: age, gender, skin tone, hair, build, typical clothing (one "
           'line, no personality). Answer as JSON: {"characters": [{"name": "...", "look": "..."}]}')
    value = await _json_chat(writer, system, ask, 1024)
    out = []
    for item in value.get("characters", [])[:4]:
        if isinstance(item, dict) and str(item.get("name", "")).strip() and str(item.get("look", "")).strip():
            out.append({"name": str(item["name"]).strip()[:40], "look": str(item["look"]).strip()[:300]})
    return out


async def scene_visuals(writer: "OllamaWriter", system: str, title: str, heading: str, segments: list[str],
                        cast: list[dict[str, str]]) -> list[dict[str, str]]:
    """For each narration segment of a chapter: what is seen, an image prompt, the camera and the lighting."""
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(segments))
    people = "\n".join(f"- {c['name']}: {c['look']}" for c in cast) or "(no recurring characters)"
    ask = (f"Video: {title}\nChapter: {heading}\nRecurring characters (refer to them by name):\n{people}\n\n"
           f"The chapter's narration, cut into {len(segments)} scenes:\n{numbered}\n\n"
           "For EACH scene, in order, describe one still image that shows exactly what that narration says (if it "
           "mentions a person praying, show a person praying; separation shows separation). Vary the shots; never repeat "
           "an image. Give: visual (one sentence), prompt (a concise image-generation prompt, subject and action first, "
           "under 45 words, no text in the image, name characters exactly as listed), camera (shot and angle), lighting.\n"
           'Answer as JSON: {"scenes": [{"visual": "...", "prompt": "...", "camera": "...", "lighting": "..."}]}')
    value = await _json_chat(writer, system, ask, 600 * len(segments) + 500)
    items = value.get("scenes", [])
    if not isinstance(items, list) or len(items) != len(segments):
        raise ProviderError(f"the script model described {len(items) if isinstance(items, list) else 0} scenes for "
                            f"{len(segments)} in “{heading}”; try again")
    return [{key: str((item or {}).get(key, "")).strip()[:600] for key in ("visual", "prompt", "camera", "lighting")} for item in items]


async def thumbnail_concepts(writer: "OllamaWriter", system: str, title: str, script: str) -> list[dict[str, str]]:
    ask = (f"Video title: {title}\nScript (start):\n\"\"\"{script[:6000]}\"\"\"\n\nPropose 3 different high-click-through YouTube "
           "thumbnail concepts. For each: the strongest emotional angle (one phrase); a caption of 2 to 5 words that is NOT "
           "the title and makes people curious; a concise image prompt with one clear focal subject, strong colour contrast, "
           "no text in the image, and empty space on one side for the caption; and that side (left or right).\n"
           'Answer as JSON: {"concepts": [{"angle": "...", "caption": "...", "prompt": "...", "text_side": "left"}]}')
    value = await _json_chat(writer, system, ask, 1500)
    out = []
    for item in value.get("concepts", [])[:3]:
        caption = re.sub(r"\s+", " ", str((item or {}).get("caption", ""))).strip().strip('"')
        if caption and caption.lower() != title.lower() and str(item.get("prompt", "")).strip():
            out.append({"angle": str(item.get("angle", "")).strip()[:120], "caption": caption[:40],
                        "prompt": str(item["prompt"]).strip()[:600], "text_side": "right" if item.get("text_side") == "right" else "left"})
    if not out:
        raise ProviderError("the script model didn't propose usable thumbnails; try again")
    return out


def image_prompt(scene_prompt: str, cast: list[dict[str, str]], preset: str, aspect: str = "16:9") -> str:
    """The prompt sent to the image model: the scene first (image models weigh the start most), each character who
    appears replaced by their fixed look, then the channel's look."""
    prompt = scene_prompt
    for person in cast:
        prompt = re.sub(rf"\b{re.escape(person['name'])}\b", f"{person['name']} ({person['look']})", prompt, count=1, flags=re.I)
    style = preset.strip() or "cinematic still, realistic photography, natural light, high detail"
    return f"{prompt}, {style}, {'vertical' if aspect == '9:16' else 'wide'} composition"


class ImageService:
    """creators-image on ghana: one PNG per prompt (SDXL or FLUX.1-schnell); the seed makes it repeatable."""

    def __init__(self, base_url: str, token: str, timeout: float = 300.0):
        if not base_url or len(token) < 32:
            raise RuntimeError("IMAGE_URL and CREATORS_TTS_TOKEN are required")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    @classmethod
    def from_env(cls) -> "ImageService | None":
        url, token = os.environ.get("IMAGE_URL", ""), os.environ.get("CREATORS_TTS_TOKEN", "")
        return cls(url, token) if url and token else None

    async def generate(self, prompt: str, width: int, height: int, seed: int) -> tuple[bytes, str]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.base_url + "/v1/image", headers={"Authorization": f"Bearer {self.token}"},
                                             json={"prompt": prompt[:2000], "width": width, "height": height, "seed": seed})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"the image service refused the prompt ({exc.response.status_code})") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("the image service didn't answer") from exc
        return response.content, response.headers.get("x-model", "")


def caption_thumbnail(background: bytes, caption: str, side: str, size: tuple[int, int] = (1280, 720)) -> bytes:
    """The 1280x720 thumbnail: the background cropped to 16:9, the caption large and bold on the free side, white with
    a dark outline so it reads on a phone."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    image = ImageOps.fit(Image.open(io.BytesIO(background)).convert("RGB"), size)
    draw = ImageDraw.Draw(image)
    words = caption.upper().split()
    lines = [" ".join(words[i:i + 2]) for i in range(0, len(words), 2)] or [caption.upper()]
    font_path = next((p for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",) if os.path.exists(p)), None)
    box_w = int(size[0] * 0.46)
    font_size = 150
    while font_size > 40:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default(font_size)
        widths = [draw.textbbox((0, 0), line, font=font, stroke_width=8)[2] for line in lines]
        if max(widths) <= box_w and font_size * 1.1 * len(lines) <= size[1] * 0.8:
            break
        font_size -= 6
    line_h = int(font_size * 1.1)
    y = (size[1] - line_h * len(lines)) // 2
    for line, width in zip(lines, widths):
        x = int(size[0] * 0.05) if side == "left" else size[0] - width - int(size[0] * 0.05)
        draw.text((x, y), line, font=font, fill=(255, 255, 255), stroke_width=max(4, font_size // 14), stroke_fill=(10, 10, 10))
        y += line_h
    out = io.BytesIO(); image.save(out, format="PNG"); return out.getvalue()

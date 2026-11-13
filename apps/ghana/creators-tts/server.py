"""creators-tts: Kokoro-82M text-to-speech on ghana's GPU for Creators Home (Offer 35).

Runs on the DGX Spark (GB10). cuDNN has no conv_transpose1d engine for this GPU yet, so cuDNN is off and PyTorch's
own CUDA kernels are used; the CUDA 13.0 NVIDIA base image matches the driver (newer images run in forward-
compatibility mode, where cuFFT fails). One synthesis at a time: the GPU is shared with the other Creators models.
Every call needs the bearer token; the port is published on ghana's Tailscale address only.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import os
import time

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

torch.backends.cudnn.enabled = False
from kokoro import KPipeline  # noqa: E402

SAMPLE_RATE = 24000
TOKEN = os.environ.get("CREATORS_TTS_TOKEN", "")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Kokoro v1.0 English voices: id, name, gender, accent. (lang 'a' = American English, 'b' = British English.)
VOICES = [
    *[(v, v[3:].capitalize(), "female", "American") for v in
      ("af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_nova", "af_river", "af_jessica", "af_kore", "af_aoede", "af_alloy")],
    *[(v, v[3:].capitalize(), "male", "American") for v in
      ("am_michael", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_onyx", "am_puck")],
    *[(v, v[3:].capitalize(), "female", "British") for v in ("bf_emma", "bf_isabella", "bf_alice", "bf_lily")],
    *[(v, v[3:].capitalize(), "male", "British") for v in ("bm_george", "bm_lewis", "bm_daniel", "bm_fable")],
]
VOICE_IDS = {v[0] for v in VOICES}

app = FastAPI(title="Creators Home voice service")
pipelines: dict[str, KPipeline] = {}
lock = asyncio.Lock()


def pipeline(voice: str) -> KPipeline:
    lang = voice[0]  # 'a' or 'b'
    if lang not in pipelines:
        pipelines[lang] = KPipeline(lang_code=lang, device=DEVICE)
    return pipelines[lang]


def authorised(authorization: str | None) -> None:
    provided = (authorization or "").removeprefix("Bearer ")
    if len(TOKEN) < 32 or not hmac.compare_digest(provided.encode(), TOKEN.encode()):
        raise HTTPException(401, "token required")


class SynthesisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=6000)
    voice: str
    speed: float = Field(default=1.0, ge=0.6, le=1.6)


@app.on_event("startup")
async def warm() -> None:
    pipeline("af_heart"); pipeline("bf_emma")  # load both language models once


@app.get("/health")
async def health():
    return {"status": "ok", "device": DEVICE, "voices": len(VOICES)}


@app.get("/v1/voices")
async def voices(authorization: str | None = Header(default=None)):
    authorised(authorization)
    return {"voices": [{"id": v, "name": n, "gender": g, "accent": a} for v, n, g, a in VOICES], "sample_rate": SAMPLE_RATE}


@app.post("/v1/synthesize")
async def synthesize(body: SynthesisInput, authorization: str | None = Header(default=None)):
    authorised(authorization)
    if body.voice not in VOICE_IDS:
        raise HTTPException(400, "unknown voice")

    def run() -> tuple[bytes, float]:
        chunks = [audio for _, _, audio in pipeline(body.voice)(body.text, voice=body.voice, speed=body.speed)]
        if not chunks:
            raise ValueError("nothing to say")
        audio = np.concatenate([c.cpu().numpy() if hasattr(c, "cpu") else c for c in chunks])
        buffer = io.BytesIO()
        sf.write(buffer, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buffer.getvalue(), len(audio) / SAMPLE_RATE

    started = time.time()
    async with lock:
        try:
            data, seconds = await asyncio.to_thread(run)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return Response(data, media_type="audio/wav", headers={
        "X-Audio-Seconds": f"{seconds:.2f}", "X-Render-Seconds": f"{time.time() - started:.2f}"})

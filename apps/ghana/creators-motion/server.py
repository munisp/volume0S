"""creators-motion: image-to-video on ghana's GPU for Creators Home (Offer 35). Wan 2.2 TI2V-5B (Apache-2.0) with the
FastWan distilled transformer (FastVideo, Apache-2.0) by default: 3 steps without guidance instead of ~40 with it,
about 25x less GPU time on the GB10 for a very similar picture. MOTION_TRANSFORMER="" uses the original transformer.

A scene's still image + what happens in it -> a 720p clip that starts on that image, so the characters and the look
stay the scene's. A scene longer than one shot (5 s) is made of shots chained end to start: each next shot begins on
the previous shot's last frame, so the motion carries on without a cut. One clip at a time; the same seed, image and prompt give the same clip. Bearer token;
published on Tailscale only.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import io
import os
import tempfile
import time

import torch
from diffusers import AutoencoderKLWan, WanImageToVideoPipeline
from diffusers.utils import export_to_video
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

TOKEN = os.environ.get("CREATORS_TTS_TOKEN", "")  # one token for the Creators Home services on ghana
MODEL = os.environ.get("MOTION_MODEL", "Wan-AI/Wan2.2-TI2V-5B-Diffusers")
TRANSFORMER = os.environ.get("MOTION_TRANSFORMER", "FastVideo/FastWan2.2-TI2V-5B-FullAttn-Diffusers")
FAST = "FastWan" in TRANSFORMER
STEPS = int(os.environ.get("MOTION_STEPS", "0")) or (3 if FAST else 40)
GUIDANCE = float(os.environ.get("MOTION_GUIDANCE", "1.0" if FAST else "5.0"))
WIDTH, HEIGHT = int(os.environ.get("MOTION_WIDTH", "1280")), int(os.environ.get("MOTION_HEIGHT", "704"))
# Without guidance the negative prompt isn't used, so what to avoid is said in the prompt itself.
AVOID = " No text, captions, logos or brand names anywhere; steady faces and hands; no flicker."
FPS = 24
NEGATIVE = ("static, still image, frozen, text, subtitles, watermark, logo, blurry, low quality, jpeg artifacts, deformed, "
            "disfigured, extra fingers, bad hands, bad face, morphing, flicker, overexposed")
app = FastAPI(title="Creators Home motion service")
lock = asyncio.Lock()
pipe = None


def authorised(authorization: str | None) -> None:
    provided = (authorization or "").removeprefix("Bearer ")
    if len(TOKEN) < 32 or not hmac.compare_digest(provided.encode(), TOKEN.encode()):
        raise HTTPException(401, "token required")


class MotionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(min_length=100, max_length=20_000_000)  # base64 PNG or JPEG: the clip's first frame
    prompt: str = Field(min_length=3, max_length=2000)          # what moves: action and camera
    seconds: float = Field(default=5.0, ge=1.0, le=30.0)        # shots of up to 5 s (the model's 121 frames at 24 fps)
    width: int = Field(default=0, ge=0, le=1280, multiple_of=32)   # 0: the service's size (MOTION_WIDTH x MOTION_HEIGHT)
    height: int = Field(default=0, ge=0, le=1280, multiple_of=32)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    steps: int = Field(default=0, ge=0, le=60)


@app.on_event("startup")
async def load() -> None:
    global pipe
    torch.backends.cuda.matmul.allow_tf32 = True
    vae = AutoencoderKLWan.from_pretrained(MODEL, subfolder="vae", torch_dtype=torch.float32, cache_dir="/models/hub")
    extra = {}
    if TRANSFORMER:
        from diffusers import WanTransformer3DModel
        extra["transformer"] = WanTransformer3DModel.from_pretrained(TRANSFORMER, subfolder="transformer", torch_dtype=torch.bfloat16, cache_dir="/models/hub")
    pipe = WanImageToVideoPipeline.from_pretrained(MODEL, vae=vae, torch_dtype=torch.bfloat16, cache_dir="/models/hub", **extra).to("cuda")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok" if pipe is not None else "loading", "model": MODEL, "transformer": TRANSFORMER, "steps": STEPS, "fps": FPS,
            "size": f"{WIDTH}x{HEIGHT}"}


def fit(image: Image.Image, width: int, height: int) -> Image.Image:
    """Crop to the clip's shape (centre) and scale: the scene image is 1344x768, the clip 1280x704."""
    image = image.convert("RGB")
    target = width / height
    w, h = image.size
    if w / h > target:
        nw = round(h * target); image = image.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = round(w / target); image = image.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return image.resize((width, height), Image.LANCZOS)


def shots(seconds: float) -> list[int]:
    """Frame counts of the shots for `seconds` (each 4k+1 frames, at most 121; shots overlap by their joining frame)."""
    n = max(1, -(-round(seconds * FPS) // 120))
    each = round(seconds * FPS / n)
    return [int(round(each / 4)) * 4 + 1] * n


def generate(body: MotionInput) -> tuple[bytes, dict]:
    width, height = body.width or WIDTH, body.height or HEIGHT
    image = fit(Image.open(io.BytesIO(base64.b64decode(body.image))), width, height)
    started, frames_out = time.time(), []
    for index, frames in enumerate(shots(body.seconds)):
        out = pipe(image=image, prompt=body.prompt + AVOID, negative_prompt=NEGATIVE, height=height, width=width,
                   num_frames=frames, num_inference_steps=body.steps or STEPS, guidance_scale=GUIDANCE, output_type="pil",
                   generator=torch.Generator("cuda").manual_seed(body.seed + index)).frames[0]
        frames_out.extend(out if index == 0 else out[1:])  # the next shot's first frame is this shot's last
        image = out[-1]
    out = frames_out
    frames = len(out)
    took = time.time() - started
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        export_to_video(out, f.name, fps=FPS, quality=8)
        data = open(f.name, "rb").read()
    return data, {"frames": frames, "seconds": round(frames / FPS, 2), "took": round(took, 1)}


@app.post("/v1/animate")
async def animate(body: MotionInput, authorization: str | None = Header(default=None)) -> Response:
    authorised(authorization)
    if pipe is None:
        raise HTTPException(503, "the motion model is still loading")
    async with lock:
        data, info = await asyncio.to_thread(generate, body)
    name = (TRANSFORMER or MODEL).split("/")[-1].replace("-FullAttn-Diffusers", "").replace("-Diffusers", "")
    return Response(data, media_type="video/mp4", headers={"X-Motion-Model": name, "X-Motion-Seconds": str(info["seconds"]),
                                                           "X-Motion-Took": str(info["took"])})

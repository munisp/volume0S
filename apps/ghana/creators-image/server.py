"""creators-image: text-to-image on ghana's GPU for Creators Home (Offer 35): FLUX.1-schnell (Apache-2.0) or SDXL 1.0
(CreativeML Open RAIL++-M), chosen by IMAGE_MODEL.

Scene images (16:9 for videos, 9:16 for Shorts) and thumbnail backgrounds. One image at a time; same seed + prompt
gives the same image, which is how a project keeps its look. Bearer token; published on Tailscale only.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import os
import time

import torch
from diffusers import AutoPipelineForText2Image
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

TOKEN = os.environ.get("CREATORS_TTS_TOKEN", "")  # one token for the Creators Home services on ghana
MODEL = os.environ.get("IMAGE_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")
FLUX = "flux" in MODEL.lower()
# FLUX.1-schnell is distilled: 4 steps, no guidance. SDXL needs ~30 steps with guidance and takes a negative prompt.
# SDXL-Lightning (ByteDance, same licence as SDXL): the SDXL UNet distilled to 4 steps, no guidance. Off: plain SDXL
# with DPM++ 2M Karras at 22 steps (~7x slower on the GB10).
LIGHTNING = not FLUX and os.environ.get("SDXL_LIGHTNING", "1") == "1"
DEFAULT_STEPS, GUIDANCE = (4, 0.0) if FLUX or LIGHTNING else (22, 6.0)
NEGATIVE = "text, words, letters, watermark, logo, signature, blurry, deformed hands, extra fingers, distorted face, low quality"
app = FastAPI(title="Creators Home image service")
lock = asyncio.Lock()
pipe = None


def authorised(authorization: str | None) -> None:
    provided = (authorization or "").removeprefix("Bearer ")
    if len(TOKEN) < 32 or not hmac.compare_digest(provided.encode(), TOKEN.encode()):
        raise HTTPException(401, "token required")


class ImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=3, max_length=2000)
    width: int = Field(default=1344, ge=256, le=1920, multiple_of=16)
    height: int = Field(default=768, ge=256, le=1920, multiple_of=16)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    steps: int = Field(default=0, ge=0, le=50)  # 0: the model's default


@app.on_event("startup")
async def load() -> None:
    global pipe
    kwargs = {} if FLUX else {"variant": "fp16"}
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    pipe = AutoPipelineForText2Image.from_pretrained(MODEL, torch_dtype=torch.bfloat16 if FLUX else torch.float16,
                                                     cache_dir="/models/hub", **kwargs).to("cuda")
    if LIGHTNING:
        from diffusers import EulerDiscreteScheduler
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file
        weights = hf_hub_download("ByteDance/SDXL-Lightning", "sdxl_lightning_4step_unet.safetensors", cache_dir="/models/hub")
        pipe.unet.load_state_dict(load_file(weights, device="cuda"))
        pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    elif not FLUX:
        from diffusers import DPMSolverMultistepScheduler
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True,
                                                                 algorithm_type="dpmsolver++")


@app.get("/health")
async def health():
    return {"status": "ok" if pipe is not None else "loading", "model": MODEL + (" + SDXL-Lightning 4-step" if LIGHTNING else "")}


@app.post("/v1/image")
async def image(body: ImageInput, authorization: str | None = Header(default=None)):
    authorised(authorization)
    if pipe is None:
        raise HTTPException(503, "the model is still loading")

    def run() -> bytes:
        generator = torch.Generator("cuda").manual_seed(body.seed)
        extra = {"max_sequence_length": 256} if FLUX else ({} if LIGHTNING else {"negative_prompt": NEGATIVE})
        result = pipe(body.prompt, width=body.width, height=body.height, num_inference_steps=body.steps or DEFAULT_STEPS,
                      guidance_scale=GUIDANCE, generator=generator, **extra).images[0]
        out = io.BytesIO(); result.save(out, format="PNG"); return out.getvalue()

    started = time.time()
    async with lock:
        data = await asyncio.to_thread(run)
    return Response(data, media_type="image/png", headers={"X-Render-Seconds": f"{time.time() - started:.2f}", "X-Model": MODEL + ("+lightning4" if LIGHTNING else "")})

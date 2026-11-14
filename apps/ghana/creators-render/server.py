"""creators-render: assembles a Creators Home video on ghana's CPU cores with FFmpeg.

Input (multipart): spec.json (scenes with image file name and seconds, chapter audio file names in order, options)
plus the files. Every scene becomes a 1080p30 clip for exactly its seconds (in parallel): its AI motion shots (from
creators-motion) joined and fitted to the narration, or, for a still scene, a slow zoom or pan over its image;
the clips are joined and laid over the chapters' narration, so each picture is on screen while its words are said.
Subtitles (SRT) come from the scenes' narration and timings and can be burned in. Output: H.264/AAC MP4 (faststart).
Bearer token; published on Tailscale only.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

TOKEN = os.environ.get("CREATORS_TTS_TOKEN", "")
FPS, W, H = 30, 1920, 1080
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
app = FastAPI(title="Creators Home render service")
lock = asyncio.Semaphore(1)  # one video at a time; each uses all cores


def authorised(authorization: str | None) -> None:
    provided = (authorization or "").removeprefix("Bearer ")
    if len(TOKEN) < 32 or not hmac.compare_digest(provided.encode(), TOKEN.encode()):
        raise HTTPException(401, "token required")


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:])


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def subtitles(scenes: list[dict]) -> str:
    """Each scene's narration in short cues (two lines of ~42 characters), timed within the scene by word count."""
    cues, start, n = [], 0.0, 1
    for scene in scenes:
        words, seconds = scene["narration"].split(), float(scene["seconds"])
        chunks, current = [], []
        for word in words:
            if current and len(" ".join(current + [word])) > 84:
                chunks.append(current); current = []
            current.append(word)
        if current:
            chunks.append(current)
        t = start
        for chunk in chunks:
            length = seconds * len(chunk) / max(len(words), 1)
            text = " ".join(chunk)
            if len(text) > 42:  # two lines, split near the middle
                cut = text.rfind(" ", 0, len(text) // 2 + 6)
                text = text[:cut] + "\n" + text[cut + 1:] if cut > 0 else text
            cues.append(f"{n}\n{srt_time(t)} --> {srt_time(t + length - 0.05)}\n{text}\n"); n += 1; t += length
        start += seconds
    return "\n".join(cues)


def clip(image: Path, seconds: float, index: int, out: Path) -> None:
    """A slow Ken Burns move: zoom in, zoom out, or pan, alternating so consecutive scenes don't feel the same."""
    frames = max(1, round(seconds * FPS))
    zoom = {0: "min(1.0+0.10*on/{f},1.10)", 1: "max(1.10-0.10*on/{f},1.0)", 2: "1.08", 3: "1.08"}[index % 4].format(f=frames)
    x = {0: "iw/2-(iw/zoom/2)", 1: "iw/2-(iw/zoom/2)", 2: "(iw-iw/zoom)*on/{f}".format(f=frames),
         3: "(iw-iw/zoom)*(1-on/{f})".format(f=frames)}[index % 4]
    vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
          f"zoompan=z='{zoom}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p")
    run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(image), "-vf", vf, "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-r", str(FPS), str(out)])


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


def motion_clip(shots: list[Path], seconds: float, out: Path) -> None:
    """The scene's motion shots back to back, scaled to 1080p30 and fitted to exactly `seconds`: a little slower if
    they run short (at most 1.25x, which reads as natural), then the last frame held for any remainder; trimmed if long."""
    joined = out.with_suffix(".joined.mp4")
    if len(shots) == 1:
        shutil.copy(shots[0], joined)
    else:
        listing = out.with_suffix(".txt")
        listing.write_text("".join(f"file '{p.name}'\n" for p in shots))
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)])
    have = duration(joined) or seconds
    slow = min(max(seconds / have, 1.0), 1.25)
    hold = max(seconds - have * slow, 0)
    vf = (f"setpts={slow:.4f}*PTS,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
          f"tpad=stop_mode=clone:stop_duration={hold + 0.1:.3f},format=yuv420p")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-an", "-vf", vf, "-frames:v", str(max(1, round(seconds * FPS))),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-r", str(FPS), str(out)])


@app.get("/health")
async def health():
    return {"status": "ok", "ffmpeg": shutil.which("ffmpeg") is not None, "cores": os.cpu_count()}


@app.post("/v1/render")
async def render(request: Request, authorization: str | None = Header(default=None)):
    authorised(authorization)
    form = await request.form(max_files=1000, max_part_size=200 * 1024 * 1024)
    spec = json.loads(await form["spec"].read())
    work = Path(tempfile.mkdtemp(prefix="render-", dir="/work"))
    try:
        for key, value in form.multi_items():
            if key == "spec":
                continue
            if not SAFE.fullmatch(key):
                raise HTTPException(400, f"bad file name {key!r}")
            (work / key).write_bytes(await value.read())
        scenes, chapters = spec["scenes"], spec["chapters"]
        if not scenes or not chapters or any(not (work / s["image"]).exists() for s in scenes) or any(not (work / c).exists() for c in chapters) \
                or any(not (work / m).exists() or not SAFE.fullmatch(m) for s in scenes for m in s.get("motion") or []):
            raise HTTPException(400, "missing scenes, chapter audio or files")
        started = time.time()
        async with lock:
            await asyncio.to_thread(build, work, spec)
        out = work / "video.mp4"
        return FileResponse(out, media_type="video/mp4", headers={"X-Render-Seconds": f"{time.time() - started:.1f}"},
                            background=BackgroundTask(shutil.rmtree, work, True))
    except HTTPException:
        shutil.rmtree(work, True); raise
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(work, True)
        raise HTTPException(500, f"render failed: {str(exc)[:600]}") from exc


def build(work: Path, spec: dict) -> None:
    scenes, chapters = spec["scenes"], spec["chapters"]
    with ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) // 2)) as pool:
        def scene(item: tuple[int, dict]) -> None:
            i, s = item
            if s.get("motion"):
                motion_clip([work / m for m in s["motion"]], float(s["seconds"]), work / f"clip-{i:04d}.mp4")
            else:
                clip(work / s["image"], float(s["seconds"]), i, work / f"clip-{i:04d}.mp4")
        list(pool.map(scene, enumerate(scenes)))
    (work / "clips.txt").write_text("".join(f"file 'clip-{i:04d}.mp4'\n" for i in range(len(scenes))))
    (work / "audio.txt").write_text("".join(f"file '{c}'\n" for c in chapters))
    (work / "captions.srt").write_text(subtitles(scenes))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "audio.txt"), "-c:a", "pcm_s16le", str(work / "narration.wav")])
    video_filter = []
    if spec.get("burn_subtitles"):
        style = "FontName=DejaVu Sans,FontSize=20,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,Outline=3,Shadow=0,MarginV=60"
        video_filter = ["-vf", f"subtitles={work / 'captions.srt'}:force_style='{style}'"]
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "clips.txt"), "-i", str(work / "narration.wav"),
         *video_filter, "-map", "0:v", "-map", "1:a", "-c:v", "libx264" if video_filter else "copy",
         *(["-preset", "veryfast", "-crf", "19"] if video_filter else []),
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(work / "video.mp4")])

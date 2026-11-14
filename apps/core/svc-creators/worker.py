"""creators-worker: makes what Creators Home stages ask for (scripts, voiceovers), one job at a time.

Claims the oldest queued job from creator_jobs (two workers never take the same one), renews its lease while
working, and writes the result into the project's shared record with the stage moved on. A job whose worker
stopped is taken up again after its lease runs out, at most three attempts; then its stage is marked failed.
Run: python worker.py (same image and secret as svc-creators).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any

import model
from providers import (AudioStore, ImageService, OllamaWriter, ProviderError, RenderService, VoiceService, caption_thumbnail,
                       captions, chapter_marks, characters, chunks_for_speech, image_prompt, join_wavs, scene_visuals, seo_package,
                       thumbnail_concepts, to_jpeg)
from store import PostgresCreatorStore

log = logging.getLogger("creators-worker")
WORKER = "worker:creators"
LANGUAGES = {"en": "English", "fr": "French", "yo": "Yoruba", "ha": "Hausa", "ig": "Igbo", "pcm": "Nigerian Pidgin"}


def system_prompt(channel: dict[str, Any]) -> str:
    parts = ["You are a professional YouTube scriptwriter. You write narration that holds attention from the first "
             "line to the last: a strong hook, clear progression, storytelling, concrete examples, natural transitions."]
    if channel.get("niche"):
        parts.append(f"Channel: {channel['name']} ({channel['niche']}).")
    if channel.get("audience"):
        parts.append(f"Audience: {channel['audience']}.")
    if channel.get("writing_instructions"):
        parts.append("The channel's own writing instructions, which always apply:\n" + channel["writing_instructions"])
    language = LANGUAGES.get((channel.get("language") or "en").split("-")[0], "English")
    parts.append(f"Write in {language}.")
    return "\n\n".join(parts)


def brief(project: dict[str, Any]) -> str:
    lines = [f"Video title: {project['title']}", f"Topic: {project['topic']}",
             f"Length: {project['duration_minutes']} minutes of narration."]
    if project.get("audience"):
        lines.append(f"This video's audience: {project['audience']}")
    if project.get("style"):
        lines.append(f"Style for this video: {project['style']}")
    return "\n".join(lines)


def scene_spans(planned: list[float], target: float, floor: float = 0.2) -> list[float]:
    """How long each scene of a chapter stays on screen: its planned share of the chapter's recorded length, never
    under `floor` seconds, and adding up to exactly the recording (picture and voice never drift apart)."""
    n = len(planned)
    if target <= floor * n:
        spans = [target / n] * n
    else:
        have = sum(planned) or 1.0
        spans = [floor + (target - floor * n) * p / have for p in planned]
    spans = [round(x, 3) for x in spans]
    longest = max(range(n), key=lambda i: spans[i])
    spans[longest] = round(spans[longest] + target - sum(spans), 3)
    return spans


class Worker:
    def __init__(self, store: Any, writer: Any, voices: Any, audio: Any, images: Any = None, renderer: Any = None):
        self.store, self.writer, self.voices, self.audio, self.images, self.renderer = store, writer, voices, audio, images, renderer

    async def save(self, tenant: str, project_id: str, change, event: dict[str, Any]) -> dict[str, Any]:
        """Applies `change` to the latest version of the project; retries if someone saved in between."""
        for _ in range(5):
            project = await self.store.project(tenant, project_id)
            if project is None:
                raise ProviderError("the project no longer exists")
            change(project)
            try:
                return await self.store.save_project(tenant, project, project["version"], event | {"actor": WORKER})
            except LookupError:
                await asyncio.sleep(0.5)
        raise ProviderError("the project kept changing while the result was being saved")

    async def progress(self, job: dict[str, Any], text: str) -> None:
        await self.store.job_progress(job["tenant_id"], job["id"], text)

    async def run_script(self, job: dict[str, Any]) -> str:
        tenant, pid = job["tenant_id"], job["project_id"]
        project = await self.store.project(tenant, pid)
        channel = await self.store.channel(tenant, project["channel_id"])
        system, about = system_prompt(channel or {}), brief(project)
        target = model.target_words(project["duration_minutes"])
        await self.progress(job, "planning the chapters")
        plan = await self.writer.outline(system, about, model.script_outline_size(project["duration_minutes"]), target)
        texts: list[str] = []
        for index, chapter in enumerate(plan):
            await self.progress(job, f"writing chapter {index + 1} of {len(plan)}: {chapter['heading']}")
            texts.append(await self.writer.chapter(system, about, plan, index, texts[-1] if texts else ""))
        script = "\n\n".join(f"# {c['heading']}\n{t}" for c, t in zip(plan, texts))
        record = model.script_record(script, project["duration_minutes"], f"ai:{getattr(self.writer, 'model', 'model')}")
        record["outline"] = plan

        def apply(p: dict[str, Any]) -> None:
            if p["stages"]["script"]["state"] != "running":
                raise ProviderError("the script stage was changed while it was being written; result discarded")
            p["record"]["script"] = record
            model.finish_job(p["stages"], "script", True, f"{record['words']} words, {len(plan)} chapters")
        await self.save(tenant, pid, apply, {"kind": "script_saved", "stage": "script",
                                             "detail": {"words": record["words"], "sections": len(plan), "by": "ai"}})
        return f"{record['words']} words"

    async def run_voiceover(self, job: dict[str, Any]) -> str:
        tenant, pid = job["tenant_id"], job["project_id"]
        voice, speed = job["params"]["voice"], float(job["params"].get("speed", 1.0))
        project = await self.store.project(tenant, pid)
        script = project["record"].get("script") or {}
        if project["stages"]["script_approval"]["state"] != "approved" or not script.get("sections"):
            raise ProviderError("the script isn't approved")
        script_sha = hashlib.sha256(script["text"].encode()).hexdigest()
        sections, everything, total = [], [], 0.0
        for section in script["sections"]:
            await self.progress(job, f"voicing chapter {section['index']} of {len(script['sections'])}: {section['heading']}")
            parts = [await self.voices.speak(chunk, voice, speed) for chunk in chunks_for_speech(section["text"])]
            audio, seconds = join_wavs(parts)
            key = self.audio.key(tenant, pid, f"voice/{script_sha[:12]}/chapter-{section['index']:02d}.wav")
            await self.audio.put(key, audio)
            sections.append({"index": section["index"], "heading": section["heading"], "key": key, "seconds": round(seconds, 2)})
            everything.append(audio); total += seconds
        full, _ = join_wavs(everything, pause_seconds=0.8)
        full_key = self.audio.key(tenant, pid, f"voice/{script_sha[:12]}/narration.wav")
        await self.audio.put(full_key, full)
        record = {"voice": voice, "speed": speed, "script_sha256": script_sha, "sections": sections, "full_key": full_key,
                  "total_seconds": round(total, 1), "provider": "kokoro", "updated_at": model.now()}

        def apply(p: dict[str, Any]) -> None:
            current = p["record"].get("script") or {}
            if hashlib.sha256(current.get("text", "").encode()).hexdigest() != script_sha or p["stages"]["voiceover"]["state"] != "running":
                raise ProviderError("the script changed while it was being voiced; result discarded")
            p["record"]["voiceover"] = record
            model.finish_job(p["stages"], "voiceover", True, f"{round(total / 60, 1)} minutes, voice {voice}")
        await self.save(tenant, pid, apply, {"kind": "voiceover_saved", "stage": "voiceover",
                                             "detail": {"voice": voice, "seconds": record["total_seconds"]}})
        return f"{round(total / 60, 1)} minutes"

    async def run_scenes(self, job: dict[str, Any]) -> str:
        """The approved narration cut into scenes (every word, in order), each with what is seen and an image prompt;
        durations from the recorded voice; the recurring characters with one fixed look."""
        tenant, pid = job["tenant_id"], job["project_id"]
        project = await self.store.project(tenant, pid)
        channel = await self.store.channel(tenant, project["channel_id"]) or {}
        script, voice = project["record"].get("script") or {}, project["record"].get("voiceover") or {}
        system = system_prompt(channel)
        await self.progress(job, "finding the recurring characters")
        cast = await characters(self.writer, system, script.get("text", ""))
        seconds = {s["index"]: s["seconds"] for s in voice.get("sections", [])}
        scenes: list[dict[str, Any]] = []
        for section in script.get("sections", []):
            await self.progress(job, f"planning the scenes of chapter {section['index']} of {len(script['sections'])}")
            segments = model.split_scenes(section["text"])
            visuals = await scene_visuals(self.writer, system, project["title"], section["heading"], segments, cast)
            words = sum(len(s.split()) for s in segments) or 1
            for segment, visual in zip(segments, visuals):
                scenes.append({"number": len(scenes) + 1, "chapter": section["index"], "narration": segment, **visual,
                               "seconds": round(seconds.get(section["index"], words / model.WORDS_PER_MINUTE * 60) * len(segment.split()) / words, 2),
                               "seed": int(hashlib.sha256(f"{pid}:{len(scenes)}".encode()).hexdigest()[:8], 16) % 2**31,
                               "image": None})

        def apply(p: dict[str, Any]) -> None:
            if p["stages"]["scenes"]["state"] != "running":
                raise ProviderError("the scenes stage was changed meanwhile; result discarded")
            p["record"]["characters"] = cast
            p["record"]["scenes"] = scenes
            model.finish_job(p["stages"], "scenes", True, f"{len(scenes)} scenes, {len(cast)} recurring characters")
        await self.save(tenant, pid, apply, {"kind": "scenes_saved", "stage": "scenes", "detail": {"scenes": len(scenes), "characters": len(cast)}})
        return f"{len(scenes)} scenes"

    async def run_images(self, job: dict[str, Any]) -> str:
        """One 16:9 image per scene (or only the scenes asked for), each with its own seed so it can be made again."""
        if self.images is None:
            raise ProviderError("the image service isn't set up")
        tenant, pid = job["tenant_id"], job["project_id"]
        project = await self.store.project(tenant, pid)
        channel = await self.store.channel(tenant, project["channel_id"]) or {}
        scenes, cast = project["record"].get("scenes") or [], project["record"].get("characters") or []
        wanted = set(job["params"].get("scenes") or [s["number"] for s in scenes])
        made: dict[int, dict[str, Any]] = {}
        for scene in [s for s in scenes if s["number"] in wanted]:
            await self.progress(job, f"image {len(made) + 1} of {len(wanted)} (scene {scene['number']})")
            prompt = image_prompt(scene["prompt"] or scene["visual"], cast, channel.get("visual_preset", ""))
            data, engine = await self.images.generate(prompt, 1344, 768, scene["seed"])
            key = self.audio.key(tenant, pid, f"images/scene-{scene['number']:03d}-{scene['seed']}.png")
            await self.audio.put(key, data, "image/png")
            made[scene["number"]] = {"key": key, "prompt": prompt, "model": engine, "made_at": model.now()}

        def apply(p: dict[str, Any]) -> None:
            if p["stages"]["images"]["state"] != "running":
                raise ProviderError("the images stage was changed meanwhile; result discarded")
            for s in p["record"].get("scenes") or []:
                if s["number"] in made:
                    s["image"] = made[s["number"]]
            missing = sum(1 for s in p["record"].get("scenes") or [] if not s.get("image"))
            model.finish_job(p["stages"], "images", True, f"{len(made)} images made" + (f", {missing} scenes without one" if missing else ""))
        await self.save(tenant, pid, apply, {"kind": "images_saved", "stage": "images", "detail": {"images": len(made)}})
        return f"{len(made)} images"

    async def run_thumbnail(self, job: dict[str, Any]) -> str:
        """Three thumbnail concepts: a caption that isn't the title, drawn large on a background made for it."""
        if self.images is None:
            raise ProviderError("the image service isn't set up")
        tenant, pid = job["tenant_id"], job["project_id"]
        project = await self.store.project(tenant, pid)
        channel = await self.store.channel(tenant, project["channel_id"]) or {}
        await self.progress(job, "thinking up thumbnail concepts")
        concepts = await thumbnail_concepts(self.writer, system_prompt(channel), project["title"], (project["record"].get("script") or {}).get("text", ""))
        options = []
        for index, concept in enumerate(concepts, start=1):
            await self.progress(job, f"making thumbnail {index} of {len(concepts)}")
            seed = int(hashlib.sha256(f"{pid}:thumb:{index}:{model.now()}".encode()).hexdigest()[:8], 16) % 2**31
            prompt = image_prompt(concept["prompt"] + f", empty space on the {concept['text_side']} side", [], channel.get("visual_preset", ""))
            background, _ = await self.images.generate(prompt, 1344, 768, seed)
            key = self.audio.key(tenant, pid, f"thumbnails/thumb-{index}-{seed}.png")
            await self.audio.put(key, caption_thumbnail(background, concept["caption"], concept["text_side"]), "image/png")
            options.append(concept | {"key": key, "seed": seed})

        def apply(p: dict[str, Any]) -> None:
            if p["stages"]["thumbnail"]["state"] != "running":
                raise ProviderError("the thumbnail stage was changed meanwhile; result discarded")
            p["record"]["thumbnail"] = {"options": options, "chosen": None, "made_at": model.now()}
            model.finish_job(p["stages"], "thumbnail", True, f"{len(options)} options; choose one")
        await self.save(tenant, pid, apply, {"kind": "thumbnails_saved", "stage": "thumbnail", "detail": {"options": len(options)}})
        return f"{len(options)} thumbnails"

    async def run_seo(self, job: dict[str, Any]) -> str:
        tenant, pid = job["tenant_id"], job["project_id"]
        project = await self.store.project(tenant, pid)
        channel = await self.store.channel(tenant, project["channel_id"]) or {}
        script, voice = project["record"].get("script") or {}, project["record"].get("voiceover") or {}
        await self.progress(job, "writing the description, keywords and tags")
        package = await seo_package(self.writer, system_prompt(channel), project["title"], script.get("text", ""),
                                    chapter_marks(script.get("sections", []), voice.get("sections", [])))
        package["made_at"] = model.now()

        def apply(p: dict[str, Any]) -> None:
            if p["stages"]["seo"]["state"] != "running":
                raise ProviderError("the SEO stage was changed meanwhile; result discarded")
            p["record"]["seo"] = package
            model.finish_job(p["stages"], "seo", True, f"{len(package['tags'])} tags, {len(package['chapters'])} chapters")
        await self.save(tenant, pid, apply, {"kind": "seo_saved", "stage": "seo", "detail": {"tags": len(package["tags"])}})
        return f"{len(package['tags'])} tags"

    async def run_assembly(self, job: dict[str, Any]) -> str:
        """The finished video: every scene image on screen for exactly its narration, over the recorded chapters."""
        if self.renderer is None:
            raise ProviderError("the render service isn't set up")
        import tempfile
        tenant, pid = job["tenant_id"], job["project_id"]
        project = await self.store.project(tenant, pid)
        scenes, voice = project["record"].get("scenes") or [], project["record"].get("voiceover") or {}
        if not scenes or any(not (s.get("image") or {}).get("key") for s in scenes):
            raise ProviderError("every scene needs an image first")
        await self.progress(job, f"gathering {len(scenes)} images and the narration")
        files: dict[str, bytes] = {}
        chapter_seconds = {v["index"]: v["seconds"] for v in voice.get("sections", [])}
        timed = []
        for chapter, items in __import__("itertools").groupby(scenes, key=lambda s: s["chapter"]):
            items = list(items)
            target, have = chapter_seconds.get(chapter, sum(s["seconds"] for s in items)), sum(s["seconds"] for s in items) or 1
            spans = scene_spans([s["seconds"] for s in items], target)
            for scene, span in zip(items, spans):
                name = f"scene-{scene['number']:03d}.jpg"
                files[name] = to_jpeg(await self.audio.get(scene["image"]["key"]))
                timed.append({"image": name, "seconds": span, "narration": scene["narration"]})
        chapters = []
        for section in voice.get("sections", []):
            name = f"chapter-{section['index']:02d}.wav"
            files[name] = await self.audio.get(section["key"]); chapters.append(name)
        burn = bool(job["params"].get("burn_subtitles"))
        await self.progress(job, f"rendering {round(sum(t['seconds'] for t in timed) / 60, 1)} minutes of 1080p video on ghana")
        with tempfile.NamedTemporaryFile(suffix=".mp4", dir="/tmp") as out:
            took = await self.renderer.render({"scenes": timed, "chapters": chapters, "burn_subtitles": burn}, files, out.name)
            size = os.path.getsize(out.name)
            if size < 10_000:
                raise ProviderError("the render service returned an empty video")
            stamp = hashlib.sha256(f"{pid}:{model.now()}".encode()).hexdigest()[:10]
            key = self.audio.key(tenant, pid, f"video/{stamp}/video.mp4")
            await self.progress(job, "storing the video")
            await self.audio.put_file(key, out.name, "video/mp4")
        srt_key = self.audio.key(tenant, pid, f"video/{stamp}/captions.srt")
        await self.audio.put(srt_key, captions(timed).encode(), "application/x-subrip")
        record = {"key": key, "captions_key": srt_key, "bytes": size, "seconds": round(sum(t["seconds"] for t in timed), 1),
                  "width": 1920, "height": 1080, "fps": 30, "burned_subtitles": burn, "render_seconds": took, "made_at": model.now()}

        def apply(p: dict[str, Any]) -> None:
            if p["stages"]["assembly"]["state"] != "running":
                raise ProviderError("the assembly stage was changed meanwhile; result discarded")
            p["record"]["video"] = record
            model.finish_job(p["stages"], "assembly", True, f"{round(record['seconds'] / 60, 1)} minutes, 1080p")
        await self.save(tenant, pid, apply, {"kind": "video_saved", "stage": "assembly", "detail": {"seconds": record["seconds"], "bytes": size}})
        return f"{round(record['seconds'] / 60, 1)} min video"

    async def fail(self, job: dict[str, Any], reason: str) -> None:
        def apply(p: dict[str, Any]) -> None:
            if p["stages"][model.JOB_STAGE[job["kind"]]]["state"] == "running":
                model.finish_job(p["stages"], job["kind"], False, reason)
        try:
            await self.save(job["tenant_id"], job["project_id"], apply, {"kind": "job_failed", "stage": model.JOB_STAGE[job["kind"]],
                                                                         "detail": {"job": job["kind"], "error": reason}})
        finally:
            await self.store.finish_job(job["tenant_id"], job["id"], False, reason)

    async def once(self) -> bool:
        for job in await self.store.abandoned_jobs():
            await self.fail(job, "stopped three times without finishing")
        job = await self.store.claim_job()
        if job is None:
            return False
        log.info("job %s %s for %s/%s (attempt %s)", job["id"], job["kind"], job["tenant_id"], job["project_id"], job["attempts"])
        try:
            runner = {"script": self.run_script, "voiceover": self.run_voiceover, "scenes": self.run_scenes,
                      "images": self.run_images, "thumbnail": self.run_thumbnail, "seo": self.run_seo,
                      "assembly": self.run_assembly}[job["kind"]]
            done = await runner(job)
            await self.store.finish_job(job["tenant_id"], job["id"], True)
            log.info("job %s done: %s", job["id"], done)
        except ProviderError as exc:
            await self.fail(job, str(exc))
        except Exception as exc:  # noqa: BLE001 - recorded on the stage; the loop keeps serving other jobs
            log.exception("job %s failed", job["id"])
            await self.fail(job, f"unexpected error ({exc.__class__.__name__})")
        return True


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = await PostgresCreatorStore.connect(os.environ["DATABASE_URL"])
    voices, audio = VoiceService.from_env(), AudioStore.from_env()
    if voices is None or audio is None:
        raise SystemExit("TTS_URL, CREATORS_TTS_TOKEN and ASSET_BUCKET are required")
    worker = Worker(store, OllamaWriter.from_env(), voices, audio, ImageService.from_env(), RenderService.from_env())
    log.info("creators-worker ready (script model %s)", worker.writer.model)
    while True:
        if not await worker.once():
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())

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
from providers import (_json_chat, AudioStore, ImageService, MotionService, OllamaWriter, ProviderError, RenderService, VoiceService, caption_thumbnail,
                       captions, chapter_marks, characters, chunks_for_speech, image_prompt, join_wavs, motion_prompt, scene_visuals, seo_package,
                       thumbnail_concepts, to_jpeg)
import research as rs
import youtube as yt
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
    notes = rs.research_notes((project.get("record") or {}).get("research"))
    if notes:
        lines.append(notes)
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
    def __init__(self, store: Any, writer: Any, voices: Any, audio: Any, images: Any = None, renderer: Any = None, youtube: Any = None,
                 motion: Any = None):
        self.store, self.writer, self.voices, self.audio, self.images, self.renderer = store, writer, voices, audio, images, renderer
        self.youtube, self.motion = youtube, motion
        self.retry_wait = 30.0  # seconds before a scene's clip is tried again
        self.current: dict[str, Any] | None = None  # the job in hand (handed back if the worker is stopped)
        self.last_progress = ""

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
        self.last_progress = text
        await self.store.job_progress(job["tenant_id"], job["id"], text)

    async def heartbeat(self, job: dict[str, Any]) -> None:
        """Keeps the job's lease while one long step runs (a render can take longer than the lease)."""
        while True:
            await asyncio.sleep(120)
            try:
                await self.store.job_progress(job["tenant_id"], job["id"], self.last_progress)
            except Exception:  # noqa: BLE001 - a missed beat only shortens the lease
                log.warning("lease renewal failed for job %s", job["id"])

    async def run_research(self, job: dict[str, Any]) -> str:
        """Angles, viewer questions, points to cover, search keywords and scored title options. Suggestions only:
        the creator still picks (or types) the title and approves it."""
        tenant, pid = job["tenant_id"], job["project_id"]
        project = await self.store.project(tenant, pid)
        channel = await self.store.channel(tenant, project["channel_id"]) or {}
        await self.progress(job, "researching angles, questions and titles")
        system = ("You are a YouTube strategist who researches videos before they are made. You know what this audience "
                  "searches for and clicks, and you never invent statistics or sources.")
        found = rs.parse_research(await _json_chat(self.writer, system, rs.research_ask(project, channel), 3000), project.get("title", ""))
        found["model"], found["made_at"] = getattr(self.writer, "model", "model"), model.now()

        def apply(p: dict[str, Any]) -> None:
            if p["stages"]["research"]["state"] != "running":
                raise ProviderError("the research stage was changed meanwhile; result discarded")
            p["record"]["research"] = found
            title = p["record"].setdefault("title", {})
            title["options"] = [t["title"] for t in found["titles"]]
            model.finish_job(p["stages"], "research", True, f"{len(found['titles'])} titles, best scores {found['titles'][0]['score']}")
        await self.save(tenant, pid, apply, {"kind": "research_saved", "stage": "research", "detail": {"titles": len(found["titles"])}})
        return f"{len(found['titles'])} titles"

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

    async def run_motion(self, job: dict[str, Any]) -> str:
        """A moving clip per scene, starting on the scene's image (Wan 2.2 on ghana). Scenes set to stay still are
        skipped. Clips already made from the current image are kept unless the scene was asked for by number, so a
        job that was interrupted carries on where it stopped. Saved every few scenes."""
        if self.motion is None:
            raise ProviderError("the motion service isn't set up")
        tenant, pid = job["tenant_id"], job["project_id"]
        project = await self.store.project(tenant, pid)
        cast = project["record"].get("characters") or []
        asked = set(job["params"].get("scenes") or [])
        quality = job["params"].get("quality", "standard")
        todo = [s for s in project["record"].get("scenes") or [] if (not asked or s["number"] in asked) and not s.get("still")
                and (s.get("image") or {}).get("key") and (asked or not model.motion_ready(s))]
        pending: dict[int, dict[str, Any]] = {}
        made, seconds_made, spent = 0, 0.0, 0.0

        def keep(p: dict[str, Any]) -> None:
            if p["stages"]["images"]["state"] != "running":
                raise ProviderError("the visuals were changed meanwhile; motion stopped")
            for s in p["record"].get("scenes") or []:
                if s["number"] in pending and (s.get("image") or {}).get("made_at") == pending[s["number"]]["image_made_at"]:
                    s["motion"] = pending[s["number"]]

        for scene in todo:
            left = ""
            if made:
                per_second = spent / max(seconds_made, 1)
                left = f", about {max(1, round(per_second * sum(float(s['seconds']) for s in todo[made:]) / 60))} min left"
            await self.progress(job, f"animating scene {scene['number']} ({made + 1} of {len(todo)}){left}")
            image = await self.audio.get(scene["image"]["key"])
            prompt = motion_prompt(scene, cast)
            seconds = float(scene["seconds"])
            for attempt in range(3):
                try:
                    data, engine, took = await self.motion.animate(to_jpeg(image), prompt, seconds, scene["seed"], quality)
                    break
                except ProviderError:
                    if attempt == 2:
                        if pending:
                            await self.store_motion(tenant, pid, keep)
                        raise
                    await self.progress(job, f"scene {scene['number']}: the motion service didn't answer, trying again")
                    await asyncio.sleep(self.retry_wait)
            key = self.audio.key(tenant, pid, f"motion/scene-{scene['number']:03d}-{scene['seed']}-{hashlib.sha256(prompt.encode()).hexdigest()[:8]}.mp4")
            await self.audio.put(key, data, "video/mp4")
            pending[scene["number"]] = {"key": key, "image_key": scene["image"]["key"], "image_made_at": scene["image"].get("made_at"), "prompt": prompt, "model": engine, "quality": quality,
                                        "seconds": seconds, "bytes": len(data), "took": took, "made_at": model.now()}
            made, seconds_made, spent = made + 1, seconds_made + seconds, spent + (took or 1)
            if len(pending) >= 4:
                await self.store_motion(tenant, pid, keep); pending.clear()

        def finish(p: dict[str, Any]) -> None:
            keep(p)
            scenes = p["record"].get("scenes") or []
            moving = sum(1 for s in scenes if model.motion_ready(s))
            model.finish_job(p["stages"], "motion", True, f"{moving} of {len(scenes)} scenes in motion"
                             + (f", {sum(1 for s in scenes if s.get('still'))} kept still" if any(s.get("still") for s in scenes) else ""))
        await self.save(tenant, pid, finish, {"kind": "motion_saved", "stage": "images", "detail": {"clips": made}})
        return f"{made} clips"

    async def store_motion(self, tenant: str, pid: str, change) -> None:
        """Saves clips made so far without a history entry per scene (one 'motion made' entry at the end)."""
        for _ in range(5):
            project = await self.store.project(tenant, pid)
            change(project)
            try:
                await self.store.save_project(tenant, project, project["version"], {"kind": "motion_progress", "stage": "images", "actor": WORKER})
                return
            except LookupError:
                await asyncio.sleep(0.5)

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
        """The finished video: every scene (its motion clip, or its image with a slow zoom) on screen for exactly its narration, over the recorded chapters."""
        import shutil
        import tempfile
        workdir = tempfile.mkdtemp(prefix="assembly-", dir="/tmp")
        try:
            return await self._assemble(job, workdir)
        finally:
            shutil.rmtree(workdir, True)

    async def _assemble(self, job: dict[str, Any], workdir: str) -> str:
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
                entry = {"image": name, "seconds": span, "narration": scene["narration"]}
                if model.motion_ready(scene):
                    clip = f"scene-{scene['number']:03d}.mp4"
                    path = os.path.join(workdir, clip)
                    await self.audio.download(scene["motion"]["key"], path)
                    files[clip], entry["motion"] = path, [clip]
                timed.append(entry)
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

    async def run_publish(self, job: dict[str, Any]) -> str:
        """Uploads the approved video with its title, description, tags, privacy (or schedule), then the chosen
        thumbnail and the captions. The video is read from storage in chunks."""
        if self.youtube is None:
            raise ProviderError("YouTube isn't set up")
        tenant, pid, params = job["tenant_id"], job["project_id"], job["params"]
        project = await self.store.project(tenant, pid)
        link = await self.store.youtube(tenant, project["channel_id"])
        if link is None or link["youtube_channel_id"] != params["youtube_channel_id"]:
            raise ProviderError("the channel's YouTube connection changed; connect it again")
        seo, video, thumb = project["record"]["seo"], project["record"]["video"], project["record"]["thumbnail"]
        try:
            token = await self.youtube.access_token(yt.unseal(link["refresh_token_sealed"]))
            status: dict[str, Any] = {"privacyStatus": params["privacy"], "selfDeclaredMadeForKids": bool(params["made_for_kids"])}
            if params.get("publish_at"):
                status["publishAt"] = params["publish_at"]
            metadata = {"snippet": {"title": project["title"][:100], "description": seo["full_description"][:5000],
                                    "tags": seo["tags"], "categoryId": params.get("category_id", "27")}, "status": status}
            await self.progress(job, f"uploading the video ({round(video['bytes'] / 1e6)} MB)")
            size, chunks = await self.audio.stream(video["key"])
            video_id = await self.youtube.upload(token, metadata, size, chunks)
            notes = []
            await self.progress(job, "setting the thumbnail")
            try:
                await self.youtube.set_thumbnail(token, video_id, to_jpeg(await self.audio.get(thumb["options"][thumb["chosen"] - 1]["key"]), 90))
            except yt.YouTubeError as exc:
                notes.append(str(exc))
            await self.progress(job, "adding the captions")
            try:
                await self.youtube.add_captions(token, video_id, await self.audio.get(video["captions_key"]))
            except yt.YouTubeError as exc:
                notes.append(str(exc))
        except yt.YouTubeError as exc:
            raise ProviderError(str(exc)) from exc
        record = {"video_id": video_id, "url": f"https://youtu.be/{video_id}", "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
                  "privacy": params["privacy"], "publish_at": params.get("publish_at", ""), "channel": link["title"],
                  "notes": notes, "uploaded_at": model.now()}

        def apply(p: dict[str, Any]) -> None:
            if p["stages"]["publishing"]["state"] != "running":
                raise ProviderError("the publishing stage was changed meanwhile")
            p["record"]["youtube"] = record
            model.finish_job(p["stages"], "publish", True, f"uploaded {params['privacy']}" + (f", goes public {params['publish_at']}" if params.get("publish_at") else ""))
        await self.save(tenant, pid, apply, {"kind": "published", "stage": "publishing", "detail": {"video_id": video_id, "privacy": params["privacy"]}})
        return f"YouTube {video_id}"

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
        self.current, self.last_progress = job, "started"
        beat = asyncio.ensure_future(self.heartbeat(job))
        try:
            runner = {"script": self.run_script, "voiceover": self.run_voiceover, "scenes": self.run_scenes,
                      "images": self.run_images, "thumbnail": self.run_thumbnail, "seo": self.run_seo,
                      "assembly": self.run_assembly, "publish": self.run_publish, "research": self.run_research,
                  "motion": self.run_motion}[job["kind"]]
            done = await runner(job)
            await self.store.finish_job(job["tenant_id"], job["id"], True)
            log.info("job %s done: %s", job["id"], done)
        except ProviderError as exc:
            await self.fail(job, str(exc))
        except Exception as exc:  # noqa: BLE001 - recorded on the stage; the loop keeps serving other jobs
            log.exception("job %s failed", job["id"])
            await self.fail(job, f"unexpected error ({exc.__class__.__name__})")
        finally:
            beat.cancel()
            self.current = None
        return True


async def serve(worker: Worker, stop: asyncio.Event) -> None:
    """Runs jobs until told to stop. On SIGTERM (a redeploy) the job in hand is handed back, so the next worker
    starts it at once instead of it waiting out its lease."""
    while not stop.is_set():
        task = asyncio.ensure_future(worker.once())
        stopper = asyncio.ensure_future(stop.wait())
        done, _ = await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            stopper.cancel()
            if not task.result():
                try:
                    await asyncio.wait_for(stop.wait(), 3)
                except asyncio.TimeoutError:
                    pass
            continue
        task.cancel()
        job = worker.current
        if job is not None:
            await worker.store.release_job(job["tenant_id"], job["id"])
            log.info("stopping: job %s handed back", job["id"])
        return


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = await PostgresCreatorStore.connect(os.environ["DATABASE_URL"])
    voices, audio = VoiceService.from_env(), AudioStore.from_env()
    if voices is None or audio is None:
        raise SystemExit("TTS_URL, CREATORS_TTS_TOKEN and ASSET_BUCKET are required")
    worker = Worker(store, OllamaWriter.from_env(), voices, audio, ImageService.from_env(), RenderService.from_env(), yt.YouTube.from_env(),
                    MotionService.from_env())
    log.info("creators-worker ready (script model %s)", worker.writer.model)
    import signal
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await serve(worker, stop)


if __name__ == "__main__":
    asyncio.run(main())

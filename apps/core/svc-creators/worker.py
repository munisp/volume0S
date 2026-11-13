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
from providers import AudioStore, OllamaWriter, ProviderError, VoiceService, chunks_for_speech, join_wavs
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


class Worker:
    def __init__(self, store: Any, writer: Any, voices: Any, audio: Any):
        self.store, self.writer, self.voices, self.audio = store, writer, voices, audio

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
            done = await (self.run_script(job) if job["kind"] == "script" else self.run_voiceover(job))
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
    worker = Worker(store, OllamaWriter.from_env(), voices, audio)
    log.info("creators-worker ready (script model %s)", worker.writer.model)
    while True:
        if not await worker.once():
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())

"""Creators Home project model: the 12 production stages, their states and the rules for moving between them.

Every project has one shared record (title, script, voice, scenes, thumbnail, SEO, video, publishing). Each stage
reads what the stages before it produced and writes its own section, so the voiceover is generated from the
approved script, the scenes from the same narration, and the SEO package from the finished content.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# (key, label, kind). "work" stages are produced (by AI or by hand); "approval" stages are a person's decision and
# nothing after them starts until they are approved.
STAGES: list[tuple[str, str, str]] = [
    ("research", "Research", "work"),
    ("title", "Title selection", "approval"),
    ("script", "Script generation", "work"),
    ("script_approval", "Script approval", "approval"),
    ("voiceover", "Voiceover", "work"),
    ("scenes", "Scene breakdown", "work"),
    ("images", "Image generation", "work"),
    ("thumbnail", "Thumbnail", "work"),
    ("seo", "SEO package", "work"),
    ("assembly", "Video assembly", "work"),
    ("final_review", "Final review", "approval"),
    ("publishing", "YouTube publishing", "approval"),
]
STAGE_KEYS = [key for key, _, _ in STAGES]
APPROVAL_STAGES = {key for key, _, kind in STAGES if kind == "approval"}
STATES = {"pending", "running", "needs_approval", "approved", "done", "failed", "skipped"}
FINISHED = {"approved", "done", "skipped"}

DURATIONS = (5, 10, 15, 20, 30, 45)
WORDS_PER_MINUTE = 150  # spoken narration; a 15-minute video needs about 2,250 words


class ProjectError(ValueError):
    """A request that breaks a project rule; `code` is the API error code."""

    def __init__(self, message: str, code: str = "invalid_request", status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def target_words(duration_minutes: int) -> int:
    if duration_minutes not in DURATIONS:
        raise ProjectError(f"duration must be one of {', '.join(map(str, DURATIONS))} minutes", status=400)
    return duration_minutes * WORDS_PER_MINUTE


def new_stages() -> dict[str, dict[str, Any]]:
    return {key: {"state": "pending", "updated_at": None, "note": ""} for key in STAGE_KEYS}


def stage_index(stage: str) -> int:
    if stage not in STAGE_KEYS:
        raise ProjectError(f"unknown stage {stage!r}", "unknown_stage", 404)
    return STAGE_KEYS.index(stage)


def earlier_finished(stages: dict[str, dict[str, Any]], stage: str) -> bool:
    return all(stages[key]["state"] in FINISHED for key in STAGE_KEYS[:stage_index(stage)])


def set_state(stages: dict[str, dict[str, Any]], stage: str, state: str, note: str = "") -> None:
    if state not in STATES:
        raise ProjectError(f"unknown stage state {state!r}", status=400)
    stages[stage] = {"state": state, "updated_at": now(), "note": note[:1000]}


# Stages that don't build on every stage before them: the thumbnail comes from the title and the approved script, so
# remaking the voice, scenes or images leaves it alone.
BUILT_ON = {"thumbnail": "script_approval", "seo": "voiceover"}


def reset_after(stages: dict[str, dict[str, Any]], stage: str) -> list[str]:
    """Everything after `stage` that was built on it goes back to pending."""
    reset = []
    for key in STAGE_KEYS[stage_index(stage) + 1:]:
        if key in BUILT_ON and stage_index(stage) > stage_index(BUILT_ON[key]):
            continue
        if stages[key]["state"] != "pending":
            reset.append(key)
        stages[key] = {"state": "pending", "updated_at": now(), "note": ""}
    return reset


def current_stage(stages: dict[str, dict[str, Any]]) -> str | None:
    """The first stage that isn't finished: where the project stands."""
    return next((key for key in STAGE_KEYS if stages[key]["state"] not in FINISHED), None)


def approve(stages: dict[str, dict[str, Any]], stage: str, note: str = "") -> None:
    stage_index(stage)
    if stage not in APPROVAL_STAGES:
        raise ProjectError(f"{stage} is produced, not approved", "not_an_approval")
    if stages[stage]["state"] != "needs_approval":
        raise ProjectError(f"{stage} isn't waiting for approval", "invalid_state")
    if not earlier_finished(stages, stage):
        raise ProjectError("an earlier stage isn't finished yet", "earlier_stage_open")
    set_state(stages, stage, "approved", note)


def reopen(stages: dict[str, dict[str, Any]], stage: str, reason: str) -> list[str]:
    stage_index(stage)
    if not reason.strip():
        raise ProjectError("say why the stage is reopened", status=400)
    if stages[stage]["state"] == "pending":
        raise ProjectError(f"{stage} hasn't started", "invalid_state")
    state = "needs_approval" if stage in APPROVAL_STAGES and stages[stage]["state"] == "approved" else "pending"
    set_state(stages, stage, state, reason)
    return reset_after(stages, stage)


def split_sections(script: str) -> list[dict[str, Any]]:
    """Chapters of a script: a line starting with '#' or 'CHAPTER'/'SCENE' starts one; otherwise one section.
    The voiceover and the scenes are generated per section, in this order."""
    sections: list[dict[str, Any]] = []
    heading, lines = "Opening", []
    for raw in script.splitlines():
        line = raw.strip()
        if line.startswith("#") or re.match(r"^(chapter|scene|part)\b", line, re.I):
            if any(item.strip() for item in lines):
                sections.append({"heading": heading, "text": "\n".join(lines).strip()})
            heading, lines = line.lstrip("#").strip()[:200] or "Section", []
        else:
            lines.append(raw)
    if any(item.strip() for item in lines):
        sections.append({"heading": heading, "text": "\n".join(lines).strip()})
    for index, section in enumerate(sections, start=1):
        section["index"] = index
        section["words"] = len(section["text"].split())
    return sections


def script_record(text: str, duration_minutes: int, source: str) -> dict[str, Any]:
    sections = split_sections(text)
    words = sum(section["words"] for section in sections)
    if words < 50:
        raise ProjectError("the script needs at least 50 words of narration", status=400)
    target = target_words(duration_minutes)
    return {
        "text": text, "sections": sections, "words": words, "target_words": target,
        "estimated_minutes": round(words / WORDS_PER_MINUTE, 1), "source": source, "updated_at": now(),
    }


# Jobs: work the creators worker does for a stage. A job can only start when the stages before its stage are finished
# (an approved title before the script; an approved script before the voice), and starting one sends everything
# built on its stage back to not started.
JOB_STAGE = {"research": "research", "script": "script", "voiceover": "voiceover", "scenes": "scenes", "images": "images", "thumbnail": "thumbnail",
             "seo": "seo", "assembly": "assembly", "publish": "publishing",
             "motion": "images"}  # motion clips are made from the scene images, as part of the visuals
# What has to be finished first: an approved title before the script, the approved script before the voice and the
# thumbnail, the voiceover (its timings) before the scenes, the scenes before their images.
JOB_NEEDS: dict[str, str | None] = {"research": None, "script": "title", "voiceover": "script_approval", "scenes": "voiceover", "images": "scenes",
             "thumbnail": "script_approval", "seo": "voiceover", "assembly": "images", "publish": "final_review",
             "motion": "images"}
# Jobs whose result nothing later is built on: making them again resets nothing.
SIDE_JOBS = {"research", "thumbnail", "seo"}  # research only suggests: the creator still picks the title
SCENE_WORDS = 25  # about 10 seconds of narration per scene
VOICE_SPEEDS = (0.6, 1.6)


def start_job(stages: dict[str, dict[str, Any]], kind: str, partial: bool = False) -> list[str]:
    if kind not in JOB_STAGE:
        raise ProjectError(f"unknown job {kind!r}", status=400)
    stage, needs = JOB_STAGE[kind], JOB_NEEDS[kind]
    if needs and stages[needs]["state"] not in FINISHED:
        what = needs.replace("_approval", "").replace("_", " ")
        raise ProjectError(f"{'approve' if needs in APPROVAL_STAGES else 'finish'} the {what} first", "earlier_stage_open")
    if stages[stage]["state"] == "running":
        raise ProjectError(f"the {kind} is already being made", "job_running")
    if kind == "research" and stages["title"]["state"] == "approved":
        raise ProjectError("the title is approved; reopen it to research again", "invalid_state")
    if kind == "script" and stages["script_approval"]["state"] == "approved":
        raise ProjectError("the script is approved; reopen it to write a new one", "invalid_state")
    # The thumbnail doesn't depend on the scenes or images, and remaking some images keeps the rest: nothing else changes.
    reset = [] if kind in SIDE_JOBS or partial else reset_after(stages, stage)
    set_state(stages, stage, "running", "queued")
    return reset


def finish_job(stages: dict[str, dict[str, Any]], kind: str, ok: bool, note: str) -> None:
    stage = JOB_STAGE[kind]
    if not ok and kind == "motion":
        # The images are all still there: the video can be made (with stills where clips are missing) or motion run again,
        # which carries on from the scenes already animated.
        set_state(stages, stage, "done", f"motion stopped: {note}. Animate again to carry on")
        return
    if not ok:
        set_state(stages, stage, "failed", note)
        return
    set_state(stages, stage, "done", note)
    if kind == "script":
        set_state(stages, "script_approval", "needs_approval")
    if kind == "assembly":
        set_state(stages, "final_review", "needs_approval")


def motion_ready(scene: dict[str, Any]) -> bool:
    """A scene plays as motion when it has a clip made from its current image and isn't set to stay still."""
    clip, image = scene.get("motion") or {}, scene.get("image") or {}
    return (bool(clip.get("key")) and not scene.get("still") and not clip.get("stale")
            and clip.get("image_key") == image.get("key") and clip.get("image_made_at") == image.get("made_at"))


def script_outline_size(duration_minutes: int) -> int:
    """How many chapters a script of this length gets: one roughly every 2.5 minutes, at least 3."""
    return max(3, min(18, round(duration_minutes / 2.5)))


def split_scenes(text: str, words: int = SCENE_WORDS) -> list[str]:
    """A chapter's narration as consecutive scenes of whole sentences, about `words` words each. Every word lands in
    exactly one scene, in order: the scenes are the narration, cut, never rewritten."""
    import re as _re
    sentences = [s.strip() for s in _re.split(r"(?<=[.!?])\s+|\n\s*\n", text) if s.strip()]
    scenes, current = [], []
    for sentence in sentences:
        current.append(sentence)
        if sum(len(s.split()) for s in current) >= words:
            scenes.append(" ".join(current)); current = []
    if current:
        if scenes and sum(len(s.split()) for s in current) < words // 3:
            scenes[-1] += " " + " ".join(current)
        else:
            scenes.append(" ".join(current))
    return scenes

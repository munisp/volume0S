"""svc-creators: Creators Home (Offer 35), a YouTube production line for creators.

Channels (each with its own writing instructions, visual preset and default voice) and video projects. A project
holds one shared record that every production stage reads and writes, and the state of its 12 stages; approval
stages are a person's decision, and nothing after one starts until it is approved. Every change is kept in the
project's history.

Callers: svc-orchestrator for the console, and the creators worker, both with the service token and
X-VolumeOS-Tenant. The orchestrator checks the person's role and passes who they are as X-VolumeOS-Actor.
"""
from __future__ import annotations

import hmac
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

import model
from model import ProjectError
from providers import AudioStore, ProviderError, VoiceService
import youtube as yt
from store import PostgresCreatorStore

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
HANDLE = re.compile(r"^@?[A-Za-z0-9._-]{3,30}$")
ACTOR = re.compile(r"^[A-Za-z0-9@._:+-]{1,254}$")


def plain(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) > limit or any(ch in value for ch in "\x00"):
        raise ValueError(f"at most {limit} characters of plain text")
    return value


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChannelInput(InputModel):
    name: str = Field(min_length=1, max_length=80)
    youtube_handle: str = Field(default="", max_length=31)
    niche: str = Field(default="", max_length=200)
    audience: str = Field(default="", max_length=500)
    language: str = Field(default="en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    writing_instructions: str = Field(default="", max_length=8000)
    visual_preset: str = Field(default="", max_length=2000)
    default_voice: str = Field(default="", max_length=80)

    @field_validator("youtube_handle")
    @classmethod
    def handle(cls, value: str) -> str:
        value = value.strip()
        if value and not HANDLE.fullmatch(value):
            raise ValueError("a YouTube handle looks like @MoneyFilesDecoded")
        return value if not value or value.startswith("@") else "@" + value


class ChannelUpdate(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    youtube_handle: str | None = Field(default=None, max_length=31)
    niche: str | None = Field(default=None, max_length=200)
    audience: str | None = Field(default=None, max_length=500)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    writing_instructions: str | None = Field(default=None, max_length=8000)
    visual_preset: str | None = Field(default=None, max_length=2000)
    default_voice: str | None = Field(default=None, max_length=80)

    @field_validator("youtube_handle")
    @classmethod
    def handle(cls, value: str | None) -> str | None:
        return None if value is None else ChannelInput.handle(value)


class ProjectInput(InputModel):
    channel_id: str = Field(pattern=model.UUID.pattern)
    topic: str = Field(min_length=3, max_length=2000)
    title: str = Field(default="", max_length=200)
    duration_minutes: Literal[5, 10, 15, 20, 30, 45]
    style: str = Field(default="", max_length=1000)
    audience: str = Field(default="", max_length=1000)


class ProjectUpdate(InputModel):
    topic: str | None = Field(default=None, min_length=3, max_length=2000)
    duration_minutes: Literal[5, 10, 15, 20, 30, 45] | None = None
    style: str | None = Field(default=None, max_length=1000)
    audience: str | None = Field(default=None, max_length=1000)
    version: int = Field(ge=1)


class TitleInput(InputModel):
    chosen: str = Field(min_length=3, max_length=100)  # YouTube's title limit
    options: list[str] = Field(default_factory=list, max_length=20)
    version: int = Field(ge=1)


class ScriptInput(InputModel):
    text: str = Field(min_length=1, max_length=120_000)  # a 45-minute script is about 6,750 words
    version: int = Field(ge=1)


class JobInput(InputModel):
    kind: Literal["script", "voiceover", "scenes", "images", "thumbnail", "seo", "assembly", "publish"]
    privacy: Literal["private", "unlisted", "public"] = "private"   # publish
    publish_at: str = Field(default="", max_length=40)              # publish: ISO time to go public (scheduled)
    made_for_kids: bool = False
    category_id: str = Field(default="27", pattern=r"^\d{1,3}$")    # 27 Education, 22 People & Blogs
    burn_subtitles: bool = False  # assembly: subtitles in the picture as well as the separate captions file
    scenes: list[int] = Field(default_factory=list, max_length=500)  # images: only these scenes (empty: all)
    voice: str = Field(default="", max_length=40, pattern=r"^([a-z]{2}_[a-z]{2,20})?$")
    speed: float = Field(default=1.0, ge=0.6, le=1.6)
    version: int = Field(ge=1)


class SceneEdit(InputModel):
    visual: str = Field(default="", max_length=600)
    prompt: str = Field(min_length=3, max_length=600)
    version: int = Field(ge=1)


class ThumbnailChoice(InputModel):
    chosen: int = Field(ge=1, le=10)
    version: int = Field(ge=1)


class PreviewInput(InputModel):
    voice: str = Field(pattern=r"^[a-z]{2}_[a-z]{2,20}$")
    text: str = Field(default="", max_length=400)
    speed: float = Field(default=1.0, ge=0.6, le=1.6)


class StageAction(InputModel):
    note: str = Field(default="", max_length=1000)
    version: int = Field(ge=1)


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def view(project: dict[str, Any]) -> dict[str, Any]:
    """A project as the console shows it: its stages in order with labels, and where it stands."""
    stages = [{"key": key, "label": label, "kind": kind, **project["stages"][key]} for key, label, kind in model.STAGES]
    return project | {"stages": stages, "current_stage": model.current_stage(project["stages"]),
                      "target_words": model.target_words(project["duration_minutes"])}


def create_app(store: Any = None, voices: Any = None, audio: Any = None, youtube: Any = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.store is None:
            app.state.store = await PostgresCreatorStore.connect(os.environ["DATABASE_URL"])
        if app.state.voices is None:
            app.state.voices = VoiceService.from_env()
        if app.state.audio is None:
            app.state.audio = AudioStore.from_env()
        if app.state.youtube is None:
            app.state.youtube = yt.YouTube.from_env()
        yield
        if hasattr(app.state.store, "close"):
            await app.state.store.close()

    app = FastAPI(title="VolumeOS Creators Home", lifespan=lifespan)
    app.state.store, app.state.voices, app.state.audio, app.state.youtube = store, voices, audio, youtube
    console = os.environ.get("CONSOLE_URL", "https://volumeos.newfire.app").rstrip("/")
    token = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")

    def caller(request: Request) -> tuple[str, str] | JSONResponse:
        """Service token + business header (+ who is acting); returns (tenant, actor) or the refusal."""
        provided = request.headers.get("authorization", "").removeprefix("Bearer ")
        if len(token) < 32 or not hmac.compare_digest(provided.encode(), token.encode()):
            return error(401, "unauthorized", "service token required")
        tenant = request.headers.get("x-volumeos-tenant", "")
        if not SAFE_ID.fullmatch(tenant) or tenant == "platform":
            return error(400, "invalid_tenant", "a business is required")
        actor = request.headers.get("x-volumeos-actor", "")
        return tenant, actor if ACTOR.fullmatch(actor) else ""

    @app.exception_handler(ProviderError)
    async def provider_error(_request: Request, exc: ProviderError):
        return error(503, "provider_unavailable", str(exc))

    @app.exception_handler(ProjectError)
    async def project_error(_request: Request, exc: ProjectError):
        return error(exc.status, exc.code, str(exc))

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        await app.state.store.ready()
        return {"status": "ready"}

    @app.get("/v1/creators/stages")
    async def stages(request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        return {"stages": [{"key": k, "label": label, "kind": kind} for k, label, kind in model.STAGES],
                "durations": list(model.DURATIONS), "words_per_minute": model.WORDS_PER_MINUTE}

    # Channels -----------------------------------------------------------------------------------------------------
    @app.get("/v1/creators/channels")
    async def channels(request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        return {"channels": await app.state.store.channels(who[0])}

    @app.post("/v1/creators/channels", status_code=201)
    async def create_channel(body: ChannelInput, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            return await app.state.store.create_channel(who[0], body.model_dump())
        except KeyError as exc:
            return error(409, "duplicate_channel", str(exc).strip("'"))

    def channel_id_ok(channel_id: str) -> bool:
        return bool(model.UUID.fullmatch(channel_id))

    @app.patch("/v1/creators/channels/{channel_id}")
    async def update_channel(channel_id: str, body: ChannelUpdate, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        found = channel_id_ok(channel_id) and await app.state.store.update_channel(
            who[0], channel_id, body.model_dump(exclude_none=True))
        return found or error(404, "not_found", "no such channel in this business")

    @app.delete("/v1/creators/channels/{channel_id}")
    async def delete_channel(channel_id: str, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            removed = channel_id_ok(channel_id) and await app.state.store.delete_channel(who[0], channel_id)
        except ValueError:
            return error(409, "channel_in_use", "the channel still has projects; delete or move them first")
        return {"status": "deleted"} if removed else error(404, "not_found", "no such channel in this business")

    # Projects -----------------------------------------------------------------------------------------------------
    async def load(tenant: str, project_id: str) -> dict[str, Any]:
        project = model.UUID.fullmatch(project_id) and await app.state.store.project(tenant, project_id)
        if not project:
            raise ProjectError("no such project in this business", "not_found", 404)
        return project

    async def save(tenant: str, project: dict[str, Any], version: int, event: dict[str, Any]) -> dict[str, Any]:
        if project["version"] != version:
            raise ProjectError("the project changed since you opened it; reload it first", "stale_version")
        try:
            return view(await app.state.store.save_project(tenant, project, version, event))
        except LookupError as exc:
            raise ProjectError("the project changed since you opened it; reload it first", "stale_version") from exc

    @app.get("/v1/creators/projects")
    async def projects(request: Request, channel_id: str | None = Query(default=None)):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        if channel_id and not channel_id_ok(channel_id):
            return error(400, "invalid_request", "channel_id must be a channel id")
        return {"projects": [view(p) for p in await app.state.store.projects(who[0], channel_id)]}

    @app.post("/v1/creators/projects", status_code=201)
    async def create_project(body: ProjectInput, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        if not await app.state.store.channel(tenant, body.channel_id):
            return error(404, "not_found", "no such channel in this business")
        stages = model.new_stages()
        record: dict[str, Any] = {"costs": [], "title": {}}
        if body.title.strip():
            # A title the creator already has: research is optional, the title still needs approving.
            record["title"] = {"chosen": body.title.strip(), "options": [body.title.strip()], "source": "creator"}
            model.set_state(stages, "research", "skipped", "title supplied by the creator")
            model.set_state(stages, "title", "needs_approval")
        project = {"channel_id": body.channel_id, "title": body.title.strip(), "topic": body.topic.strip(),
                   "duration_minutes": body.duration_minutes, "style": body.style.strip(),
                   "audience": body.audience.strip(), "stages": stages, "record": record}
        return view(await app.state.store.create_project(tenant, project, actor))

    @app.get("/v1/creators/projects/{project_id}")
    async def project(project_id: str, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        return view(await load(who[0], project_id))

    @app.patch("/v1/creators/projects/{project_id}")
    async def update_project(project_id: str, body: ProjectUpdate, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        project = await load(tenant, project_id)
        changes = body.model_dump(exclude_none=True, exclude={"version"})
        if not changes:
            return view(project)
        # The brief feeds the script: changing it after the script exists starts the script over.
        reset: list[str] = []
        if project["stages"]["script"]["state"] != "pending":
            reset = model.reset_after(project["stages"], "title")
        project.update(changes)
        return await save(tenant, project, body.version, {"kind": "brief_changed", "actor": actor,
                                                          "detail": {"changed": sorted(changes), "reset": reset}})

    @app.put("/v1/creators/projects/{project_id}/title")
    async def set_title(project_id: str, body: TitleInput, request: Request):
        """The chosen title (typed, or picked from research). It waits for approval; the script comes after."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        project = await load(tenant, project_id)
        stages = project["stages"]
        if stages["research"]["state"] == "pending":
            model.set_state(stages, "research", "skipped", "title chosen without research")
        elif stages["research"]["state"] not in model.FINISHED:
            raise ProjectError("research is still running", "earlier_stage_open")
        options = [o.strip() for o in body.options if o.strip()][:20]
        chosen = body.chosen.strip()
        project["record"]["title"] = {"chosen": chosen, "options": options or [chosen], "source": "creator"}
        project["title"] = chosen
        reset = model.reset_after(stages, "title")
        model.set_state(stages, "title", "needs_approval")
        return await save(tenant, project, body.version, {"kind": "title_set", "stage": "title", "actor": actor,
                                                          "detail": {"title": chosen, "reset": reset}})

    @app.put("/v1/creators/projects/{project_id}/script")
    async def set_script(project_id: str, body: ScriptInput, request: Request):
        """A script written or edited by hand (or saved by the worker). It needs the approved title first and is
        then waiting for approval; voice and scenes are built from the approved version only."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        project = await load(tenant, project_id)
        stages = project["stages"]
        if stages["title"]["state"] != "approved":
            raise ProjectError("approve the title before the script", "earlier_stage_open")
        source = "worker" if actor.startswith("worker:") else "creator"
        project["record"]["script"] = model.script_record(body.text, project["duration_minutes"], source)
        reset = model.reset_after(stages, "script")
        model.set_state(stages, "script", "done", f"{project['record']['script']['words']} words")
        model.set_state(stages, "script_approval", "needs_approval")
        return await save(tenant, project, body.version, {
            "kind": "script_saved", "stage": "script", "actor": actor,
            "detail": {"words": project["record"]["script"]["words"],
                       "sections": len(project["record"]["script"]["sections"]), "reset": reset}})

    @app.post("/v1/creators/projects/{project_id}/stages/{stage}/approve")
    async def approve(project_id: str, stage: str, body: StageAction, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        if not actor:
            return error(400, "actor_required", "approvals must name the person approving")
        project = await load(tenant, project_id)
        model.approve(project["stages"], stage, body.note)
        record = project["record"].setdefault("approvals", {})
        record[stage] = {"by": actor, "at": model.now(), "note": body.note}
        return await save(tenant, project, body.version, {"kind": "approved", "stage": stage, "actor": actor,
                                                          "detail": {"note": body.note}})

    @app.post("/v1/creators/projects/{project_id}/stages/{stage}/reopen")
    async def reopen(project_id: str, stage: str, body: StageAction, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        project = await load(tenant, project_id)
        reset = model.reopen(project["stages"], stage, body.note)
        project["record"].get("approvals", {}).pop(stage, None)
        return await save(tenant, project, body.version, {"kind": "reopened", "stage": stage, "actor": actor,
                                                          "detail": {"reason": body.note, "reset": reset}})

    # Jobs (the creators worker makes these) ---------------------------------------------------------------------
    @app.post("/v1/creators/projects/{project_id}/jobs", status_code=202)
    async def start_job(project_id: str, body: JobInput, request: Request):
        """Starts writing the script, or voicing the approved script with the chosen voice."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        if not actor:
            return error(400, "actor_required", "jobs must name the person starting them")
        project = await load(tenant, project_id)
        params: dict[str, Any] = {}
        if body.kind == "voiceover":
            if not body.voice:
                return error(400, "invalid_request", "choose a voice")
            if app.state.voices is None:
                return error(503, "voice_not_configured", "the voice service isn't set up on this deployment")
            if body.voice not in {v["id"] for v in await app.state.voices.voices()}:
                return error(400, "invalid_request", "unknown voice")
            params = {"voice": body.voice, "speed": body.speed}
        partial = False
        if body.kind == "images":
            numbers = {s["number"] for s in project["record"].get("scenes") or []}
            if body.scenes and not set(body.scenes) <= numbers:
                return error(400, "invalid_request", "those scenes aren't in this video")
            partial = bool(body.scenes) and project["stages"]["images"]["state"] in model.FINISHED
            params = {"scenes": sorted(set(body.scenes))}
        if body.kind == "publish":
            channel_link = await app.state.store.youtube(tenant, project["channel_id"])
            if app.state.youtube is None or channel_link is None:
                return error(409, "youtube_not_connected", "connect this channel's YouTube account first")
            if not (project["record"].get("video") or {}).get("key") or not (project["record"].get("thumbnail") or {}).get("chosen") or not project["record"].get("seo"):
                return error(409, "earlier_stage_open", "the video, a chosen thumbnail and the SEO package are needed")
            from datetime import datetime, timezone
            when = None
            if body.publish_at:
                try:
                    when = datetime.fromisoformat(body.publish_at.replace("Z", "+00:00")).astimezone(timezone.utc)
                except ValueError:
                    return error(400, "invalid_request", "publish_at must be an ISO date and time")
                if when <= datetime.now(timezone.utc):
                    return error(400, "invalid_request", "a scheduled time must be in the future")
            # YouTube only schedules private videos; they turn public at publish_at.
            params = {"privacy": "private" if when else body.privacy, "publish_at": when.isoformat().replace("+00:00", "Z") if when else "",
                      "made_for_kids": body.made_for_kids, "category_id": body.category_id, "youtube_channel_id": channel_link["youtube_channel_id"]}
        if body.kind == "assembly":
            if any(not (sc.get("image") or {}).get("key") for sc in project["record"].get("scenes") or []):
                return error(409, "earlier_stage_open", "every scene needs an image first")
            params = {"burn_subtitles": body.burn_subtitles}
        if project["version"] != body.version:
            raise ProjectError("the project changed since you opened it; reload it first", "stale_version")
        reset = model.start_job(project["stages"], body.kind, partial)
        try:
            saved, job = await app.state.store.start_job(tenant, project, body.version, body.kind, params, actor, {
                "kind": "job_started", "stage": model.JOB_STAGE[body.kind], "detail": {"job": body.kind, **params, "reset": reset}})
        except KeyError:
            return error(409, "job_running", f"the {body.kind} is already being made")
        except LookupError as exc:
            raise ProjectError("the project changed since you opened it; reload it first", "stale_version") from exc
        return {"project": view(saved), "job": job}

    @app.get("/v1/creators/projects/{project_id}/jobs")
    async def jobs(project_id: str, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        await load(who[0], project_id)
        return {"jobs": await app.state.store.jobs(who[0], project_id)}

    # Voices and audio -------------------------------------------------------------------------------------------
    @app.get("/v1/creators/voices")
    async def voices(request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        if app.state.voices is None:
            return error(503, "voice_not_configured", "the voice service isn't set up on this deployment")
        return {"voices": await app.state.voices.voices()}

    @app.post("/v1/creators/voices/preview")
    async def preview(body: PreviewInput, request: Request):
        """A short sample of a voice (audition before choosing); nothing is stored."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        if app.state.voices is None:
            return error(503, "voice_not_configured", "the voice service isn't set up on this deployment")
        text = body.text.strip() or "Welcome back. Today we look at something most people never notice, and why it matters to you."
        return Response(await app.state.voices.speak(text, body.voice, body.speed), media_type="audio/wav")

    @app.get("/v1/creators/projects/{project_id}/audio/{name}")
    async def audio(project_id: str, name: str, request: Request):
        """The narration (name 'narration') or one chapter's voiceover ('chapter-NN') of this project's current voice."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        project = await load(who[0], project_id)
        voice = project["record"].get("voiceover") or {}
        if name == "narration":
            key = voice.get("full_key")
        else:
            key = next((s["key"] for s in voice.get("sections", []) if f"chapter-{s['index']:02d}" == name), None)
        if not key or app.state.audio is None or not key.startswith(f"{who[0]}/creators/{project_id}/"):
            return error(404, "not_found", "no such audio for this project")
        return Response(await app.state.audio.get(key), media_type="audio/wav",
                        headers={"Content-Disposition": f'attachment; filename="{project_id[:8]}-{name}.wav"'})

    # Scenes, images and thumbnails --------------------------------------------------------------------------------
    @app.put("/v1/creators/projects/{project_id}/scenes/{number}")
    async def edit_scene(project_id: str, number: int, body: SceneEdit, request: Request):
        """A person's own description and image prompt for one scene; its image is then out of date until remade."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        project = await load(tenant, project_id)
        scene = next((s for s in project["record"].get("scenes") or [] if s["number"] == number), None)
        if scene is None:
            return error(404, "not_found", "no such scene in this video")
        if project["stages"]["images"]["state"] == "running":
            raise ProjectError("images are being made; wait for them to finish", "job_running")
        scene.update(prompt=body.prompt.strip(), visual=body.visual.strip() or scene["visual"], edited_by=actor)
        if scene.get("image"):
            scene["image"]["stale"] = True
        return await save(tenant, project, body.version, {"kind": "scene_edited", "stage": "scenes", "actor": actor,
                                                          "detail": {"scene": number}})

    @app.put("/v1/creators/projects/{project_id}/thumbnail")
    async def choose_thumbnail(project_id: str, body: ThumbnailChoice, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        project = await load(tenant, project_id)
        thumb = project["record"].get("thumbnail") or {}
        if not 1 <= body.chosen <= len(thumb.get("options", [])):
            return error(404, "not_found", "no such thumbnail option")
        thumb.update(chosen=body.chosen, chosen_by=actor, chosen_at=model.now())
        return await save(tenant, project, body.version, {"kind": "thumbnail_chosen", "stage": "thumbnail", "actor": actor,
                                                          "detail": {"chosen": body.chosen, "caption": thumb["options"][body.chosen - 1]["caption"]}})

    @app.get("/v1/creators/projects/{project_id}/images/{name}")
    async def picture(project_id: str, name: str, request: Request):
        """A scene's image ('scene-NNN') or a thumbnail option ('thumb-N'), from this project only."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        project = await load(who[0], project_id)
        key = None
        if re.fullmatch(r"scene-\d{3}", name):
            scene = next((s for s in project["record"].get("scenes") or [] if s["number"] == int(name[6:])), None)
            key = ((scene or {}).get("image") or {}).get("key")
        elif re.fullmatch(r"thumb-\d", name):
            options = (project["record"].get("thumbnail") or {}).get("options", [])
            key = options[int(name[6:]) - 1]["key"] if 1 <= int(name[6:]) <= len(options) else None
        if not key or app.state.audio is None or not key.startswith(f"{who[0]}/creators/{project_id}/"):
            return error(404, "not_found", "no such image for this project")
        return Response(await app.state.audio.get(key), media_type="image/png")

    @app.get("/v1/creators/projects/{project_id}/images.zip")
    async def images_zip(project_id: str, request: Request):
        """Every scene image of the video, numbered in order, in one ZIP."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        project = await load(who[0], project_id)
        scenes = [s for s in project["record"].get("scenes") or [] if (s.get("image") or {}).get("key")]
        if not scenes or app.state.audio is None:
            return error(404, "not_found", "no images yet")
        import io as _io, zipfile
        out = _io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
            for s in scenes:
                if s["image"]["key"].startswith(f"{who[0]}/creators/{project_id}/"):
                    archive.writestr(f"scene-{s['number']:03d}.png", await app.state.audio.get(s["image"]["key"]))
        return Response(out.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{project_id[:8]}-scenes.zip"'})

    @app.get("/v1/creators/projects/{project_id}/video/{name}")
    async def video(project_id: str, name: str, request: Request):
        """The finished video ('video.mp4', streamed) or its captions ('captions.srt'), from this project only."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        project = await load(who[0], project_id)
        made = project["record"].get("video") or {}
        key = {"video.mp4": made.get("key"), "captions.srt": made.get("captions_key")}.get(name)
        if not key or app.state.audio is None or not key.startswith(f"{who[0]}/creators/{project_id}/"):
            return error(404, "not_found", "no such file for this project")
        size, chunks = await app.state.audio.stream(key)
        filename = f"{(project['title'] or 'video')[:60]}{'.mp4' if name == 'video.mp4' else '.srt'}".replace('"', "")
        return StreamingResponse(chunks, media_type="video/mp4" if name == "video.mp4" else "application/x-subrip",
                                 headers={"Content-Length": str(size), "Content-Disposition": f'attachment; filename="{filename}"'})

    # YouTube ------------------------------------------------------------------------------------------------------
    @app.get("/v1/creators/channels/{channel_id}/youtube")
    async def youtube_status(channel_id: str, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        if not channel_id_ok(channel_id) or not await app.state.store.channel(who[0], channel_id):
            return error(404, "not_found", "no such channel in this business")
        link = await app.state.store.youtube(who[0], channel_id)
        return {"configured": app.state.youtube is not None, "connected": link is not None,
                "youtube": {k: link[k] for k in ("youtube_channel_id", "title", "handle", "subscribers", "connected_at")} if link else None}

    @app.post("/v1/creators/channels/{channel_id}/youtube/connect")
    async def youtube_connect(channel_id: str, request: Request):
        """The Google sign-in address for this channel; the state names the business, channel and person."""
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        tenant, actor = who
        if app.state.youtube is None:
            return error(503, "youtube_not_configured", "YouTube isn't set up on this deployment yet (no Google OAuth client)")
        if not actor:
            return error(400, "actor_required", "connecting must name the person doing it")
        if not channel_id_ok(channel_id) or not await app.state.store.channel(tenant, channel_id):
            return error(404, "not_found", "no such channel in this business")
        return {"url": app.state.youtube.authorize_url(yt.sign_state(tenant, channel_id, actor))}

    @app.get("/v1/creators/youtube/callback")
    async def youtube_callback(code: str = "", state: str = "", error_: str = Query(default="", alias="error")):
        """Google sends the browser back here (public route). Only a valid, unexpired signed state is acted on."""
        found = yt.read_state(state) if state else None
        if found is None:
            return RedirectResponse(f"{console}/services/creators?youtube=invalid", status_code=303)
        back = f"{console}/services/creators?channel={found['channel_id']}&youtube="
        if error_ or not code or app.state.youtube is None:
            return RedirectResponse(back + "cancelled", status_code=303)
        try:
            tokens = await app.state.youtube.exchange(code)
            info = await app.state.youtube.my_channel(tokens["access_token"])
            await app.state.store.save_youtube(found["tenant"], found["channel_id"], info, yt.seal(tokens["refresh_token"]), found["actor"])
        except yt.YouTubeError:
            return RedirectResponse(back + "failed", status_code=303)
        return RedirectResponse(back + "connected", status_code=303)

    @app.delete("/v1/creators/channels/{channel_id}/youtube")
    async def youtube_disconnect(channel_id: str, request: Request):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        link = channel_id_ok(channel_id) and await app.state.store.youtube(who[0], channel_id)
        if not link:
            return error(404, "not_found", "this channel has no YouTube connection")
        if app.state.youtube is not None:
            try:
                await app.state.youtube.revoke(yt.unseal(link["refresh_token_sealed"]))
            except Exception:  # noqa: BLE001 - the link is removed whatever Google says
                pass
        await app.state.store.delete_youtube(who[0], channel_id)
        return {"status": "disconnected"}

    @app.get("/v1/creators/projects/{project_id}/events")
    async def events(project_id: str, request: Request, limit: int = Query(default=100, ge=1, le=500)):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        await load(who[0], project_id)
        return {"events": await app.state.store.events(who[0], project_id, limit)}

    return app


app = create_app()

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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

import model
from model import ProjectError
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


def create_app(store: Any = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.store is None:
            app.state.store = await PostgresCreatorStore.connect(os.environ["DATABASE_URL"])
        yield
        if hasattr(app.state.store, "close"):
            await app.state.store.close()

    app = FastAPI(title="VolumeOS Creators Home", lifespan=lifespan)
    app.state.store = store
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

    @app.get("/v1/creators/projects/{project_id}/events")
    async def events(project_id: str, request: Request, limit: int = Query(default=100, ge=1, le=500)):
        who = caller(request)
        if isinstance(who, JSONResponse):
            return who
        await load(who[0], project_id)
        return {"events": await app.state.store.events(who[0], project_id, limit)}

    return app


app = create_app()

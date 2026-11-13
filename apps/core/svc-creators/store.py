"""Tenant-isolated Creators Home data: channels, projects (the shared project record and its stages) and history.

Every table has a tenant_id and FORCE ROW LEVEL SECURITY: the service connects as the tables' owner, and Postgres
would otherwise skip the policies for it. Every query runs inside tenant_tx, which sets volumeos.tenant_id.
"""
from __future__ import annotations

import copy
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from model import now

SCHEMA = [("001_creators", """
CREATE TABLE IF NOT EXISTS creator_channels (
    id uuid NOT NULL,
    tenant_id text NOT NULL,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    youtube_handle text NOT NULL DEFAULT '',
    niche text NOT NULL DEFAULT '',
    audience text NOT NULL DEFAULT '',
    language text NOT NULL DEFAULT 'en',
    writing_instructions text NOT NULL DEFAULT '' CHECK (char_length(writing_instructions) <= 8000),
    visual_preset text NOT NULL DEFAULT '' CHECK (char_length(visual_preset) <= 2000),
    default_voice text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS creator_channels_name ON creator_channels (tenant_id, lower(name));

CREATE TABLE IF NOT EXISTS creator_projects (
    id uuid NOT NULL,
    tenant_id text NOT NULL,
    channel_id uuid NOT NULL,
    title text NOT NULL DEFAULT '' CHECK (char_length(title) <= 200),
    topic text NOT NULL CHECK (char_length(topic) BETWEEN 1 AND 2000),
    duration_minutes integer NOT NULL CHECK (duration_minutes IN (5,10,15,20,30,45)),
    style text NOT NULL DEFAULT '',
    audience text NOT NULL DEFAULT '',
    stages jsonb NOT NULL,
    record jsonb NOT NULL DEFAULT '{}'::jsonb,
    version integer NOT NULL DEFAULT 1,
    created_by text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, channel_id) REFERENCES creator_channels (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS creator_projects_channel ON creator_projects (tenant_id, channel_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS creator_events (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    project_id uuid NOT NULL,
    kind text NOT NULL,
    stage text NOT NULL DEFAULT '',
    actor text NOT NULL DEFAULT '',
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS creator_events_project ON creator_events (tenant_id, project_id, id DESC);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['creator_channels','creator_projects','creator_events'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''volumeos.tenant_id'', true)) '
                       'WITH CHECK (tenant_id = current_setting(''volumeos.tenant_id'', true))', t);
    END LOOP;
END $$;
""")]

CHANNEL_FIELDS = ("name", "youtube_handle", "niche", "audience", "language", "writing_instructions", "visual_preset",
                  "default_voice")


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class PostgresCreatorStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "PostgresCreatorStore":
        store = cls(await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=10, command_timeout=30))
        await store.migrate()
        return store

    async def close(self) -> None:
        await self.pool.close()

    async def ready(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.fetchval("SELECT 1")

    async def migrate(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute("SELECT pg_advisory_lock(hashtext('volumeos.creators.migrations'))")
            try:
                await connection.execute("CREATE TABLE IF NOT EXISTS creator_schema_migrations "
                                         "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
                for version, sql in SCHEMA:
                    if await connection.fetchval("SELECT 1 FROM creator_schema_migrations WHERE version=$1", version):
                        continue
                    async with connection.transaction():
                        await connection.execute(sql)
                        await connection.execute("INSERT INTO creator_schema_migrations (version) VALUES ($1)", version)
            finally:
                await connection.execute("SELECT pg_advisory_unlock(hashtext('volumeos.creators.migrations'))")

    @asynccontextmanager
    async def tenant_tx(self, tenant: str) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT set_config('volumeos.tenant_id', $1, true)", tenant)
                yield connection

    # Channels ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _channel(row: asyncpg.Record) -> dict[str, Any]:
        out = {key: row[key] for key in CHANNEL_FIELDS}
        out.update(id=str(row["id"]), created_at=_iso(row["created_at"]), updated_at=_iso(row["updated_at"]))
        return out

    async def channels(self, tenant: str) -> list[dict[str, Any]]:
        async with self.tenant_tx(tenant) as tx:
            rows = await tx.fetch("SELECT * FROM creator_channels ORDER BY lower(name)")
        return [self._channel(row) for row in rows]

    async def channel(self, tenant: str, channel_id: str) -> dict[str, Any] | None:
        async with self.tenant_tx(tenant) as tx:
            row = await tx.fetchrow("SELECT * FROM creator_channels WHERE id=$1", uuid.UUID(channel_id))
        return self._channel(row) if row else None

    async def create_channel(self, tenant: str, fields: dict[str, Any]) -> dict[str, Any]:
        async with self.tenant_tx(tenant) as tx:
            if await tx.fetchval("SELECT 1 FROM creator_channels WHERE lower(name)=lower($1)", fields["name"]):
                raise KeyError("a channel with this name already exists")
            row = await tx.fetchrow(
                f"INSERT INTO creator_channels (id, tenant_id, {', '.join(CHANNEL_FIELDS)}) "
                f"VALUES ($1, $2, {', '.join(f'${i}' for i in range(3, 3 + len(CHANNEL_FIELDS)))}) RETURNING *",
                uuid.uuid4(), tenant, *[fields.get(key, "") for key in CHANNEL_FIELDS])
        return self._channel(row)

    async def update_channel(self, tenant: str, channel_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        changes = {key: value for key, value in fields.items() if key in CHANNEL_FIELDS}
        if not changes:
            return await self.channel(tenant, channel_id)
        sets = ", ".join(f"{key}=${i}" for i, key in enumerate(changes, start=2))
        async with self.tenant_tx(tenant) as tx:
            row = await tx.fetchrow(f"UPDATE creator_channels SET {sets}, updated_at=now() WHERE id=$1 RETURNING *",
                                    uuid.UUID(channel_id), *changes.values())
        return self._channel(row) if row else None

    async def delete_channel(self, tenant: str, channel_id: str) -> bool:
        async with self.tenant_tx(tenant) as tx:
            if await tx.fetchval("SELECT 1 FROM creator_projects WHERE channel_id=$1 LIMIT 1", uuid.UUID(channel_id)):
                raise ValueError("the channel still has projects")
            return (await tx.execute("DELETE FROM creator_channels WHERE id=$1", uuid.UUID(channel_id))) == "DELETE 1"

    # Projects ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _project(row: asyncpg.Record) -> dict[str, Any]:
        return {
            "id": str(row["id"]), "channel_id": str(row["channel_id"]), "title": row["title"], "topic": row["topic"],
            "duration_minutes": row["duration_minutes"], "style": row["style"], "audience": row["audience"],
            "stages": _json(row["stages"]), "record": _json(row["record"]), "version": row["version"],
            "created_by": row["created_by"], "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
        }

    async def projects(self, tenant: str, channel_id: str | None) -> list[dict[str, Any]]:
        async with self.tenant_tx(tenant) as tx:
            if channel_id:
                rows = await tx.fetch("SELECT * FROM creator_projects WHERE channel_id=$1 ORDER BY updated_at DESC",
                                      uuid.UUID(channel_id))
            else:
                rows = await tx.fetch("SELECT * FROM creator_projects ORDER BY updated_at DESC LIMIT 200")
        return [self._project(row) for row in rows]

    async def project(self, tenant: str, project_id: str) -> dict[str, Any] | None:
        async with self.tenant_tx(tenant) as tx:
            row = await tx.fetchrow("SELECT * FROM creator_projects WHERE id=$1", uuid.UUID(project_id))
        return self._project(row) if row else None

    async def create_project(self, tenant: str, project: dict[str, Any], actor: str) -> dict[str, Any]:
        async with self.tenant_tx(tenant) as tx:
            row = await tx.fetchrow(
                "INSERT INTO creator_projects (id, tenant_id, channel_id, title, topic, duration_minutes, style, audience,"
                " stages, record, created_by) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11) RETURNING *",
                uuid.uuid4(), tenant, uuid.UUID(project["channel_id"]), project["title"], project["topic"],
                project["duration_minutes"], project["style"], project["audience"], json.dumps(project["stages"]),
                json.dumps(project["record"]), actor)
            await tx.execute("INSERT INTO creator_events (tenant_id, project_id, kind, actor, detail) "
                             "VALUES ($1,$2,'created',$3,$4::jsonb)", tenant, row["id"], actor,
                             json.dumps({"title": project["title"], "duration_minutes": project["duration_minutes"]}))
        return self._project(row)

    async def save_project(self, tenant: str, project: dict[str, Any], expected_version: int,
                           event: dict[str, Any]) -> dict[str, Any]:
        """Writes the whole project if nobody changed it since it was read (version), with its history entry."""
        async with self.tenant_tx(tenant) as tx:
            row = await tx.fetchrow(
                "UPDATE creator_projects SET title=$3, topic=$4, duration_minutes=$5, style=$6, audience=$7,"
                " stages=$8::jsonb, record=$9::jsonb, version=version+1, updated_at=now()"
                " WHERE id=$1 AND version=$2 RETURNING *",
                uuid.UUID(project["id"]), expected_version, project["title"], project["topic"],
                project["duration_minutes"], project["style"], project["audience"], json.dumps(project["stages"]),
                json.dumps(project["record"]))
            if row is None:
                raise LookupError("the project changed since it was read")
            await tx.execute("INSERT INTO creator_events (tenant_id, project_id, kind, stage, actor, detail) "
                             "VALUES ($1,$2,$3,$4,$5,$6::jsonb)", tenant, row["id"], event["kind"],
                             event.get("stage", ""), event.get("actor", ""), json.dumps(event.get("detail", {})))
        return self._project(row)

    async def events(self, tenant: str, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        async with self.tenant_tx(tenant) as tx:
            rows = await tx.fetch("SELECT * FROM creator_events WHERE project_id=$1 ORDER BY id DESC LIMIT $2",
                                  uuid.UUID(project_id), limit)
        return [{"kind": row["kind"], "stage": row["stage"], "actor": row["actor"], "detail": _json(row["detail"]),
                 "created_at": _iso(row["created_at"])} for row in rows]


class InMemoryCreatorStore:
    """The same contract for tests: every lookup is by tenant, as the database's row security enforces."""

    def __init__(self):
        self.channel_rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.project_rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.event_rows: list[dict[str, Any]] = []

    async def ready(self) -> None:
        return None

    async def channels(self, tenant):
        return sorted((copy.deepcopy(c) for (t, _), c in self.channel_rows.items() if t == tenant), key=lambda c: c["name"].lower())

    async def channel(self, tenant, channel_id):
        found = self.channel_rows.get((tenant, channel_id))
        return copy.deepcopy(found) if found else None

    async def create_channel(self, tenant, fields):
        if any(t == tenant and c["name"].lower() == fields["name"].lower() for (t, _), c in self.channel_rows.items()):
            raise KeyError("a channel with this name already exists")
        row = {key: fields.get(key, "") for key in CHANNEL_FIELDS} | {"id": str(uuid.uuid4()), "created_at": now(), "updated_at": now()}
        self.channel_rows[(tenant, row["id"])] = row
        return copy.deepcopy(row)

    async def update_channel(self, tenant, channel_id, fields):
        row = self.channel_rows.get((tenant, channel_id))
        if not row:
            return None
        row.update({key: value for key, value in fields.items() if key in CHANNEL_FIELDS}, updated_at=now())
        return copy.deepcopy(row)

    async def delete_channel(self, tenant, channel_id):
        if any(t == tenant and p["channel_id"] == channel_id for (t, _), p in self.project_rows.items()):
            raise ValueError("the channel still has projects")
        return self.channel_rows.pop((tenant, channel_id), None) is not None

    async def projects(self, tenant, channel_id):
        rows = [copy.deepcopy(p) for (t, _), p in self.project_rows.items() if t == tenant and (not channel_id or p["channel_id"] == channel_id)]
        return sorted(rows, key=lambda p: p["updated_at"], reverse=True)

    async def project(self, tenant, project_id):
        found = self.project_rows.get((tenant, project_id))
        return copy.deepcopy(found) if found else None

    async def create_project(self, tenant, project, actor):
        row = copy.deepcopy(project) | {"id": str(uuid.uuid4()), "version": 1, "created_by": actor, "created_at": now(), "updated_at": now()}
        self.project_rows[(tenant, row["id"])] = row
        self.event_rows.append({"tenant": tenant, "project_id": row["id"], "kind": "created", "stage": "", "actor": actor,
                                "detail": {"title": project["title"], "duration_minutes": project["duration_minutes"]}, "created_at": now()})
        return copy.deepcopy(row)

    async def save_project(self, tenant, project, expected_version, event):
        current = self.project_rows.get((tenant, project["id"]))
        if not current or current["version"] != expected_version:
            raise LookupError("the project changed since it was read")
        row = copy.deepcopy(project) | {"version": expected_version + 1, "updated_at": now()}
        self.project_rows[(tenant, project["id"])] = row
        self.event_rows.append({"tenant": tenant, "project_id": project["id"], "kind": event["kind"], "stage": event.get("stage", ""),
                                "actor": event.get("actor", ""), "detail": event.get("detail", {}), "created_at": now()})
        return copy.deepcopy(row)

    async def events(self, tenant, project_id, limit=100):
        rows = [e for e in self.event_rows if e["tenant"] == tenant and e["project_id"] == project_id]
        return [{key: e[key] for key in ("kind", "stage", "actor", "detail", "created_at")} for e in reversed(rows)][:limit]

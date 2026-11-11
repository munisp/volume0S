"""Tenant-isolated campaign data: contact lists, AI drafts, dispatches and per-recipient sends.

Every table has a tenant_id and FORCE ROW LEVEL SECURITY: the service connects as the tables' owner, and Postgres
would otherwise skip the policies for it. Every query runs inside tenant_tx, which sets volumeos.tenant_id.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

SCHEMA = [
    ("001_campaigns", """
CREATE TABLE IF NOT EXISTS campaign_contacts (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    first_name text NOT NULL DEFAULT '' CHECK (char_length(first_name) <= 80),
    email text CHECK (email IS NULL OR char_length(email) <= 254),
    phone text CHECK (phone IS NULL OR phone ~ '^\\+[1-9][0-9]{7,14}$'),
    email_consent boolean NOT NULL DEFAULT false,
    whatsapp_consent boolean NOT NULL DEFAULT false,
    source text NOT NULL DEFAULT 'import' CHECK (char_length(source) <= 80),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (email IS NOT NULL OR phone IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS campaign_contacts_email ON campaign_contacts (tenant_id, lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS campaign_contacts_phone ON campaign_contacts (tenant_id, phone) WHERE phone IS NOT NULL;

CREATE TABLE IF NOT EXISTS campaign_drafts (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    campaign_id text NOT NULL,
    subject text NOT NULL,
    email_body text NOT NULL,
    whatsapp_text text NOT NULL,
    model text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, campaign_id)
);

CREATE TABLE IF NOT EXISTS campaign_dispatches (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    campaign_id text NOT NULL,
    cycle_id text NOT NULL,
    draft_id text NOT NULL,
    approval_id text NOT NULL,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (tenant_id, campaign_id, cycle_id)
);

CREATE TABLE IF NOT EXISTS campaign_sends (
    tenant_id text NOT NULL,
    dispatch_id text NOT NULL,
    contact_id uuid NOT NULL,
    channel text NOT NULL CHECK (channel IN ('email','whatsapp')),
    status text NOT NULL CHECK (status IN ('sent','test_address','blocked','failed')),
    provider_id text NOT NULL DEFAULT '',
    detail text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, dispatch_id, contact_id, channel)
);

CREATE TABLE IF NOT EXISTS campaign_nudges (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    channel text NOT NULL,
    recipients integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['campaign_contacts','campaign_drafts','campaign_dispatches','campaign_sends','campaign_nudges'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''volumeos.tenant_id'', true)) '
                       'WITH CHECK (tenant_id = current_setting(''volumeos.tenant_id'', true))', t);
    END LOOP;
END $$;
"""),
]

CONTACT_COLUMNS = "id,tenant_id,first_name,email,phone,email_consent,whatsapp_consent,source,created_at,updated_at"


def contact_row(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "first_name": row["first_name"], "email": row["email"], "phone": row["phone"],
        "email_consent": row["email_consent"], "whatsapp_consent": row["whatsapp_consent"], "source": row["source"],
        "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
    }


def draft_row(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "draft_id": row["id"], "campaign_id": row["campaign_id"], "subject": row["subject"],
        "email_body": row["email_body"], "whatsapp_text": row["whatsapp_text"], "model": row["model"],
        "created_at": row["created_at"].isoformat(),
    }


class PostgresCampaignStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "PostgresCampaignStore":
        store = cls(await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=10, command_timeout=30))
        await store.migrate()
        return store

    async def migrate(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute("SELECT pg_advisory_lock(hashtext('volumeos.campaigns.migrations'))")
            try:
                await connection.execute(
                    "CREATE TABLE IF NOT EXISTS campaign_schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
                for version, sql in SCHEMA:
                    if await connection.fetchval("SELECT 1 FROM campaign_schema_migrations WHERE version=$1", version):
                        continue
                    async with connection.transaction():
                        await connection.execute(sql)
                        await connection.execute("INSERT INTO campaign_schema_migrations(version) VALUES ($1)", version)
            finally:
                await connection.execute("SELECT pg_advisory_unlock(hashtext('volumeos.campaigns.migrations'))")

    async def close(self) -> None:
        await self.pool.close()

    async def ready(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.fetchval("SELECT 1")

    @asynccontextmanager
    async def tenant_tx(self, tenant_id: str) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT set_config('volumeos.tenant_id', $1, true)", tenant_id)
                yield connection

    # Contacts -------------------------------------------------------------------------------------------------
    async def import_contacts(self, tenant_id: str, contacts: list[dict[str, Any]]) -> dict[str, int]:
        """Adds new contacts and updates known ones (matched by email, else phone). Consent is only ever widened
        by an import that says so explicitly; it is never silently granted."""
        added = updated = 0
        async with self.tenant_tx(tenant_id) as connection:
            for c in contacts:
                existing = None
                if c.get("email"):
                    existing = await connection.fetchrow(
                        "SELECT id FROM campaign_contacts WHERE tenant_id=$1 AND lower(email)=lower($2)", tenant_id, c["email"])
                if existing is None and c.get("phone"):
                    existing = await connection.fetchrow(
                        "SELECT id FROM campaign_contacts WHERE tenant_id=$1 AND phone=$2", tenant_id, c["phone"])
                if existing is None:
                    await connection.execute(
                        "INSERT INTO campaign_contacts (id,tenant_id,first_name,email,phone,email_consent,whatsapp_consent,source) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                        uuid.uuid4(), tenant_id, c.get("first_name", ""), c.get("email"), c.get("phone"),
                        bool(c.get("email_consent")), bool(c.get("whatsapp_consent")), c.get("source", "import"))
                    added += 1
                else:
                    await connection.execute(
                        "UPDATE campaign_contacts SET first_name=COALESCE(NULLIF($3,''), first_name), "
                        "email=COALESCE($4, email), phone=COALESCE($5, phone), "
                        "email_consent=$6, whatsapp_consent=$7, updated_at=now() WHERE tenant_id=$1 AND id=$2",
                        tenant_id, existing["id"], c.get("first_name", ""), c.get("email"), c.get("phone"),
                        bool(c.get("email_consent")), bool(c.get("whatsapp_consent")))
                    updated += 1
        return {"added": added, "updated": updated}

    async def list_contacts(self, tenant_id: str, limit: int, offset: int) -> dict[str, Any]:
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                f"SELECT {CONTACT_COLUMNS} FROM campaign_contacts WHERE tenant_id=$1 ORDER BY created_at DESC, id LIMIT $2 OFFSET $3",
                tenant_id, limit, offset)
            totals = await connection.fetchrow(
                "SELECT count(*) AS total, count(*) FILTER (WHERE email IS NOT NULL AND email_consent) AS email, "
                "count(*) FILTER (WHERE phone IS NOT NULL AND whatsapp_consent) AS whatsapp "
                "FROM campaign_contacts WHERE tenant_id=$1", tenant_id)
        return {"contacts": [contact_row(r) for r in rows], "total": totals["total"],
                "reachable": {"email": totals["email"], "whatsapp": totals["whatsapp"]}}

    async def delete_contact(self, tenant_id: str, contact_id: str) -> bool:
        async with self.tenant_tx(tenant_id) as connection:
            result = await connection.execute(
                "DELETE FROM campaign_contacts WHERE tenant_id=$1 AND id=$2", tenant_id, uuid.UUID(contact_id))
        return result.endswith(" 1")

    async def audience(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                f"SELECT {CONTACT_COLUMNS} FROM campaign_contacts WHERE tenant_id=$1 "
                "AND ((email IS NOT NULL AND email_consent) OR (phone IS NOT NULL AND whatsapp_consent)) ORDER BY created_at, id",
                tenant_id)
        return [contact_row(r) for r in rows]

    # Drafts ---------------------------------------------------------------------------------------------------
    async def get_draft(self, tenant_id: str, draft_id: str) -> dict[str, Any] | None:
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow("SELECT * FROM campaign_drafts WHERE tenant_id=$1 AND id=$2", tenant_id, draft_id)
        return draft_row(row) if row else None

    async def draft_for_campaign(self, tenant_id: str, campaign_id: str) -> dict[str, Any] | None:
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                "SELECT * FROM campaign_drafts WHERE tenant_id=$1 AND campaign_id=$2", tenant_id, campaign_id)
        return draft_row(row) if row else None

    async def save_draft(self, tenant_id: str, draft: dict[str, Any]) -> dict[str, Any]:
        async with self.tenant_tx(tenant_id) as connection:
            await connection.execute(
                "INSERT INTO campaign_drafts (id,tenant_id,campaign_id,subject,email_body,whatsapp_text,model) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (tenant_id, campaign_id) DO NOTHING",
                draft["draft_id"], tenant_id, draft["campaign_id"], draft["subject"], draft["email_body"],
                draft["whatsapp_text"], draft["model"])
            row = await connection.fetchrow(
                "SELECT * FROM campaign_drafts WHERE tenant_id=$1 AND campaign_id=$2", tenant_id, draft["campaign_id"])
        return draft_row(row)

    # Dispatches -----------------------------------------------------------------------------------------------
    async def begin_dispatch(self, tenant_id: str, value: dict[str, Any], dispatch_id: str) -> dict[str, Any] | None:
        """Records the dispatch once; returns its stored result if it already finished (a retried request)."""
        async with self.tenant_tx(tenant_id) as connection:
            await connection.execute(
                "INSERT INTO campaign_dispatches (id,tenant_id,campaign_id,cycle_id,draft_id,approval_id) "
                "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (tenant_id, campaign_id, cycle_id) DO NOTHING",
                dispatch_id, tenant_id, value["campaign_id"], value["cycle_id"], value["draft_id"], value["approval_id"])
            row = await connection.fetchrow(
                "SELECT result FROM campaign_dispatches WHERE tenant_id=$1 AND campaign_id=$2 AND cycle_id=$3",
                tenant_id, value["campaign_id"], value["cycle_id"])
        result = row["result"] if row else None
        return json.loads(result) if isinstance(result, str) else result

    async def sent_before(self, tenant_id: str, dispatch_id: str) -> dict[tuple[str, str], str]:
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                "SELECT contact_id, channel, status FROM campaign_sends WHERE tenant_id=$1 AND dispatch_id=$2", tenant_id, dispatch_id)
        return {(str(r["contact_id"]), r["channel"]): r["status"] for r in rows}

    async def record_send(self, tenant_id: str, dispatch_id: str, contact_id: str, channel: str, status: str,
                          provider_id: str = "", detail: str = "") -> None:
        async with self.tenant_tx(tenant_id) as connection:
            await connection.execute(
                "INSERT INTO campaign_sends (tenant_id,dispatch_id,contact_id,channel,status,provider_id,detail) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (tenant_id,dispatch_id,contact_id,channel) DO UPDATE "
                "SET status=EXCLUDED.status, provider_id=EXCLUDED.provider_id, detail=EXCLUDED.detail "
                "WHERE campaign_sends.status='failed'",
                tenant_id, dispatch_id, uuid.UUID(contact_id), channel, status, provider_id, detail[:300])

    async def finish_dispatch(self, tenant_id: str, dispatch_id: str, result: dict[str, Any]) -> None:
        async with self.tenant_tx(tenant_id) as connection:
            await connection.execute(
                "UPDATE campaign_dispatches SET result=$3::jsonb, completed_at=now() WHERE tenant_id=$1 AND id=$2",
                tenant_id, dispatch_id, json.dumps(result))

    async def dispatch_summary(self, tenant_id: str, campaign_id: str) -> list[dict[str, Any]]:
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                "SELECT d.cycle_id, s.channel, s.status, count(*) AS n FROM campaign_dispatches d "
                "JOIN campaign_sends s ON s.tenant_id=d.tenant_id AND s.dispatch_id=d.id "
                "WHERE d.tenant_id=$1 AND d.campaign_id=$2 GROUP BY 1,2,3 ORDER BY 1,2,3", tenant_id, campaign_id)
        return [{"cycle_id": r["cycle_id"], "channel": r["channel"], "status": r["status"], "count": r["n"]} for r in rows]

    async def record_nudge(self, tenant_id: str, nudge_id: str, channel: str, recipients: int) -> bool:
        async with self.tenant_tx(tenant_id) as connection:
            result = await connection.execute(
                "INSERT INTO campaign_nudges (id,tenant_id,channel,recipients) VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                nudge_id, tenant_id, channel, recipients)
        return result.endswith(" 1")

    async def nudge_sent(self, tenant_id: str, nudge_id: str) -> bool:
        async with self.tenant_tx(tenant_id) as connection:
            return bool(await connection.fetchval("SELECT 1 FROM campaign_nudges WHERE tenant_id=$1 AND id=$2", tenant_id, nudge_id))

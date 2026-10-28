"""Production PostgreSQL/RLS lead store; no in-memory production fallback."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
from typing import AsyncIterator

import asyncpg
from leads import CadenceAction, Conversion, Lead, LeadError, LeadInput, Score, TerritoryAssignment


class PostgresLeadStore:
    def __init__(self, pool: asyncpg.Pool): self.pool = pool
    @classmethod
    async def connect(cls, database_url: str):
        if not database_url: raise RuntimeError("DATABASE_URL is required")
        return cls(await asyncpg.create_pool(database_url, min_size=1, max_size=10, command_timeout=30))
    async def close(self): await self.pool.close()
    async def ready(self):
        async with self.pool.acquire() as connection: await connection.fetchval("SELECT 1")
    @asynccontextmanager
    async def tenant_tx(self, tenant_id: str) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT set_config('volumeos.tenant_id',$1,true)", tenant_id)
                yield connection

    @staticmethod
    def _lead(row) -> Lead:
        return Lead(*(row[key] for key in ("tenant_id", "lead_id", "dedupe_key", "business_name", "phone", "contact_name",
                    "address", "niche", "h3_r9", "consent_reference", "stage", "created_at_unix", "updated_at_unix", "version")))

    async def get(self, tenant_id, lead_id):
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow("SELECT * FROM leads WHERE tenant_id=$1 AND lead_id=$2", tenant_id, lead_id)
        return self._lead(row) if row else None

    async def ingest(self, tenant_id, value: LeadInput, lead: Lead, fingerprint, lineage):
        async with self.tenant_tx(tenant_id) as connection:
            source = await connection.fetchrow("SELECT lead_id,fingerprint FROM lead_sources WHERE tenant_id=$1 AND source=$2 AND source_record_id=$3 FOR UPDATE",
                                                tenant_id, value.source, value.source_record_id)
            if source:
                if source["fingerprint"] != fingerprint: raise LeadError("source replay conflicts with original content")
                row = await connection.fetchrow("SELECT * FROM leads WHERE tenant_id=$1 AND lead_id=$2", tenant_id, source["lead_id"])
                return self._lead(row)
            existing = await connection.fetchrow("SELECT * FROM leads WHERE tenant_id=$1 AND dedupe_key=$2 FOR UPDATE", tenant_id, lead.dedupe_key)
            if existing:
                lead_id = existing["lead_id"]
                await connection.execute("INSERT INTO lead_lineage(tenant_id,lead_id,event) VALUES ($1,$2,$3::jsonb)",
                                          tenant_id, lead_id, json.dumps({"type": "duplicate-source", **lineage}))
            else:
                values = (lead.tenant_id, lead.lead_id, lead.dedupe_key, lead.business_name, lead.phone, lead.contact_name,
                          lead.address, lead.niche, lead.h3_r9, lead.consent_reference, lead.stage, lead.created_at_unix, lead.updated_at_unix, lead.version)
                await connection.execute("INSERT INTO leads VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)", *values)
                lead_id = lead.lead_id
                await connection.execute("INSERT INTO lead_lineage(tenant_id,lead_id,event) VALUES ($1,$2,$3::jsonb)",
                                          tenant_id, lead_id, json.dumps({"type": "ingest", **lineage}))
            await connection.execute("INSERT INTO lead_sources(tenant_id,source,source_record_id,lead_id,fingerprint,lineage) VALUES ($1,$2,$3,$4,$5,$6::jsonb)",
                                      tenant_id, value.source, value.source_record_id, lead_id, fingerprint, json.dumps(lineage))
            return self._lead(existing) if existing else lead

    async def save(self, lead):
        async with self.tenant_tx(lead.tenant_id) as connection:
            result = await connection.execute("UPDATE leads SET stage=$3,updated_at_unix=$4,version=$5 WHERE tenant_id=$1 AND lead_id=$2 AND version=$6",
                                               lead.tenant_id, lead.lead_id, lead.stage, lead.updated_at_unix, lead.version, lead.version - 1)
            if result != "UPDATE 1": raise LeadError("lead update conflict")
    async def add_lineage(self, tenant_id, lead_id, event):
        async with self.tenant_tx(tenant_id) as connection:
            await connection.execute("INSERT INTO lead_lineage(tenant_id,lead_id,event) VALUES ($1,$2,$3::jsonb)", tenant_id, lead_id, json.dumps(event))
    async def add_score(self, score: Score):
        async with self.tenant_tx(score.tenant_id) as connection:
            await connection.execute("INSERT INTO lead_scores VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8)", score.tenant_id, score.lead_id,
                                      score.model_version, score.weight_version, score.gold_lineage_id, score.score, json.dumps(score.reasons), score.created_at_unix)
    async def actions(self, tenant_id, lead_id):
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch("SELECT * FROM lead_cadence WHERE tenant_id=$1 AND lead_id=$2", tenant_id, lead_id)
        return [CadenceAction(row["tenant_id"], row["lead_id"], row["step"], row["status"], row["approval_id"], row["created_at_unix"]) for row in rows]
    async def add_action(self, action):
        async with self.tenant_tx(action.tenant_id) as connection:
            await connection.execute("INSERT INTO lead_cadence VALUES ($1,$2,$3,$4,$5,$6)", action.tenant_id, action.lead_id, action.step, action.status, action.approval_id, action.created_at_unix)
    async def conversion(self, tenant_id, lead_id):
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow("SELECT * FROM lead_conversions WHERE tenant_id=$1 AND lead_id=$2", tenant_id, lead_id)
        return Conversion(*(row[key] for key in ("tenant_id", "conversion_id", "lead_id", "opportunity_id", "consent_reference", "actor", "created_at_unix"))) if row else None
    async def add_conversion(self, conversion):
        async with self.tenant_tx(conversion.tenant_id) as connection:
            await connection.execute("INSERT INTO lead_conversions VALUES ($1,$2,$3,$4,$5,$6,$7)", conversion.tenant_id, conversion.conversion_id,
                                      conversion.lead_id, conversion.opportunity_id, conversion.consent_reference, conversion.actor, conversion.created_at_unix)
    async def assignment(self, tenant_id, lead_id):
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow("SELECT * FROM lead_territory_assignments WHERE tenant_id=$1 AND lead_id=$2", tenant_id, lead_id)
        return TerritoryAssignment(*(row[key] for key in ("tenant_id", "lead_id", "h3_r9", "owner_id", "model_version", "weight_version", "gold_lineage_id", "assigned_at_unix"))) if row else None
    async def add_assignment(self, assignment):
        async with self.tenant_tx(assignment.tenant_id) as connection:
            await connection.execute("INSERT INTO lead_territory_assignments VALUES ($1,$2,$3,$4,$5,$6,$7,$8)", assignment.tenant_id, assignment.lead_id,
                                      assignment.h3_r9, assignment.owner_id, assignment.model_version, assignment.weight_version,
                                      assignment.gold_lineage_id, assignment.assigned_at_unix)


class PostgresConsentRegistry:
    def __init__(self, store: PostgresLeadStore): self.store = store
    async def granted(self, tenant_id, reference, purpose):
        async with self.store.tenant_tx(tenant_id) as connection:
            return await connection.fetchval("SELECT status='granted' FROM consents WHERE tenant_id=$1 AND reference=$2 AND purpose=$3", tenant_id, reference, purpose) is True

"""PostgreSQL/RLS persistence for privacy records; no production memory fallback."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
from typing import AsyncIterator

import asyncpg

from privacy import Consent, DSAR, PrivacyError, canonical


class PostgresPrivacyStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "PostgresPrivacyStore":
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        return cls(await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=10, command_timeout=30))

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

    async def consent(self, tenant_id: str, reference: str) -> Consent | None:
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow("SELECT * FROM consents WHERE tenant_id=$1 AND reference=$2", tenant_id, reference)
        return self._consent(row) if row else None

    async def append_consent(self, consent: Consent) -> None:
        async with self.tenant_tx(consent.tenant_id) as connection:
            current = await connection.fetchrow(
                "SELECT version,subject_digest FROM consents WHERE tenant_id=$1 AND reference=$2 FOR UPDATE",
                consent.tenant_id, consent.reference,
            )
            if current and (current["version"] >= consent.version or current["subject_digest"] != consent.subject_digest):
                raise PrivacyError("consent version or subject conflict")
            await connection.execute(
                "INSERT INTO consents (tenant_id,reference,subject_digest,purpose,lawful_basis,status,version,actor,created_at_unix) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT (tenant_id,reference) DO UPDATE SET "
                "purpose=EXCLUDED.purpose,lawful_basis=EXCLUDED.lawful_basis,status=EXCLUDED.status,"
                "version=EXCLUDED.version,actor=EXCLUDED.actor,created_at_unix=EXCLUDED.created_at_unix",
                *self._consent_values(consent),
            )
            await connection.execute(
                "INSERT INTO consent_history (tenant_id,reference,subject_digest,purpose,lawful_basis,status,version,actor,created_at_unix) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)", *self._consent_values(consent),
            )

    async def dsar(self, tenant_id: str, request_id: str) -> DSAR | None:
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow("SELECT * FROM dsar_requests WHERE tenant_id=$1 AND request_id=$2", tenant_id, request_id)
        return self._dsar(row) if row else None

    async def create_dsar(self, request: DSAR) -> None:
        async with self.tenant_tx(request.tenant_id) as connection:
            try:
                await connection.execute(
                    "INSERT INTO dsar_requests (tenant_id,request_id,subject_digest,kind,status,actor,created_at_unix,deletion) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'{}'::jsonb)",
                    request.tenant_id, request.request_id, request.subject_digest, request.kind,
                    request.status, request.actor, request.created_at_unix,
                )
            except asyncpg.UniqueViolationError as exc:
                raise PrivacyError("DSAR request already exists") from exc

    async def update_dsar(self, request: DSAR) -> None:
        async with self.tenant_tx(request.tenant_id) as connection:
            result = await connection.execute(
                "UPDATE dsar_requests SET status=$3,export_manifest=$4::jsonb,deletion=$5::jsonb "
                "WHERE tenant_id=$1 AND request_id=$2 AND status NOT IN ('exported','deleted')",
                request.tenant_id, request.request_id, request.status,
                json.dumps(request.export_manifest) if request.export_manifest else None,
                json.dumps(request.deletion),
            )
            if result != "UPDATE 1":
                raise PrivacyError("DSAR update conflict")

    async def audit(self, tenant_id: str, event: dict) -> None:
        async with self.tenant_tx(tenant_id) as connection:
            previous = await connection.fetchval(
                "SELECT event_sha256 FROM privacy_audit WHERE tenant_id=$1 ORDER BY sequence DESC LIMIT 1", tenant_id,
            ) or "0" * 64
            payload = {"tenant_id": tenant_id, **event, "previous_sha256": previous}
            digest = __import__("hashlib").sha256(canonical(payload).encode()).hexdigest()
            await connection.execute(
                "INSERT INTO privacy_audit (tenant_id,event,previous_sha256,event_sha256) VALUES ($1,$2::jsonb,$3,$4)",
                tenant_id, json.dumps(event, sort_keys=True), previous, digest,
            )

    @staticmethod
    def _consent_values(consent: Consent) -> tuple:
        return (consent.tenant_id, consent.reference, consent.subject_digest, consent.purpose,
                consent.lawful_basis, consent.status, consent.version, consent.actor, consent.created_at_unix)

    @staticmethod
    def _consent(row) -> Consent:
        return Consent(*(row[key] for key in ("tenant_id", "reference", "subject_digest", "purpose", "lawful_basis",
                                               "status", "version", "actor", "created_at_unix")))

    @staticmethod
    def _document(value, default):
        return json.loads(value) if isinstance(value, str) else (value if value is not None else default)

    def _dsar(self, row) -> DSAR:
        return DSAR(row["tenant_id"], row["request_id"], row["subject_digest"], row["kind"], row["status"],
                    row["actor"], row["created_at_unix"], self._document(row["export_manifest"], None),
                    self._document(row["deletion"], {}))

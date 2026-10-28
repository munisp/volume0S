"""PostgreSQL/RLS durability for resumable census uploads."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from sync import CensusError, Publication, Upload


class PostgresCensusStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "PostgresCensusStore":
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

    async def create(self, upload: Upload) -> bool:
        async with self.tenant_tx(upload.tenant_id) as connection:
            row = await connection.fetchrow(
                "INSERT INTO census_uploads (tenant_id,upload_id,event_id,device_id,manifest) "
                "VALUES ($1,$2,$3,$4,$5::jsonb) ON CONFLICT DO NOTHING RETURNING upload_id",
                upload.tenant_id, upload.upload_id, upload.event_id, upload.device_id,
                json.dumps(self._manifest(upload), separators=(",", ":")),
            )
        return row is not None

    async def get(self, tenant_id: str, upload_id: str) -> Upload | Publication | None:
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                "SELECT event_id,device_id,manifest,published_document FROM census_uploads "
                "WHERE tenant_id=$1 AND upload_id=$2", tenant_id, upload_id,
            )
        if row is None:
            return None
        if row["published_document"] is not None:
            return Publication(tenant_id, row["device_id"], upload_id, row["event_id"], self._text(row["published_document"]))
        manifest = row["manifest"]
        if isinstance(manifest, str):
            manifest = json.loads(manifest)
        return Upload(tenant_id=tenant_id, upload_id=upload_id, event_id=row["event_id"],
                      device_id=row["device_id"], **manifest)

    async def chunk(self, tenant_id: str, upload_id: str, index: int) -> tuple[str, bytes] | None:
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                "SELECT sha256,content FROM census_chunks WHERE tenant_id=$1 AND upload_id=$2 AND chunk_index=$3",
                tenant_id, upload_id, index,
            )
        return (row["sha256"], bytes(row["content"])) if row else None

    async def put_chunk(self, tenant_id: str, upload_id: str, index: int, digest: str, data: bytes) -> None:
        async with self.tenant_tx(tenant_id) as connection:
            await connection.execute(
                "INSERT INTO census_chunks (tenant_id,upload_id,chunk_index,sha256,content) VALUES ($1,$2,$3,$4,$5) "
                "ON CONFLICT (tenant_id,upload_id,chunk_index) DO NOTHING",
                tenant_id, upload_id, index, digest, data,
            )

    async def chunk_indexes(self, tenant_id: str, upload_id: str) -> set[int]:
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                "SELECT chunk_index FROM census_chunks WHERE tenant_id=$1 AND upload_id=$2",
                tenant_id, upload_id,
            )
        return {int(row["chunk_index"]) for row in rows}

    async def publish(self, publication: Publication) -> None:
        async with self.tenant_tx(publication.tenant_id) as connection:
            row = await connection.fetchrow(
                "UPDATE census_uploads SET published_document=$4::jsonb,published_at=now() "
                "WHERE tenant_id=$1 AND upload_id=$2 AND event_id=$3 AND device_id=$5 "
                "AND published_document IS NULL RETURNING upload_id",
                publication.tenant_id, publication.upload_id, publication.event_id,
                publication.document_json, publication.device_id,
            )
            if row is None:
                existing = await connection.fetchrow(
                    "SELECT published_document FROM census_uploads WHERE tenant_id=$1 AND upload_id=$2 "
                    "AND event_id=$3 AND device_id=$4",
                    publication.tenant_id, publication.upload_id, publication.event_id, publication.device_id,
                )
                if existing is None or self._text(existing["published_document"]) != publication.document_json:
                    raise CensusError("publication does not match its active upload")
            await connection.execute(
                "DELETE FROM census_chunks WHERE tenant_id=$1 AND upload_id=$2",
                publication.tenant_id, publication.upload_id,
            )

    @staticmethod
    def _manifest(upload: Upload) -> dict:
        return {
            "actor": upload.actor, "total_chunks": upload.total_chunks, "total_bytes": upload.total_bytes,
            "payload_sha256": upload.payload_sha256, "captured_at_unix": upload.captured_at_unix,
            "latitude": upload.latitude, "longitude": upload.longitude, "accuracy_m": upload.accuracy_m,
            "consent_reference": upload.consent_reference, "lawful_basis": upload.lawful_basis,
        }

    @staticmethod
    def _text(value) -> str:
        if isinstance(value, str):
            value = json.loads(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

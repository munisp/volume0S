"""Tenant-isolated reads of immutable documents and append-only delivery receipts."""
from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, AsyncIterator, Protocol

import asyncpg

from calculations import ar_aging_rows, partner_share_rows
from models import Delivery, StoredDocument, validate_scope


class DocumentStore(Protocol):
    async def ready(self) -> None: ...
    async def list_documents(self, tenant_id: str, limit: int) -> list[dict[str, Any]]: ...
    async def get_document(self, tenant_id: str, kind: str, document_id: str) -> StoredDocument | None: ...
    async def get_delivery(self, tenant_id: str, kind: str, document_id: str, recipient: str) -> Delivery | None: ...
    async def record_delivery(self, tenant_id: str, kind: str, document_id: str, recipient: str, provider_message_id: str) -> Delivery: ...
    async def ar_aging(self, tenant_id: str, as_of: date) -> dict[str, Any]: ...
    async def create_partner_contract(self, value: dict[str, Any]) -> dict[str, Any]: ...
    async def list_partner_contracts(self, tenant_id: str, partner_id: str | None, limit: int) -> list[dict[str, Any]]: ...
    async def partner_share_statement(self, tenant_id: str, partner_id: str, start: date, end: date) -> dict[str, Any]: ...


class PostgresDocumentStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "PostgresDocumentStore":
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

    async def list_documents(self, tenant_id: str, limit: int) -> list[dict[str, Any]]:
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                "SELECT kind,id,tenant_id,document_number,structured,created_at,pdf FROM ("
                "SELECT 'invoice' AS kind,id,tenant_id,document_number,structured,created_at,pdf FROM invoices "
                "UNION ALL SELECT 'receipt',id,tenant_id,document_number,structured,created_at,pdf FROM receipts"
                ") documents WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT $2",
                tenant_id, limit,
            )
        # The union above deliberately projects tenant scope in the outer query;
        # PostgreSQL RLS still applies independently to both source tables.
        return [self._metadata(row) for row in rows]

    def _metadata(self, row: asyncpg.Record) -> dict[str, Any]:
        structured = row["structured"]
        if isinstance(structured, str):
            structured = json.loads(structured)
        return {
            "kind": row["kind"], "document_id": row["id"], "tenant_id": row["tenant_id"],
            "document_number": row["document_number"], "structured": structured,
            "created_at": row["created_at"].isoformat(),
            "pdf_sha256": hashlib.sha256(row["pdf"]).hexdigest(),
        }

    async def get_document(self, tenant_id: str, kind: str, document_id: str) -> StoredDocument | None:
        validate_scope(tenant_id, document_id, kind)
        table = "invoices" if kind == "invoice" else "receipts"
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                f"SELECT id,tenant_id,document_number,structured,pdf,created_at FROM {table} WHERE id=$1 AND tenant_id=$2",
                document_id, tenant_id,
            )
        if row is None:
            return None
        structured = row["structured"]
        if isinstance(structured, str):
            structured = json.loads(structured)
        return StoredDocument(kind, row["id"], row["tenant_id"], row["document_number"], structured, row["pdf"], row["created_at"])

    async def get_delivery(self, tenant_id: str, kind: str, document_id: str, recipient: str) -> Delivery | None:
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                "SELECT * FROM document_deliveries WHERE tenant_id=$1 AND document_kind=$2 AND document_id=$3 AND channel='whatsapp' AND recipient=$4",
                tenant_id, kind, document_id, recipient,
            )
        return self._delivery(row) if row else None

    async def record_delivery(self, tenant_id: str, kind: str, document_id: str, recipient: str, provider_message_id: str) -> Delivery:
        delivery_id = "delivery:" + hashlib.blake2s(
            "\x1f".join((tenant_id, kind, document_id, recipient)).encode(), digest_size=16
        ).hexdigest()
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                "INSERT INTO document_deliveries (id,tenant_id,document_kind,document_id,channel,recipient,provider_message_id,status) "
                "VALUES ($1,$2,$3,$4,'whatsapp',$5,$6,'provider_accepted') "
                "ON CONFLICT DO NOTHING RETURNING *",
                delivery_id, tenant_id, kind, document_id, recipient, provider_message_id,
            )
            if row is None:
                row = await connection.fetchrow(
                    "SELECT * FROM document_deliveries WHERE tenant_id=$1 AND document_kind=$2 AND document_id=$3 AND channel='whatsapp' AND recipient=$4",
                    tenant_id, kind, document_id, recipient,
                )
                if row is None or row["provider_message_id"] != provider_message_id:
                    raise ValueError("delivery idempotency conflict")
        return self._delivery(row)

    async def ar_aging(self, tenant_id: str, as_of: date) -> dict[str, Any]:
        """Age due-on-issue prepaid invoices, excluding later receipts."""
        end_exclusive = datetime.combine(as_of + timedelta(days=1), time.min, tzinfo=timezone.utc)
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                "SELECT i.id,i.document_number,i.issue_date,i.currency,i.total_minor,"
                "COALESCE(SUM(r.amount_minor),0) AS paid_minor "
                "FROM invoices i LEFT JOIN receipts r ON r.invoice_id=i.id "
                "AND r.tenant_id=i.tenant_id AND r.created_at<$2 "
                "WHERE i.tenant_id=$1 AND i.issue_date<=$3 "
                "GROUP BY i.id,i.document_number,i.issue_date,i.currency,i.total_minor "
                "ORDER BY i.issue_date,i.id",
                tenant_id, end_exclusive, as_of,
            )
        return ar_aging_rows(tenant_id, as_of, rows)

    async def create_partner_contract(self, value: dict[str, Any]) -> dict[str, Any]:
        async with self.tenant_tx(value["tenant_id"]) as connection:
            row = await connection.fetchrow(
                "INSERT INTO partner_share_contracts "
                "(id,tenant_id,partner_id,offer,basis_points,effective_from,effective_to) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (id) DO NOTHING RETURNING *",
                value["contract_id"], value["tenant_id"], value["partner_id"], value["offer"],
                value["basis_points"], value["effective_from"], value["effective_to"],
            )
            if row is None:
                row = await connection.fetchrow(
                    "SELECT * FROM partner_share_contracts WHERE id=$1 AND tenant_id=$2",
                    value["contract_id"], value["tenant_id"],
                )
                if row is None or any((
                    row["partner_id"] != value["partner_id"], row["offer"] != value["offer"],
                    int(row["basis_points"]) != value["basis_points"],
                    row["effective_from"] != value["effective_from"], row["effective_to"] != value["effective_to"],
                )):
                    raise ValueError("partner contract id already exists with different terms")
        return self._partner_contract(row)

    async def list_partner_contracts(self, tenant_id: str, partner_id: str | None, limit: int) -> list[dict[str, Any]]:
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                "SELECT * FROM partner_share_contracts WHERE tenant_id=$1 "
                "AND ($2::text IS NULL OR partner_id=$2) ORDER BY created_at DESC,id LIMIT $3",
                tenant_id, partner_id, limit,
            )
        return [self._partner_contract(row) for row in rows]

    def _partner_contract(self, row: asyncpg.Record) -> dict[str, Any]:
        return {
            "contract_id": row["id"], "tenant_id": row["tenant_id"],
            "partner_id": row["partner_id"], "offer": row["offer"],
            "basis_points": int(row["basis_points"]),
            "effective_from": row["effective_from"].isoformat(),
            "effective_to": row["effective_to"].isoformat() if row["effective_to"] else None,
            "created_at": row["created_at"].isoformat(),
        }

    async def partner_share_statement(self, tenant_id: str, partner_id: str, start: date, end: date) -> dict[str, Any]:
        """Calculate exact integer shares from immutable, posted billing receipts."""
        end_exclusive = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                "SELECT r.id,r.document_number,r.amount_minor,r.currency,r.ledger_transfer_id,r.created_at,"
                "i.structured->>'offer' AS offer,c.id AS contract_id,c.basis_points "
                "FROM receipts r JOIN invoices i ON i.id=r.invoice_id AND i.tenant_id=r.tenant_id "
                "JOIN LATERAL ("
                "SELECT c.id,c.basis_points FROM partner_share_contracts c "
                "WHERE c.tenant_id=r.tenant_id AND c.partner_id=$2 AND c.offer=i.structured->>'offer' "
                "AND c.effective_from<=r.created_at AND (c.effective_to IS NULL OR c.effective_to>r.created_at) "
                "ORDER BY c.effective_from DESC,c.created_at DESC,c.id DESC LIMIT 1"
                ") c ON TRUE WHERE r.tenant_id=$1 AND r.created_at>=$3 AND r.created_at<$4 "
                "ORDER BY r.created_at,r.id",
                tenant_id, partner_id,
                datetime.combine(start, time.min, tzinfo=timezone.utc), end_exclusive,
            )
        return partner_share_rows(tenant_id, partner_id, start, end, rows)

    def _delivery(self, row: asyncpg.Record) -> Delivery:
        return Delivery(
            row["id"], row["tenant_id"], row["document_kind"], row["document_id"],
            row["channel"], row["recipient"], row["provider_message_id"], row["status"], row["created_at"],
        )

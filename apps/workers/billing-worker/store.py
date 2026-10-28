"""Tenant-isolated append-only billing document persistence.

Invoice amounts are accounting documents, not a balance cache. TigerBeetle remains
the only source for available funds and posted account balances.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import AsyncIterator, Protocol

import asyncpg

from documents import (
    InvoiceDraft,
    ReceiptDraft,
    invoice_fields,
    receipt_fields,
    render_invoice_pdf,
    render_receipt_pdf,
)
from models import DocumentResult, DunningRecord, FxCloseInput, FxRevaluation, UsagePostingResult, WeeklyUsagePosting


class BillingStore(Protocol):
    async def record_invoice(self, invoice: InvoiceDraft) -> str: ...

    async def record_receipt(self, invoice: InvoiceDraft, receipt: ReceiptDraft) -> DocumentResult: ...

    async def record_documents(self, invoice: InvoiceDraft, receipt: ReceiptDraft) -> DocumentResult: ...

    async def record_usage(self, posting: WeeklyUsagePosting, result: UsagePostingResult) -> None: ...

    async def record_dunning(self, record: DunningRecord) -> None: ...

    async def record_fx(self, value: FxCloseInput, revaluation: FxRevaluation, transfer_id: str | None) -> None: ...


class PostgresBillingStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "PostgresBillingStore":
        pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=10, command_timeout=30)
        store = cls(pool)
        await store.migrate()
        return store

    async def close(self) -> None:
        await self.pool.close()

    async def migrate(self) -> None:
        migrations = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
        async with self.pool.acquire() as connection:
            await connection.execute("SELECT pg_advisory_lock(hashtext('volumeos.billing.migrations'))")
            try:
                await connection.execute("CREATE TABLE IF NOT EXISTS billing_schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
                for path in migrations:
                    version = path.stem
                    applied = await connection.fetchval("SELECT version FROM billing_schema_migrations WHERE version=$1", version)
                    if applied is None:
                        async with connection.transaction():
                            await connection.execute(path.read_text())
                            await connection.execute("INSERT INTO billing_schema_migrations(version) VALUES ($1)", version)
            finally:
                await connection.execute("SELECT pg_advisory_unlock(hashtext('volumeos.billing.migrations'))")

    @asynccontextmanager
    async def tenant_tx(self, tenant_id: str) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT set_config('volumeos.tenant_id', $1, true)", tenant_id)
                yield connection

    async def record_invoice(self, invoice: InvoiceDraft) -> str:
        """Issue an invoice before attempting the prepaid reservation."""
        async with self.tenant_tx(invoice.tenant_id) as connection:
            existing = await connection.fetchrow(
                "SELECT document_number,structured FROM invoices WHERE id=$1 AND tenant_id=$2",
                invoice.invoice_id,
                invoice.tenant_id,
            )
            if existing is not None:
                expected = replace(invoice, invoice_number=existing["document_number"])
                if json.loads(existing["structured"]) != invoice_fields(expected):
                    raise ValueError("existing invoice has different accounting fields")
                return existing["document_number"]
            year = int(invoice.issue_date[:4])
            invoice_number = await self._number(connection, invoice.tenant_id, year, "invoice")
            invoice = replace(invoice, invoice_number=invoice_number)
            await connection.execute(
                "INSERT INTO invoices (id,tenant_id,subscription_id,cycle,document_number,issue_date,currency,subtotal_minor,tax_minor,total_minor,seller_tin,buyer_tin,structured,pdf) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
                invoice.invoice_id,
                invoice.tenant_id,
                invoice.subscription_id,
                invoice.cycle,
                invoice.invoice_number,
                invoice.issue_date,
                invoice.currency,
                invoice.subtotal_minor,
                invoice.tax_minor,
                invoice.total_minor,
                invoice.seller["tin"],
                invoice.buyer["tin"],
                json.dumps(invoice_fields(invoice), separators=(",", ":")),
                render_invoice_pdf(invoice),
            )
            return invoice_number

    async def record_receipt(self, invoice: InvoiceDraft, receipt: ReceiptDraft) -> DocumentResult:
        """Append a receipt only after the matching ledger posting exists."""
        if invoice.invoice_id != receipt.invoice_id or invoice.tenant_id != receipt.tenant_id:
            raise ValueError("invoice and receipt scope differ")
        async with self.tenant_tx(invoice.tenant_id) as connection:
            invoice_row = await connection.fetchrow(
                "SELECT document_number,structured FROM invoices WHERE id=$1 AND tenant_id=$2",
                invoice.invoice_id,
                invoice.tenant_id,
            )
            if invoice_row is None:
                raise ValueError("receipt requires a previously issued same-tenant invoice")
            numbered_invoice = replace(invoice, invoice_number=invoice_row["document_number"])
            if json.loads(invoice_row["structured"]) != invoice_fields(numbered_invoice):
                raise ValueError("receipt invoice fields differ from the issued invoice")
            existing = await connection.fetchrow(
                "SELECT id,document_number,ledger_transfer_id,amount_minor FROM receipts WHERE invoice_id=$1 AND tenant_id=$2",
                invoice.invoice_id,
                invoice.tenant_id,
            )
            if existing is not None:
                if str(existing["ledger_transfer_id"]) != receipt.transfer_id or int(existing["amount_minor"]) != receipt.amount_minor:
                    raise ValueError("existing receipt has different money fields")
                return DocumentResult(
                    invoice.invoice_id,
                    invoice_row["document_number"],
                    str(existing["id"]),
                    existing["document_number"],
                )
            year = int(invoice.issue_date[:4])
            receipt_number = await self._number(connection, invoice.tenant_id, year, "receipt")
            receipt = replace(receipt, receipt_number=receipt_number)
            await connection.execute(
                "INSERT INTO receipts (id,invoice_id,tenant_id,document_number,ledger_transfer_id,amount_minor,currency,structured,pdf) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                receipt.receipt_id,
                receipt.invoice_id,
                receipt.tenant_id,
                receipt.receipt_number,
                int(receipt.transfer_id),
                receipt.amount_minor,
                receipt.currency,
                json.dumps(receipt_fields(receipt), separators=(",", ":")),
                render_receipt_pdf(receipt),
            )
            return DocumentResult(invoice.invoice_id, invoice_row["document_number"], receipt.receipt_id, receipt_number)

    async def record_documents(self, invoice: InvoiceDraft, receipt: ReceiptDraft) -> DocumentResult:
        await self.record_invoice(invoice)
        return await self.record_receipt(invoice, receipt)

    async def _number(self, connection: asyncpg.Connection, tenant_id: str, year: int, kind: str) -> str:
        number = await connection.fetchval(
            "INSERT INTO billing_sequences (tenant_id,fiscal_year,kind,next_number) VALUES ($1,$2,$3,2) "
            "ON CONFLICT (tenant_id,fiscal_year,kind) DO UPDATE SET next_number=billing_sequences.next_number+1 "
            "RETURNING next_number-1",
            tenant_id,
            year,
            kind,
        )
        tenant_token = hashlib.blake2s(tenant_id.encode(), digest_size=4).hexdigest()
        prefix = "INV" if kind == "invoice" else "RCT"
        return f"{prefix}-{year}-{tenant_token}-{int(number):06d}"

    async def record_usage(self, posting: WeeklyUsagePosting, result: UsagePostingResult) -> None:
        """Persist an immutable posting manifest, never a derived balance."""
        if (
            posting.event_id != result.event_id
            or posting.tenant_id != result.tenant_id
            or posting.amount_minor != result.amount_minor
            or posting.provider_cost_minor != result.provider_cost_minor
            or result.state != "posted"
        ):
            raise ValueError("usage posting manifest does not match the closed week")
        async with self.tenant_tx(posting.tenant_id) as connection:
            existing = await connection.fetchrow(
                "SELECT ledger_transfer_id,provider_cost_transfer_id,event_count,amount_minor,provider_cost_minor "
                "FROM usage_week_postings WHERE event_id=$1",
                posting.event_id,
            )
            expected_cost_id = int(result.provider_cost_transfer_id) if result.provider_cost_transfer_id else None
            if existing is not None:
                recorded_cost = existing["provider_cost_transfer_id"]
                recorded_cost_id = str(recorded_cost) if recorded_cost is not None else ""
                if (
                    str(existing["ledger_transfer_id"]) != result.transfer_id
                    or recorded_cost_id != result.provider_cost_transfer_id
                    or int(existing["event_count"]) != posting.event_count
                    or int(existing["amount_minor"]) != posting.amount_minor
                    or int(existing["provider_cost_minor"]) != posting.provider_cost_minor
                ):
                    raise ValueError("usage event ID was already used with another posting")
                return
            await connection.execute(
                "INSERT INTO usage_week_postings (event_id,tenant_id,provider,channel,campaign_id,offer,week,first_day,last_day,event_count,amount_minor,provider_cost_minor,currency,ledger_transfer_id,provider_cost_transfer_id) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)",
                posting.event_id,
                posting.tenant_id,
                posting.provider,
                posting.channel,
                posting.campaign_id,
                posting.offer,
                posting.week,
                posting.first_day,
                posting.last_day,
                posting.event_count,
                posting.amount_minor,
                posting.provider_cost_minor,
                posting.currency,
                int(result.transfer_id),
                expected_cost_id,
            )

    async def record_dunning(self, record: DunningRecord) -> None:
        async with self.tenant_tx(record.tenant_id) as connection:
            await connection.execute(
                "INSERT INTO dunning_cases (tenant_id,subscription_id,cycle,state,attempts,recovery_reference,updated_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,now()) ON CONFLICT (tenant_id,subscription_id,cycle) DO UPDATE SET "
                "state=EXCLUDED.state,attempts=EXCLUDED.attempts,recovery_reference=EXCLUDED.recovery_reference,updated_at=now()",
                record.tenant_id,
                record.subscription_id,
                record.cycle,
                record.state,
                record.attempts,
                record.recovery_reference or None,
            )

    async def record_fx(self, value: FxCloseInput, revaluation: FxRevaluation, transfer_id: str | None) -> None:
        async with self.tenant_tx("platform") as connection:
            existing = await connection.fetchrow("SELECT ledger_transfer_id FROM fx_closes WHERE event_id=$1", value.event_id)
            if existing is not None:
                recorded = existing["ledger_transfer_id"]
                if (recorded is None) != (transfer_id is None) or (recorded is not None and str(recorded) != transfer_id):
                    raise ValueError("FX close event ID was already used with another transfer")
                return
            await connection.execute(
                "INSERT INTO fx_closes (event_id,tenant_id,provider,month,usd_minor,previous_ngn_minor_per_usd,closing_ngn_minor_per_usd,revaluation_minor,direction,ledger_transfer_id) "
                "VALUES ($1,'platform',$2,$3,$4,$5,$6,$7,$8,$9)",
                value.event_id,
                value.provider,
                value.month,
                value.usd_minor,
                value.previous_ngn_minor_per_usd,
                value.closing_ngn_minor_per_usd,
                revaluation.amount_minor,
                revaluation.direction,
                int(transfer_id) if transfer_id is not None else None,
            )

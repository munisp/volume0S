"""The real Postgres store, against a real database (the unit tests use a fake store, which hid a date-type bug:
asyncpg refuses ISO text for a `date` column, so no billing cycle could issue an invoice).

Runs only when BILLING_TEST_DATABASE_URL points at a throwaway database, e.g.
  docker run -d --name bw-pg -e POSTGRES_PASSWORD=pw -p 55432:5432 postgres:16-alpine
  BILLING_TEST_DATABASE_URL=postgresql://postgres:pw@localhost:55432/postgres pytest tests/test_store_postgres.py
"""
import os

import pytest

from documents import invoice_draft, receipt_draft
from models import DunningRecord, FxCloseInput, WeeklyUsagePosting, calculate_fx_revaluation
from store import PostgresBillingStore
from tests.test_billing import cycle, posting_result, reservation, usage

URL = os.environ.get("BILLING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="BILLING_TEST_DATABASE_URL is not set")


async def test_every_store_write_round_trips_on_postgres():
    store = await PostgresBillingStore.connect(URL)
    try:
        await store.migrate()
        await store.migrate()  # idempotent

        value = cycle()
        invoice = invoice_draft(value)
        number = await store.record_invoice(invoice)
        assert number and await store.record_invoice(invoice) == number  # replay returns the same number

        documents = await store.record_receipt(invoice, receipt_draft(invoice, reservation()))
        assert documents.invoice_number == number and documents.receipt_number
        assert (await store.record_receipt(invoice, receipt_draft(invoice, reservation()))).receipt_number == documents.receipt_number

        posting = WeeklyUsagePosting.from_days([usage()])
        await store.record_usage(posting, posting_result(posting))
        await store.record_usage(posting, posting_result(posting))  # replay is a no-op

        await store.record_dunning(DunningRecord(value.tenant_id, value.subscription_id, value.cycle, "nudging", 1))
        await store.record_dunning(DunningRecord(value.tenant_id, value.subscription_id, value.cycle, "recovered", 1, "topup-1"))

        fx = FxCloseInput("platform", "openai", "2026-10", 10000, 150000, 160000)
        await store.record_fx(fx, calculate_fx_revaluation(fx), "1000")

        async with store.pool.acquire() as connection:
            row = await connection.fetchrow("SELECT issue_date::text AS d, total_minor FROM invoices WHERE document_number=$1", number)
            assert row["d"] == value.issue_date and int(row["total_minor"]) == value.total_minor
            days = await connection.fetchrow("SELECT first_day::text AS a, last_day::text AS b FROM usage_week_postings WHERE event_id=$1", posting.event_id)
            assert (days["a"], days["b"]) == (posting.first_day, posting.last_day)
    finally:
        await store.close()

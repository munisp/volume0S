"""Insights indexer: VolumeOS's own business data, summarised into OpenSearch for the Intelligence page and report search.

Every INTERVAL_SECONDS, for each business (read as that business, so row-level security still applies):
  - one monthly report per month with activity (volumeos-reports-v1): money in and charged, invoices, campaign reach
    per channel, social posts, contacts, Studio packs and images delivered;
  - activity events (volumeos-logs-v1): top-up credited, invoice issued, payment settled, campaign sent, pack delivered.
Documents have deterministic ids, so every run overwrites rather than duplicates. Never authoritative state: the ledger
and the documents service stay the source of truth.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Iterable

REPORTS, LOGS = "volumeos-reports-v1", "volumeos-logs-v1"
MAPPINGS = {
    REPORTS: {"properties": {
        "tenant_id": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
        "report_id": {"type": "keyword"}, "offer": {"type": "text"}, "title": {"type": "text"}, "summary": {"type": "text"},
        "event_date": {"type": "date"}, "month": {"type": "keyword"}, "metrics": {"type": "object"},
        "updated_at": {"type": "date"}}},
    LOGS: {"properties": {
        "tenant_id": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
        "service": {"type": "text"}, "level": {"type": "text"}, "message": {"type": "text"}, "kind": {"type": "keyword"},
        "event_date": {"type": "date"}, "trace_id": {"type": "keyword"}, "amount_minor": {"type": "long"}}},
}
log = logging.getLogger("insights-indexer")


def naira(minor: int | float) -> str:
    return f"₦{int(minor) / 100:,.2f}"


def month_of(value: date | datetime) -> str:
    return value.strftime("%Y-%m")


def empty_metrics() -> dict[str, int]:
    return {"paid_in_minor": 0, "topups": 0, "charged_minor": 0, "invoices": 0, "receipts": 0, "emails": 0, "whatsapp": 0,
            "social_posts": 0, "campaigns_sent": 0, "campaign_spend_minor": 0, "studio_packs": 0, "studio_images": 0,
            "contacts_added": 0}


def build_documents(tenant_id: str, business: str, rows: dict[str, list[dict[str, Any]]], packs: Iterable[dict[str, Any]],
                    now: datetime) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
    """Pure: database rows + delivered Studio packs → (report docs, log docs), each as (id, body)."""
    months: dict[str, dict[str, int]] = defaultdict(empty_metrics)
    logs: list[tuple[str, dict]] = []

    def event(event_id: str, when: datetime | date, kind: str, service: str, message: str, amount: int | None = None) -> None:
        body = {"tenant_id": tenant_id, "service": service, "level": "info", "message": message, "kind": kind,
                "event_date": when.date().isoformat() if isinstance(when, datetime) else when.isoformat(), "trace_id": event_id}
        if amount is not None:
            body["amount_minor"] = int(amount)
        logs.append((f"{tenant_id}:{event_id}", body))

    for r in rows.get("topups", []):
        m = months[month_of(r["created_at"])]; m["paid_in_minor"] += int(r["amount_minor"]); m["topups"] += 1
        event(r["event_id"], r["created_at"], "topup", "collections", f"Top-up of {naira(r['amount_minor'])} received ({r['provider']} {r['reference']})", int(r["amount_minor"]))
    for r in rows.get("invoices", []):
        m = months[month_of(r["issue_date"])]; m["invoices"] += 1
        event(r["id"], r["issue_date"], "invoice", "billing", f"Invoice {r['document_number']} issued for {naira(r['total_minor'])} (cycle {r['cycle']})", int(r["total_minor"]))
    for r in rows.get("receipts", []):
        m = months[month_of(r["created_at"])]; m["receipts"] += 1; m["charged_minor"] += int(r["amount_minor"])
        event(r["id"], r["created_at"], "payment", "billing", f"Payment of {naira(r['amount_minor'])} settled ({r['document_number']})", int(r["amount_minor"]))
    for r in rows.get("dispatches", []):
        m = months[month_of(r["created_at"])]; m["campaigns_sent"] += 1
        spend = int(r.get("amount_minor") or 0)  # posted to the ledger once the campaign was sent
        m["campaign_spend_minor"] += spend; m["charged_minor"] += spend
        sent = {k: int(r.get(k) or 0) for k in ("emails", "whatsapp", "social")}
        m["emails"] += sent["emails"]; m["whatsapp"] += sent["whatsapp"]; m["social_posts"] += sent["social"]
        parts = [f"{n} {label}" for n, label in ((sent["emails"], "emails"), (sent["whatsapp"], "WhatsApp messages"), (sent["social"], "social posts")) if n]
        event(r["id"], r["created_at"], "campaign", "campaigns",
              f"Campaign {r['campaign_id']} sent: {', '.join(parts) or 'nothing delivered'}" + (f" ({naira(spend)} charged)" if spend else ""), spend or None)
    for r in rows.get("contacts", []):
        months[r["month"]]["contacts_added"] += int(r["n"])
    for p in packs:
        m = months[month_of(p["delivered_at"])]; m["studio_packs"] += 1; m["studio_images"] += int(p["images"])
        event(f"studio:{p['pack_id']}", p["delivered_at"], "studio", "studio", f"Studio pack {p['pack_id']} delivered: {p['images']} images")

    reports = []
    for month, m in sorted(months.items()):
        first = date.fromisoformat(month + "-01")
        label = first.strftime("%B %Y")
        reached = m["emails"] + m["whatsapp"]
        summary = (f"{business or tenant_id} in {label}: {naira(m['paid_in_minor'])} paid in, {naira(m['charged_minor'])} charged "
                   f"({m['invoices']} invoices, {naira(m['campaign_spend_minor'])} on campaigns); {m['campaigns_sent']} campaigns reached {reached} people "
                   f"({m['emails']} by email, {m['whatsapp']} by WhatsApp) and made {m['social_posts']} social posts; "
                   f"{m['studio_images']} Studio images in {m['studio_packs']} packs; {m['contacts_added']} contacts added.")
        reports.append((f"monthly:{tenant_id}:{month}", {
            "tenant_id": tenant_id, "report_id": f"monthly:{tenant_id}:{month}", "offer": "all", "month": month,
            "title": f"{business or tenant_id} — {label}", "summary": summary, "event_date": first.isoformat(),
            "metrics": m, "updated_at": now.isoformat()}))
    return reports, logs


# ── I/O ─────────────────────────────────────────────────────────────────────────────────────────────────────
QUERIES = {
    "topups": "SELECT event_id, provider, reference, amount_minor::bigint AS amount_minor, created_at FROM collection_receipts "
              "WHERE tenant_id=$1 AND disposition='credited'",
    "invoices": "SELECT id, document_number, cycle, total_minor, issue_date FROM invoices WHERE tenant_id=$1",
    "receipts": "SELECT id, document_number, amount_minor, created_at FROM receipts WHERE tenant_id=$1",
    "dispatches": "SELECT d.id, d.campaign_id, d.created_at, d.amount_minor, "
                  "(SELECT count(*) FROM campaign_sends s WHERE s.tenant_id=d.tenant_id AND s.dispatch_id=d.id AND s.channel='email' AND s.status IN ('sent','test_address')) AS emails, "
                  "(SELECT count(*) FROM campaign_sends s WHERE s.tenant_id=d.tenant_id AND s.dispatch_id=d.id AND s.channel='whatsapp' AND s.status IN ('sent','test_address')) AS whatsapp, "
                  "(SELECT count(*) FROM campaign_social_posts p WHERE p.tenant_id=d.tenant_id AND p.dispatch_id=d.id AND p.status='sent') AS social "
                  "FROM campaign_dispatches d WHERE d.tenant_id=$1 AND d.completed_at IS NOT NULL",
    "contacts": "SELECT to_char(created_at, 'YYYY-MM') AS month, count(*) AS n FROM campaign_contacts WHERE tenant_id=$1 GROUP BY 1",
}


async def read_business(pool, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("SELECT set_config('volumeos.tenant_id', $1, true)", tenant_id)
            for name, sql in QUERIES.items():
                try:
                    async with connection.transaction():  # a savepoint: one missing table mustn't abort the rest
                        out[name] = [dict(r) for r in await connection.fetch(sql, tenant_id)]
                except Exception as exc:  # a table a service hasn't created yet: index the rest
                    if "does not exist" not in str(exc):
                        raise
                    out[name] = []
    return out


def delivered_packs(s3, bucket: str, tenant_id: str) -> list[dict[str, Any]]:
    packs = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{tenant_id}/packs/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/delivered/manifest.json"):
                manifest = json.loads(s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read())
                if manifest.get("tenant_id") == tenant_id:
                    images = len([a for a in manifest.get("assets", []) if "/whatsapp/" in a])
                    packs.append({"pack_id": manifest.get("pack_id", ""), "images": images, "delivered_at": obj["LastModified"]})
    return packs


async def run_once(pool, s3, bucket: str, opensearch) -> dict[str, int]:
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("SELECT set_config('volumeos.tenant_id', 'platform', true)")  # read-only client list
            tenants = [dict(r) for r in await connection.fetch("SELECT id, name FROM tenants ORDER BY id")]
    for index, mapping in MAPPINGS.items():
        if not opensearch.indices.exists(index=index):
            opensearch.indices.create(index=index, body={"mappings": mapping})
    totals = {"businesses": 0, "reports": 0, "events": 0}
    now = datetime.now(timezone.utc)
    for tenant in tenants:
        rows = await read_business(pool, tenant["id"])
        packs = await asyncio.to_thread(delivered_packs, s3, bucket, tenant["id"]) if s3 else []
        reports, logs = build_documents(tenant["id"], tenant["name"], rows, packs, now)
        for index, docs in ((REPORTS, reports), (LOGS, logs)):
            for doc_id, body in docs:
                opensearch.index(index=index, id=doc_id, body=json.loads(json.dumps(body, default=str)))
        totals["businesses"] += 1; totals["reports"] += len(reports); totals["events"] += len(logs)
    opensearch.indices.refresh(index=f"{REPORTS},{LOGS}")
    return totals


async def main() -> None:
    import asyncpg
    import boto3
    from opensearchpy import OpenSearch
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pool = await asyncpg.create_pool(dsn=os.environ["DATABASE_URL"], min_size=1, max_size=3)
    url = os.environ["OPENSEARCH_URL"]
    opensearch = OpenSearch(hosts=[url], http_auth=(os.environ["OPENSEARCH_USERNAME"], os.environ["OPENSEARCH_PASSWORD"]),
                            use_ssl=url.startswith("https://"), verify_certs=False, timeout=20)
    bucket = os.environ.get("ASSET_BUCKET", "")
    s3 = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL")) if bucket else None
    interval = int(os.environ.get("INTERVAL_SECONDS", "600"))
    while True:
        try:
            log.info("indexed %s", await run_once(pool, s3, bucket, opensearch))
        except Exception:  # noqa: BLE001 - keep the loop alive; the next run retries everything
            log.exception("indexing run failed")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())

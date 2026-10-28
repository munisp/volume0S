"""Exact minor-unit calculations over immutable billing document rows."""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable


def ar_aging_rows(tenant_id: str, as_of: date, rows: Iterable[Any]) -> dict[str, Any]:
    names = ("current", "days_1_30", "days_31_60", "days_61_90", "days_over_90")
    totals: dict[str, dict[str, Any]] = {}
    invoices = []
    for row in rows:
        total = int(row["total_minor"])
        paid = int(row["paid_minor"])
        if total < 0 or paid < 0 or paid > total:
            raise ValueError("invoice and posted receipt amounts are inconsistent")
        outstanding = total - paid
        if outstanding == 0:
            continue
        # Prepaid invoices are due on issue; age is therefore days past due.
        due_date = row["issue_date"]
        age = (as_of - due_date).days
        bucket = "current" if age == 0 else "days_1_30" if age <= 30 else "days_31_60" if age <= 60 else "days_61_90" if age <= 90 else "days_over_90"
        currency = row["currency"]
        report = totals.setdefault(currency, {**{name: 0 for name in names}, "outstanding_minor": 0})
        report[bucket] += outstanding
        report["outstanding_minor"] += outstanding
        invoices.append({
            "invoice_id": row["id"], "document_number": row["document_number"],
            "issue_date": due_date.isoformat(), "due_date": due_date.isoformat(), "age_days": age,
            "currency": currency, "total_minor": str(total), "paid_minor": str(paid),
            "outstanding_minor": str(outstanding), "bucket": bucket,
        })
    return {
        "schema": "volumeos.ar-aging.v1", "tenant_id": tenant_id,
        "as_of": as_of.isoformat(), "basis": "prepaid_due_on_issue",
        "totals": {
            currency: {key: str(value) for key, value in values.items()}
            for currency, values in sorted(totals.items())
        },
        "invoices": invoices,
    }


def partner_share_rows(
    tenant_id: str, partner_id: str, start: date, end: date, rows: Iterable[Any],
) -> dict[str, Any]:
    entries = []
    totals: dict[str, dict[str, int]] = {}
    for row in rows:
        amount = int(row["amount_minor"])
        basis_points = int(row["basis_points"])
        if amount < 0 or not 0 <= basis_points <= 10_000:
            raise ValueError("posted receipt or partner terms are invalid")
        share = amount * basis_points // 10_000
        currency = row["currency"]
        total = totals.setdefault(currency, {"receipt_minor": 0, "partner_share_minor": 0})
        total["receipt_minor"] += amount
        total["partner_share_minor"] += share
        entries.append({
            "receipt_id": row["id"], "document_number": row["document_number"],
            "offer": row["offer"], "contract_id": row["contract_id"],
            "ledger_transfer_id": str(row["ledger_transfer_id"]),
            "posted_at": row["created_at"].isoformat(), "currency": currency,
            "receipt_minor": str(amount), "basis_points": basis_points,
            "partner_share_minor": str(share),
        })
    return {
        "schema": "volumeos.partner-share.v1", "tenant_id": tenant_id,
        "partner_id": partner_id, "period_start": start.isoformat(), "period_end": end.isoformat(),
        "receipt_count": len(entries),
        "totals": {
            currency: {key: str(value) for key, value in values.items()}
            for currency, values in sorted(totals.items())
        },
        "receipts": entries,
    }

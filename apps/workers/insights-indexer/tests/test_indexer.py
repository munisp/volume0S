from datetime import date, datetime, timezone

from indexer import build_documents

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
D = lambda d: datetime.fromisoformat(d).replace(tzinfo=timezone.utc)


def rows():
    return {
        "topups": [{"event_id": "collection:a", "provider": "paystack", "reference": "r1", "amount_minor": 500000, "created_at": D("2026-11-02T10:00")},
                   {"event_id": "collection:b", "provider": "paystack", "reference": "r2", "amount_minor": 200000, "created_at": D("2026-10-30T10:00")}],
        "invoices": [{"id": "invoice:1", "document_number": "INV-1", "cycle": "2026-11", "total_minor": 400000, "issue_date": date(2026, 11, 10)}],
        "receipts": [{"id": "receipt:1", "document_number": "RCT-1", "amount_minor": 400000, "created_at": D("2026-11-10T12:00")}],
        "dispatches": [{"id": "dispatch:1", "campaign_id": "winback", "created_at": D("2026-11-11T09:00"), "emails": 2, "whatsapp": 1, "social": 2, "amount_minor": 10000}],
        "contacts": [{"month": "2026-11", "n": 3}],
    }


def test_monthly_reports_add_up_per_month_and_carry_the_business():
    packs = [{"pack_id": "p1", "images": 5, "delivered_at": D("2026-11-11T13:00")}]
    reports, logs = build_documents("acme", "Acme Ltd", rows(), packs, NOW)
    by_month = {body["month"]: body for _, body in reports}
    nov, oct_ = by_month["2026-11"]["metrics"], by_month["2026-10"]["metrics"]
    assert nov["paid_in_minor"] == 500000 and oct_["paid_in_minor"] == 200000 and nov["topups"] == 1 and oct_["topups"] == 1
    assert nov["charged_minor"] == 410000 and nov["campaign_spend_minor"] == 10000 and nov["invoices"] == 1 and nov["receipts"] == 1
    assert (nov["emails"], nov["whatsapp"], nov["social_posts"], nov["campaigns_sent"]) == (2, 1, 2, 1)
    assert (nov["studio_packs"], nov["studio_images"], nov["contacts_added"]) == (1, 5, 3)
    assert by_month["2026-11"]["event_date"] == "2026-11-01" and by_month["2026-11"]["title"] == "Acme Ltd — November 2026"
    assert "3 people" in by_month["2026-11"]["summary"]
    assert all(body["tenant_id"] == "acme" for _, body in reports + logs)


def test_events_have_stable_ids_and_plain_messages():
    reports, logs = build_documents("acme", "Acme Ltd", rows(), [], NOW)
    again, logs_again = build_documents("acme", "Acme Ltd", rows(), [], NOW)
    assert [i for i, _ in logs] == [i for i, _ in logs_again]  # reruns overwrite, never duplicate
    messages = {body["kind"]: body["message"] for _, body in logs}
    assert messages["topup"].startswith("Top-up of ₦") and messages["invoice"] == "Invoice INV-1 issued for ₦4,000.00 (cycle 2026-11)"
    assert messages["campaign"] == "Campaign winback sent: 2 emails, 1 WhatsApp messages, 2 social posts (₦100.00 charged)"
    assert len({i for i, _ in logs}) == len(logs)


def test_a_business_with_no_activity_gets_no_reports():
    assert build_documents("quiet", "Quiet Ltd", {}, [], NOW) == ([], [])

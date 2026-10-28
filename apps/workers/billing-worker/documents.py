"""E-invoice-ready structured documents and deterministic PDF rendering.

The field map prepares VolumeOS documents for a later certified FIRS/NRS
submission adapter; it does not claim that local PDF generation is certification.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from models import BillingCycleInput, ReservationResult, deterministic_id


@dataclass(frozen=True)
class InvoiceDraft:
    invoice_id: str
    tenant_id: str
    subscription_id: str
    cycle: str
    issue_date: str
    currency: str
    seller: dict[str, str]
    buyer: dict[str, str]
    lines: tuple[dict[str, Any], ...]
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    invoice_number: str = ""


@dataclass(frozen=True)
class ReceiptDraft:
    receipt_id: str
    invoice_id: str
    tenant_id: str
    transfer_id: str
    amount_minor: int
    currency: str
    receipt_number: str = ""


def invoice_draft(value: BillingCycleInput) -> InvoiceDraft:
    value.validate()
    lines = [
        {
            "code": f"OFFER-{value.offer}-RETAINER",
            "description": f"Offer {value.offer} prepaid retainer for {value.cycle}",
            "quantity": 1,
            "amount_minor": str(value.retainer_minor),
        }
    ]
    if value.setup_minor:
        lines.append({
            "code": "SETUP",
            "description": "Service setup",
            "quantity": 1,
            "amount_minor": str(value.setup_minor),
        })
    if value.usage_minor:
        lines.append({
            "code": "USAGE-ESTIMATE",
            "description": "Billed usage overage",
            "quantity": 1,
            "amount_minor": str(value.usage_minor),
        })
    return InvoiceDraft(
        invoice_id=deterministic_id("invoice", value.tenant_id, value.subscription_id, value.cycle),
        tenant_id=value.tenant_id,
        subscription_id=value.subscription_id,
        cycle=value.cycle,
        issue_date=value.issue_date,
        currency=value.currency,
        seller={"legal_name": value.seller.legal_name, "tin": value.seller.tin, "address": value.seller.address},
        buyer={"legal_name": value.buyer.legal_name, "tin": value.buyer.tin, "address": value.buyer.address},
        lines=tuple(lines),
        subtotal_minor=value.subtotal_minor,
        tax_minor=value.tax_minor,
        total_minor=value.total_minor,
    )


def receipt_draft(invoice: InvoiceDraft, reservation: ReservationResult) -> ReceiptDraft:
    if reservation.state != "posted" or reservation.tenant_id != invoice.tenant_id:
        raise ValueError("a receipt requires a posted same-tenant reservation")
    if int(reservation.amount_minor) != invoice.total_minor:
        raise ValueError("receipt amount must equal the invoice total")
    return ReceiptDraft(
        receipt_id=deterministic_id("receipt", invoice.invoice_id, reservation.transfer_id),
        invoice_id=invoice.invoice_id,
        tenant_id=invoice.tenant_id,
        transfer_id=reservation.transfer_id,
        amount_minor=invoice.total_minor,
        currency=invoice.currency,
    )


def invoice_fields(invoice: InvoiceDraft) -> dict[str, Any]:
    return {
        "profile": "volumeos-firs-nrs-phase-map-v1",
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "issue_date": invoice.issue_date,
        "billing_cycle": invoice.cycle,
        "currency": invoice.currency,
        "seller": invoice.seller,
        "buyer": invoice.buyer,
        "lines": list(invoice.lines),
        "subtotal_minor": str(invoice.subtotal_minor),
        "tax_minor": str(invoice.tax_minor),
        "total_minor": str(invoice.total_minor),
        "payment_means": "prepaid_tigerbeetle",
    }


def receipt_fields(receipt: ReceiptDraft) -> dict[str, Any]:
    return {
        "receipt_id": receipt.receipt_id,
        "receipt_number": receipt.receipt_number,
        "invoice_id": receipt.invoice_id,
        "ledger_transfer_id": receipt.transfer_id,
        "amount_minor": str(receipt.amount_minor),
        "currency": receipt.currency,
        "status": "paid_posted",
    }


def with_numbers(invoice: InvoiceDraft, receipt: ReceiptDraft, invoice_number: str, receipt_number: str) -> tuple[InvoiceDraft, ReceiptDraft]:
    return replace(invoice, invoice_number=invoice_number), replace(receipt, receipt_number=receipt_number)


def render_invoice_pdf(invoice: InvoiceDraft) -> bytes:
    lines = [
        "VolumeOS tax invoice",
        f"Invoice: {invoice.invoice_number}",
        f"Issue date: {invoice.issue_date}  Cycle: {invoice.cycle}",
        f"Seller: {invoice.seller['legal_name']} (TIN {invoice.seller['tin']})",
        f"Buyer: {invoice.buyer['legal_name']} (TIN {invoice.buyer['tin']})",
        "",
    ]
    lines.extend(f"{line['description']}: {_money(invoice.currency, int(line['amount_minor']))}" for line in invoice.lines)
    lines.extend([
        "",
        f"Subtotal: {_money(invoice.currency, invoice.subtotal_minor)}",
        f"Tax: {_money(invoice.currency, invoice.tax_minor)}",
        f"Total paid from prepaid balance: {_money(invoice.currency, invoice.total_minor)}",
        f"Structured profile: {invoice_fields(invoice)['profile']}",
    ])
    return _pdf(lines)


def render_receipt_pdf(receipt: ReceiptDraft) -> bytes:
    return _pdf([
        "VolumeOS payment receipt",
        f"Receipt: {receipt.receipt_number}",
        f"Invoice reference: {receipt.invoice_id}",
        f"Ledger transfer: {receipt.transfer_id}",
        f"Amount: {_money(receipt.currency, receipt.amount_minor)}",
        "Status: posted to TigerBeetle",
    ])


def _money(currency: str, minor: int) -> str:
    major, fraction = divmod(minor, 100)
    return f"{currency} {major:,}.{fraction:02d}"


def _pdf(lines: list[str]) -> bytes:
    output = BytesIO()
    document = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    width, height = A4
    y = height - 64
    document.setTitle(lines[0])
    for index, line in enumerate(lines):
        document.setFont("Helvetica-Bold" if index == 0 else "Helvetica", 16 if index == 0 else 10)
        for chunk in _wrap(line, 94):
            document.drawString(54, y, chunk)
            y -= 18 if index == 0 else 15
        if y < 64:
            document.showPage()
            y = height - 64
    document.save()
    return output.getvalue()


def _wrap(value: str, width: int) -> list[str]:
    if not value:
        return [""]
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines

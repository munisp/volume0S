"""Offer 05: Docling extraction, confidence review, and accounting exports."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
import io
from typing import Any

from common import Artifact, Approval, OfferError, identifier, release_artifact, sha256

EXPORTS = {"zoho", "quickbooks", "sage"}
REQUIRED_FIELDS = ("document_number", "issue_date", "seller_tin", "buyer_tin", "currency", "net_minor", "tax_minor", "total_minor")


@dataclass(frozen=True)
class ExtractedDocument:
    tenant_id: str
    job_id: str
    source_sha256: str
    docling_version: str
    document_type: str
    fields: dict[str, Any]
    confidence: dict[str, float]

    def validated(self) -> "ExtractedDocument":
        identifier(self.tenant_id, "tenant"); identifier(self.job_id, "job"); identifier(self.docling_version, "Docling version")
        if len(self.source_sha256) != 64 or self.document_type not in {"invoice", "receipt", "bank-statement"}:
            raise OfferError("source digest and supported document classification required")
        if set(self.fields) != set(REQUIRED_FIELDS) or set(self.confidence) != set(REQUIRED_FIELDS):
            raise OfferError("extracted fields and confidence scores must exactly match the accounting schema")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1 for value in self.confidence.values()):
            raise OfferError("confidence must be normalized")
        for key in ("net_minor", "tax_minor", "total_minor"):
            if isinstance(self.fields[key], bool) or not isinstance(self.fields[key], int) or self.fields[key] < 0:
                raise OfferError("accounting amounts must be non-negative exact minor units")
        if self.fields["currency"] not in {"NGN", "USD"}:
            raise OfferError("unsupported accounting currency")
        if int(self.fields["net_minor"]) + int(self.fields["tax_minor"]) != int(self.fields["total_minor"]):
            raise OfferError("extracted accounting amounts do not reconcile")
        return self

    def exceptions(self, threshold: float = 0.95) -> list[str]:
        self.validated()
        if not 0 <= threshold <= 1:
            raise OfferError("invalid confidence threshold")
        return sorted(field for field, confidence in self.confidence.items() if confidence < threshold)


def accounting_csv(document: ExtractedDocument, target: str, *, reviewer: str | None = None,
                   review_reason: str | None = None, threshold: float = 0.95) -> Artifact:
    document.validated()
    if target not in EXPORTS:
        raise OfferError("unsupported accounting export target")
    exceptions = document.exceptions(threshold)
    if exceptions and (not reviewer or not review_reason):
        raise OfferError(f"human extraction review required for: {','.join(exceptions)}")
    payload = accounting_csv_bytes(document)
    # Export metadata stays in the object manifest; raw extracted PII is not logged.
    return Artifact(document.tenant_id, "05", document.job_id, 1,
                    f"{document.tenant_id}/offers/05/{document.job_id}/{target}-v1.csv", sha256(payload), "text/csv").validated()


def accounting_csv_bytes(document: ExtractedDocument) -> bytes:
    rows = [["document_number", "issue_date", "seller_tin", "buyer_tin", "currency", "net_minor", "tax_minor", "total_minor"]]
    rows.append([str(document.fields[field]) for field in REQUIRED_FIELDS])
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue().encode()


def release_export(artifact: Artifact, approvals: list[Approval]) -> Artifact:
    if artifact.offer != "05":
        raise OfferError("not an accounting export")
    return release_artifact(artifact, approvals)


class DoclingClient:
    def __init__(self, transport, endpoint: str, service_token: str):
        if not endpoint.startswith("https://") or not service_token:
            raise OfferError("HTTPS Docling endpoint and service credential required")
        self.transport, self.endpoint, self.token = transport, endpoint.rstrip("/"), service_token

    async def parse(self, tenant_id: str, job_id: str, source_key: str, source_sha256: str, version: str) -> ExtractedDocument:
        if not source_key.startswith(f"{tenant_id}/documents/{job_id}/"):
            raise OfferError("source document is outside the tenant/job boundary")
        response = await self.transport.post(self.endpoint + "/v1/parse",
            json={"source_object_key": source_key, "source_sha256": source_sha256, "pipeline_version": version},
            headers={"Authorization": f"Bearer {self.token}", "Idempotency-Key": f"docling:{tenant_id}:{job_id}", "X-VolumeOS-Tenant": tenant_id})
        response.raise_for_status(); document = response.json()
        return ExtractedDocument(tenant_id, job_id, source_sha256, version, document["document_type"],
                                 document["fields"], document["confidence"]).validated()

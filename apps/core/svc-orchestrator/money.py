"""Authenticated server-to-server billing and collection clients.

Browser OIDC claims authorize the tenant action; this client supplies the separate
service credential required by the money APIs. Financial responses are passed
through only after checking their JSON shape and tenant scope.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import httpx


class MoneyServiceError(Exception):
    def __init__(self, message: str, status: int = 503, code: str = "money_service_unavailable"):
        super().__init__(message)
        self.status = status
        self.code = code


class MoneyServices:
    def __init__(self, billing_url: str, collections_url: str, documents_url: str, service_token: str):
        if not billing_url or not collections_url or not documents_url or len(service_token) < 32:
            raise ValueError("money service URLs and a valid service token are required")
        self.billing_url = billing_url.rstrip("/")
        self.collections_url = collections_url.rstrip("/")
        self.documents_url = documents_url.rstrip("/")
        self.service_token = service_token

    @classmethod
    def from_env(cls) -> "MoneyServices":
        return cls(
            os.environ.get("BILLING_SERVICE_URL", ""),
            os.environ.get("COLLECTIONS_SERVICE_URL", ""),
            os.environ.get("DOCUMENT_SERVICE_URL", ""),
            os.environ.get("VOLUMEOS_SERVICE_TOKEN", ""),
        )

    async def balance(self, tenant_id: str, currency: str) -> dict[str, Any]:
        return await self._request(
            self.billing_url, "GET", f"/v1/tenants/{tenant_id}/balance/{currency}", tenant_id,
        )

    async def topup(self, tenant_id: str, provider: str, reference: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._request(
            self.collections_url, "POST", f"/v1/tenants/{tenant_id}/checkouts/{provider}/{reference}",
            tenant_id, payload,
        )
        if result.get("reference") != reference or result.get("provider") != provider:
            raise MoneyServiceError("Collection service changed the checkout scope.", 502, "invalid_money_response")
        if result.get("status") != "awaiting_confirmation" or not isinstance(result.get("checkout_url"), str):
            raise MoneyServiceError("Collection service returned an incomplete checkout.", 502, "invalid_money_response")
        return result

    async def collection_receipt(self, tenant_id: str, provider: str, reference: str) -> dict[str, Any]:
        result = await self._request(
            self.collections_url, "GET",
            f"/v1/tenants/{tenant_id}/collection-expectations/{provider}/{reference}", tenant_id,
        )
        if (result.get("disposition") != "credited" or result.get("tenant_id") != tenant_id
                or result.get("provider") != provider or result.get("reference") != reference):
            raise MoneyServiceError("Collection has not been credited to this tenant.", 409, "collection_not_credited")
        event_id = result.get("event_id")
        if not isinstance(event_id, str) or not event_id.startswith("collection:") or len(event_id) > 128:
            raise MoneyServiceError("Collection receipt has an invalid recovery reference.", 502, "invalid_money_response")
        return result

    async def open_suspense(self) -> dict[str, Any]:
        result = await self._request(
            self.collections_url, "GET", "/v1/tenants/platform/collection-suspense?limit=500", "platform",
        )
        receipts = result.get("receipts")
        if not isinstance(receipts, list):
            raise MoneyServiceError("Collection review queue is invalid.", 502, "invalid_money_response")
        for receipt in receipts:
            self._check_suspense_receipt(receipt)
        return result

    async def resolve_suspense(
        self, provider: str, reference: str, tenant_id: str, approved_by: str, reason: str,
    ) -> dict[str, Any]:
        result = await self._request(
            self.collections_url, "POST",
            f"/v1/tenants/platform/collection-suspense/{provider}/{reference}/resolutions",
            "platform",
            {"tenant_id": tenant_id, "approved_by": approved_by, "reason": reason},
        )
        if (
            result.get("schema") != "volumeos.collection-resolution.v1"
            or result.get("status") != "resolved" or result.get("provider") != provider
            or result.get("reference") != reference or result.get("tenant_id") != tenant_id
            or result.get("approved_by") != approved_by or result.get("reason") != reason
            or not isinstance(result.get("resolution_id"), str)
            or not str(result.get("ledger_transfer_id", "")).isdigit()
        ):
            raise MoneyServiceError("Suspense resolution failed scope validation.", 502, "invalid_money_response")
        return result

    @staticmethod
    def _check_suspense_receipt(receipt: Any) -> None:
        if (
            not isinstance(receipt, dict) or receipt.get("disposition") != "suspense"
            or receipt.get("provider") not in {"paystack", "flutterwave", "moniepoint"}
            or not isinstance(receipt.get("reference"), str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}", receipt["reference"])
            or not isinstance(receipt.get("event_id"), str)
            or not receipt["event_id"].startswith("collection:")
            or not str(receipt.get("amount_minor", "")).isdigit()
            or int(receipt["amount_minor"]) <= 0 or receipt.get("currency") not in {"NGN", "USD"}
            or not str(receipt.get("ledger_transfer_id", "")).isdigit()
        ):
            raise MoneyServiceError("Collection review queue failed validation.", 502, "invalid_money_response")

    async def documents(self, tenant_id: str) -> dict[str, Any]:
        result = await self._request(
            self.documents_url, "GET", f"/v1/tenants/{tenant_id}/documents", tenant_id,
        )
        documents = result.get("documents")
        if not isinstance(documents, list) or any(
            not isinstance(item, dict) or item.get("tenant_id") != tenant_id for item in documents
        ):
            raise MoneyServiceError("Document service returned an invalid tenant feed.", 502, "invalid_money_response")
        return result

    async def ar_aging(self, tenant_id: str, as_of: str) -> dict[str, Any]:
        result = await self._request(
            self.documents_url, "GET", f"/v1/tenants/{tenant_id}/reports/ar-aging?as_of={as_of}", tenant_id,
        )
        self._check_report(result, "volumeos.ar-aging.v1", tenant_id)
        return result

    async def partner_statement(self, tenant_id: str, partner_id: str, start: str, end: str) -> dict[str, Any]:
        result = await self._request(
            self.documents_url, "GET",
            f"/v1/tenants/{tenant_id}/reports/partner-share/{partner_id}?start={start}&end={end}", tenant_id,
        )
        self._check_report(result, "volumeos.partner-share.v1", tenant_id)
        if result.get("partner_id") != partner_id or result.get("period_start") != start or result.get("period_end") != end:
            raise MoneyServiceError("Partner statement changed scope.", 502, "invalid_money_response")
        return result

    async def create_partner_contract(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._request(
            self.documents_url, "POST", f"/v1/tenants/{tenant_id}/partner-contracts", tenant_id, payload,
        )
        if result.get("tenant_id") != tenant_id or result.get("contract_id") != payload["contract_id"] or result.get("partner_id") != payload["partner_id"]:
            raise MoneyServiceError("Partner contract changed scope.", 502, "invalid_money_response")
        return result

    async def partner_contracts(self, tenant_id: str, partner_id: str | None) -> dict[str, Any]:
        suffix = f"?partner_id={partner_id}" if partner_id else ""
        result = await self._request(
            self.documents_url, "GET", f"/v1/tenants/{tenant_id}/partner-contracts{suffix}", tenant_id,
        )
        contracts = result.get("contracts")
        if not isinstance(contracts, list) or any(not isinstance(row, dict) or row.get("tenant_id") != tenant_id for row in contracts):
            raise MoneyServiceError("Partner contract feed failed validation.", 502, "invalid_money_response")
        return result

    async def trial_balance(self, currency: str) -> dict[str, Any]:
        result = await self._request(
            self.documents_url, "GET", f"/v1/tenants/platform/reports/trial-balance/{currency}", "platform",
        )
        self._check_report(result, "volumeos.trial-balance.v1", "platform")
        if (result.get("currency") != currency or result.get("balanced") is not True
                or result.get("total_debits_posted") != result.get("total_credits_posted")):
            raise MoneyServiceError("Trial balance failed validation.", 502, "invalid_money_response")
        return result

    async def export_ar_aging(self, tenant_id: str, as_of: str) -> dict[str, Any]:
        result = await self._request(
            self.documents_url, "POST", f"/v1/tenants/{tenant_id}/reports/ar-aging/exports", tenant_id, {"as_of": as_of},
        )
        return self._check_export(result, "volumeos.ar-aging.v1", tenant_id, "ar-aging")

    async def export_partner_statement(self, tenant_id: str, partner_id: str, start: str, end: str) -> dict[str, Any]:
        result = await self._request(
            self.documents_url, "POST", f"/v1/tenants/{tenant_id}/reports/partner-share/{partner_id}/exports",
            tenant_id, {"start": start, "end": end},
        )
        checked = self._check_export(result, "volumeos.partner-share.v1", tenant_id, "partner-share")
        if result["report"].get("partner_id") != partner_id:
            raise MoneyServiceError("Partner export changed scope.", 502, "invalid_money_response")
        return checked

    async def export_trial_balance(self, currency: str) -> dict[str, Any]:
        result = await self._request(
            self.documents_url, "POST", f"/v1/tenants/platform/reports/trial-balance/{currency}/exports", "platform",
        )
        checked = self._check_export(result, "volumeos.trial-balance.v1", "platform", "trial-balance")
        if result["report"].get("currency") != currency:
            raise MoneyServiceError("Trial-balance export changed currency.", 502, "invalid_money_response")
        return checked

    def _check_report(self, report: Any, schema: str, tenant_id: str) -> None:
        if not isinstance(report, dict) or report.get("schema") != schema:
            raise MoneyServiceError("Financial report failed schema validation.", 502, "invalid_money_response")
        if schema != "volumeos.trial-balance.v1" and report.get("tenant_id") != tenant_id:
            raise MoneyServiceError("Financial report changed tenant scope.", 502, "invalid_money_response")

    def _check_export(self, result: dict[str, Any], schema: str, tenant_id: str, kind: str) -> dict[str, Any]:
        report = result.get("report")
        metadata = result.get("export")
        self._check_report(report, schema, tenant_id)
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        expected_key = f"{tenant_id}/financial-exports/{kind}/{digest}.json"
        if (
            not isinstance(metadata, dict) or metadata.get("schema") != "volumeos.financial-export.v1"
            or metadata.get("sha256") != digest or metadata.get("object_key") != expected_key
            or metadata.get("object_lock") != "COMPLIANCE" or metadata.get("immutable") is not True
            or not isinstance(metadata.get("retained_until"), str) or not isinstance(metadata.get("created"), bool)
        ):
            raise MoneyServiceError("Financial export failed integrity validation.", 502, "invalid_money_response")
        return result

    async def financial_export(self, tenant_id: str, kind: str, digest: str) -> tuple[bytes, str]:
        path = f"/v1/tenants/{tenant_id}/financial-exports/{kind}/{digest}"
        headers = {
            "Authorization": f"Bearer {self.service_token}", "X-VolumeOS-Tenant": tenant_id,
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.get(self.documents_url + path, headers=headers)
        except httpx.HTTPError as exc:
            raise MoneyServiceError("Financial export storage is unavailable.") from exc
        if response.is_error:
            raise MoneyServiceError("Financial export was not found or failed retention checks.", 404, "not_found")
        if (
            response.headers.get("Content-Type", "").split(";", 1)[0] != "application/json"
            or len(response.content) > 10 * 1024 * 1024
            or response.headers.get("X-Object-Lock-Mode") != "COMPLIANCE"
            or not response.headers.get("X-Object-Lock-Retain-Until")
            or hashlib.sha256(response.content).hexdigest() != digest
        ):
            raise MoneyServiceError("Financial export failed download validation.", 502, "invalid_money_response")
        return response.content, digest

    async def document_pdf(self, tenant_id: str, kind: str, document_id: str) -> tuple[bytes, str]:
        path = f"/v1/tenants/{tenant_id}/documents/{kind}/{document_id}"
        metadata = await self._request(self.documents_url, "GET", path, tenant_id)
        expected_hash = metadata.get("pdf_sha256")
        number = metadata.get("document_number")
        if (
            metadata.get("tenant_id") != tenant_id or metadata.get("document_id") != document_id
            or metadata.get("kind") != kind or not isinstance(expected_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            or not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", number)
        ):
            raise MoneyServiceError("Document metadata failed validation.", 502, "invalid_money_response")
        headers = {
            "Authorization": f"Bearer {self.service_token}",
            "X-VolumeOS-Tenant": tenant_id, "Accept": "application/pdf",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.get(self.documents_url + path + "/pdf", headers=headers)
        except httpx.HTTPError as exc:
            raise MoneyServiceError("Document service is unavailable.") from exc
        if response.is_error:
            raise MoneyServiceError("Document PDF was not found in this tenant.", 404, "not_found")
        if (
            response.headers.get("Content-Type", "").split(";", 1)[0] != "application/pdf"
            or len(response.content) > 10 * 1024 * 1024 or not response.content.startswith(b"%PDF")
            or hashlib.sha256(response.content).hexdigest() != expected_hash
        ):
            raise MoneyServiceError("Document PDF failed integrity validation.", 502, "invalid_money_response")
        return response.content, number

    async def _request(self, base: str, method: str, path: str, tenant_id: str,
                       payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.service_token}",
            "X-VolumeOS-Tenant": tenant_id,
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.request(method, base + path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise MoneyServiceError("Money service is unavailable.") from exc
        try:
            result = response.json()
        except ValueError as exc:
            raise MoneyServiceError("Money service returned invalid JSON.", 502, "invalid_money_response") from exc
        if not isinstance(result, dict):
            raise MoneyServiceError("Money service returned a non-object response.", 502, "invalid_money_response")
        if response.is_error:
            detail = result.get("error") if isinstance(result.get("error"), dict) else {}
            # Preserve client-safe financial states without exposing transport details.
            code = detail.get("code") if isinstance(detail.get("code"), str) else "money_service_error"
            message = detail.get("message") if isinstance(detail.get("message"), str) else "Money service request failed."
            status = response.status_code if response.status_code in {400, 401, 402, 403, 404, 409, 422} else 502
            raise MoneyServiceError(message, status, code)
        return result

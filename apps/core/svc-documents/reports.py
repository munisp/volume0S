"""Financial report ingress validation and retention-locked immutable exports.

TigerBeetle remains the balance store. PostgreSQL is used only for document
metadata; immutable report bytes live in object storage, never in SQL rows.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import httpx
from botocore.exceptions import ClientError

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXPORT_KINDS = {"trial-balance", "ar-aging", "partner-share"}
MAX_REPORT_BYTES = 10 * 1024 * 1024


class ReportError(Exception):
    """Client-safe report or export failure."""


def validate_export_scope(tenant_id: str, kind: str, digest: str | None = None) -> None:
    if not SAFE_ID.fullmatch(tenant_id) or kind not in EXPORT_KINDS:
        raise ValueError("valid tenant and financial report kind are required")
    if digest is not None and not SHA256.fullmatch(digest):
        raise ValueError("export digest must be a lowercase SHA-256 hash")


def canonical_report_bytes(report: dict[str, Any]) -> bytes:
    value = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(value) > MAX_REPORT_BYTES:
        raise ReportError("Financial report exceeds the export size limit.")
    return value


class BillingReports:
    def __init__(self, billing_url: str, service_token: str, http: httpx.AsyncClient | None = None):
        if not billing_url or len(service_token) < 32:
            raise ValueError("billing URL and a valid service token are required")
        self.billing_url = billing_url.rstrip("/")
        self.service_token = service_token
        self.http = http

    @classmethod
    def from_env(cls, service_token: str) -> "BillingReports":
        return cls(os.environ.get("BILLING_SERVICE_URL", ""), service_token)

    async def trial_balance(self, currency: str) -> dict[str, Any]:
        if currency not in {"NGN", "USD"}:
            raise ValueError("currency must be NGN or USD")
        headers = {
            "Authorization": f"Bearer {self.service_token}",
            "X-VolumeOS-Tenant": "platform",
            "Accept": "application/json",
        }
        try:
            if self.http is None:
                async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                    response = await client.get(
                        f"{self.billing_url}/v1/tenants/platform/reports/trial-balance/{currency}",
                        headers=headers,
                    )
            else:
                response = await self.http.get(
                    f"{self.billing_url}/v1/tenants/platform/reports/trial-balance/{currency}",
                    headers=headers,
                )
        except httpx.HTTPError as error:
            raise ReportError("TigerBeetle reporting is unavailable.") from error
        if len(response.content) > MAX_REPORT_BYTES:
            raise ReportError("TigerBeetle report exceeds the size limit.")
        if response.is_error:
            raise ReportError("TigerBeetle could not produce a balanced trial balance.")
        try:
            report = response.json()
        except ValueError as error:
            raise ReportError("TigerBeetle returned an invalid report.") from error
        validate_trial_balance(report, currency)
        return report


def _unsigned(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"0|[1-9][0-9]*", value))


def _signed(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value))


def validate_trial_balance(report: Any, currency: str) -> None:
    if not isinstance(report, dict):
        raise ReportError("TigerBeetle report must be a JSON object.")
    accounts = report.get("accounts")
    revenue = report.get("revenue_per_offer")
    if (
        report.get("schema") != "volumeos.trial-balance.v1"
        or report.get("currency") != currency
        or report.get("balanced") is not True
        or not isinstance(accounts, list)
        or len(accounts) > 100_000
        or report.get("account_count") != len(accounts)
        or not isinstance(revenue, list)
        or not _unsigned(report.get("total_debits_posted"))
        or not _unsigned(report.get("total_credits_posted"))
        or report["total_debits_posted"] != report["total_credits_posted"]
    ):
        raise ReportError("TigerBeetle report failed trial-balance validation.")
    debit_fields = ("debits_pending", "debits_posted", "credits_pending", "credits_posted")
    for account in accounts:
        if (
            not isinstance(account, dict)
            or not _unsigned(account.get("account_id"))
            or not isinstance(account.get("account_class"), str)
            or isinstance(account.get("code"), bool)
            or not isinstance(account.get("code"), int)
            or not 0 <= account["code"] <= 65535
            or any(not _unsigned(account.get(field)) for field in debit_fields)
        ):
            raise ReportError("TigerBeetle report contains an invalid account row.")
    for row in revenue:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("offer"), str)
            or not re.fullmatch(r"0[1-8]", row["offer"])
            or not _unsigned(row.get("debits_posted"))
            or not _unsigned(row.get("credits_posted"))
            or not _signed(row.get("net_credit_minor"))
        ):
            raise ReportError("TigerBeetle report contains an invalid offer row.")


class FinancialExports:
    """Content-addressed S3 exports with compliance-mode retention."""

    def __init__(
        self,
        bucket: str,
        client: Any,
        retention_days: int = 2555,
        now: Any = None,
    ):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise ValueError("a valid financial export bucket is required")
        if not 2555 <= retention_days <= 36_500:
            raise ValueError("financial export retention must be between 7 and 100 years")
        self.bucket = bucket
        self.client = client
        self.retention_days = retention_days
        self.now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_env(cls) -> "FinancialExports":
        bucket = os.environ.get("FINANCIAL_EXPORT_BUCKET", "")
        retention = int(os.environ.get("FINANCIAL_EXPORT_RETENTION_DAYS", "2555"))
        client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"))
        return cls(bucket, client, retention)

    def _key(self, tenant_id: str, kind: str, digest: str) -> str:
        return f"{tenant_id}/financial-exports/{kind}/{digest}.json"

    def _check_retention(self, metadata: dict[str, Any]) -> datetime:
        if metadata.get("ObjectLockMode") != "COMPLIANCE":
            raise ReportError("Financial export is not protected by compliance-mode retention.")
        retained = metadata.get("ObjectLockRetainUntilDate")
        if not isinstance(retained, datetime):
            raise ReportError("Financial export has no verifiable retention deadline.")
        if retained.tzinfo is None:
            retained = retained.replace(tzinfo=timezone.utc)
        if retained <= self.now():
            raise ReportError("Financial export retention has expired.")
        return retained

    def put(self, tenant_id: str, kind: str, report: dict[str, Any]) -> dict[str, Any]:
        validate_export_scope(tenant_id, kind)
        content = canonical_report_bytes(report)
        digest = hashlib.sha256(content).hexdigest()
        key = self._key(tenant_id, kind, digest)
        try:
            try:
                metadata = self.client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
                    raise
                metadata = None
            if metadata is not None:
                retained = self._check_retention(metadata)
                existing = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read(MAX_REPORT_BYTES + 1)
                if existing != content:
                    raise ReportError("Financial export content-address invariant failed.")
                return self._metadata(key, digest, retained, False)
            retained = self.now() + timedelta(days=self.retention_days)
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType="application/json",
                ObjectLockMode="COMPLIANCE",
                ObjectLockRetainUntilDate=retained,
            )
        except (ClientError, BotoCoreError) as error:
            raise ReportError("Retention-locked financial export storage is unavailable.") from error
        return self._metadata(key, digest, retained, True)

    def get(self, tenant_id: str, kind: str, digest: str) -> tuple[bytes, dict[str, Any]]:
        validate_export_scope(tenant_id, kind, digest)
        key = self._key(tenant_id, kind, digest)
        try:
            metadata = self.client.head_object(Bucket=self.bucket, Key=key)
            retained = self._check_retention(metadata)
            content = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read(MAX_REPORT_BYTES + 1)
        except (ClientError, BotoCoreError) as error:
            raise ReportError("Financial export was not found or is unavailable.") from error
        if len(content) > MAX_REPORT_BYTES or hashlib.sha256(content).hexdigest() != digest:
            raise ReportError("Financial export failed integrity validation.")
        return content, self._metadata(key, digest, retained, False)

    def _metadata(self, key: str, digest: str, retained: datetime, created: bool) -> dict[str, Any]:
        return {
            "schema": "volumeos.financial-export.v1",
            "object_key": key,
            "sha256": digest,
            "retained_until": retained.astimezone(timezone.utc).isoformat(),
            "object_lock": "COMPLIANCE",
            "immutable": True,
            "created": created,
        }


# Imported after the class definitions to keep test doubles lightweight.
from botocore.exceptions import BotoCoreError  # noqa: E402

"""Offer 07: registry evidence, renewal calendar, and reviewed paperwork."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from common import Artifact, Approval, OfferError, canonical, identifier, release_artifact, sha256


@dataclass(frozen=True)
class Licence:
    tenant_id: str
    licence_id: str
    registry: str
    registry_reference: str
    expires_on: str
    portal_url: str

    def validated(self) -> "Licence":
        identifier(self.tenant_id, "tenant"); identifier(self.licence_id, "licence"); identifier(self.registry, "registry")
        identifier(self.registry_reference, "registry reference")
        try:
            date.fromisoformat(self.expires_on)
        except ValueError as exc:
            raise OfferError("licence expiry must be ISO yyyy-mm-dd") from exc
        parsed = urlparse(self.portal_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise OfferError("registry portal must be an HTTPS URL without embedded credentials")
        return self


def renewal_calendar(licence: Licence, today: date, lead_days: tuple[int, ...] = (90, 60, 30, 14, 7, 1)) -> list[dict[str, str]]:
    licence.validated(); expiry = date.fromisoformat(licence.expires_on)
    if expiry < today:
        return [{"status": "expired", "action": "immediate-human-review", "due_on": today.isoformat()}]
    remaining = (expiry - today).days
    due = [days for days in lead_days if remaining <= days]
    if not due:
        return []
    days = min(due)
    return [{"status": "renewal-due", "action": "prepare-reviewed-paperwork", "due_on": today.isoformat(),
             "expires_on": expiry.isoformat(), "lead_days": str(days)}]


@dataclass(frozen=True)
class PortalCheck:
    tenant_id: str
    licence_id: str
    checked_at: str
    registry_status: str
    evidence_sha256: str
    screenshot_object_key: str

    def validated(self) -> "PortalCheck":
        identifier(self.tenant_id, "tenant"); identifier(self.licence_id, "licence")
        datetime.fromisoformat(self.checked_at.replace("Z", "+00:00"))
        if self.registry_status not in {"active", "renewal-open", "expired", "not-found", "manual-review"} \
                or len(self.evidence_sha256) != 64 or not self.screenshot_object_key.startswith(f"{self.tenant_id}/registry/{self.licence_id}/"):
            raise OfferError("registry check lacks a supported status or tenant-scoped evidence")
        return self


def paperwork(licence: Licence, check: PortalCheck, values: dict[str, str], version: int = 1) -> Artifact:
    licence.validated(); check.validated()
    if (licence.tenant_id, licence.licence_id) != (check.tenant_id, check.licence_id):
        raise OfferError("portal evidence belongs to another licence")
    allowed = {"business_name", "registration_number", "tin", "registered_address", "renewal_period"}
    if not values.get("business_name") or set(values) - allowed or any(len(value) > 1000 for value in values.values()):
        raise OfferError("paperwork fields are missing or unsupported")
    payload = paperwork_payload(licence, check, values)
    return Artifact(licence.tenant_id, "07", licence.licence_id, version,
                    f"{licence.tenant_id}/offers/07/{licence.licence_id}/paperwork-v{version}.json",
                    sha256(payload), "application/json").validated()


def paperwork_payload(licence: Licence, check: PortalCheck, values: dict[str, str]) -> bytes:
    document = {"registry": licence.registry, "registry_reference": licence.registry_reference,
                "portal_check_sha256": check.evidence_sha256, "values": values}
    return canonical(document).encode()


def release_paperwork(artifact: Artifact, approvals: list[Approval]) -> Artifact:
    if artifact.offer != "07":
        raise OfferError("not compliance paperwork")
    return release_artifact(artifact, approvals)


class BrowserAgentClient:
    """Allowlisted browser-worker adapter; credentials are secret references, never page/log content."""
    def __init__(self, transport, endpoint: str, service_token: str, allowed_registries: set[str]):
        if not endpoint.startswith("https://") or not service_token or not allowed_registries:
            raise OfferError("browser worker, credential, and registry allowlist required")
        self.transport, self.endpoint, self.token, self.allowed = transport, endpoint.rstrip("/"), service_token, allowed_registries

    async def check(self, licence: Licence, evidence_key: str) -> PortalCheck:
        licence.validated()
        if licence.registry not in self.allowed:
            raise OfferError("registry is not allowlisted for browser automation")
        response = await self.transport.post(self.endpoint + "/v1/registry-checks",
            json={"registry": licence.registry, "portal_url": licence.portal_url, "registry_reference": licence.registry_reference,
                  "evidence_object_key": evidence_key},
            headers={"Authorization": f"Bearer {self.token}", "Idempotency-Key": f"registry:{licence.tenant_id}:{licence.licence_id}"})
        response.raise_for_status(); document = response.json()
        return PortalCheck(licence.tenant_id, licence.licence_id, document["checked_at"], document["registry_status"],
                           document["evidence_sha256"], evidence_key).validated()

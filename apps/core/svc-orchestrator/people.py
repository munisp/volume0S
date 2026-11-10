"""People (sign-ins) of one business, managed through svc-provisioning, which holds the Keycloak admin credentials.

The orchestrator decides who may do what (super operators only; only platform operators grant super-operator);
svc-provisioning only ever touches people whose tenant_id is the business in the request.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

ROLES = ("super-operator", "delivery", "sales", "analyst", "finance", "auditor", "partner", "client-owner")


class PeopleServiceError(Exception):
    def __init__(self, message: str, status: int = 503, code: str = "people_service_unavailable"):
        super().__init__(message)
        self.status = status
        self.code = code


class PeopleService:
    def __init__(self, base_url: str, service_token: str):
        if not base_url or len(service_token) < 32:
            raise RuntimeError("PROVISIONING_URL and VOLUMEOS_SERVICE_TOKEN are required for people management")
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token

    @classmethod
    def from_env(cls) -> "PeopleService | None":
        url = os.environ.get("PROVISIONING_URL", "")
        return cls(url, os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")) if url else None

    async def list(self, tenant_id: str) -> dict[str, Any]:
        return await self._request("GET", "/v1/people", tenant_id)

    async def create(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v1/people", tenant_id, payload)

    async def update(self, tenant_id: str, person_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PATCH", f"/v1/people/{person_id}", tenant_id, payload)

    async def _request(self, method: str, path: str, tenant_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.service_token}", "X-VolumeOS-Tenant": tenant_id, "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.request(method, self.base_url + path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise PeopleServiceError("People service is unavailable.") from exc
        try:
            result = response.json()
        except ValueError as exc:
            raise PeopleServiceError("People service returned invalid JSON.", 502, "invalid_people_response") from exc
        if response.is_error:
            detail = result.get("error") if isinstance(result, dict) and isinstance(result.get("error"), dict) else {}
            # 503, not 502, for anything else: Cloudflare replaces a 502's body with its own page, losing the message.
            status = response.status_code if response.status_code in {400, 404, 409, 422} else 503
            raise PeopleServiceError(str(detail.get("message") or "People service request failed."), status,
                                     str(detail.get("code") or "people_service_error"))
        if not isinstance(result, dict):
            raise PeopleServiceError("People service returned a non-object response.", 502, "invalid_people_response")
        return result

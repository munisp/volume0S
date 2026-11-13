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
    name = "People service"

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

    async def _request(self, method: str, path: str, tenant_id: str, payload: dict[str, Any] | None = None,
                       params: dict[str, Any] | None = None, actor: str = "") -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.service_token}", "X-VolumeOS-Tenant": tenant_id, "Accept": "application/json"}
        if actor:
            headers["X-VolumeOS-Actor"] = actor  # who is acting, for approvals and the history
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.request(method, self.base_url + path, json=payload, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise PeopleServiceError(f"{self.name} is unavailable.") from exc
        try:
            result = response.json()
        except ValueError as exc:
            raise PeopleServiceError(f"{self.name} returned invalid JSON.", 502, "invalid_people_response") from exc
        if response.is_error:
            detail = result.get("error") if isinstance(result, dict) and isinstance(result.get("error"), dict) else {}
            # 503, not 502, for anything else: Cloudflare replaces a 502's body with its own page, losing the message.
            status = response.status_code if response.status_code in {400, 404, 409, 422} else 503
            if response.status_code == 422 and isinstance(result, dict) and "detail" in result:
                detail = {"message": "Check these fields: " + ", ".join(
                    str(e.get("loc", ["", "?"])[-1]) for e in result["detail"] if isinstance(e, dict)), "code": "invalid_request"}
            raise PeopleServiceError(str(detail.get("message") or f"{self.name} request failed."), status,
                                     str(detail.get("code") or "people_service_error"))
        if not isinstance(result, dict):
            raise PeopleServiceError(f"{self.name} returned a non-object response.", 502, "invalid_people_response")
        return result


class CampaignsService(PeopleService):
    """svc-campaigns: contact lists, campaign drafts and send results for the console."""
    name = "Campaigns service"

    @classmethod
    def from_env(cls) -> "CampaignsService | None":
        url = os.environ.get("CAMPAIGNS_URL", "")
        return cls(url, os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")) if url else None

    async def contacts(self, tenant_id: str, limit: int, offset: int) -> dict[str, Any]:
        return await self._request("GET", "/v1/contacts", tenant_id, params={"limit": limit, "offset": offset})

    async def import_contacts(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v1/contacts/import", tenant_id, payload)

    async def delete_contact(self, tenant_id: str, contact_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v1/contacts/{contact_id}", tenant_id)

    async def social(self, tenant_id: str) -> dict[str, Any]:
        return await self._request("GET", "/v1/social", tenant_id)

    async def connect_social(self, tenant_id: str, api_key: str) -> dict[str, Any]:
        return await self._request("PUT", "/v1/social", tenant_id, {"api_key": api_key})

    async def disconnect_social(self, tenant_id: str) -> dict[str, Any]:
        return await self._request("DELETE", "/v1/social", tenant_id)

    async def campaign(self, tenant_id: str, campaign_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/campaigns/{campaign_id}", tenant_id)


class CreatorsService(PeopleService):
    """svc-creators (Offer 35 Creators Home): channels, video projects and their production stages."""
    name = "Creators Home"

    @classmethod
    def from_env(cls) -> "CreatorsService | None":
        url = os.environ.get("CREATORS_URL", "")
        return cls(url, os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")) if url else None

    async def call(self, method: str, path: str, tenant_id: str, actor: str, payload: dict[str, Any] | None = None,
                   params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request(method, "/v1/creators" + path, tenant_id, payload, params, actor)

    async def raw(self, method: str, path: str, tenant_id: str, actor: str,
                  payload: dict[str, Any] | None = None) -> tuple[bytes, str, str]:
        """Audio from svc-creators (voice previews, narration): the bytes, their type and the download name."""
        headers = {"Authorization": f"Bearer {self.service_token}", "X-VolumeOS-Tenant": tenant_id}
        if actor:
            headers["X-VolumeOS-Actor"] = actor
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                response = await client.request(method, self.base_url + "/v1/creators" + path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise PeopleServiceError(f"{self.name} is unavailable.") from exc
        if response.is_error:
            try:
                detail = response.json().get("error") or {}
            except ValueError:
                detail = {}
            status = response.status_code if response.status_code in {400, 404, 409, 422} else 503
            raise PeopleServiceError(str(detail.get("message") or f"{self.name} request failed."), status,
                                     str(detail.get("code") or "creators_error"))
        return response.content, response.headers.get("content-type", "application/octet-stream"), \
            response.headers.get("content-disposition", "")

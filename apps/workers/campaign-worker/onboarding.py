"""M1 onboarding saga. Every side effect uses a real configured HTTP endpoint.

TigerBeetle account provisioning is an append-only saga step. The saga activates
a tenant only after identity, tenancy, ledger accounts, and required channel
providers succeed.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


@dataclass
class OnboardingInput:
    tenant_id: str
    name: str
    slug: str
    corridor: str
    offers: list[str]
    postiz_integration_id: str | None = None


@dataclass
class OnboardingResult:
    tenant_id: str
    listmonk_list_id: str
    postiz_channel_id: str
    status: str


def validate_onboarding(value: OnboardingInput) -> None:
    if not SAFE_ID.fullmatch(value.tenant_id) or not SAFE_ID.fullmatch(value.corridor):
        raise ApplicationError("valid tenant_id and corridor are required", non_retryable=True)
    if not SLUG.fullmatch(value.slug) or not 2 <= len(value.name.strip()) <= 120:
        raise ApplicationError("valid tenant name and slug are required", non_retryable=True)
    if not value.offers or any(offer not in {"01", "02", "06"} for offer in value.offers):
        raise ApplicationError("M1 onboarding requires offers 01, 02, or 06", non_retryable=True)
    if "02" in value.offers and (
        value.postiz_integration_id is None or not SAFE_ID.fullmatch(value.postiz_integration_id)
    ):
        raise ApplicationError("a connected Postiz integration is required for offer 02", non_retryable=True)


def saga_compensations(completed: list[str]) -> list[str]:
    order = ["identity", "tenancy", "billing", "listmonk", "postiz", "active", "welcome"]
    if any(step not in order for step in completed):
        raise ValueError("unknown saga step")
    # Postiz validation is read-only: the customer-owned OAuth integration must
    # never be deleted by onboarding compensation.
    return [f"undo:{step}" for step in reversed(completed) if step in {"identity", "tenancy", "listmonk"}]


async def request(service_env: str, method: str, path: str, tenant_id: str, key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base = os.environ.get(service_env, "").rstrip("/")
    if not base:
        raise ApplicationError(f"{service_env} is not configured", non_retryable=True)
    headers = {"X-VolumeOS-Tenant": tenant_id, "Idempotency-Key": key}
    token = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    auth = None
    if service_env == "LISTMONK_ADMIN_URL":
        username = os.environ.get("LISTMONK_ADMIN_USER", "")
        password = os.environ.get("LISTMONK_ADMIN_PASSWORD", "")
        if not username or not password:
            raise ApplicationError("ListMonk admin credentials are not configured", non_retryable=True)
        auth = (username, password)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.request(method, base + path, json=payload, headers=headers, auth=auth)
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {}
        body = response.json()
    if not isinstance(body, dict):
        raise ApplicationError(f"{service_env} returned an invalid response")
    return body


async def provision(value: OnboardingInput, step: str, service_env: str, path: str, payload: dict[str, Any], result_key: str) -> str:
    activity.heartbeat()
    result = await request(service_env, "POST", path, value.tenant_id, f"onboarding:{value.tenant_id}:{step}", payload)
    identifier = str(result.get(result_key) or result.get("data", {}).get("id") or "")
    if not identifier:
        raise ApplicationError(f"{step} response is missing {result_key}")
    return identifier


@activity.defn
async def provision_identity(value: OnboardingInput) -> str:
    activity.heartbeat()
    result = await request("IDENTITY_ADMIN_URL", "POST", "/v1/tenants", value.tenant_id,
                           f"onboarding:{value.tenant_id}:identity", {
                               "tenant_id": value.tenant_id, "name": value.name, "corridor": value.corridor,
                           })
    client_id = str(result.get("client_id") or result.get("data", {}).get("id") or "")
    if not client_id:
        raise ApplicationError("identity response is missing client_id")
    # Only a client created by this attempt may be deleted during compensation.
    # An empty resource marker preserves a pre-existing Keycloak client on retry.
    return client_id if result.get("created") is True else ""


@activity.defn
async def create_tenancy(value: OnboardingInput) -> int:
    await provision(value, "tenancy", "TENANCY_URL", "/v1/tenants/current", {
        "name": value.name, "slug": value.slug,
    }, "id")
    result = await request("TENANCY_URL", "POST", "/v1/tenants/current/transitions", value.tenant_id,
                           f"onboarding:{value.tenant_id}:onboarding", {"to": "onboarding", "version": 1, "reason": "M1 onboarding saga"})
    version = int(result.get("version", 0))
    if version != 2:
        raise ApplicationError("tenancy did not enter onboarding at version 2")
    return version


@activity.defn
async def provision_billing_accounts(value: OnboardingInput) -> int:
    """Provision canonical NGN/USD accounts before any service can be activated."""
    activity.heartbeat()
    result = await request(
        "BILLING_SERVICE_URL", "POST", f"/v1/tenants/{value.tenant_id}/accounts", value.tenant_id,
        f"onboarding:{value.tenant_id}:billing",
        {"currencies": ["NGN", "USD"], "offers": value.offers},
    )
    count = result.get("accounts_provisioned")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ApplicationError("billing service did not confirm account provisioning", non_retryable=True)
    return count


@activity.defn
async def initialize_listmonk(value: OnboardingInput) -> str:
    return await provision(value, "listmonk", "LISTMONK_ADMIN_URL", "/api/lists", {
        "name": f"volumeos-{value.tenant_id}", "type": "private", "optin": "double",
    }, "list_id")


@activity.defn
async def initialize_postiz(value: OnboardingInput) -> str:
    # The provisioner validates the customer's existing OAuth connection using a
    # tenant-scoped Dapr secret; it neither creates nor owns that connection.
    return await provision(value, "postiz", "POSTIZ_PROVISIONER_URL", "/v1/tenant-channels", {
        "tenant_id": value.tenant_id, "integration_id": value.postiz_integration_id,
        "offers": value.offers,
    }, "channel_id")


@activity.defn
async def activate_tenancy(value: OnboardingInput, version: int) -> int:
    result = await request("TENANCY_URL", "POST", "/v1/tenants/current/transitions", value.tenant_id,
                           f"onboarding:{value.tenant_id}:active", {"to": "active", "version": version, "reason": "M1 providers initialized"})
    activated_version = int(result.get("version", 0))
    if activated_version != version + 1:
        raise ApplicationError("tenancy activation returned an invalid version")
    return activated_version


@activity.defn
async def send_welcome(value: OnboardingInput) -> str:
    return await provision(value, "welcome", "MESSAGING_URL", "/v1/messages", {
        "template": "tenant_welcome", "corridor": value.corridor, "offers": value.offers,
    }, "message_id")


@activity.defn
async def compensate_onboarding(value: OnboardingInput, compensation: str, resource_id: str) -> None:
    if compensation == "undo:identity" and not resource_id:
        return
    targets = {
        "undo:listmonk": ("LISTMONK_ADMIN_URL", "DELETE", "/api/lists/" + resource_id),
        "undo:identity": ("IDENTITY_ADMIN_URL", "DELETE", "/v1/tenants/" + value.tenant_id),
    }
    if compensation == "undo:tenancy":
        # Offboarding is allowed from prospect/onboarding/active and retains audit history.
        await request("TENANCY_URL", "POST", "/v1/tenants/current/transitions", value.tenant_id,
                      f"onboarding:{value.tenant_id}:compensate", {"to": "offboarded", "version": int(resource_id), "reason": "M1 onboarding compensation"})
        return
    if compensation not in targets:
        raise ApplicationError("unknown compensation", non_retryable=True)
    service_env, method, path = targets[compensation]
    await request(service_env, method, path, value.tenant_id, f"onboarding:{value.tenant_id}:{compensation}")


@workflow.defn
class OnboardingWorkflow:
    @workflow.run
    async def run(self, value: OnboardingInput) -> OnboardingResult:
        validate_onboarding(value)
        options = {"start_to_close_timeout": timedelta(minutes=2), "retry_policy": RetryPolicy(maximum_attempts=3)}
        completed: list[str] = []
        resources = {"identity": value.tenant_id, "tenancy": "1"}
        try:
            resources["identity"] = await workflow.execute_activity(provision_identity, value, **options)
            completed.append("identity")
            version = await workflow.execute_activity(create_tenancy, value, **options)
            resources["tenancy"] = str(version)
            completed.append("tenancy")
            await workflow.execute_activity(provision_billing_accounts, value, **options)
            completed.append("billing")
            if "01" in value.offers:
                resources["listmonk"] = await workflow.execute_activity(initialize_listmonk, value, **options)
                completed.append("listmonk")
            if "02" in value.offers:
                resources["postiz"] = await workflow.execute_activity(initialize_postiz, value, **options)
                completed.append("postiz")
            version = await workflow.execute_activity(activate_tenancy, args=[value, version], **options)
            resources["tenancy"] = str(version)
            completed.append("active")
            await workflow.execute_activity(send_welcome, value, **options)
            completed.append("welcome")
        except BaseException:
            for compensation in saga_compensations(completed):
                step = compensation.removeprefix("undo:")
                await workflow.execute_activity(
                    compensate_onboarding, args=[value, compensation, resources.get(step, value.tenant_id)],
                    start_to_close_timeout=timedelta(minutes=2), retry_policy=RetryPolicy(maximum_attempts=10),
                )
            raise
        return OnboardingResult(
            tenant_id=value.tenant_id, listmonk_list_id=resources.get("listmonk", ""),
            postiz_channel_id=resources.get("postiz", ""), status="active",
        )

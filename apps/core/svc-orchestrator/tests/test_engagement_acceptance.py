"""Opt-in local service acceptance for plan-only engagement workflows.

This exercises real FastAPI routes, RS256/JWKS authentication, a Temporal server,
the production engagement worker/activity, and an HTTP S3-compatible service.
It is local integration evidence, not staging or production acceptance.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import boto3
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import httpx
import jwt
import pytest
from temporalio.api.testservice.v1 import LockTimeSkippingRequest
from temporalio.client import WorkflowFailureError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auth import OIDCAuthenticator
from main import create_app
from workflows import TemporalWorkflows

pytestmark = pytest.mark.skipif(
    os.environ.get("VOLUMEOS_ENGAGEMENT_ACCEPTANCE") != "1",
    reason="opt-in local Temporal/S3 service acceptance",
)


class JWKSHandler(BaseHTTPRequestHandler):
    document = "{}"

    def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        content = self.document.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, _format, *_args):
        return


def identity_service():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="engagement-acceptance", use="sig", alg="RS256")
    JWKSHandler.document = json.dumps({"keys": [jwk]})
    server = ThreadingHTTPServer(("127.0.0.1", 0), JWKSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return key, server, thread


def token(key, tenant: str, actor: str, roles: list[str]) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": "https://identity.engagement-acceptance.local",
            "aud": "volumeos-acceptance",
            "sub": actor,
            "tenant_id": tenant,
            "roles": roles,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        key,
        algorithm="RS256",
        headers={"kid": "engagement-acceptance"},
    )


def engagement_input(engagement_id: str, offer: str = "27") -> dict[str, str]:
    return {
        "engagement_id": engagement_id,
        "offer": offer,
        "title": "Acceptance planning",
        "scope": "Verify the authenticated plan and exact-digest human approval boundary.",
        "requested_outcome": "A reviewable plan with no charge or external delivery.",
    }


async def request(client: TestClient, method: str, path: str, **kwargs):
    return await asyncio.to_thread(client.request, method, path, **kwargs)


async def workflow_status(
    client: TestClient,
    engagement_id: str,
    authorization: str,
    expected: str = "awaiting-approval",
) -> dict[str, Any]:
    deadline = time.monotonic() + 45
    latest = None
    while time.monotonic() < deadline:
        response = await request(
            client, "GET", f"/v1/engagements/{engagement_id}",
            headers={"Authorization": authorization},
        )
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] == expected:
            return latest
        await asyncio.sleep(0.1)
    raise AssertionError(f"engagement did not reach {expected}: {latest}")


async def awaiting(client: TestClient, engagement_id: str, authorization: str) -> dict[str, Any]:
    return await workflow_status(client, engagement_id, authorization)


@pytest.mark.asyncio
async def test_engagement_http_temporal_identity_and_s3_acceptance(tmp_path, monkeypatch):
    try:
        from moto.server import ThreadedMotoServer
    except ImportError:
        pytest.fail("Install moto[server] to run local engagement acceptance")

    worker_root = Path(__file__).parents[3] / "workers" / "offer-worker"
    sys.path.insert(0, str(worker_root))
    from engagements import EngagementWorkflow, build_engagement_plan, plan_store

    s3 = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    s3.start()
    host, port = s3.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    for name, value in {
        "AWS_ACCESS_KEY_ID": "acceptance-only",
        "AWS_SECRET_ACCESS_KEY": "acceptance-only-not-a-production-secret",
        "AWS_DEFAULT_REGION": "us-east-1",
        "S3_ENDPOINT_URL": endpoint,
        "ASSET_BUCKET": "engagement-acceptance",
    }.items():
        monkeypatch.setenv(name, value)
    plan_store.__dict__.pop("instance", None)
    objects = boto3.client("s3", endpoint_url=endpoint)
    objects.create_bucket(Bucket=os.environ["ASSET_BUCKET"])

    key, jwks_server, jwks_thread = identity_service()
    issuer = "https://identity.engagement-acceptance.local"
    authenticator = OIDCAuthenticator(
        issuer=issuer,
        audience="volumeos-acceptance",
        jwks_url=f"http://127.0.0.1:{jwks_server.server_port}/jwks.json",
    )
    operator = "Bearer " + token(key, "tenant-a", "operator-1", ["delivery"])
    other_tenant = "Bearer " + token(key, "tenant-b", "operator-2", ["delivery"])
    sales = "Bearer " + token(key, "tenant-a", "sales-1", ["sales"])

    try:
        environment = await WorkflowEnvironment.start_time_skipping(
            download_dest_dir=os.environ.get(
                "TEMPORAL_TEST_SERVER_DOWNLOAD_DIR", str(tmp_path / "temporal")
            ),
            test_server_existing_path=os.environ.get("TEMPORAL_TEST_SERVER_PATH"),
        )
    except BaseException:
        await authenticator._client.aclose()
        jwks_server.shutdown()
        jwks_server.server_close()
        jwks_thread.join(timeout=5)
        s3.stop()
        sys.path.remove(str(worker_root))
        raise
    try:
        # Prove the shared client before exercising HTTP. The application creates
        # its own client in the TestClient portal's event loop through the same
        # production lifespan path used by the deployed orchestrator.
        await TemporalWorkflows(environment.client).ready()
        # Keep wall-clock waits and HTTP polling from advancing the 30-day
        # approval timer. Expiry runs separately in a fresh time-skipping server.
        await environment.client.test_service.lock_time_skipping(LockTimeSkippingRequest())
        monkeypatch.setenv("TEMPORAL_HOSTPORT", environment.client.service_client.config.target_host)
        monkeypatch.setenv("TEMPORAL_NAMESPACE", environment.client.namespace)
        app = create_app(authenticator=authenticator, qa_assets=object())
        app.state.configure_money = False
        worker = Worker(
            environment.client,
            task_queue="engagements-v1",
            workflows=[EngagementWorkflow],
            activities=[build_engagement_plan],
        )
        async with worker:
            with TestClient(app) as client:
                health = await request(client, "GET", "/readyz")
                assert health.status_code == 200, health.text
                unauthorized = await request(client, "GET", "/v1/engagements")
                assert unauthorized.status_code == 401
                forbidden = await request(
                    client, "POST", "/v1/engagements",
                    headers={"Authorization": sales}, json=engagement_input("forbidden"),
                )
                assert forbidden.status_code == 403

                started = await request(
                    client, "POST", "/v1/engagements",
                    headers={"Authorization": operator}, json=engagement_input("approved-plan"),
                )
                assert started.status_code == 202, started.text
                assert started.json()["delivery_mode"] == "plan-only"
                snapshot = await awaiting(client, "approved-plan", operator)
                plan = snapshot["plan"]
                assert snapshot["tenant_id"] == "tenant-a"
                assert snapshot["commercial_status"] == "source-only-unpriced"
                assert snapshot["charges_enabled"] is False
                assert snapshot["external_actions_enabled"] is False
                assert not ({"amount_minor", "currency", "price", "tiers"} & snapshot["document"].keys())

                stored = await asyncio.to_thread(
                    objects.get_object, Bucket=os.environ["ASSET_BUCKET"], Key=plan["object_key"]
                )
                content = stored["Body"].read()
                assert hashlib.sha256(content).hexdigest() == plan["sha256"]
                assert json.loads(content) == snapshot["document"]
                manifest = await asyncio.to_thread(
                    objects.get_object,
                    Bucket=os.environ["ASSET_BUCKET"], Key=plan["object_key"] + ".manifest.json",
                )
                manifest_document = json.loads(manifest["Body"].read())
                assert manifest_document["tenant_id"] == "tenant-a"
                assert manifest_document["sha256"] == plan["sha256"]

                hidden = await request(
                    client, "GET", "/v1/engagements/approved-plan",
                    headers={"Authorization": other_tenant},
                )
                assert hidden.status_code == 404
                stale = await request(
                    client, "POST", "/v1/engagements/approved-plan/review",
                    headers={"Authorization": operator},
                    json={"decision": "approved", "reason": "Stale check.", "plan_sha256": "0" * 64},
                )
                assert stale.status_code == 409
                assert stale.json()["error"]["code"] == "stale_plan"

                reviewed = await request(
                    client, "POST", "/v1/engagements/approved-plan/review",
                    headers={"Authorization": operator},
                    json={
                        "decision": "approved",
                        "reason": "Plan bytes, controls, and digest verified.",
                        "plan_sha256": plan["sha256"],
                    },
                )
                assert reviewed.status_code == 202, reviewed.text
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    terminal = await request(
                        client, "GET", "/v1/engagements/approved-plan",
                        headers={"Authorization": operator},
                    )
                    if terminal.json()["status"] == "approved":
                        break
                    await asyncio.sleep(0.1)
                terminal = terminal.json()
                assert terminal["status"] == "approved"
                assert terminal["review"]["reviewer"] == "operator-1"
                assert terminal["review"]["plan_sha256"] == plan["sha256"]
                assert terminal["charges_enabled"] is False
                assert terminal["external_actions_enabled"] is False
                repeated = await request(
                    client, "POST", "/v1/engagements/approved-plan/review",
                    headers={"Authorization": operator},
                    json={
                        "decision": "rejected", "reason": "Second decision must fail.",
                        "plan_sha256": plan["sha256"],
                    },
                )
                assert repeated.status_code == 409

                visibility_supported = True
                try:
                    direct_executions = [
                        execution async for execution in environment.client.list_workflows(
                            'WorkflowId STARTS_WITH "engagement:tenant-a:"'
                        )
                    ]
                except RPCError as exc:
                    if exc.status != RPCStatusCode.UNIMPLEMENTED:
                        raise
                    visibility_supported = False
                    warnings.warn(
                        "Temporal Java test server has no workflow visibility API; "
                        "HTTP list assertions require a visibility-enabled environment.",
                        RuntimeWarning,
                    )
                if visibility_supported:
                    assert [execution.id for execution in direct_executions] == [
                        "engagement:tenant-a:approved-plan"
                    ]
                    listed = await request(
                        client, "GET", "/v1/engagements", headers={"Authorization": operator}
                    )
                    assert listed.status_code == 200, listed.text
                    assert [item["engagement_id"] for item in listed.json()["engagements"]] == ["approved-plan"]
                if visibility_supported:
                    other_list = await request(
                        client, "GET", "/v1/engagements", headers={"Authorization": other_tenant}
                    )
                    assert other_list.status_code == 200, other_list.text
                    assert other_list.json()["engagements"] == []

                rejected_start = await request(
                    client, "POST", "/v1/engagements",
                    headers={"Authorization": operator}, json=engagement_input("rejected-plan", "34"),
                )
                assert rejected_start.status_code == 202
                rejected_plan = await awaiting(client, "rejected-plan", operator)
                rejected = await request(
                    client, "POST", "/v1/engagements/rejected-plan/review",
                    headers={"Authorization": operator},
                    json={
                        "decision": "rejected", "reason": "Monitoring scope is not authorized.",
                        "plan_sha256": rejected_plan["plan"]["sha256"],
                    },
                )
                assert rejected.status_code == 202
                rejected_snapshot = await workflow_status(
                    client, "rejected-plan", operator, "rejected"
                )
                assert rejected_snapshot["external_actions_enabled"] is False

                keys = await asyncio.to_thread(
                    lambda: [
                        item["Key"] for item in objects.list_objects_v2(
                            Bucket=os.environ["ASSET_BUCKET"]
                        ).get("Contents", [])
                    ]
                )
                assert len(keys) == 4
                assert all(key.startswith("tenant-a/engagements/") for key in keys)
    finally:
        await environment.shutdown()
        await authenticator._client.aclose()
        jwks_server.shutdown()
        jwks_server.server_close()
        jwks_thread.join(timeout=5)
        s3.stop()
        sys.path.remove(str(worker_root))


@pytest.mark.asyncio
async def test_engagement_real_temporal_approval_expiry(tmp_path, monkeypatch):
    """Advance the real 30-day timer in a fresh server with no HTTP client."""
    try:
        from moto.server import ThreadedMotoServer
    except ImportError:
        pytest.fail("Install moto[server] to run local engagement acceptance")

    worker_root = Path(__file__).parents[3] / "workers" / "offer-worker"
    sys.path.insert(0, str(worker_root))
    from engagements import EngagementWorkflow, build_engagement_plan, plan_store

    s3 = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    s3.start()
    host, port = s3.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    for name, value in {
        "AWS_ACCESS_KEY_ID": "acceptance-only",
        "AWS_SECRET_ACCESS_KEY": "acceptance-only-not-a-production-secret",
        "AWS_DEFAULT_REGION": "us-east-1",
        "S3_ENDPOINT_URL": endpoint,
        "ASSET_BUCKET": "engagement-expiry-acceptance",
    }.items():
        monkeypatch.setenv(name, value)
    plan_store.__dict__.pop("instance", None)
    objects = boto3.client("s3", endpoint_url=endpoint)
    objects.create_bucket(Bucket=os.environ["ASSET_BUCKET"])

    try:
        environment = await WorkflowEnvironment.start_time_skipping(
            download_dest_dir=os.environ.get(
                "TEMPORAL_TEST_SERVER_DOWNLOAD_DIR", str(tmp_path / "temporal")
            ),
            test_server_existing_path=os.environ.get("TEMPORAL_TEST_SERVER_PATH"),
        )
    except BaseException:
        s3.stop()
        sys.path.remove(str(worker_root))
        raise
    try:
        worker = Worker(
            environment.client,
            task_queue="engagements-v1",
            workflows=[EngagementWorkflow],
            activities=[build_engagement_plan],
        )
        async with worker:
            handle = await environment.client.start_workflow(
                EngagementWorkflow,
                {**engagement_input("expiring-plan", "33"), "tenant_id": "tenant-a"},
                id="engagement:tenant-a:expiring-plan",
                task_queue="engagements-v1",
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
            # Awaiting the result unlocks time skipping and fires the workflow's
            # real 30-day approval timer; no wall-clock month is required.
            with pytest.raises(WorkflowFailureError) as failure:
                await handle.result()
            assert isinstance(failure.value.cause, ApplicationError)
            assert "approval expired" in str(failure.value.cause)
            snapshot = await handle.query("status")
            assert snapshot["status"] == "approval-expired"
            assert snapshot["review"] is None
            assert snapshot["charges_enabled"] is False
            assert snapshot["external_actions_enabled"] is False
            stored = await asyncio.to_thread(
                objects.get_object,
                Bucket=os.environ["ASSET_BUCKET"],
                Key=snapshot["plan"]["object_key"],
            )
            assert hashlib.sha256(stored["Body"].read()).hexdigest() == snapshot["plan"]["sha256"]
    finally:
        await environment.shutdown()
        s3.stop()
        sys.path.remove(str(worker_root))

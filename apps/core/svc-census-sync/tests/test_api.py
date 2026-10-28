import base64
import hashlib
import json

from fastapi.testclient import TestClient

from auth import AuthenticationError, Claims
from main import create_app
from sync import InMemoryCensusStore

KEY = b"census-api-masking-key-with-32-bytes!"
EVENT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class Authenticator:
    async def authenticate(self, authorization):
        if authorization != "Bearer valid":
            raise AuthenticationError("invalid")
        return Claims("tenant-a", "agent-1", "device-1", frozenset({"field-agent"}))


class Sink:
    def __init__(self):
        self.calls = []

    async def publish(self, topic, key, document_json):
        self.calls.append((topic, key, document_json))


def client():
    sink = Sink()
    app = create_app(InMemoryCensusStore(), sink, Authenticator(), KEY)
    return TestClient(app), sink


def headers(device="device-1"):
    return {"Authorization": "Bearer valid", "X-VolumeOS-Device": device}


def start_body(content):
    return {
        "upload_id": "upload-api", "event_id": EVENT_ID, "total_chunks": 1,
        "total_bytes": len(content), "payload_sha256": hashlib.sha256(content).hexdigest(),
        "captured_at_unix": 1_760_000_000, "latitude": 6.5244, "longitude": 3.3792,
        "accuracy_m": 8.0, "consent_reference": "consent-api", "lawful_basis": "consent",
    }


def test_api_requires_oidc_and_exact_tenant_device_scope():
    api, _ = client()
    content = json.dumps({"business_name": "Kiosk"}).encode()
    body = start_body(content)
    path = "/v1/tenants/tenant-a/census/uploads"
    assert api.post(path, json=body).status_code == 401
    assert api.post(path, json=body, headers=headers("device-2")).status_code == 403
    assert api.post("/v1/tenants/tenant-b/census/uploads", json=body, headers=headers()).status_code == 403
    response = api.post(path, json=body, headers=headers())
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "uploading"


def test_api_resume_chunk_complete_and_replay_contract():
    api, sink = client()
    content = json.dumps({"business_name": "Kiosk", "phone": "08031234567"}).encode()
    body = start_body(content)
    collection = "/v1/tenants/tenant-a/census/uploads"
    base = collection + "/upload-api"
    assert api.post(collection, json=body, headers=headers()).status_code == 201
    encoded = base64.b64encode(content).decode()
    bad = api.put(base + "/chunks/0", json={"index": 1, "encoded": encoded, "expected_sha256": body["payload_sha256"]}, headers=headers())
    assert bad.status_code == 400
    response = api.put(base + "/chunks/0", json={
        "index": 0, "encoded": encoded, "expected_sha256": body["payload_sha256"],
    }, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["resume"] == []
    assert api.get(base, headers=headers()).json()["resume"] == []
    complete = api.post(base + "/complete", headers=headers())
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "published"
    assert api.post(base + "/complete", headers=headers()).json() == complete.json()
    assert len(sink.calls) == 1
    assert "08031234567" not in sink.calls[0][2]
    assert api.get("/health/ready").json() == {"status": "ready"}
    assert complete.headers["cache-control"] == "no-store"

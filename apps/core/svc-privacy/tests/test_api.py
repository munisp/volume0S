import os
import time

os.environ.setdefault("OIDC_ISSUER", "https://id.example")
os.environ.setdefault("OIDC_AUDIENCE", "volumeos")
os.environ.setdefault("OIDC_JWKS_URL", "https://id.example/jwks")
os.environ.setdefault("SUBJECT_DIGEST_KEY", "s" * 32)
os.environ.setdefault("IDENTITY_PROOF_KEY", "i" * 32)

from fastapi.testclient import TestClient

from auth import AuthenticationError, Claims
from main import app
from privacy import MemoryPrivacyStore, PrivacyService, identity_proof


class FakeOIDC:
    async def authenticate(self, authorization):
        if authorization != "Bearer test":
            raise AuthenticationError("missing credentials")
        return Claims("tenant-a", "officer-1", frozenset({"privacy-officer"}))


def client():
    app.state.oidc = FakeOIDC()
    app.state.service = PrivacyService(MemoryPrivacyStore(), b"s" * 32)
    return TestClient(app)


def test_consent_and_request_bound_dsar_api():
    http = client()
    headers = {"Authorization": "Bearer test"}
    consent = http.post("/v1/consents", headers=headers, json={
        "reference": "consent-1", "subject": "Test Subject", "purpose": "field-census",
        "lawful_basis": "consent", "status": "granted",
    })
    assert consent.status_code == 200, consent.text
    assert consent.json()["version"] == 1
    digest = app.state.service.subject_digest("Test Subject")
    proof = identity_proof("i" * 32, digest, "request-1", int(time.time()) + 60)
    opened = http.post("/v1/dsar", headers=headers, json={
        "request_id": "request-1", "subject": "Test Subject", "kind": "export", "identity_proof": proof,
    })
    assert opened.status_code == 200, opened.text
    key = "privacy/tenant-a/request-1/export.json"
    exported = http.post("/v1/dsar/request-1/export", headers=headers, json={
        "object_key": key, "files": [{"key": key, "sha256": "a" * 64}],
    })
    assert exported.status_code == 200, exported.text
    assert exported.json()["status"] == "exported"


def test_invalid_identity_proof_fails_closed():
    http = client()
    response = http.post("/v1/dsar", headers={"Authorization": "Bearer test"}, json={
        "request_id": "request-1", "subject": "Test Subject", "kind": "delete", "identity_proof": "x" * 40,
    })
    assert response.status_code == 409
    assert "identity proof" in response.json()["detail"]


def test_missing_auth_is_rejected():
    http = client()
    assert http.get("/v1/dsar/request-1").status_code == 401

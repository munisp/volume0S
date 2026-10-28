import os

os.environ.setdefault("OIDC_ISSUER", "https://id.example")
os.environ.setdefault("OIDC_AUDIENCE", "volumeos")
os.environ.setdefault("OIDC_JWKS_URL", "https://id.example/jwks")

from fastapi.testclient import TestClient
from auth import AuthenticationError, Claims
from leads import LeadService, MemoryLeadStore, ModelReview
from main import app


class Auth:
    async def authenticate(self, authorization):
        if authorization != "Bearer test": raise AuthenticationError("missing")
        return Claims("tenant-a", "agent-1", frozenset({"lead-agent"}))


class Gold:
    async def review(self, tenant_id, model_version, weight_version):
        return ModelReview("model", model_version, weight_version, "approved", "reviewer-1", "gold-review-1",
                           {"territory": 1.0, "engagement": 1.0, "freshness": 1.0, "completeness": 1.0})
    async def features(self, tenant_id, lead_id, h3_r9, model_version, weight_version):
        return {"tenant_id": tenant_id, "h3_r9": h3_r9, "lineage_id": "gold-1", "owner_id": "owner-1", "territory": 0.8, "engagement": 0.5, "freshness": 1.0, "completeness": 1.0}


def client():
    app.state.oidc = Auth(); app.state.service = LeadService(MemoryLeadStore(), gold=Gold())
    return TestClient(app)


def body():
    return {"source": "census", "source_record_id": "source-1", "business_name": "Test Business", "phone": "08031234567",
            "h3_r9": "89a123456789abc", "consent_reference": "consent-1", "lineage_id": "lineage-1"}


def test_authenticated_ingest_score_and_tenant_contract():
    http = client(); headers = {"Authorization": "Bearer test"}
    response = http.post("/v1/leads", headers=headers, json=body())
    assert response.status_code == 201, response.text
    lead_id = response.json()["lead_id"]
    scored = http.post(f"/v1/leads/{lead_id}/scores", headers=headers, json={"model_version": "model-v1", "weight_version": "weights-v1"})
    assert scored.status_code == 200, scored.text
    assert scored.json()["gold_lineage_id"] == "gold-1"
    assigned = http.post(f"/v1/leads/{lead_id}/territory-assignment", headers=headers, json={"model_version": "model-v1", "weight_version": "weights-v1"})
    assert assigned.status_code == 200 and assigned.json()["owner_id"] == "owner-1"
    assert http.get(f"/v1/leads/{lead_id}").status_code == 401


def test_cadence_cannot_be_approved_by_lead_agent():
    http = client(); headers = {"Authorization": "Bearer test"}
    lead_id = http.post("/v1/leads", headers=headers, json=body()).json()["lead_id"]
    due = http.get(f"/v1/leads/{lead_id}/cadence", headers=headers)
    assert due.json()["step"] == "D0" and due.json()["status"] == "requires-human-approval"
    response = http.post(f"/v1/leads/{lead_id}/cadence", headers=headers, json={"step": "D0", "approval_id": "approval-1"})
    assert response.status_code == 403

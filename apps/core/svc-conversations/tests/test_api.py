from fastapi.testclient import TestClient

from auth import AuthenticationError, Claims
from main import create_app
from test_conversations import KEY, fixture, signature, webhook


class Auth:
    async def authenticate(self, authorization):
        values = {
            "Bearer agent": Claims("tenant-a", "agent-1", frozenset({"front-desk-agent"})),
            "Bearer reviewer": Claims("tenant-a", "reviewer-1", frozenset({"front-desk-reviewer"})),
            "Bearer other": Claims("tenant-b", "agent-2", frozenset({"front-desk-agent"})),
        }
        if authorization not in values: raise AuthenticationError("missing")
        return values[authorization]


def client():
    store, service, ai, sender, booking = fixture()
    store.ready = lambda: None
    return TestClient(create_app(store, service, Auth())), service


def ingest(http, body=None):
    body = body or webhook()
    return http.post("/v1/webhooks/whatsapp/tenant-a", content=body,
                     headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature(body)})


def test_webhook_review_send_and_authentication():
    http, _ = client()
    response = ingest(http)
    assert response.status_code == 202, response.text
    created = response.json(); path = f"/v1/conversations/{created['conversation_id']}/drafts/{created['draft_sha256']}"
    queue = http.get("/v1/conversations", headers={"Authorization": "Bearer agent"})
    assert queue.status_code == 200 and queue.json()["conversations"][0]["drafts"][0]["draft_sha256"] == created["draft_sha256"]
    assert http.post(path + "/send").status_code == 401
    assert http.post(path + "/review", headers={"Authorization": "Bearer agent"},
                     json={"decision": "approved", "reason": "checked"}).status_code == 403
    reviewed = http.post(path + "/review", headers={"Authorization": "Bearer reviewer"},
                         json={"decision": "approved", "reason": "checked"})
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "approved"
    sent = http.post(path + "/send", headers={"Authorization": "Bearer agent"})
    assert sent.status_code == 200 and sent.json()["status"] == "sent"
    assert http.get(f"/v1/conversations/{created['conversation_id']}",
                    headers={"Authorization": "Bearer other"}).status_code == 404


def test_invalid_webhook_signature_is_rejected():
    http, _ = client(); body = webhook()
    response = http.post("/v1/webhooks/whatsapp/tenant-a", content=body,
                         headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})
    assert response.status_code == 401

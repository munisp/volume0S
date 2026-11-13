"""svc-creators with the in-memory store: callers, channels, projects, the stage rules and the history."""
import os

os.environ["VOLUMEOS_SERVICE_TOKEN"] = "t" * 40

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import model  # noqa: E402
from main import create_app  # noqa: E402
from store import InMemoryCreatorStore  # noqa: E402

SCRIPT = "# Hook\n" + "Have you ever waited for a sign that never seemed to come? " * 6 + \
         "\n# Chapter 1: The promise\n" + "Scripture tells of people who waited, and of what waiting built in them. " * 8


def headers(tenant="acme", actor="ada@acme.ng"):
    return {"Authorization": "Bearer " + "t" * 40, "X-VolumeOS-Tenant": tenant, "X-VolumeOS-Actor": actor}


@pytest.fixture
def client():
    with TestClient(create_app(store=InMemoryCreatorStore())) as c:
        yield c


def channel(client, tenant="acme", name="Spouse Confirmation"):
    r = client.post("/v1/creators/channels", headers=headers(tenant), json={
        "name": name, "youtube_handle": "SpouseConfirmation", "niche": "Christian relationships",
        "writing_instructions": "Biblical references, prophetic storytelling, gentle spiritual calls to action."})
    assert r.status_code == 201, r.text
    return r.json()


def project(client, channel_id, tenant="acme", **extra):
    body = {"channel_id": channel_id, "topic": "Signs God is preparing your spouse", "duration_minutes": 15} | extra
    r = client.post("/v1/creators/projects", headers=headers(tenant), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def stage(p, key):
    return next(s for s in p["stages"] if s["key"] == key)


def test_callers_need_the_service_token_and_a_business(client):
    assert client.get("/v1/creators/channels").status_code == 401
    assert client.get("/v1/creators/channels", headers={"Authorization": "Bearer wrong", "X-VolumeOS-Tenant": "acme"}).status_code == 401
    assert client.get("/v1/creators/channels", headers=headers(tenant="platform")).status_code == 400


def test_channels_keep_their_own_instructions_and_stay_in_their_business(client):
    c = channel(client)
    assert c["youtube_handle"] == "@SpouseConfirmation" and "prophetic" in c["writing_instructions"]
    assert client.post("/v1/creators/channels", headers=headers(), json={"name": "spouse confirmation"}).status_code == 409
    assert client.get("/v1/creators/channels", headers=headers("other")).json()["channels"] == []
    assert client.patch(f"/v1/creators/channels/{c['id']}", headers=headers("other"), json={"niche": "x"}).status_code == 404
    r = client.patch(f"/v1/creators/channels/{c['id']}", headers=headers(), json={"default_voice": "calm-female-1"})
    assert r.json()["default_voice"] == "calm-female-1"
    assert client.post("/v1/creators/channels", headers=headers(), json={"name": "X", "youtube_handle": "bad handle!"}).status_code == 422


def test_a_project_starts_at_research_with_a_word_target_from_its_length(client):
    c = channel(client)
    p = project(client, c["id"])
    assert p["target_words"] == 15 * model.WORDS_PER_MINUTE and p["current_stage"] == "research"
    assert [s["key"] for s in p["stages"]] == model.STAGE_KEYS and len(p["stages"]) == 12
    bad = client.post("/v1/creators/projects", headers=headers(), json={"channel_id": c["id"], "topic": "x y z", "duration_minutes": 12})
    assert bad.status_code == 422
    assert client.post("/v1/creators/projects", headers=headers("other"), json={"channel_id": c["id"], "topic": "abc", "duration_minutes": 5}).status_code == 404
    assert client.get(f"/v1/creators/projects/{p['id']}", headers=headers("other")).status_code == 404


def test_title_then_script_then_approval_in_order(client):
    c = channel(client)
    p = project(client, c["id"])
    # The script can't come before an approved title.
    assert client.put(f"/v1/creators/projects/{p['id']}/script", headers=headers(), json={"text": SCRIPT, "version": p["version"]}).status_code == 409
    p = client.put(f"/v1/creators/projects/{p['id']}/title", headers=headers(), json={
        "chosen": "7 Signs God Is Preparing Your Spouse Right Now", "options": ["A", "B"], "version": p["version"]}).json()
    assert stage(p, "research")["state"] == "skipped" and stage(p, "title")["state"] == "needs_approval"
    # Approving a produced stage, or with the wrong version, is refused.
    assert client.post(f"/v1/creators/projects/{p['id']}/stages/script/approve", headers=headers(), json={"version": p["version"]}).status_code == 409
    assert client.post(f"/v1/creators/projects/{p['id']}/stages/title/approve", headers=headers(), json={"version": p["version"] - 1}).status_code == 409
    no_actor = client.post(f"/v1/creators/projects/{p['id']}/stages/title/approve", headers=headers(actor=""), json={"version": p["version"]})
    assert no_actor.status_code == 400
    p = client.post(f"/v1/creators/projects/{p['id']}/stages/title/approve", headers=headers(), json={"version": p["version"]}).json()
    assert stage(p, "title")["state"] == "approved" and p["record"]["approvals"]["title"]["by"] == "ada@acme.ng"
    p = client.put(f"/v1/creators/projects/{p['id']}/script", headers=headers(), json={"text": SCRIPT, "version": p["version"]}).json()
    script = p["record"]["script"]
    assert [s["heading"] for s in script["sections"]] == ["Hook", "Chapter 1: The promise"]
    assert script["words"] > 100 and script["target_words"] == 2250
    assert stage(p, "script")["state"] == "done" and stage(p, "script_approval")["state"] == "needs_approval"
    p = client.post(f"/v1/creators/projects/{p['id']}/stages/script_approval/approve", headers=headers(), json={"version": p["version"]}).json()
    assert p["current_stage"] == "voiceover"


def test_reopening_a_stage_resets_everything_built_on_it_and_history_records_it(client):
    c = channel(client)
    p = project(client, c["id"], title="Money Files: Why Your Salary Disappears")
    assert stage(p, "title")["state"] == "needs_approval"
    p = client.post(f"/v1/creators/projects/{p['id']}/stages/title/approve", headers=headers(), json={"version": p["version"]}).json()
    p = client.put(f"/v1/creators/projects/{p['id']}/script", headers=headers(), json={"text": SCRIPT, "version": p["version"]}).json()
    p = client.post(f"/v1/creators/projects/{p['id']}/stages/title/reopen", headers=headers(), json={"note": "sharper title", "version": p["version"]}).json()
    assert stage(p, "title")["state"] == "needs_approval"
    assert stage(p, "script")["state"] == "pending" and stage(p, "script_approval")["state"] == "pending"
    assert "title" not in p["record"].get("approvals", {})
    kinds = [e["kind"] for e in client.get(f"/v1/creators/projects/{p['id']}/events", headers=headers()).json()["events"]]
    assert kinds == ["reopened", "script_saved", "approved", "created"]
    assert client.post(f"/v1/creators/projects/{p['id']}/stages/voiceover/reopen", headers=headers(), json={"note": "x", "version": p["version"]}).status_code == 409


def test_a_channel_with_projects_cannot_be_deleted(client):
    c = channel(client)
    project(client, c["id"])
    assert client.delete(f"/v1/creators/channels/{c['id']}", headers=headers()).status_code == 409
    empty = channel(client, name="Money Files Decoded")
    assert client.delete(f"/v1/creators/channels/{empty['id']}", headers=headers()).json() == {"status": "deleted"}


def test_short_scripts_are_refused_and_sections_split_on_headings():
    with pytest.raises(model.ProjectError):
        model.script_record("too short", 5, "creator")
    sections = model.split_sections("intro words here\nSCENE 2 the river\nwater flows\n## Part 3\nend")
    assert [s["heading"] for s in sections] == ["Opening", "SCENE 2 the river", "Part 3"]

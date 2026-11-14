"""YouTube connect and publish against a simulated Google (httpx MockTransport)."""
import json
import os
import time
from urllib.parse import parse_qs, urlparse

os.environ["VOLUMEOS_SERVICE_TOKEN"] = "t" * 40

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import youtube as yt  # noqa: E402
from main import create_app  # noqa: E402
from store import InMemoryCreatorStore  # noqa: E402
from test_worker import FakeAudio, FakeImages, FakeRender, FakeVoices, VisualWriter, headers, state, voiced_project  # noqa: E402
from worker import Worker  # noqa: E402


class Google:
    def __init__(self):
        self.calls, self.uploaded, self.metadata = [], b"", None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url); self.calls.append((request.method, url.split("?")[0]))
        if url.startswith(yt.TOKEN_URL):
            form = parse_qs(request.content.decode())
            if form.get("grant_type") == ["authorization_code"]:
                return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-secret", "expires_in": 3600})
            return httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600})
        if url.startswith(yt.REVOKE_URL):
            return httpx.Response(200)
        if url.startswith(f"{yt.API}/channels"):
            return httpx.Response(200, json={"items": [{"id": "UC123", "snippet": {"title": "Money Files Decoded", "customUrl": "@moneyfiles"}, "statistics": {"subscriberCount": "1200"}}]})
        if url.startswith(f"{yt.UPLOAD}/videos") and request.method == "POST":
            self.metadata = json.loads(request.content)
            return httpx.Response(200, headers={"location": "https://upload.example/session-1"})
        if url.startswith("https://upload.example/session-1"):
            self.uploaded += request.content
            return httpx.Response(200, json={"id": "vid-42"})
        if url.startswith(f"{yt.UPLOAD}/thumbnails/set") or url.startswith(f"{yt.UPLOAD}/captions"):
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def setup():
    google = Google()
    youtube = yt.YouTube("cid", "csecret", "https://volumeos.newfire.app/v1/creators/youtube/callback", transport=httpx.MockTransport(google.handler))
    store, voices, audio = InMemoryCreatorStore(), FakeVoices(), FakeAudio()
    with TestClient(create_app(store=store, voices=voices, audio=audio, youtube=youtube)) as client:
        yield client, store, voices, audio, youtube, google


def connect(client, channel_id):
    url = client.post(f"/v1/creators/channels/{channel_id}/youtube/connect", headers=headers()).json()["url"]
    query = parse_qs(urlparse(url).query)
    assert query["access_type"] == ["offline"] and "youtube.upload" in query["scope"][0] and query["redirect_uri"][0].endswith("/youtube/callback")
    return client.get("/v1/creators/youtube/callback", params={"code": "abc", "state": query["state"][0]}, follow_redirects=False)


async def test_connect_publish_and_disconnect(setup):
    client, store, voices, audio, youtube, google = setup
    worker = Worker(store, VisualWriter(), voices, audio, FakeImages(), FakeRender(), youtube)
    p = await voiced_project(client, store, voices, audio)
    ch = p["channel_id"]
    assert client.get(f"/v1/creators/channels/{ch}/youtube", headers=headers()).json() == {"configured": True, "connected": False, "youtube": None}
    # A forged or expired state does nothing.
    assert client.get("/v1/creators/youtube/callback", params={"code": "x", "state": "forged"}, follow_redirects=False).headers["location"].endswith("youtube=invalid")
    old = yt.sign_state("acme", ch, "ada@acme.ng", now=time.time() - 3600)
    assert client.get("/v1/creators/youtube/callback", params={"code": "x", "state": old}, follow_redirects=False).headers["location"].endswith("youtube=invalid")
    r = connect(client, ch)
    assert r.status_code == 303 and r.headers["location"].endswith("youtube=connected")
    status = client.get(f"/v1/creators/channels/{ch}/youtube", headers=headers()).json()
    assert status["connected"] and status["youtube"]["title"] == "Money Files Decoded" and "refresh_token_sealed" not in status["youtube"]
    assert b"rt-secret" not in store.youtube_rows[("acme", ch)]["refresh_token_sealed"]  # never stored plain
    assert client.get(f"/v1/creators/channels/{ch}/youtube", headers=headers("other")).status_code == 404
    # Publishing needs the approved final video.
    assert client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "publish", "version": p["version"]}).status_code == 409
    for kind in ("scenes", "images", "thumbnail", "seo"):
        p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
        client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": kind, "version": p["version"]}); await worker.once()
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    p = client.put(f"/v1/creators/projects/{p['id']}/thumbnail", headers=headers(), json={"chosen": 1, "version": p["version"]}).json()
    client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "assembly", "version": p["version"]}); await worker.once()
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    p = client.post(f"/v1/creators/projects/{p['id']}/stages/final_review/approve", headers=headers(), json={"version": p["version"]}).json()
    assert client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "publish", "publish_at": "2020-01-01T00:00:00Z", "version": p["version"]}).status_code == 400
    r = client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "publish", "privacy": "public", "publish_at": "2099-01-01T09:00:00Z", "version": p["version"]})
    assert r.status_code == 202
    await worker.once()
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    assert state(p, "publishing") == "done" and p["record"]["youtube"]["video_id"] == "vid-42" and p["record"]["youtube"]["url"] == "https://youtu.be/vid-42"
    meta = google.metadata
    assert meta["snippet"]["title"] == p["title"] and meta["snippet"]["tags"] == p["record"]["seo"]["tags"] and "Chapters:" in meta["snippet"]["description"]
    assert meta["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "publishAt": "2099-01-01T09:00:00Z"}  # scheduled = private until then
    assert google.uploaded == audio.objects[p["record"]["video"]["key"]]
    assert any("thumbnails/set" in u for _, u in google.calls) and any(u.endswith("/captions") for _, u in google.calls)
    # Disconnecting revokes Google's access.
    assert client.delete(f"/v1/creators/channels/{ch}/youtube", headers=headers()).json() == {"status": "disconnected"}
    assert (("POST", yt.REVOKE_URL) in google.calls) and client.get(f"/v1/creators/channels/{ch}/youtube", headers=headers()).json()["connected"] is False


def test_without_a_google_client_youtube_says_it_is_not_set_up():
    with TestClient(create_app(store=InMemoryCreatorStore(), voices=FakeVoices(), audio=FakeAudio())) as client:
        ch = client.post("/v1/creators/channels", headers=headers(), json={"name": "X"}).json()["id"]
        assert client.post(f"/v1/creators/channels/{ch}/youtube/connect", headers=headers()).status_code == 503
        assert client.get(f"/v1/creators/channels/{ch}/youtube", headers=headers()).json()["configured"] is False

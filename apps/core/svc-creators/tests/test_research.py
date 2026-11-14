"""Step 5: research job, title scoring, launch plan (checklist, upload times, real-data baseline)."""
import json
import os
from datetime import datetime, timezone

os.environ["VOLUMEOS_SERVICE_TOKEN"] = "t" * 40

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import research as rs  # noqa: E402
import youtube as yt  # noqa: E402
from main import create_app  # noqa: E402
from providers import ProviderError  # noqa: E402
from store import InMemoryCreatorStore  # noqa: E402
from test_worker import FakeAudio, FakeVoices, FakeWriter, headers, state  # noqa: E402
from worker import Worker  # noqa: E402

ANSWER = {
    "angles": [{"angle": "The payday illusion", "why": "everyone feels rich on the 25th"}, {"angle": "Small leaks", "why": "relatable"}],
    "audience_questions": ["Why am I broke before month end?", "How much should I save?"],
    "must_cover": ["Lifestyle creep", "Inflation in Nigeria is about 30% (check)", "A simple 50/30/20 budget"],
    "avoid": ["Preaching", "Promising quick riches"],
    "keywords": {"primary": ["salary budgeting", "save money nigeria", "where does my salary go"], "secondary": ["budget tips"]},
    "titles": [{"title": "Where Does My Salary Go? 5 Leaks Draining Your Pay", "clarity": 9, "curiosity": 8, "why": "question + number"},
               {"title": "Why You're Broke Before the 15th (Salary Budgeting)", "clarity": 8, "curiosity": 9, "why": "pain point"},
               {"title": "SALARY GONE!!! THE TRUTH NOBODY TELLS YOU ABOUT MONEY IN NIGERIA TODAY OK", "clarity": 3, "curiosity": 6, "why": "shouty"},
               {"title": "x", "clarity": 5, "curiosity": 5},
               {"title": "Where Does My Salary Go? 5 Leaks Draining Your Pay", "clarity": 9, "curiosity": 8}],
    "current_title": {"clarity": 7, "curiosity": 6, "why": "clear but flat"},
}


class ResearchWriter(FakeWriter):
    def __init__(self, answer=ANSWER):
        super().__init__(); self.answer, self.asks, self.briefs = answer, [], []

    async def _chat(self, system, ask, json_mode=False, max_tokens=0, temperature=0):
        self.asks.append(ask); return json.dumps(self.answer)

    async def outline(self, system, brief, chapters, target):
        self.briefs.append(brief); return await super().outline(system, brief, chapters, target)


@pytest.fixture
def setup():
    store, voices, audio = InMemoryCreatorStore(), FakeVoices(), FakeAudio()
    with TestClient(create_app(store=store, voices=voices, audio=audio)) as client:
        yield client, store, voices, audio


def test_title_scores_add_up_and_explain_themselves():
    good = rs.score_title("Where Does My Salary Go? 5 Leaks Draining Your Pay", ["where does my salary go"], 9, 8)
    assert good["score"] >= 90 and good["rated_by_model"] and {p["name"] for p in good["parts"]} >= {"Length", "Search words", "Readability"}
    shouty = rs.score_title("SALARY GONE!!! THE TRUTH NOBODY TELLS YOU ABOUT MONEY IN NIGERIA TODAY OK", ["salary budgeting"], 3, 6)
    assert shouty["score"] < 50
    late = rs.score_title("The real reason you are always out of cash: salary budgeting", ["salary budgeting"])
    assert not late["rated_by_model"] and next(p for p in late["parts"] if p["name"] == "Search words")["points"] == 12


def test_upload_times_are_in_the_audience_zone_and_ahead():
    zone, label = rs.audience_zone("Young Nigerian professionals")
    assert zone == "Africa/Lagos" and rs.audience_zone("folks in London")[0] == "Europe/London"
    plan = rs.upload_times(zone, label, now=datetime(2026, 11, 19, 16, 30, tzinfo=timezone.utc))  # Thu 5:30 pm Lagos
    assert [s["local"] for s in plan["slots"]] == ["Fri 20 Nov, 5 pm", "Sat 21 Nov, 10 am", "Sun 22 Nov, 10 am", "Thu 26 Nov, 5 pm"]
    assert plan["slots"][0]["at"] == "2026-11-20T16:00:00Z" and "not measured" in plan["note"]


def test_baseline_is_real_numbers_only():
    assert rs.baseline([]) is None
    vids = [{"title": f"v{i}", "views": v, "published_at": "2026-11-01T00:00:00Z"} for i, v in enumerate([100, 400, 900, 1500, 30000])]
    b = rs.baseline(vids, now=datetime(2026, 11, 11, tzinfo=timezone.utc))
    assert b["median_views"] == 900 and b["best"]["views"] == 30000 and b["median_views_per_day"] == 90.0 and b["typical_range"][0] <= 900 <= b["typical_range"][1]


async def test_research_job_suggests_titles_and_feeds_the_script(setup):
    client, store, voices, audio = setup
    ch = client.post("/v1/creators/channels", headers=headers(), json={"name": "Money Files", "audience": "Young Nigerian professionals"}).json()
    p = client.post("/v1/creators/projects", headers=headers(), json={"channel_id": ch["id"], "topic": "Why your salary disappears", "duration_minutes": 5}).json()
    assert state(p, "research") == "pending"
    r = client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "research", "version": p["version"]})
    assert r.status_code == 202 and state(r.json()["project"], "research") == "running"
    p = r.json()["project"]
    assert client.put(f"/v1/creators/projects/{p['id']}/title", headers=headers(), json={"chosen": "Mine", "version": p["version"]}).status_code == 409
    writer = ResearchWriter()
    await Worker(store, writer, voices, audio).once()
    assert "Young Nigerian professionals" in writer.asks[0] and "never invent statistics" in writer.asks[0]
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    found = p["record"]["research"]
    assert state(p, "research") == "done" and state(p, "title") == "pending"  # the creator still picks
    assert [t["title"] for t in found["titles"]][:2] == ["Where Does My Salary Go? 5 Leaks Draining Your Pay", "Why You're Broke Before the 15th (Salary Budgeting)"]
    assert len(found["titles"]) == 3 and found["titles"][0]["score"] >= found["titles"][-1]["score"]  # junk and duplicates dropped, best first
    assert p["record"]["title"]["options"] == [t["title"] for t in found["titles"]] and "YouTube search data" in found["note"]
    pick = found["titles"][1]["title"]
    p = client.put(f"/v1/creators/projects/{p['id']}/title", headers=headers(), json={"chosen": pick, "options": [pick], "version": p["version"]}).json()
    assert p["record"]["title"]["source"] == "research"
    launch = client.get(f"/v1/creators/projects/{p['id']}/launch", headers=headers()).json()
    assert launch["title_score"]["rated_by_model"] and launch["title_score"]["score"] == found["titles"][1]["score"]
    assert launch["times"]["zone"] == "Africa/Lagos" and len(launch["times"]["slots"]) == 4
    assert launch["baseline"] is None and "Connect" in launch["baseline_note"]
    assert {c["key"]: c["ok"] for c in launch["checklist"]}["title_keyword"] is True
    p = client.post(f"/v1/creators/projects/{p['id']}/stages/title/approve", headers=headers(), json={"version": p["version"]}).json()
    assert client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "research", "version": p["version"]}).status_code == 409
    client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "script", "version": p["version"]})
    await Worker(store, writer, voices, audio).once()
    assert "Answer these viewer questions" in writer.briefs[0] and "Lifestyle creep" in writer.briefs[0] and "(check)" not in writer.briefs[0]


async def test_thin_research_fails_the_stage_and_a_title_can_still_be_typed(setup):
    client, store, voices, audio = setup
    ch = client.post("/v1/creators/channels", headers=headers(), json={"name": "X"}).json()
    p = client.post("/v1/creators/projects", headers=headers(), json={"channel_id": ch["id"], "topic": "Budgets", "title": "My own title", "duration_minutes": 5}).json()
    assert state(p, "research") == "skipped"
    p = client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "research", "version": p["version"]}).json()["project"]
    assert state(p, "title") == "needs_approval"  # research suggests; it doesn't undo the creator's title
    await Worker(store, ResearchWriter({"titles": []}), voices, audio).once()
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    assert state(p, "research") == "failed" and "too thin" in next(s for s in p["stages"] if s["key"] == "research")["note"]
    p = client.put(f"/v1/creators/projects/{p['id']}/title", headers=headers(), json={"chosen": "My own title", "version": p["version"]}).json()
    assert state(p, "research") == "skipped"
    assert client.post(f"/v1/creators/projects/{p['id']}/stages/title/approve", headers=headers(), json={"version": p["version"]}).status_code == 200


def test_launch_baseline_comes_from_the_channels_own_videos():
    def handler(request):
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if "/channels" in url:
            return httpx.Response(200, json={"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        if "/playlistItems" in url:
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": f"v{i}"}} for i in range(4)]})
        if "/videos" in url:
            return httpx.Response(200, json={"items": [{"id": f"v{i}", "snippet": {"title": f"Video {i}", "publishedAt": "2026-10-01T00:00:00Z"},
                                                        "statistics": {"viewCount": str(v)}} for i, v in enumerate([200, 800, 1000, 5000])]})
        return httpx.Response(404)
    store = InMemoryCreatorStore()
    youtube = yt.YouTube("cid", "cs", "https://x/cb", transport=httpx.MockTransport(handler))
    with TestClient(create_app(store=store, voices=FakeVoices(), audio=FakeAudio(), youtube=youtube)) as client:
        ch = client.post("/v1/creators/channels", headers=headers(), json={"name": "X"}).json()
        p = client.post("/v1/creators/projects", headers=headers(), json={"channel_id": ch["id"], "topic": "Budgets", "title": "A title", "duration_minutes": 5}).json()
        import asyncio
        asyncio.run(store.save_youtube("acme", ch["id"], {"youtube_channel_id": "UC1", "title": "Mine", "handle": "", "subscribers": 10}, yt.seal("rt"), "a"))
        launch = client.get(f"/v1/creators/projects/{p['id']}/launch", headers=headers()).json()
        assert launch["baseline"]["median_views"] == 900 and launch["baseline"]["videos"] == 4 and "from YouTube" in launch["baseline"]["note"]
        assert client.get(f"/v1/creators/projects/{p['id']}/launch", headers=headers("other")).status_code == 404

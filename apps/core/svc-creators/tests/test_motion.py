"""Full-motion videos: a moving clip per scene from its image, scenes optionally kept still, and the video made from both."""
import os

os.environ["VOLUMEOS_SERVICE_TOKEN"] = "t" * 40

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import create_app  # noqa: E402
from store import InMemoryCreatorStore  # noqa: E402
from test_worker import FakeAudio, FakeImages, FakeRender, FakeVoices, VisualWriter, headers, state, voiced_project  # noqa: E402
from worker import Worker  # noqa: E402


class FakeMotion:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    async def animate(self, image, prompt, seconds, seed, quality="standard"):
        self.calls.append((len(image), prompt, seconds, seed, quality))
        if self.fail_on and len(self.calls) == self.fail_on:
            from providers import ProviderError
            raise ProviderError("the motion service didn't answer")
        return b"\x00\x00\x00\x18ftypmp42clip" + bytes([len(self.calls)]) * 500, "Wan2.2-TI2V-5B", 2.0


@pytest.fixture
def setup():
    store, voices, audio = InMemoryCreatorStore(), FakeVoices(), FakeAudio()
    with TestClient(create_app(store=store, voices=voices, audio=audio, motion=True)) as client:
        yield client, store, voices, audio


def get(client, p):
    return client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()


def job(client, p, kind, **extra):
    return client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": kind, "version": p["version"], **extra})


async def visual_project(client, store, voices, audio, worker):
    p = await voiced_project(client, store, voices, audio)
    job(client, p, "scenes"); await worker.once()
    p = get(client, p); job(client, p, "images"); await worker.once()
    return get(client, p)


async def test_motion_clips_still_scenes_and_the_video(setup):
    client, store, voices, audio = setup
    motion, render = FakeMotion(), FakeRender()
    worker = Worker(store, VisualWriter(), voices, audio, FakeImages(), render, motion=motion)
    p = await voiced_project(client, store, voices, audio)
    assert job(client, p, "motion").status_code == 409  # needs the images first
    job(client, p, "scenes"); await worker.once()
    p = get(client, p); job(client, p, "images"); await worker.once()
    p = get(client, p)
    scenes = p["record"]["scenes"]
    assert len(scenes) >= 3
    # Keep scene 2 as a still image.
    s2 = scenes[1]
    p = client.put(f"/v1/creators/projects/{p['id']}/scenes/2", headers=headers(), json={"prompt": s2["prompt"], "still": True, "version": p["version"]}).json()
    assert p["record"]["scenes"][1]["still"] is True and not p["record"]["scenes"][1]["image"].get("stale")  # only the switch changed
    assert job(client, p, "motion", quality="4k").status_code in (400, 422)
    r = job(client, p, "motion", quality="hd")
    assert r.status_code == 202 and state(r.json()["project"], "images") == "running" and r.json()["job"]["params"]["seconds"] > 0
    await worker.once()
    p = get(client, p)
    assert state(p, "images") == "done" and f"{len(scenes) - 1} of {len(scenes)} scenes in motion" in next(s for s in p["stages"] if s["key"] == "images")["note"]
    assert len(motion.calls) == len(scenes) - 1 and all("No text on screen" in c[1] for c in motion.calls)
    assert motion.calls[0][2] == scenes[0]["seconds"] and motion.calls[0][3] == scenes[0]["seed"] and motion.calls[0][4] == "hd"
    assert p["record"]["scenes"][1].get("motion") is None and p["record"]["scenes"][0]["motion"]["model"] == "Wan2.2-TI2V-5B"
    clip = client.get(f"/v1/creators/projects/{p['id']}/motion/scene-001.mp4", headers=headers())
    assert clip.status_code == 200 and clip.headers["content-type"] == "video/mp4" and clip.content.startswith(b"\x00\x00\x00\x18ftyp")
    assert client.get(f"/v1/creators/projects/{p['id']}/motion/scene-002.mp4", headers=headers()).status_code == 404
    assert client.get(f"/v1/creators/projects/{p['id']}/motion/scene-001.mp4", headers=headers("other")).status_code == 404
    events = [e["kind"] for e in client.get(f"/v1/creators/projects/{p['id']}/events", headers=headers()).json()["events"]]
    assert events.count("motion_saved") == 1  # one history entry, however many scenes
    # The video: motion for animated scenes, the still image (zoom) for scene 2.
    job(client, p, "assembly"); await worker.once()
    spec, _ = render.specs[-1]
    assert spec["scenes"][0]["motion"] == ["scene-001.mp4"] and "motion" not in spec["scenes"][1]
    assert render.sent["scene-001.mp4"] == audio.objects[p["record"]["scenes"][0]["motion"]["key"]]
    # Remaking one scene's image makes its old clip unusable until it is animated again.
    p = get(client, p); job(client, p, "images", scenes=[1]); await worker.once()
    p = get(client, p)
    job(client, p, "assembly"); await worker.once()
    assert "motion" not in render.specs[-1][0]["scenes"][0] and render.specs[-1][0]["scenes"][2]["motion"] == ["scene-003.mp4"]
    p = get(client, p); calls = len(motion.calls)
    job(client, p, "motion", scenes=[1]); await worker.once()
    assert len(motion.calls) == calls + 1


async def test_an_interrupted_motion_job_resumes_and_all_still_is_refused(setup):
    client, store, voices, audio = setup

    class Down(FakeMotion):  # ghana stops answering from the 6th clip on
        async def animate(self, *a, **k):
            if len(self.calls) >= 5:
                self.calls.append(a)
                from providers import ProviderError
                raise ProviderError("the motion service didn't answer")
            return await super().animate(*a)

    worker = Worker(store, VisualWriter(), voices, audio, FakeImages(), FakeRender(), motion=Down())
    worker.retry_wait = 0
    p = await visual_project(client, store, voices, audio, worker)
    n = len(p["record"]["scenes"])
    job(client, p, "motion"); await worker.once()
    p = get(client, p)
    images = next(s for s in p["stages"] if s["key"] == "images")
    assert images["state"] == "done" and "motion stopped" in images["note"] and "Animate again" in images["note"]
    assert len(worker.motion.calls) == 5 + 3  # three tries on the failing scene
    assert sum(1 for s in p["record"]["scenes"] if s.get("motion")) == 5  # everything made before the failure is kept
    worker.motion = FakeMotion()
    assert job(client, p, "motion").status_code == 202
    await worker.once()
    p = get(client, p)
    assert len(worker.motion.calls) == n - 5 and all(s.get("motion") for s in p["record"]["scenes"])
    for s in p["record"]["scenes"]:
        p = client.put(f"/v1/creators/projects/{p['id']}/scenes/{s['number']}", headers=headers(), json={"prompt": s["prompt"], "still": True, "version": p["version"]}).json()
    r = job(client, p, "motion")
    assert r.status_code == 400 and "still" in r.text


def test_motion_is_refused_plainly_when_not_set_up():
    with TestClient(create_app(store=InMemoryCreatorStore(), voices=FakeVoices(), audio=FakeAudio(), motion=False)) as client:
        ch = client.post("/v1/creators/channels", headers=headers(), json={"name": "X"}).json()
        p = client.post("/v1/creators/projects", headers=headers(), json={"channel_id": ch["id"], "topic": "Budgets", "title": "T title", "duration_minutes": 5}).json()
        assert job(client, p, "motion").status_code == 503

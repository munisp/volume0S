"""Jobs and the creators worker with fakes for the script model, the voice service and storage."""
import hashlib
import io
import os
import wave

os.environ["VOLUMEOS_SERVICE_TOKEN"] = "t" * 40

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import create_app  # noqa: E402
from providers import ProviderError, chunks_for_speech, join_wavs, plain_text  # noqa: E402
from store import InMemoryCreatorStore  # noqa: E402
from worker import Worker  # noqa: E402


def wav(seconds: float) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\x01\x00" * int(24000 * seconds))
    return out.getvalue()


class FakeWriter:
    model = "fake-model"

    def __init__(self, fail_at=None):
        self.fail_at, self.systems = fail_at, []

    async def outline(self, system, brief, chapters, target):
        self.systems.append(system)
        return [{"heading": f"Part {i}", "purpose": "p", "words": target // chapters} for i in range(1, chapters + 1)]

    async def chapter(self, system, brief, plan, index, previous):
        if self.fail_at == index:
            raise ProviderError("the script model didn't answer")
        return ("A sentence for the narrator to say clearly. " * (plan[index]["words"] // 8 + 1)).strip()


class FakeVoices:
    def __init__(self):
        self.calls = []

    async def voices(self):
        return [{"id": "af_heart", "name": "Heart", "gender": "female", "accent": "American"}]

    async def speak(self, text, voice, speed):
        self.calls.append((voice, speed, len(text)))
        return wav(0.5)


class FakeAudio:
    def __init__(self):
        self.objects = {}

    @staticmethod
    def key(tenant, project, name):
        return f"{tenant}/creators/{project}/{name}"

    async def put(self, key, data, content_type="audio/wav"):
        self.objects[key] = data

    async def get(self, key, limit=0):
        return self.objects[key]


def headers(tenant="acme", actor="ada@acme.ng"):
    return {"Authorization": "Bearer " + "t" * 40, "X-VolumeOS-Tenant": tenant, "X-VolumeOS-Actor": actor}


@pytest.fixture
def setup():
    store, voices, audio = InMemoryCreatorStore(), FakeVoices(), FakeAudio()
    with TestClient(create_app(store=store, voices=voices, audio=audio)) as client:
        yield client, store, voices, audio


def approved_title_project(client, minutes=5):
    ch = client.post("/v1/creators/channels", headers=headers(), json={"name": "Money Files", "writing_instructions": "Relatable examples."}).json()
    p = client.post("/v1/creators/projects", headers=headers(), json={"channel_id": ch["id"], "topic": "salaries", "title": "Where Your Salary Goes", "duration_minutes": minutes}).json()
    return client.post(f"/v1/creators/projects/{p['id']}/stages/title/approve", headers=headers(), json={"version": p["version"]}).json()


def state(p, key):
    return next(s for s in p["stages"] if s["key"] == key)["state"]


async def test_script_job_writes_chapters_with_the_channel_instructions_then_waits_for_approval(setup):
    client, store, voices, audio = setup
    p = approved_title_project(client)
    r = client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "script", "version": p["version"]})
    assert r.status_code == 202 and state(r.json()["project"], "script") == "running"
    assert client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "script", "version": r.json()["project"]["version"]}).status_code == 409
    writer = FakeWriter()
    assert await Worker(store, writer, voices, audio).once() is True
    assert "Relatable examples." in writer.systems[0]
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    assert state(p, "script") == "done" and state(p, "script_approval") == "needs_approval"
    assert len(p["record"]["script"]["sections"]) == 3 and p["record"]["script"]["source"] == "ai:fake-model"
    assert client.get(f"/v1/creators/projects/{p['id']}/jobs", headers=headers()).json()["jobs"][0]["status"] == "done"


async def test_voiceover_needs_the_approved_script_and_matches_it_chapter_by_chapter(setup):
    client, store, voices, audio = setup
    p = approved_title_project(client)
    assert client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "voiceover", "voice": "af_heart", "version": p["version"]}).status_code == 409
    p = client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "script", "version": p["version"]}).json()["project"]
    await Worker(store, FakeWriter(), voices, audio).once()
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    p = client.post(f"/v1/creators/projects/{p['id']}/stages/script_approval/approve", headers=headers(), json={"version": p["version"]}).json()
    assert client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "voiceover", "voice": "xx_nobody", "version": p["version"]}).status_code == 400
    r = client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "voiceover", "voice": "af_heart", "speed": 0.9, "version": p["version"]})
    assert r.status_code == 202
    await Worker(store, FakeWriter(), voices, audio).once()
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    vo = p["record"]["voiceover"]
    assert state(p, "voiceover") == "done" and vo["voice"] == "af_heart" and vo["speed"] == 0.9
    assert vo["script_sha256"] == hashlib.sha256(p["record"]["script"]["text"].encode()).hexdigest()
    assert [s["index"] for s in vo["sections"]] == [1, 2, 3] and all(voice == "af_heart" for voice, _, _ in voices.calls)
    got = client.get(f"/v1/creators/projects/{p['id']}/audio/chapter-01", headers=headers())
    assert got.status_code == 200 and got.content[:4] == b"RIFF"
    assert client.get(f"/v1/creators/projects/{p['id']}/audio/narration", headers=headers()).status_code == 200
    assert client.get(f"/v1/creators/projects/{p['id']}/audio/chapter-01", headers=headers("other")).status_code == 404
    assert client.get(f"/v1/creators/projects/{p['id']}/audio/chapter-09", headers=headers()).status_code == 404


async def test_a_failed_job_marks_its_stage_failed_with_the_reason(setup):
    client, store, voices, audio = setup
    p = approved_title_project(client)
    client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "script", "version": p["version"]})
    await Worker(store, FakeWriter(fail_at=1), voices, audio).once()
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    stage = next(s for s in p["stages"] if s["key"] == "script")
    assert stage["state"] == "failed" and "didn't answer" in stage["note"]
    assert client.get(f"/v1/creators/projects/{p['id']}/jobs", headers=headers()).json()["jobs"][0]["status"] == "failed"
    # A failed stage can be started again.
    assert client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "script", "version": p["version"]}).status_code == 202


async def test_a_result_for_a_script_changed_meanwhile_is_discarded(setup):
    client, store, voices, audio = setup
    p = approved_title_project(client)
    p = client.post(f"/v1/creators/projects/{p['id']}/jobs", headers=headers(), json={"kind": "script", "version": p["version"]}).json()["project"]
    # The title is reopened while the script is being written: the stage is no longer running.
    client.post(f"/v1/creators/projects/{p['id']}/stages/title/reopen", headers=headers(), json={"note": "new angle", "version": p["version"]})
    await Worker(store, FakeWriter(), voices, audio).once()
    p = client.get(f"/v1/creators/projects/{p['id']}", headers=headers()).json()
    assert "script" not in p["record"] and state(p, "script") == "pending"


def test_voice_preview_and_list(setup):
    client, *_ = setup
    assert client.get("/v1/creators/voices", headers=headers()).json()["voices"][0]["id"] == "af_heart"
    r = client.post("/v1/creators/voices/preview", headers=headers(), json={"voice": "af_heart"})
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert client.post("/v1/creators/voices/preview", headers=headers(), json={"voice": "../etc"}).status_code == 422


def test_narration_cleanup_chunking_and_joining():
    text = plain_text("<think>plan</think>\n## Chapter 2\n**Welcome** back.\n[MUSIC SWELLS]\nNarrator: hi\nReal line (music) here.")
    assert "think" not in text and "Chapter 2" not in text and "MUSIC" not in text and "**" not in text and "Welcome back." in text
    long = ("This is a sentence. " * 200).strip()
    parts = chunks_for_speech(long + "\n\nShort one.", limit=500)
    assert all(len(c) <= 500 for c in parts) and parts[-1] == "Short one."
    audio, seconds = join_wavs([wav(1.0), wav(0.5)], pause_seconds=0.5)
    assert abs(seconds - 2.0) < 0.01 and audio[:4] == b"RIFF"

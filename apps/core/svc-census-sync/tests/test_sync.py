import base64
import hashlib
import hmac
import json

import pytest

from store import PostgresCensusStore
from sync import (
    CensusError,
    CensusSync,
    InMemoryCensusStore,
    Upload,
    decode_chunk,
    normalize_phone,
)

KEY = b"census-test-masking-key-with-32-bytes"
EVENT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class Sink:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def publish(self, topic, key, document_json):
        if self.fail:
            raise TimeoutError("temporary broker outage")
        self.calls.append((topic, key, document_json))


def payload():
    return json.dumps({
        "business_name": "Ada's Salon",
        "niche": "beauty",
        "address": "12 Market Road, Kano",
        "notes": "Walk-in customers",
        "phone": "0803 123 4567",
    }, separators=(",", ":")).encode()


def upload_for(content, **changes):
    values = dict(
        tenant_id="tenant-a",
        upload_id="upload-1",
        event_id=EVENT_ID,
        device_id="device-1",
        actor="field-agent-1",
        total_chunks=2,
        total_bytes=len(content),
        payload_sha256=hashlib.sha256(content).hexdigest(),
        captured_at_unix=1_760_000_000,
        latitude=12.0022,
        longitude=8.592,
        accuracy_m=12.5,
        consent_reference="consent-1",
        lawful_basis="consent",
    )
    values.update(changes)
    return Upload(**values)


def encoded_chunks(content, count=2):
    split = (len(content) + count - 1) // count
    chunks = [content[index:index + split] for index in range(0, len(content), split)]
    assert len(chunks) == count
    return [(base64.b64encode(chunk).decode(), hashlib.sha256(chunk).hexdigest()) for chunk in chunks]


def service(sink=None):
    return CensusSync(InMemoryCensusStore(), sink or Sink(), KEY)


@pytest.mark.asyncio
async def test_out_of_order_resume_complete_and_chunk_cleanup():
    content = payload()
    upload = upload_for(content)
    sink = Sink()
    sync = service(sink)
    assert (await sync.start(upload))["resume"] == [0, 1]
    chunks = encoded_chunks(content)
    await sync.put_chunk(upload.tenant_id, upload.device_id, upload.upload_id, 1, *chunks[1])
    resumed = await sync.resume(upload.tenant_id, upload.device_id, upload.upload_id)
    assert resumed["resume"] == [0]
    restarted = await sync.start(upload)
    assert restarted["resume"] == [0]
    await sync.put_chunk(upload.tenant_id, upload.device_id, upload.upload_id, 0, *chunks[0])
    completed = await sync.complete(upload.tenant_id, upload.device_id, upload.upload_id)
    assert completed == {
        "upload_id": upload.upload_id,
        "event_id": EVENT_ID,
        "status": "published",
        "resume": [],
    }
    assert len(sink.calls) == 1
    assert sync.store.chunks == {}
    document = json.loads(sink.calls[0][2])
    assert document["envelope"]["event_id"] == EVENT_ID
    assert document["envelope"]["tenant_id"] == upload.tenant_id
    assert document["payload"]["phone_hash"] == hmac.new(KEY, b"+2348031234567", hashlib.sha256).hexdigest()
    assert "phone" not in document["payload"]
    assert "0803" not in sink.calls[0][2]


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_does_not_duplicate_publication():
    content = payload()
    upload = upload_for(content)
    sink = Sink()
    sync = service(sink)
    await sync.start(upload)
    for index, chunk in enumerate(encoded_chunks(content)):
        await sync.put_chunk(upload.tenant_id, upload.device_id, upload.upload_id, index, *chunk)
    first = await sync.complete(upload.tenant_id, upload.device_id, upload.upload_id)
    assert await sync.complete(upload.tenant_id, upload.device_id, upload.upload_id) == first
    assert await sync.resume(upload.tenant_id, upload.device_id, upload.upload_id) == first
    assert await sync.start(upload) == first
    assert len(sink.calls) == 1


@pytest.mark.asyncio
async def test_corruption_conflicting_replacement_and_incomplete_upload_are_denied():
    content = payload()
    upload = upload_for(content)
    sync = service()
    await sync.start(upload)
    chunks = encoded_chunks(content)
    bad_digest = "0" * 64
    with pytest.raises(CensusError, match="digest does not match"):
        await sync.put_chunk(upload.tenant_id, upload.device_id, upload.upload_id, 0, chunks[0][0], bad_digest)
    await sync.put_chunk(upload.tenant_id, upload.device_id, upload.upload_id, 0, *chunks[0])
    with pytest.raises(CensusError, match="already used"):
        await sync.put_chunk(upload.tenant_id, upload.device_id, upload.upload_id, 0, *chunks[1])
    with pytest.raises(CensusError, match="all chunks"):
        await sync.complete(upload.tenant_id, upload.device_id, upload.upload_id)


@pytest.mark.asyncio
async def test_assembled_payload_manifest_and_unsupported_fields_are_enforced():
    content = payload()
    bad_hash = upload_for(content, payload_sha256="0" * 64)
    sync = service()
    await sync.start(bad_hash)
    for index, chunk in enumerate(encoded_chunks(content)):
        await sync.put_chunk(bad_hash.tenant_id, bad_hash.device_id, bad_hash.upload_id, index, *chunk)
    with pytest.raises(CensusError, match="size or digest"):
        await sync.complete(bad_hash.tenant_id, bad_hash.device_id, bad_hash.upload_id)

    extra = json.dumps({"business_name": "Shop", "unexpected": "deny"}).encode()
    other = upload_for(extra, upload_id="upload-2", event_id="01ARZ3NDEKTSV4RRFFQ69G5FAW")
    sync = service()
    await sync.start(other)
    for index, chunk in enumerate(encoded_chunks(extra)):
        await sync.put_chunk(other.tenant_id, other.device_id, other.upload_id, index, *chunk)
    with pytest.raises(CensusError, match="unsupported"):
        await sync.complete(other.tenant_id, other.device_id, other.upload_id)


@pytest.mark.asyncio
async def test_cross_tenant_and_cross_device_access_is_denied_before_and_after_publication():
    content = payload()
    upload = upload_for(content)
    sync = service()
    await sync.start(upload)
    with pytest.raises(CensusError):
        await sync.resume("tenant-b", upload.device_id, upload.upload_id)
    with pytest.raises(CensusError):
        await sync.resume(upload.tenant_id, "device-2", upload.upload_id)
    for index, chunk in enumerate(encoded_chunks(content)):
        await sync.put_chunk(upload.tenant_id, upload.device_id, upload.upload_id, index, *chunk)
    await sync.complete(upload.tenant_id, upload.device_id, upload.upload_id)
    with pytest.raises(CensusError):
        await sync.complete(upload.tenant_id, "device-2", upload.upload_id)
    with pytest.raises(CensusError):
        await sync.start(upload_for(content, device_id="device-2"))


@pytest.mark.asyncio
async def test_sink_failure_preserves_resumable_chunks():
    content = payload()
    upload = upload_for(content)
    sink = Sink(fail=True)
    sync = service(sink)
    await sync.start(upload)
    for index, chunk in enumerate(encoded_chunks(content)):
        await sync.put_chunk(upload.tenant_id, upload.device_id, upload.upload_id, index, *chunk)
    with pytest.raises(TimeoutError):
        await sync.complete(upload.tenant_id, upload.device_id, upload.upload_id)
    assert (await sync.resume(upload.tenant_id, upload.device_id, upload.upload_id))["resume"] == []
    assert len(sync.store.chunks) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"event_id": "not-a-ulid"},
        {"tenant_id": "../tenant"},
        {"total_chunks": 0},
        {"total_chunks": 65},
        {"total_bytes": 8 * 1024 * 1024 + 1},
        {"accuracy_m": 101},
        {"lawful_basis": "unknown"},
    ],
)
def test_upload_bounds(changes):
    with pytest.raises(CensusError):
        upload_for(payload(), **changes)


def test_chunk_and_phone_bounds():
    with pytest.raises(CensusError):
        decode_chunk("not base64!")
    with pytest.raises(CensusError):
        decode_chunk(base64.b64encode(b"x" * (256 * 1024 + 1)).decode())
    assert normalize_phone("+234 803 123 4567") == "+2348031234567"
    with pytest.raises(CensusError):
        normalize_phone("0201234567")
    with pytest.raises(ValueError):
        CensusSync(InMemoryCensusStore(), Sink(), b"short")


def test_postgres_jsonb_publication_comparison_uses_canonical_json():
    assert PostgresCensusStore._text('{"z":1,"a":"é"}') == '{"a":"é","z":1}'
    assert PostgresCensusStore._text({"z": 1, "a": "é"}) == '{"a":"é","z":1}'

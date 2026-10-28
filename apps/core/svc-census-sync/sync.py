"""Tenant/device-bound, resumable census publication through Fluvio.

The service stores chunks only while an upload is active. A completed upload emits
one canonical, duplicate-safe event and removes its temporary chunk payloads.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Protocol

ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_CHUNKS = 64
MAX_CHUNK_BYTES = 256 * 1024
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


class CensusError(ValueError):
    pass


@dataclass(frozen=True)
class Upload:
    tenant_id: str
    upload_id: str
    event_id: str
    device_id: str
    actor: str
    total_chunks: int
    total_bytes: int
    payload_sha256: str
    captured_at_unix: int
    latitude: float
    longitude: float
    accuracy_m: float
    consent_reference: str
    lawful_basis: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "upload_id", "device_id", "actor", "consent_reference"):
            if not SAFE_ID.fullmatch(getattr(self, name)):
                raise CensusError(f"{name} is not a safe bounded identifier")
        if not ULID.fullmatch(self.event_id):
            raise CensusError("event_id must be a canonical uppercase ULID")
        if not 1 <= self.total_chunks <= MAX_CHUNKS:
            raise CensusError("total_chunks exceeds the reviewed upload bound")
        if not 1 <= self.total_bytes <= MAX_UPLOAD_BYTES:
            raise CensusError("total_bytes exceeds the reviewed upload bound")
        if not SHA256.fullmatch(self.payload_sha256):
            raise CensusError("payload_sha256 must be a lowercase SHA-256 digest")
        if not 0 <= self.captured_at_unix <= 4_102_444_800:
            raise CensusError("captured_at_unix is outside the supported range")
        if not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180:
            raise CensusError("geotag coordinates are outside valid bounds")
        if not 0 < self.accuracy_m <= 100:
            raise CensusError("geotag accuracy must be positive and no more than 100 metres")
        if self.lawful_basis not in {"consent", "legitimate_interest", "contract", "legal_obligation"}:
            raise CensusError("a reviewed lawful basis is required")


@dataclass(frozen=True)
class Publication:
    tenant_id: str
    device_id: str
    upload_id: str
    event_id: str
    document_json: str


class CensusStore(Protocol):
    async def create(self, upload: Upload) -> bool: ...
    async def get(self, tenant_id: str, upload_id: str) -> Upload | Publication | None: ...
    async def chunk(self, tenant_id: str, upload_id: str, index: int) -> tuple[str, bytes] | None: ...
    async def put_chunk(self, tenant_id: str, upload_id: str, index: int, digest: str, data: bytes) -> None: ...
    async def chunk_indexes(self, tenant_id: str, upload_id: str) -> set[int]: ...
    async def publish(self, publication: Publication) -> None: ...


class CensusSink(Protocol):
    async def publish(self, topic: str, key: str, document_json: str) -> None: ...


def canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decode_chunk(encoded: str) -> bytes:
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise CensusError("chunk data is not valid base64") from exc
    if not 1 <= len(data) <= MAX_CHUNK_BYTES:
        raise CensusError("chunk exceeds the reviewed size bound")
    return data


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("0") and len(digits) == 11:
        digits = "234" + digits[1:]
    if not re.fullmatch(r"234[789][01]\d{8}", digits):
        raise CensusError("phone is not a supported Nigerian MSISDN")
    return "+" + digits


class CensusSync:
    def __init__(self, store: CensusStore, sink: CensusSink, masking_key: bytes, topic: str = "census.pings"):
        if len(masking_key) < 32:
            raise ValueError("census masking key must contain at least 32 bytes")
        if not SAFE_ID.fullmatch(topic):
            raise ValueError("invalid Fluvio census topic")
        self.store, self.sink, self.masking_key, self.topic = store, sink, masking_key, topic

    async def start(self, upload: Upload) -> dict[str, Any]:
        created = await self.store.create(upload)
        current = await self.store.get(upload.tenant_id, upload.upload_id)
        if current is None:
            raise CensusError("upload could not be durable")
        if isinstance(current, Publication):
            if (current.tenant_id, current.device_id, current.event_id) != (
                upload.tenant_id, upload.device_id, upload.event_id
            ):
                raise CensusError("upload ID was already used with different metadata")
            return self.status(current)
        if not created and current != upload:
            raise CensusError("upload ID was already used with different metadata")
        return self.status(current, await self.store.chunk_indexes(upload.tenant_id, upload.upload_id))

    def _upload(self, current: Upload | Publication | None, tenant_id: str, device_id: str) -> Upload:
        if not isinstance(current, Upload) or current.tenant_id != tenant_id or current.device_id != device_id:
            raise CensusError("upload is unavailable for this tenant/device or is already complete")
        return current

    async def put_chunk(self, tenant_id: str, device_id: str, upload_id: str,
                        index: int, encoded: str, expected_sha256: str) -> dict[str, Any]:
        current = self._upload(await self.store.get(tenant_id, upload_id), tenant_id, device_id)
        if not 0 <= index < current.total_chunks or not SHA256.fullmatch(expected_sha256):
            raise CensusError("chunk index or digest is invalid")
        data = decode_chunk(encoded)
        actual = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            raise CensusError("chunk digest does not match its content")
        existing = await self.store.chunk(tenant_id, upload_id, index)
        if existing is not None and existing != (expected_sha256, data):
            raise CensusError("chunk index was already used with different content")
        if existing is None:
            await self.store.put_chunk(tenant_id, upload_id, index, expected_sha256, data)
        indexes = await self.store.chunk_indexes(tenant_id, upload_id)
        return self.status(current, indexes)

    async def resume(self, tenant_id: str, device_id: str, upload_id: str) -> dict[str, Any]:
        current = await self.store.get(tenant_id, upload_id)
        if isinstance(current, Publication):
            if (current.tenant_id, current.device_id, current.upload_id) != (tenant_id, device_id, upload_id):
                raise CensusError("publication scope is invalid")
            return self.status(current)
        upload = self._upload(current, tenant_id, device_id)
        return self.status(upload, await self.store.chunk_indexes(tenant_id, upload_id))

    async def complete(self, tenant_id: str, device_id: str, upload_id: str) -> dict[str, Any]:
        current = await self.store.get(tenant_id, upload_id)
        if isinstance(current, Publication):
            if (current.tenant_id, current.device_id, current.upload_id) != (tenant_id, device_id, upload_id):
                raise CensusError("publication scope is invalid")
            return self.status(current)
        upload = self._upload(current, tenant_id, device_id)
        indexes = await self.store.chunk_indexes(tenant_id, upload_id)
        if indexes != set(range(upload.total_chunks)):
            raise CensusError("all chunks must be durable before completion")
        chunks = [await self.store.chunk(tenant_id, upload_id, index) for index in range(upload.total_chunks)]
        if any(chunk is None for chunk in chunks):
            raise CensusError("chunk set changed during completion")
        content = b"".join(chunk[1] for chunk in chunks if chunk is not None)
        if len(content) != upload.total_bytes or not hmac.compare_digest(
            hashlib.sha256(content).hexdigest(), upload.payload_sha256
        ):
            raise CensusError("assembled census payload fails its size or digest manifest")
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CensusError("census payload must be UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise CensusError("census payload must be an object")
        document = self._event(upload, payload)
        # A transient sink failure leaves the resumable chunks intact. If the process
        # fails after publication, event_id deduplication protects the downstream log.
        await self.sink.publish(self.topic, tenant_id, document)
        publication = Publication(upload.tenant_id, upload.device_id, upload.upload_id, upload.event_id, document)
        await self.store.publish(publication)
        return self.status(publication)

    def _event(self, upload: Upload, payload: dict[str, Any]) -> str:
        allowed = {"business_name", "niche", "address", "notes", "phone"}
        if set(payload) - allowed or not str(payload.get("business_name", "")).strip():
            raise CensusError("census payload fields are missing or unsupported")
        for key in allowed - {"phone"}:
            if key in payload and (not isinstance(payload[key], str) or len(payload[key]) > 1000):
                raise CensusError(f"{key} is invalid")
        phone_hash = None
        if payload.get("phone") is not None:
            normalized = normalize_phone(str(payload["phone"]))
            phone_hash = hmac.new(self.masking_key, normalized.encode(), hashlib.sha256).hexdigest()
        clean = {key: value.strip() for key, value in payload.items() if key != "phone" and isinstance(value, str)}
        clean.update({
            "business_name": str(payload["business_name"]).strip(),
            "latitude": upload.latitude, "longitude": upload.longitude,
            "accuracy_m": upload.accuracy_m, "consent_reference": upload.consent_reference,
            "lawful_basis": upload.lawful_basis, "device_id": upload.device_id,
        })
        if phone_hash:
            clean["phone_hash"] = phone_hash
        return canonical({
            "envelope": {
                "event_id": upload.event_id, "tenant_id": upload.tenant_id,
                "source": "census-sync", "schema": "volumeos.census.v1",
                "ts_unix": upload.captured_at_unix, "lineage_id": upload.upload_id,
            },
            "payload": clean,
        })

    @staticmethod
    def status(current: Upload | Publication, indexes: set[int] | None = None) -> dict[str, Any]:
        if isinstance(current, Publication):
            return {"upload_id": current.upload_id, "event_id": current.event_id, "status": "published", "resume": []}
        received = indexes or set()
        return {
            "upload_id": current.upload_id, "event_id": current.event_id,
            "status": "uploading", "received_chunks": len(received),
            "total_chunks": current.total_chunks,
            "resume": sorted(set(range(current.total_chunks)) - received),
        }


class InMemoryCensusStore:
    """Async test double; production uses PostgreSQL transactions and RLS."""

    def __init__(self):
        self.uploads: dict[tuple[str, str], Upload | Publication] = {}
        self.chunks: dict[tuple[str, str, int], tuple[str, bytes]] = {}

    async def ready(self) -> None:
        return None

    async def create(self, upload: Upload) -> bool:
        key = (upload.tenant_id, upload.upload_id)
        if key in self.uploads:
            return False
        self.uploads[key] = upload
        return True

    async def get(self, tenant_id: str, upload_id: str) -> Upload | Publication | None:
        return self.uploads.get((tenant_id, upload_id))

    async def chunk(self, tenant_id: str, upload_id: str, index: int):
        return self.chunks.get((tenant_id, upload_id, index))

    async def put_chunk(self, tenant_id: str, upload_id: str, index: int, digest: str, data: bytes) -> None:
        self.chunks[(tenant_id, upload_id, index)] = (digest, bytes(data))

    async def chunk_indexes(self, tenant_id: str, upload_id: str) -> set[int]:
        return {key[2] for key in self.chunks if key[:2] == (tenant_id, upload_id)}

    async def publish(self, publication: Publication) -> None:
        key = (publication.tenant_id, publication.upload_id)
        current = self.uploads.get(key)
        if isinstance(current, Publication):
            if current != publication:
                raise CensusError("upload was already published with different content")
            return
        if not isinstance(current, Upload) or current.event_id != publication.event_id:
            raise CensusError("publication does not match its active upload")
        self.uploads[key] = publication
        for chunk_key in [chunk_key for chunk_key in self.chunks if chunk_key[:2] == key]:
            del self.chunks[chunk_key]

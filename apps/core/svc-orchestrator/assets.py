"""Tenant-prefixed, manifest-bound Studio QA object access."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import PurePosixPath
from typing import Any

import boto3


class QAAssetError(Exception):
    """QA objects are missing, changed, or outside their tenant pack."""


IMAGE_SIGNATURES = (  # what a product photo upload may be, by its first bytes (not the claimed content type)
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
)
MAX_UPLOAD_BYTES = 10_000_000


def sniff_image(data: bytes) -> tuple[str, str] | None:
    for signature, extension, content_type in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return extension, content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


class S3QAAssets:
    def __init__(self, bucket: str, client: Any = None, expires_seconds: int = 300):
        if not bucket.strip():
            raise ValueError("ASSET_BUCKET is required")
        if expires_seconds < 60 or expires_seconds > 900:
            raise ValueError("QA URL expiry must be between 60 and 900 seconds")
        self.bucket = bucket
        self.expires_seconds = expires_seconds
        self.client = client or boto3.client("s3")

    async def put_upload(self, tenant_id: str, data: bytes) -> str:
        """Stores a product photo under the business's own uploads/ prefix; returns its key."""
        kind = sniff_image(data)
        if kind is None or not 0 < len(data) <= MAX_UPLOAD_BYTES:
            raise QAAssetError("Upload a PNG, JPEG or WebP photo of at most 10 MB.")
        extension, content_type = kind
        key = f"{tenant_id}/uploads/{hashlib.sha256(data).hexdigest()[:24]}.{extension}"
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                                Metadata={"tenant-id": tenant_id})
        return key

    async def validate_manifest(self, tenant_id: str, pack_id: str,
                                expected_sha256: str, expected_count: int) -> list[str]:
        return await asyncio.to_thread(
            self._validate_manifest, tenant_id, pack_id, expected_sha256, expected_count,
        )

    async def review_assets(self, tenant_id: str, pack_id: str,
                            expected_sha256: str, expected_count: int) -> list[dict[str, Any]]:
        """The QA images, as this API's own URLs: the storage server is internal, so the browser can't use signed
        storage links. Each URL is fetched with the user's token (read_qa_image re-checks everything)."""
        keys = await self.validate_manifest(tenant_id, pack_id, expected_sha256, expected_count)
        return [{"id": PurePosixPath(key).name, "url": f"/v1/studio/packs/{pack_id}/assets/{PurePosixPath(key).name}"} for key in keys]

    async def read_qa_image(self, tenant_id: str, pack_id: str, expected_sha256: str, expected_count: int, name: str) -> bytes:
        keys = await self.validate_manifest(tenant_id, pack_id, expected_sha256, expected_count)
        key = next((k for k in keys if PurePosixPath(k).name == name), None)
        if key is None:
            raise QAAssetError("That image isn't part of this pack's QA set")
        head = await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
        if head.get("ContentType") != "image/png" or not 0 < head.get("ContentLength", 0) <= 25_000_000:
            raise QAAssetError("QA asset has an invalid type or size")
        return await asyncio.to_thread(self._read, key, 25_000_000)

    async def delivered(self, tenant_id: str, pack_id: str) -> list[str]:
        """The delivered files of an approved pack (paths under its delivered/ folder)."""
        root = f"{tenant_id}/packs/{pack_id}/delivered/"
        raw = await asyncio.to_thread(self._read, root + "manifest.json", 1_048_576)
        try:
            manifest = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QAAssetError("Delivery manifest is invalid") from exc
        assets = manifest.get("assets") if isinstance(manifest, dict) else None
        if (not isinstance(assets, list) or manifest.get("tenant_id") != tenant_id or manifest.get("pack_id") != pack_id
                or not all(isinstance(a, str) and a.startswith(root) and ".." not in PurePosixPath(a).parts for a in assets)):
            raise QAAssetError("Delivery manifest doesn't match this pack")
        return [a[len(root):] for a in assets]

    async def read_delivered(self, tenant_id: str, pack_id: str, path: str) -> bytes:
        if path not in await self.delivered(tenant_id, pack_id):
            raise QAAssetError("That file isn't part of this delivered pack")
        return await asyncio.to_thread(self._read, f"{tenant_id}/packs/{pack_id}/delivered/{path}", 25_000_000)

    def _read(self, key: str, limit: int) -> bytes:
        try:
            raw = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read(limit + 1)
        except Exception as exc:
            raise QAAssetError("That file is unavailable") from exc
        if len(raw) > limit:
            raise QAAssetError("That file is too large")
        return raw

    def _validate_manifest(self, tenant_id: str, pack_id: str,
                           expected_sha256: str, expected_count: int) -> list[str]:
        prefix = f"{tenant_id}/packs/{pack_id}/qa"
        manifest_key = f"{prefix}/manifest.json"
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=manifest_key)
            raw = response["Body"].read(1_048_577)
        except Exception as exc:
            raise QAAssetError("QA manifest is unavailable") from exc
        if len(raw) > 1_048_576:
            raise QAAssetError("QA manifest exceeds its size limit")
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise QAAssetError("QA manifest changed after workflow capture")
        try:
            manifest = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QAAssetError("QA manifest is invalid") from exc
        if not isinstance(manifest, dict):
            raise QAAssetError("QA manifest must be an object")
        keys = manifest.get("assets")
        if (manifest.get("tenant_id") != tenant_id or manifest.get("pack_id") != pack_id
                or manifest.get("qa_status") != "pending" or not isinstance(keys, list)
                or not all(isinstance(key, str) for key in keys)
                or len(keys) != expected_count or len(set(keys)) != len(keys)):
            raise QAAssetError("QA manifest does not match the workflow")
        expected_root = prefix + "/"
        for key in keys:
            path = PurePosixPath(key) if isinstance(key, str) else None
            if (path is None or not key.startswith(expected_root) or ".." in path.parts
                    or path.suffix != ".png" or path.parent != PurePosixPath(prefix)):
                raise QAAssetError("QA asset is outside the current tenant pack")
        return keys

    def _presign(self, keys: list[str]) -> list[dict[str, Any]]:
        assets = []
        for key in keys:
            try:
                head = self.client.head_object(Bucket=self.bucket, Key=key)
                if head.get("ContentType") != "image/png" or not 0 < head.get("ContentLength", 0) <= 25_000_000:
                    raise QAAssetError("QA asset has an invalid type or size")
                url = self.client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key, "ResponseCacheControl": "no-store"},
                    ExpiresIn=self.expires_seconds,
                )
            except QAAssetError:
                raise
            except Exception as exc:
                raise QAAssetError("QA asset is unavailable") from exc
            assets.append({"id": PurePosixPath(key).name, "url": url, "expires_in": self.expires_seconds})
        return assets

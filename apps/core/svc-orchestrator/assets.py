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


class S3QAAssets:
    def __init__(self, bucket: str, client: Any = None, expires_seconds: int = 300):
        if not bucket.strip():
            raise ValueError("ASSET_BUCKET is required")
        if expires_seconds < 60 or expires_seconds > 900:
            raise ValueError("QA URL expiry must be between 60 and 900 seconds")
        self.bucket = bucket
        self.expires_seconds = expires_seconds
        self.client = client or boto3.client("s3")

    async def validate_manifest(self, tenant_id: str, pack_id: str,
                                expected_sha256: str, expected_count: int) -> list[str]:
        return await asyncio.to_thread(
            self._validate_manifest, tenant_id, pack_id, expected_sha256, expected_count,
        )

    async def review_assets(self, tenant_id: str, pack_id: str,
                            expected_sha256: str, expected_count: int) -> list[dict[str, Any]]:
        keys = await self.validate_manifest(tenant_id, pack_id, expected_sha256, expected_count)
        return await asyncio.to_thread(self._presign, keys)

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

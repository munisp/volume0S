import hashlib
import io
import json

import pytest

from assets import QAAssetError, S3QAAssets


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.metadata = {}
        self.signed = []

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        return self.metadata[Key]

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.signed.append((operation, Params, ExpiresIn))
        return f"https://objects.example/{Params['Key']}?signature=short-lived"


def manifest(tenant="tenant-a", pack="pack-1", keys=None, status="pending"):
    keys = keys if keys is not None else [f"{tenant}/packs/{pack}/qa/asset-01.png"]
    return json.dumps({"tenant_id": tenant, "pack_id": pack, "workflow_version": "shopfront-img2img-v1",
                       "assets": keys, "qa_status": status}, indent=2).encode()


def configured_store(raw=None):
    store = MemoryS3()
    raw = raw if raw is not None else manifest()
    key = "tenant-a/packs/pack-1/qa/manifest.json"
    asset = "tenant-a/packs/pack-1/qa/asset-01.png"
    store.objects[key] = raw
    store.objects[asset] = b"png-data"
    store.metadata[asset] = {"ContentType": "image/png", "ContentLength": 8}
    return store, hashlib.sha256(raw).hexdigest()


async def test_qa_assets_require_exact_manifest_and_return_short_lived_urls():
    store, digest = configured_store()
    qa = S3QAAssets("assets", store, expires_seconds=300)
    assets = await qa.review_assets("tenant-a", "pack-1", digest, 1)
    assert assets[0]["id"] == "asset-01.png"
    assert assets[0]["expires_in"] == 300
    assert store.signed[0][1]["ResponseCacheControl"] == "no-store"
    assert store.signed[0][2] == 300


@pytest.mark.parametrize("change", [
    lambda raw: raw + b"\n",
    lambda _raw: manifest(tenant="tenant-b"),
    lambda _raw: manifest(keys=["tenant-b/packs/pack-1/qa/asset-01.png"]),
    lambda _raw: manifest(keys=["tenant-a/packs/pack-1/qa/../asset-01.png"]),
    lambda _raw: manifest(keys=[["not-an-object-key"]]),
    lambda _raw: manifest(keys=[{"key": "not-an-object-key"}]),
    lambda _raw: manifest(status="approved"),
])
async def test_qa_manifest_tampering_and_scope_changes_fail_closed(change):
    raw = manifest()
    reviewed_digest = hashlib.sha256(raw).hexdigest()
    store, _ = configured_store(change(raw))
    qa = S3QAAssets("assets", store)
    with pytest.raises(QAAssetError):
        await qa.review_assets("tenant-a", "pack-1", reviewed_digest, 1)
    assert store.signed == []


async def test_qa_asset_type_and_size_are_checked_before_signing():
    store, digest = configured_store()
    store.metadata["tenant-a/packs/pack-1/qa/asset-01.png"] = {"ContentType": "text/html", "ContentLength": 8}
    qa = S3QAAssets("assets", store)
    with pytest.raises(QAAssetError):
        await qa.review_assets("tenant-a", "pack-1", digest, 1)
    assert store.signed == []


def test_qa_url_expiry_is_bounded():
    with pytest.raises(ValueError):
        S3QAAssets("assets", MemoryS3(), expires_seconds=3600)

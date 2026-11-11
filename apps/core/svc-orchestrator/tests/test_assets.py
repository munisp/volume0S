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


async def test_qa_assets_are_served_through_this_api_never_as_storage_links():
    # The storage server is internal (http://minio...:9000): a signed link to it can't load in the browser.
    store, digest = configured_store()
    qa = S3QAAssets("assets", store, expires_seconds=300)
    assets = await qa.review_assets("tenant-a", "pack-1", digest, 1)
    assert assets == [{"id": "asset-01.png", "url": "/v1/studio/packs/pack-1/assets/asset-01.png"}]
    assert await qa.read_qa_image("tenant-a", "pack-1", digest, 1, "asset-01.png") == b"png-data"
    with pytest.raises(QAAssetError):
        await qa.read_qa_image("tenant-a", "pack-1", digest, 1, "asset-02.png")
    assert store.signed == []


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


async def test_qa_asset_type_and_size_are_checked_before_serving():
    store, digest = configured_store()
    store.metadata["tenant-a/packs/pack-1/qa/asset-01.png"] = {"ContentType": "text/html", "ContentLength": 8}
    qa = S3QAAssets("assets", store)
    with pytest.raises(QAAssetError):
        await qa.read_qa_image("tenant-a", "pack-1", digest, 1, "asset-01.png")


async def test_delivered_files_come_only_from_the_pack_delivery_manifest():
    store = MemoryS3()
    root = "tenant-a/packs/pack-1/delivered/"
    store.objects[root + "manifest.json"] = json.dumps({"tenant_id": "tenant-a", "pack_id": "pack-1",
                                                         "assets": [root + "whatsapp/asset-01.jpg"]}).encode()
    store.objects[root + "whatsapp/asset-01.jpg"] = b"jpeg"
    qa = S3QAAssets("assets", store)
    assert await qa.delivered("tenant-a", "pack-1") == ["whatsapp/asset-01.jpg"]
    assert await qa.read_delivered("tenant-a", "pack-1", "whatsapp/asset-01.jpg") == b"jpeg"
    with pytest.raises(QAAssetError):
        await qa.read_delivered("tenant-a", "pack-1", "jumia/asset-01.jpg")
    store.objects[root + "manifest.json"] = json.dumps({"tenant_id": "tenant-a", "pack_id": "pack-1",
                                                         "assets": ["tenant-b/packs/x/delivered/a.jpg"]}).encode()
    with pytest.raises(QAAssetError):
        await qa.delivered("tenant-a", "pack-1")


def test_qa_url_expiry_is_bounded():
    with pytest.raises(ValueError):
        S3QAAssets("assets", MemoryS3(), expires_seconds=3600)

import io
import json

import pytest
from PIL import Image
from temporalio.exceptions import ApplicationError

from studio_main import (
    PACK_IMAGES,
    QAReview,
    S3AssetStore,
    StudioInput,
    deliver_studio_pack,
    delivered_prefix,
    delivery_variants,
    qa_prefix,
    record_studio_rejection,
    render_workflow,
    store_qa_assets,
    validate_studio,
)


def value(**changes):
    base = dict(
        tenant_id="tenant-1", pack_id="pack-1", product_id="product-1", tier="growth",
        source_image="tenant-1/product.png", checkpoint="product-photography.safetensors",
        materials="cotton", dimensions="30 x 20 cm", price_text="NGN 15000",
    )
    base.update(changes)
    return StudioInput(**base)


def test_versioned_graph_is_valid_and_carries_pack_entitlements():
    graph = render_workflow(value())
    assert graph["4"]["inputs"]["ckpt_name"] == "product-photography.safetensors"
    assert graph["10"]["inputs"]["image"] == "tenant-1/product.png"
    assert graph["12"]["inputs"]["amount"] == 7
    assert graph["12"]["inputs"]["samples"] == ["11", 0]
    assert graph["9"]["inputs"]["filename_prefix"] == "volumeos/tenant-1/pack-1"
    assert PACK_IMAGES == {"starter": 5, "growth": 7, "pro": 12}


def test_graph_text_cannot_inject_json_nodes():
    graph = render_workflow(value(materials='cotton", "unexpected": {"class_type":"Unresolved'))
    assert graph["6"]["class_type"] == "CLIPTextEncode"
    assert len(graph) == 9


@pytest.mark.parametrize("changes", [
    {"tenant_id": "../other"}, {"pack_id": "bad pack"}, {"tier": "free"},
    {"source_image": ""}, {"checkpoint": ""}, {"materials": "x" * 501},
])
def test_invalid_studio_job_fails_before_gpu_submission(changes):
    with pytest.raises(ApplicationError):
        validate_studio(value(**changes))


def test_delivery_formats_are_centered_jpegs():
    source = io.BytesIO()
    Image.new("RGBA", (200, 100), "red").save(source, format="PNG")
    variants = delivery_variants(source.getvalue())
    assert set(variants) == {"whatsapp", "jumia"}
    for name, size in (("whatsapp", 1080), ("jumia", 1000)):
        with Image.open(io.BytesIO(variants[name])) as image:
            assert image.format == "JPEG"
            assert image.size == (size, size)
            assert image.getpixel((0, 0)) == (255, 255, 255)


class MemoryStore:
    def __init__(self):
        self.objects = {}

    def put(self, key, data, content_type):
        self.objects[key] = (data, content_type)

    def get(self, key):
        return self.objects[key][0]


async def test_delivery_requires_human_approval(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr("studio_main.asset_store", lambda: store)
    key = f"{qa_prefix(value())}/asset-01.png"
    source = io.BytesIO()
    Image.new("RGB", (100, 100), "blue").save(source, format="PNG")
    store.put(key, source.getvalue(), "image/png")
    with pytest.raises(ApplicationError):
        await deliver_studio_pack(value(), [key], QAReview("reject", "reviewer", "wrong label"))
    with pytest.raises(ApplicationError):
        await deliver_studio_pack(value(), ["tenant-2/packs/pack-1/qa/asset.png"], QAReview("approve", "reviewer", ""))
    assert store.objects.keys() == {key}


async def test_approved_delivery_has_only_tenant_scoped_formats(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr("studio_main.asset_store", lambda: store)
    monkeypatch.setattr("studio_main.activity.heartbeat", lambda: None)
    key = f"{qa_prefix(value())}/asset-01.png"
    source = io.BytesIO()
    Image.new("RGB", (100, 100), "blue").save(source, format="PNG")
    store.put(key, source.getvalue(), "image/png")
    delivered = await deliver_studio_pack(value(), [key], QAReview("approve", "qa-1", ""))
    assert len(delivered) == 2
    assert all(key.startswith(delivered_prefix(value()) + "/") for key in delivered)
    manifest = json.loads(store.get(f"{delivered_prefix(value())}/manifest.json"))
    assert manifest["approved_by"] == "qa-1"
    assert manifest["assets"] == delivered


async def test_rejection_records_reason_without_delivery(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr("studio_main.asset_store", lambda: store)
    await record_studio_rejection(value(), QAReview("reject", "qa-1", "product label changed"))
    rejection = json.loads(store.get("tenant-1/packs/pack-1/rejection.json"))
    assert rejection["reason"] == "product label changed"
    assert not any("/delivered/" in key for key in store.objects)
    with pytest.raises(ApplicationError):
        await record_studio_rejection(value(), QAReview("reject", "qa-1", ""))


async def test_output_count_must_match_purchased_pack():
    with pytest.raises(ApplicationError):
        await store_qa_assets(value(tier="starter"), [{"filename": "only-one.png", "subfolder": "", "type": "output"}])


def test_s3_store_requires_bucket():
    with pytest.raises(ValueError):
        S3AssetStore(" ")

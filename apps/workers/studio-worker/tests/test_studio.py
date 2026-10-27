import hashlib
import io
import json
from datetime import datetime, timezone

import pytest
from PIL import Image
from temporalio.exceptions import ApplicationError

import studio_main as studio
from studio_main import (
    PACK_IMAGES,
    QAReview,
    S3AssetStore,
    StudioInput,
    StudioResult,
    StudioWorkflow,
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
        await deliver_studio_pack(value(), [key], QAReview("reject", "reviewer", "wrong label", "0" * 64))
    with pytest.raises(ApplicationError):
        await deliver_studio_pack(value(), ["tenant-2/packs/pack-1/qa/asset.png"], QAReview("approve", "reviewer", "", "0" * 64))
    assert store.objects.keys() == {key}


async def test_approved_delivery_has_only_tenant_scoped_formats(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr("studio_main.asset_store", lambda: store)
    monkeypatch.setattr("studio_main.activity.heartbeat", lambda: None)
    key = f"{qa_prefix(value())}/asset-01.png"
    source = io.BytesIO()
    Image.new("RGB", (100, 100), "blue").save(source, format="PNG")
    store.put(key, source.getvalue(), "image/png")
    manifest = json.dumps({"tenant_id": "tenant-1", "pack_id": "pack-1",
                           "workflow_version": studio.WORKFLOW_VERSION, "assets": [key], "qa_status": "pending"}, indent=2).encode()
    store.put(f"{qa_prefix(value())}/manifest.json", manifest, "application/json")
    manifest_sha256 = hashlib.sha256(manifest).hexdigest()
    delivered = await deliver_studio_pack(value(), [key], QAReview("approve", "qa-1", "", manifest_sha256))
    assert len(delivered) == 2
    assert all(key.startswith(delivered_prefix(value()) + "/") for key in delivered)
    manifest = json.loads(store.get(f"{delivered_prefix(value())}/manifest.json"))
    assert manifest["approved_by"] == "qa-1"
    assert manifest["assets"] == delivered


async def test_rejection_records_reason_without_delivery(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr("studio_main.asset_store", lambda: store)
    await record_studio_rejection(value(), QAReview("reject", "qa-1", "product label changed", "0" * 64))
    rejection = json.loads(store.get("tenant-1/packs/pack-1/rejection.json"))
    assert rejection["reason"] == "product label changed"
    assert not any("/delivered/" in key for key in store.objects)
    with pytest.raises(ApplicationError):
        await record_studio_rejection(value(), QAReview("reject", "qa-1", "", "0" * 64))


async def test_output_count_must_match_purchased_pack():
    with pytest.raises(ApplicationError):
        await store_qa_assets(value(tier="starter"), [{"filename": "only-one.png", "subfolder": "", "type": "output"}])


def test_s3_store_requires_bucket():
    with pytest.raises(ValueError):
        S3AssetStore(" ")


async def test_successful_delivery_survives_outcome_metric_failure(monkeypatch):
    expected = StudioResult("pack-1", "delivered", ["tenant-1/packs/pack-1/delivered/asset.jpg"])

    class Logger:
        def warning(self, *_args):
            pass

    async def execute(_value):
        return expected

    async def execute_activity(*_args, **_kwargs):
        raise RuntimeError("metrics unavailable")

    monkeypatch.setattr(studio.workflow, "logger", Logger())
    monkeypatch.setattr(studio.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(studio.workflow, "execute_activity", execute_activity)
    instance = StudioWorkflow()
    monkeypatch.setattr(instance, "_execute", execute)

    assert await instance.run(value()) is expected


async def test_failure_metric_error_does_not_mask_business_failure(monkeypatch):
    class Logger:
        def warning(self, *_args):
            pass

    async def execute(_value):
        raise RuntimeError("business failure")

    async def execute_activity(*_args, **_kwargs):
        raise RuntimeError("metrics unavailable")

    monkeypatch.setattr(studio.workflow, "logger", Logger())
    monkeypatch.setattr(studio.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(studio.workflow, "execute_activity", execute_activity)
    instance = StudioWorkflow()
    monkeypatch.setattr(instance, "_execute", execute)

    with pytest.raises(RuntimeError, match="business failure"):
        await instance.run(value())


async def test_delivery_fails_closed_when_reviewed_manifest_changes(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr("studio_main.asset_store", lambda: store)
    key = f"{qa_prefix(value())}/asset-01.png"
    source = io.BytesIO()
    Image.new("RGB", (100, 100), "blue").save(source, format="PNG")
    store.put(key, source.getvalue(), "image/png")
    manifest = json.dumps({"tenant_id": "tenant-1", "pack_id": "pack-1",
                           "workflow_version": studio.WORKFLOW_VERSION, "assets": [key], "qa_status": "pending"}, indent=2).encode()
    reviewed_sha256 = hashlib.sha256(manifest).hexdigest()
    store.put(f"{qa_prefix(value())}/manifest.json", manifest + b"\n", "application/json")

    with pytest.raises(ApplicationError, match="manifest changed"):
        await deliver_studio_pack(value(), [key], QAReview("approve", "qa-1", "", reviewed_sha256))
    assert not any("/delivered/" in key for key in store.objects)


def test_studio_query_and_review_are_bound_to_current_qa_manifest(monkeypatch):
    monkeypatch.setattr(studio.workflow, "now", lambda: datetime.now(timezone.utc))
    instance = StudioWorkflow()
    instance._value = value()
    instance._status = "awaiting_qa"
    instance._asset_count = 7
    instance._manifest_sha256 = "a" * 64

    instance.review(QAReview("approve", "qa-1", "", "b" * 64))
    assert instance._review is None

    instance.review(QAReview("approve", "qa-1", "", "a" * 64))
    instance.review(QAReview("reject", "qa-2", "changed decision", "a" * 64))
    snapshot = instance.status()

    assert instance._review == QAReview("approve", "qa-1", "", "a" * 64)
    assert snapshot.status == "approved"
    assert snapshot.assets == 7
    assert snapshot.manifest_sha256 == "a" * 64

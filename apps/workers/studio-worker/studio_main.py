"""Shopfront Studio M1: ComfyUI execution with mandatory human QA before delivery."""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

import boto3
import httpx
from PIL import Image
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
PACK_IMAGES = {"starter": 5, "growth": 7, "pro": 12}
WORKFLOW_VERSION = "shopfront-img2img-v1"


@dataclass
class StudioInput:
    tenant_id: str
    pack_id: str
    product_id: str
    tier: str
    source_image: str
    checkpoint: str
    materials: str
    dimensions: str
    price_text: str


@dataclass
class QAReview:
    decision: str
    reviewer: str
    reason: str


@dataclass
class StudioResult:
    pack_id: str
    status: str
    assets: list[str]


def validate_studio(value: StudioInput) -> None:
    for field in (value.tenant_id, value.pack_id, value.product_id):
        if not SAFE_ID.fullmatch(field):
            raise ApplicationError("invalid tenant, pack, or product identifier", non_retryable=True)
    if value.tier not in PACK_IMAGES:
        raise ApplicationError("tier must be starter, growth, or pro", non_retryable=True)
    if not value.source_image.strip() or not value.checkpoint.strip():
        raise ApplicationError("source_image and checkpoint are required", non_retryable=True)
    if len(value.materials) > 500 or len(value.dimensions) > 200 or len(value.price_text) > 100:
        raise ApplicationError("product metadata exceeds its limit", non_retryable=True)


def render_workflow(value: StudioInput) -> dict[str, Any]:
    validate_studio(value)
    path = Path(__file__).resolve().parent / "workflows" / "shopfront-img2img-v1.json"
    template = path.read_text()
    replacements = {
        "{{checkpoint}}": value.checkpoint,
        "{{source_image}}": value.source_image,
        "{{tenant_id}}": value.tenant_id,
        "{{pack_id}}": value.pack_id,
        "{{materials}}": value.materials,
        "{{dimensions}}": value.dimensions,
        "{{price_text}}": value.price_text,
        "{{image_count}}": str(PACK_IMAGES[value.tier]),
    }
    for key, replacement in replacements.items():
        # JSON encoding escapes user-controlled text without allowing graph injection.
        template = template.replace(key, json.dumps(replacement)[1:-1])
    graph = json.loads(template)
    if not graph or any(node.get("class_type") == "Unresolved" for node in graph.values()):
        raise ApplicationError("invalid ComfyUI workflow graph", non_retryable=True)
    return graph


def delivery_variants(source: bytes) -> dict[str, bytes]:
    """Create 1080px WhatsApp and 1000px white-background Jumia JPEGs."""
    with Image.open(io.BytesIO(source)) as opened:
        image = opened.convert("RGB")
        variants = {}
        for name, size in (("whatsapp", 1080), ("jumia", 1000)):
            copy = image.copy()
            copy.thumbnail((size, size), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (size, size), "white")
            canvas.paste(copy, ((size - copy.width) // 2, (size - copy.height) // 2))
            output = io.BytesIO()
            canvas.save(output, format="JPEG", quality=90, optimize=True)
            variants[name] = output.getvalue()
        return variants


class AssetStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...


class S3AssetStore:
    def __init__(self, bucket: str, client: Any = None):
        if not bucket.strip():
            raise ValueError("asset bucket is required")
        self.bucket = bucket
        self.client = client or boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"))

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                               Metadata={"tenant-id": key.split("/", 1)[0]})

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()


def asset_store() -> AssetStore:
    bucket = os.environ.get("ASSET_BUCKET", "assets")
    return S3AssetStore(bucket)


def qa_prefix(value: StudioInput) -> str:
    return f"{value.tenant_id}/packs/{value.pack_id}/qa"


def delivered_prefix(value: StudioInput) -> str:
    return f"{value.tenant_id}/packs/{value.pack_id}/delivered"


async def comfy_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    base = os.environ.get("COMFYUI_URL", "").rstrip("/")
    if not base:
        raise ApplicationError("COMFYUI_URL is not configured", non_retryable=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.request(method, base + path, **kwargs)
        response.raise_for_status()
        return response


@activity.defn
async def submit_studio_pack(value: StudioInput) -> str:
    graph = render_workflow(value)
    activity.heartbeat()
    response = await comfy_request("POST", "/prompt", headers={
        "Idempotency-Key": f"studio:{value.tenant_id}:{value.pack_id}",
        "X-VolumeOS-Tenant": value.tenant_id,
    }, json={"prompt": graph, "client_id": f"volumeos-{value.pack_id}"})
    prompt_id = str(response.json().get("prompt_id", ""))
    if not prompt_id:
        raise ApplicationError("ComfyUI response is missing prompt_id")
    return prompt_id


@activity.defn
async def wait_studio_pack(value: StudioInput, prompt_id: str) -> list[dict[str, str]]:
    deadline = asyncio.get_running_loop().time() + 600
    while asyncio.get_running_loop().time() < deadline:
        activity.heartbeat()
        response = await comfy_request("GET", f"/history/{prompt_id}")
        history = response.json()
        if prompt_id in history:
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise ApplicationError("ComfyUI workflow failed", non_retryable=True)
            images = []
            for node in entry.get("outputs", {}).values():
                for image in node.get("images", []):
                    images.append({key: str(image.get(key, "")) for key in ("filename", "subfolder", "type")})
            if not images:
                raise ApplicationError("ComfyUI completed without images")
            return images
        await asyncio.sleep(2)
    raise ApplicationError("ComfyUI polling timed out")


@activity.defn
async def store_qa_assets(value: StudioInput, images: list[dict[str, str]]) -> list[str]:
    if len(images) != PACK_IMAGES[value.tier]:
        raise ApplicationError("ComfyUI output count does not match the purchased pack", non_retryable=True)
    store = asset_store()
    keys = []
    for index, image in enumerate(images, start=1):
        activity.heartbeat()
        if not image["filename"]:
            raise ApplicationError("ComfyUI image filename is empty")
        response = await comfy_request("GET", "/view", params=image)
        key = f"{qa_prefix(value)}/asset-{index:02d}.png"
        await asyncio.to_thread(store.put, key, response.content, "image/png")
        keys.append(key)
    manifest = json.dumps({"tenant_id": value.tenant_id, "pack_id": value.pack_id,
                           "workflow_version": WORKFLOW_VERSION, "assets": keys, "qa_status": "pending"}, indent=2).encode()
    await asyncio.to_thread(store.put, f"{qa_prefix(value)}/manifest.json", manifest, "application/json")
    return keys


@activity.defn
async def record_studio_rejection(value: StudioInput, review: QAReview) -> None:
    if review.decision != "reject" or not review.reason.strip() or not review.reviewer.strip():
        raise ApplicationError("a rejection reason and reviewer are required", non_retryable=True)
    payload = json.dumps({"pack_id": value.pack_id, "reviewer": review.reviewer,
                          "reason": review.reason, "workflow_version": WORKFLOW_VERSION}, indent=2).encode()
    await asyncio.to_thread(asset_store().put, f"{value.tenant_id}/packs/{value.pack_id}/rejection.json",
                            payload, "application/json")


@activity.defn
async def deliver_studio_pack(value: StudioInput, qa_keys: list[str], review: QAReview) -> list[str]:
    if review.decision != "approve" or not review.reviewer.strip():
        raise ApplicationError("human approval is required before delivery", non_retryable=True)
    expected_root = qa_prefix(value) + "/"
    if not qa_keys or any(not key.startswith(expected_root) for key in qa_keys):
        raise ApplicationError("QA assets are outside this tenant pack", non_retryable=True)
    store = asset_store()
    delivered = []
    for index, key in enumerate(qa_keys, start=1):
        activity.heartbeat()
        source = await asyncio.to_thread(store.get, key)
        for channel, data in delivery_variants(source).items():
            output = f"{delivered_prefix(value)}/{channel}/asset-{index:02d}.jpg"
            await asyncio.to_thread(store.put, output, data, "image/jpeg")
            delivered.append(output)
    manifest = json.dumps({"tenant_id": value.tenant_id, "pack_id": value.pack_id,
                           "approved_by": review.reviewer, "workflow_version": WORKFLOW_VERSION,
                           "assets": delivered, "formats": {"whatsapp": "1080x1080 JPEG", "jumia": "1000x1000 JPEG"}},
                          indent=2).encode()
    await asyncio.to_thread(store.put, f"{delivered_prefix(value)}/manifest.json", manifest, "application/json")
    return delivered


@workflow.defn
class StudioWorkflow:
    def __init__(self) -> None:
        self._review: QAReview | None = None

    @workflow.signal
    def review(self, review: QAReview) -> None:
        if review.decision not in {"approve", "reject"} or not review.reviewer.strip():
            raise ApplicationError("decision and reviewer are required")
        if review.decision == "reject" and not review.reason.strip():
            raise ApplicationError("rejection reason is required")
        self._review = review

    @workflow.run
    async def run(self, value: StudioInput) -> StudioResult:
        validate_studio(value)
        options = {
            "start_to_close_timeout": timedelta(minutes=12),
            "heartbeat_timeout": timedelta(seconds=30),
            "retry_policy": RetryPolicy(maximum_attempts=3),
        }
        prompt_id = await workflow.execute_activity(submit_studio_pack, value, **options)
        images = await workflow.execute_activity(wait_studio_pack, args=[value, prompt_id], **options)
        qa_keys = await workflow.execute_activity(store_qa_assets, args=[value, images], **options)
        try:
            await workflow.wait_condition(lambda: self._review is not None, timeout=timedelta(hours=24))
        except asyncio.TimeoutError:
            return StudioResult(value.pack_id, "qa_timed_out", [])
        review = self._review
        assert review is not None
        if review.decision == "reject":
            await workflow.execute_activity(record_studio_rejection, args=[value, review], **options)
            return StudioResult(value.pack_id, "rejected", [])
        assets = await workflow.execute_activity(deliver_studio_pack, args=[value, qa_keys, review], **options)
        return StudioResult(value.pack_id, "delivered", assets)


async def main() -> None:
    client = await Client.connect(
        os.environ.get("TEMPORAL_HOSTPORT", "temporal-frontend.ops.svc.cluster.local:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "volumeos"),
    )
    worker = Worker(client, task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "studio-v1"),
                    workflows=[StudioWorkflow],
                    activities=[submit_studio_pack, wait_studio_pack, store_qa_assets,
                                record_studio_rejection, deliver_studio_pack])
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

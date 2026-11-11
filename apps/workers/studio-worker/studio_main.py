"""Shopfront Studio M1: ComfyUI execution with mandatory human QA before delivery."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import concurrent.futures

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

# Temporal re-imports this module in its workflow sandbox; these are used only by activities and can't be
# re-imported there (the worker would refuse to start: "Failed validating workflow StudioWorkflow").
with workflow.unsafe.imports_passed_through():
    import boto3
    import httpx
    from PIL import Image

    import metrics

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
PACK_IMAGES = {"starter": 5, "growth": 7, "pro": 12}
# shopfront-img2img-v1: SD-class checkpoint, 28 steps (GPU). shopfront-img2img-lcm-v1: SD 1.5 + LCM-LoRA, 6 steps,
# ~512 px working size, for CPU-only ComfyUI. Chosen per deployment with STUDIO_WORKFLOW.
WORKFLOW_VERSION = os.environ.get("STUDIO_WORKFLOW", "shopfront-img2img-v1")
# How long one pack may take to generate: minutes on a GPU, much longer on CPU.
GENERATION_SECONDS = int(os.environ.get("STUDIO_GENERATION_SECONDS", "3600"))


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
    style: str = ""  # optional art direction from the operator ("on a wooden table, warm morning light")


@dataclass
class QAReview:
    decision: str
    reviewer: str
    reason: str
    manifest_sha256: str


@dataclass(frozen=True)
class QAManifest:
    assets: list[str]
    manifest_sha256: str


@dataclass
class StudioResult:
    pack_id: str
    status: str
    assets: list[str]


@dataclass(frozen=True)
class StudioStatus:
    tenant_id: str
    pack_id: str
    product_id: str
    tier: str
    status: str
    assets: int
    manifest_sha256: str
    started_at: str
    updated_at: str


def validate_studio(value: StudioInput) -> None:
    for field in (value.tenant_id, value.pack_id, value.product_id):
        if not SAFE_ID.fullmatch(field):
            raise ApplicationError("invalid tenant, pack, or product identifier", non_retryable=True)
    if value.tier not in PACK_IMAGES:
        raise ApplicationError("tier must be starter, growth, or pro", non_retryable=True)
    if not value.source_image.strip() or not value.checkpoint.strip():
        raise ApplicationError("source_image and checkpoint are required", non_retryable=True)
    if not value.source_image.startswith(f"{value.tenant_id}/uploads/"):
        raise ApplicationError("the product photo must be one this business uploaded", non_retryable=True)
    if len(value.materials) > 500 or len(value.dimensions) > 200 or len(value.price_text) > 100 or len(value.style) > 300:
        raise ApplicationError("product metadata exceeds its limit", non_retryable=True)


def render_workflow(value: StudioInput, comfy_image: str = "") -> dict[str, Any]:
    validate_studio(value)
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", WORKFLOW_VERSION) or ".." in WORKFLOW_VERSION:
        raise ApplicationError("invalid STUDIO_WORKFLOW", non_retryable=True)
    path = Path(__file__).resolve().parent / "workflows" / f"{WORKFLOW_VERSION}.json"
    template = path.read_text()
    replacements = {
        "{{checkpoint}}": value.checkpoint,
        # The name ComfyUI gave the uploaded photo (see submit_studio_pack), not our storage key.
        "{{source_image}}": comfy_image or value.source_image,
        "{{style}}": value.style,
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
            # Fit inside the square, enlarging as well as shrinking: CPU packs come out ~560 px, and thumbnail()
            # alone only shrinks, which left a small picture in the middle of a big white square.
            scale = min(size / image.width, size / image.height)
            copy = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
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


async def upload_source_image(value: StudioInput) -> str:
    """ComfyUI's LoadImage reads from its own input folder: copy the business's uploaded photo there first."""
    data = await asyncio.to_thread(asset_store().get, value.source_image)
    with Image.open(io.BytesIO(data)) as opened:  # refuse anything that isn't a real image before it reaches ComfyUI
        opened.verify()
    digest = hashlib.sha256(data).hexdigest()[:16]
    response = await comfy_request("POST", "/upload/image", data={"subfolder": f"volumeos/{value.tenant_id}", "overwrite": "true", "type": "input"},
                                   files={"image": (f"{value.pack_id}-{digest}.png", data, "application/octet-stream")})
    body = response.json()
    name, subfolder = str(body.get("name", "")), str(body.get("subfolder", ""))
    if not name:
        raise ApplicationError("ComfyUI didn't accept the product photo")
    return f"{subfolder}/{name}" if subfolder else name


@activity.defn
async def submit_studio_pack(value: StudioInput) -> str:
    comfy_image = await upload_source_image(value)
    graph = render_workflow(value, comfy_image)
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
    deadline = asyncio.get_running_loop().time() + GENERATION_SECONDS
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
        await asyncio.sleep(5)
    raise ApplicationError("ComfyUI polling timed out")


@activity.defn
async def store_qa_assets(value: StudioInput, images: list[dict[str, str]]) -> QAManifest:
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
    manifest_sha256 = hashlib.sha256(manifest).hexdigest()
    await asyncio.to_thread(store.put, f"{qa_prefix(value)}/manifest.json", manifest, "application/json")
    await release_comfy_memory()
    return QAManifest(keys, manifest_sha256)


async def release_comfy_memory() -> None:
    """Asks ComfyUI to unload its models once a pack's images are saved. On a CPU host ComfyUI otherwise keeps
    ~4.6 GiB of weights in RAM between packs (the next pack reloads them in seconds). Best effort: never fails a pack."""
    try:
        await comfy_request("POST", "/free", json={"unload_models": True, "free_memory": True})
    except Exception as exc:  # noqa: BLE001 - housekeeping only
        activity.logger.warning("ComfyUI didn't release its memory: %s", exc)


@activity.defn
async def record_studio_rejection(value: StudioInput, review: QAReview) -> None:
    if review.decision != "reject" or not review.reason.strip() or not review.reviewer.strip():
        raise ApplicationError("a rejection reason and reviewer are required", non_retryable=True)
    payload = json.dumps({"pack_id": value.pack_id, "reviewer": review.reviewer,
                          "reason": review.reason, "manifest_sha256": review.manifest_sha256,
                          "workflow_version": WORKFLOW_VERSION}, indent=2).encode()
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
    # Bind delivery to the exact manifest reviewed by the human. A replaced or
    # partially rewritten QA manifest must fail closed instead of being delivered.
    manifest_bytes = await asyncio.to_thread(store.get, f"{expected_root}manifest.json")
    actual_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if not hmac.compare_digest(actual_sha256, review.manifest_sha256):
        raise ApplicationError("QA manifest changed after human review", non_retryable=True)
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplicationError("QA manifest is invalid", non_retryable=True) from exc
    if (manifest.get("tenant_id") != value.tenant_id or manifest.get("pack_id") != value.pack_id
            or manifest.get("workflow_version") != WORKFLOW_VERSION or manifest.get("qa_status") != "pending"
            or manifest.get("assets") != qa_keys):
        raise ApplicationError("QA manifest does not match the approved pack", non_retryable=True)
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


@activity.defn
def record_studio_outcome(result: StudioResult, started_at: datetime) -> None:
    outcome = "failed" if result.status in {"failed", "qa_timed_out"} else result.status
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    duration = (datetime.now(timezone.utc) - started_at).total_seconds()
    metrics.record_outcome(outcome, duration)


@workflow.defn
class StudioWorkflow:
    def __init__(self) -> None:
        self._review: QAReview | None = None
        self._value: StudioInput | None = None
        self._status = "generating"
        self._asset_count = 0
        self._manifest_sha256 = ""
        self._started_at = ""
        self._updated_at = ""

    def _transition(self, status: str) -> None:
        self._status = status
        self._updated_at = workflow.now().isoformat()

    @workflow.query
    def status(self) -> StudioStatus:
        value = self._value
        return StudioStatus(
            tenant_id=value.tenant_id if value else "",
            pack_id=value.pack_id if value else "",
            product_id=value.product_id if value else "",
            tier=value.tier if value else "starter",
            status=self._status,
            assets=self._asset_count,
            manifest_sha256=self._manifest_sha256,
            started_at=self._started_at,
            updated_at=self._updated_at,
        )

    @workflow.signal
    def review(self, review: QAReview) -> None:
        if review.decision not in {"approve", "reject"} or not review.reviewer.strip():
            raise ApplicationError("decision and reviewer are required")
        if review.decision == "reject" and not review.reason.strip():
            raise ApplicationError("rejection reason is required")
        if not re.fullmatch(r"[0-9a-f]{64}", review.manifest_sha256):
            raise ApplicationError("a valid QA manifest SHA-256 is required")
        # Out-of-state, duplicate, and stale-manifest reviews are ignored. The
        # HTTP boundary returns a conflict after its preflight query; a racing
        # signal must never overwrite the original decision or fail the workflow.
        if (self._status != "awaiting_qa" or self._review is not None
                or not hmac.compare_digest(review.manifest_sha256, self._manifest_sha256)):
            return
        self._review = review
        self._transition("approved" if review.decision == "approve" else "rejecting")

    @workflow.run
    async def run(self, value: StudioInput) -> StudioResult:
        started_at = workflow.now()
        self._value = value
        self._started_at = started_at.isoformat()
        self._updated_at = self._started_at
        options = {
            "start_to_close_timeout": timedelta(minutes=2),
            "retry_policy": RetryPolicy(maximum_attempts=3),
        }
        try:
            result = await self._execute(value)
        except asyncio.CancelledError:
            self._transition("cancelled")
            raise
        except BaseException:
            self._transition("failed")
            try:
                await workflow.execute_activity(
                    record_studio_outcome, args=[StudioResult(value.pack_id, "failed", []), started_at], **options
                )
            except Exception as metric_error:
                workflow.logger.warning("Studio failure metric recording failed: %s", metric_error)
            raise
        try:
            await workflow.execute_activity(record_studio_outcome, args=[result, started_at], **options)
        except Exception as metric_error:
            # Observability must not turn a delivered/rejected pack into a
            # failed workflow or trigger another business-side execution.
            workflow.logger.warning("Studio outcome metric recording failed: %s", metric_error)
        return result

    async def _execute(self, value: StudioInput) -> StudioResult:
        validate_studio(value)
        options = {
            "start_to_close_timeout": timedelta(minutes=12),
            "heartbeat_timeout": timedelta(seconds=30),
            "retry_policy": RetryPolicy(maximum_attempts=3),
        }
        prompt_id = await workflow.execute_activity(submit_studio_pack, value, **options)
        images = await workflow.execute_activity(
            wait_studio_pack, args=[value, prompt_id], start_to_close_timeout=timedelta(seconds=GENERATION_SECONDS + 300),
            heartbeat_timeout=timedelta(minutes=2), retry_policy=RetryPolicy(maximum_attempts=2))
        qa_manifest = await workflow.execute_activity(store_qa_assets, args=[value, images], **options)
        self._asset_count = len(qa_manifest.assets)
        self._manifest_sha256 = qa_manifest.manifest_sha256
        self._transition("awaiting_qa")
        try:
            await workflow.wait_condition(lambda: self._review is not None, timeout=timedelta(hours=24))
        except asyncio.TimeoutError:
            self._transition("qa_timed_out")
            return StudioResult(value.pack_id, "qa_timed_out", [])
        review = self._review
        assert review is not None
        if review.decision == "reject":
            await workflow.execute_activity(record_studio_rejection, args=[value, review], **options)
            self._transition("rejected")
            return StudioResult(value.pack_id, "rejected", [])
        self._transition("delivering")
        assets = await workflow.execute_activity(deliver_studio_pack, args=[value, qa_manifest.assets, review], **options)
        self._transition("delivered")
        return StudioResult(value.pack_id, "delivered", assets)


async def main() -> None:
    metrics.start_server(int(os.environ.get("METRICS_PORT", "9091")))
    client = await Client.connect(
        os.environ.get("TEMPORAL_HOSTPORT", "temporal-frontend.ops.svc.cluster.local:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "volumeos"),
    )
    worker = Worker(client, task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "studio-v1"),
                    workflows=[StudioWorkflow],
                    # record_studio_outcome is a plain (sync) function: Temporal needs a thread pool for it.
                    activity_executor=concurrent.futures.ThreadPoolExecutor(max_workers=4),
                    activities=[submit_studio_pack, wait_studio_pack, store_qa_assets,
                                record_studio_rejection, deliver_studio_pack, record_studio_outcome])
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

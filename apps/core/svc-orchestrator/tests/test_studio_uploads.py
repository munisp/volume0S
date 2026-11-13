"""Studio product photo uploads and pack starts: real image types only, stored under the business, packs only from
this business's uploads, the deployment's default model unless one is named."""
from fastapi.testclient import TestClient

from assets import S3QAAssets
from auth import Claims
from main import create_app
from tests.test_api import FakeAuthenticator, FakeWorkflows, auth

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType, Metadata):
        self.objects[Key] = (Body, ContentType, Metadata)


def client_for(roles=frozenset({"delivery"})):
    s3 = FakeS3()
    workflows = FakeWorkflows()
    app = create_app(workflows, FakeAuthenticator(Claims("acme", "op", roles)), S3QAAssets("bucket", client=s3))
    return TestClient(app), s3, workflows


def test_uploads_accept_real_images_only_and_stay_in_the_business():
    client, s3, _ = client_for()
    with client:
        png = client.post("/v1/studio/uploads", headers=auth() | {"Content-Type": "image/png"}, content=PNG)
        assert png.status_code == 201 and png.json()["source_image"].startswith("acme/uploads/") and png.json()["source_image"].endswith(".png")
        assert client.post("/v1/studio/uploads", headers=auth(), content=JPEG).json()["source_image"].endswith(".jpg")
        # a script with an image content type is refused: the bytes decide, not the header
        assert client.post("/v1/studio/uploads", headers=auth() | {"Content-Type": "image/png"}, content=b"<svg onload=alert(1)>").status_code == 400
        assert client.post("/v1/studio/uploads", headers=auth(), content=PNG + b"\x00" * 10_000_001).status_code == 400
    assert all(key.startswith("acme/uploads/") for key in s3.objects)
    assert {v[1] for v in s3.objects.values()} == {"image/png", "image/jpeg"}


def test_readers_cannot_upload():
    client, s3, _ = client_for(frozenset({"reader"}))
    with client:
        assert client.post("/v1/studio/uploads", headers=auth(), content=PNG).status_code == 403
    assert s3.objects == {}


def test_packs_start_only_from_this_business_uploads_with_the_default_model():
    client, _, workflows = client_for()
    pack = {"pack_id": "p1", "product_id": "tote", "tier": "starter", "style": "on a wooden table"}
    with client:
        assert client.post("/v1/studio/packs", headers=auth(), json={**pack, "source_image": "other/uploads/x.png"}).status_code == 400
        assert client.post("/v1/studio/packs", headers=auth(), json={**pack, "source_image": "acme/packs/p0/qa/asset-01.png"}).status_code == 400
        assert client.post("/v1/studio/packs", headers=auth(), json={**pack, "source_image": "acme/uploads/x.png", "checkpoint": "../../etc/passwd"}).status_code == 400
        assert client.post("/v1/studio/packs", headers=auth(), json={**pack, "source_image": "acme/uploads/x.png"}).status_code == 202
    started = [s for s in workflows.started if s[0] == "StudioWorkflow"]
    assert len(started) == 1
    assert started[0][1]["checkpoint"] == "v1-5-pruned-emaonly.safetensors" and started[0][1]["style"] == "on a wooden table"

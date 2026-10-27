#!/usr/bin/env python3
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "m1_image_delivery.py"
SPEC = importlib.util.spec_from_file_location("m1_image_delivery", MODULE_PATH)
delivery = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(delivery)

REVISION = "a" * 40
DIGESTS = {image["name"]: f"sha256:{'abcdef'[index] * 64}" for index, image in enumerate(delivery.IMAGES)}


def arguments(output: Path) -> argparse.Namespace:
    return argparse.Namespace(source_revision=REVISION, output=str(output))


def build_report(status: str = "built") -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "source_revision": REVISION,
        "images": {
            image["name"]: {
                "reference": delivery.reference(image),
                "id": f"image-id-{image['name']}",
                "user": image["user"],
            }
            for image in delivery.IMAGES
        },
    }


class FakeTools:
    def __init__(self, *args, **kwargs):
        self.digests = dict(DIGESTS)
        self.pushed = []
        self.changed = set()

    def require(self, skopeo=False):
        return None

    def inspect(self, ref):
        name = ref.rsplit("/", 1)[-1].split(":", 1)[0]
        identity = "changed" if name in self.changed else f"image-id-{name}"
        return {"Id": identity, "Config": {
            "User": next(i["user"] for i in delivery.IMAGES if i["name"] == name),
            "Labels": {"org.opencontainers.image.revision": REVISION},
        }}

    def check(self, args, timeout=120, skopeo=False):
        if args[0] == "push":
            self.pushed.append(args[1])
            return ""
        raise AssertionError(args)

    def remote_digest(self, ref):
        name = ref.rsplit("/", 1)[-1].split(":", 1)[0]
        return self.digests[name]

    def run(self, args, timeout=120, skopeo=False):
        raise AssertionError(args)


class ImageDeliveryTests(unittest.TestCase):
    def test_catalog_is_complete_pinned_and_nonroot(self):
        self.assertEqual(6, len(delivery.IMAGES))
        self.assertEqual(6, len({image["name"] for image in delivery.IMAGES}))
        self.assertIn("svc-orchestrator", {image["name"] for image in delivery.IMAGES})
        for image in delivery.IMAGES:
            self.assertNotEqual("latest", image["tag"])
            self.assertNotIn(image["user"], {"", "0", "0:0", "root"})
            self.assertTrue((delivery.ROOT / image["context"] / "Dockerfile").is_file())

    def test_python_runtime_images_require_test_stage_completion(self):
        dockerfiles = [delivery.ROOT / f"apps/workers/{name}/Dockerfile" for name in
                       ("campaign-worker", "studio-worker", "synthetic-probe")]
        dockerfiles.append(delivery.ROOT / "apps/core/svc-orchestrator/Dockerfile")
        for dockerfile in dockerfiles:
            text = dockerfile.read_text()
            self.assertIn("python -m pytest && touch /tests-passed", text)
            self.assertIn("COPY --from=test /tests-passed /app/.tests-passed", text)
            runtime = text.split("AS runtime", 1)[1]
            self.assertNotIn("COPY tests", runtime)
            self.assertNotIn("pytest>=", runtime.split("RUN pip install", 1)[1].split("\n", 1)[0])

    def test_missing_builder_fails_closed(self):
        tools = delivery.Tools("missing-podman")
        with mock.patch.object(delivery.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "required executable not found"):
                tools.require()

    def test_invalid_registry_digest_is_rejected(self):
        tools = delivery.Tools()
        with mock.patch.object(tools, "check", return_value="not-a-digest"):
            with self.assertRaisesRegex(RuntimeError, "valid digest"):
                tools.remote_digest("ghcr.io/munisp/svc-tenancy:0.2.0")

    def test_publish_requires_passed_smoke_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            delivery.save_json(output / "build.json", build_report())
            delivery.save_json(output / "smoke.json", {
                "schema_version": 1, "status": "failed", "source_revision": REVISION,
            })
            fake = FakeTools()
            with mock.patch.object(delivery, "Tools", return_value=fake):
                with self.assertRaisesRegex(RuntimeError, "current passed report"):
                    delivery.publish(arguments(output))
            self.assertEqual([], fake.pushed)

    def test_publish_records_registry_digests_and_verify_detects_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            delivery.save_json(output / "build.json", build_report())
            delivery.save_json(output / "smoke.json", {
                "schema_version": 1, "status": "passed", "source_revision": REVISION,
            })
            fake = FakeTools()
            with mock.patch.object(delivery, "Tools", return_value=fake), mock.patch.object(delivery, "git_guard"):
                self.assertEqual(0, delivery.publish(arguments(output)))
                published = delivery.load_json(output / "images.json")
                self.assertEqual("published", published["status"])
                self.assertEqual(DIGESTS, {name: value["digest"] for name, value in published["images"].items()})
                self.assertEqual(6, len(fake.pushed))
                self.assertEqual(0, delivery.verify(arguments(output)))
                fake.digests["svc-tenancy"] = "sha256:" + "f" * 64
                with self.assertRaisesRegex(RuntimeError, "registry digest changed"):
                    delivery.verify(arguments(output))

    def test_source_revision_label_is_mandatory(self):
        fake = FakeTools()
        original = fake.inspect
        def wrong_revision(ref):
            value = original(ref)
            value["Config"]["Labels"]["org.opencontainers.image.revision"] = "b" * 40
            return value
        fake.inspect = wrong_revision
        with self.assertRaisesRegex(RuntimeError, "source-revision label"):
            delivery.inspect_current(fake, delivery.IMAGES[0], REVISION)

    def test_changed_local_image_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            delivery.save_json(output / "build.json", build_report())
            delivery.save_json(output / "smoke.json", {
                "schema_version": 1, "status": "passed", "source_revision": REVISION,
            })
            fake = FakeTools()
            fake.changed.add("studio-worker")
            with mock.patch.object(delivery, "Tools", return_value=fake), mock.patch.object(delivery, "git_guard"):
                with self.assertRaisesRegex(RuntimeError, "studio-worker changed"):
                    delivery.publish(arguments(output))
            self.assertEqual([], fake.pushed)


if __name__ == "__main__":
    unittest.main()

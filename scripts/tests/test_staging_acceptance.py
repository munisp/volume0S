#!/usr/bin/env python3
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "staging_acceptance.py"
SPEC = importlib.util.spec_from_file_location("staging_acceptance", MODULE_PATH)
acceptance = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(acceptance)


def identity(output: Path) -> argparse.Namespace:
    return argparse.Namespace(
        environment="stage",
        context="volumeos-stage",
        source_revision="a" * 40,
        output=str(output),
    )


class StagingAcceptanceTests(unittest.TestCase):
    def test_catalog_is_unique_and_covers_m1_gates(self):
        checks = acceptance.catalog()
        self.assertEqual(16, len(checks))
        self.assertEqual({"ONB-001", "MSG-001", "MSG-004", "DR-001", "OBS-001", "COST-001"}, {
            check_id for check_id in checks if check_id in {
                "ONB-001", "MSG-001", "MSG-004", "DR-001", "OBS-001", "COST-001"
            }
        })
        for check in checks.values():
            self.assertTrue(check["requirements"])
            self.assertTrue(check["evidence"])

    def test_missing_kubectl_fails_closed_and_preserves_blocked_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = root / "providers.json"
            provider.write_text(json.dumps({
                "schema_version": 1,
                "environment": "stage",
                "providers": {
                    channel: {"name": f"provider-{channel}", "mode": "sandbox", "configured": True}
                    for channel in ("email", "sms", "whatsapp", "social")
                },
                "comfyui": {"namespace": "ai", "deployment": "comfyui"},
            }))
            args = identity(root / "run")
            args.provider_config = str(provider)
            with mock.patch.dict(os.environ, {"PATH": str(root), "KUBECTL": "kubectl"}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(1, acceptance.preflight(args))
            preflight = acceptance.load_json(root / "run/preflight.json")
            self.assertEqual("blocked", preflight["status"])
            self.assertEqual("passed", preflight["checks"][0]["status"])
            self.assertTrue(all(check["status"] == "blocked" for check in preflight["checks"][1:]))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1, acceptance.verify(argparse.Namespace(output=str(root / "run"))))

    def test_healthy_cluster_preflight_passes_without_recording_secret_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = root / "providers.json"
            provider.write_text(json.dumps({
                "schema_version": 1,
                "environment": "stage",
                "providers": {
                    channel: {"name": f"provider-{channel}", "mode": "sandbox", "configured": True}
                    for channel in ("email", "sms", "whatsapp", "social")
                },
                "comfyui": {"namespace": "ai", "deployment": "comfyui"},
            }))
            expected = {
                "core": {"svc-tenancy", "svc-provisioning", "listmonk", "postiz"},
                "workers": {"campaign-worker", "studio-worker", "synthetic-probe"},
                "ai": {"comfyui"},
            }
            secret_keys = {
                ("core", "svc-tenancy"): {"database-url", "service-token", "suppression-hmac-secret"},
                ("core", "svc-provisioning"): {"service-token", "keycloak-admin-client-id", "keycloak-admin-client-secret"},
                ("core", "listmonk-admin"): {"admin-password"},
                ("core", "postiz"): {"DATABASE_URL", "REDIS_URL", "JWT_SECRET"},
                ("workers", "synthetic-probe"): {"client-id", "client-secret", "service-token"},
            }

            def fake_get(_self, args):
                if args[:3] == ["get", "namespace", "core"]:
                    return 0, {"metadata": {"name": "core"}}
                if len(args) >= 4 and args[2:4] == ["get", "deployments"]:
                    namespace = args[1]
                    items = [{
                        "metadata": {"name": name},
                        "spec": {"replicas": 1},
                        "status": {"readyReplicas": 1, "updatedReplicas": 1},
                    } for name in expected[namespace]]
                    return 0, {"items": items}
                if len(args) >= 5 and args[2:4] == ["get", "secret"]:
                    namespace, name = args[1], args[4]
                    return 0, {"data": {key: "c3VwZXJzZWNyZXQ=" for key in secret_keys[(namespace, name)]}}
                if any(kind in args for kind in ("components.dapr.io", "prometheusrule", "configmap", "servicemonitor")):
                    return 0, {"metadata": {"name": args[-1]}}
                if args[:2] == ["get", "nodes"]:
                    return 0, {"items": [{"metadata": {"name": "gpu-1"}, "status": {"capacity": {"nvidia.com/gpu": "1"}}}]}
                return 1, "unexpected fake query"

            args = identity(root / "run")
            args.provider_config = str(provider)
            output = io.StringIO()
            with mock.patch.object(acceptance.shutil, "which", return_value="/fake/kubectl"), mock.patch.object(acceptance.Cluster, "get", fake_get):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, acceptance.preflight(args))
            self.assertNotIn("c3VwZXJzZWNyZXQ=", output.getvalue())
            self.assertEqual("passed", acceptance.load_json(root / "run/preflight.json")["status"])

    def test_pass_requires_real_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            acceptance.prepare_output(output)
            run = acceptance.initial_run(identity(output))
            acceptance.save_json(output / "run.json", run)
            args = argparse.Namespace(
                output=str(output), check="MSG-004", status="passed", notes="test", evidence=[]
            )
            with self.assertRaisesRegex(ValueError, "requires at least one evidence"):
                acceptance.record(args)

    def test_verifier_requires_every_check_and_detects_changed_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            acceptance.prepare_output(output)
            args = identity(output)
            run = acceptance.initial_run(args)
            for check_id in run["checks"]:
                artifact = output / "artifacts" / f"{check_id}.txt"
                artifact.write_text(f"reviewed evidence for {check_id}\n")
                run["checks"][check_id] = {
                    "status": "passed",
                    "notes": "test fixture",
                    "artifacts": [{"path": f"artifacts/{check_id}.txt", "sha256": acceptance.digest(artifact)}],
                }
            acceptance.save_json(output / "run.json", run)
            acceptance.save_json(output / "preflight.json", {
                "status": "passed", "environment": args.environment,
                "context": args.context, "source_revision": args.source_revision,
            })
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, acceptance.verify(argparse.Namespace(output=str(output))))
            self.assertEqual("accepted", acceptance.load_json(output / "run.json")["status"])
            (output / "artifacts/MSG-004.txt").write_text("changed")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1, acceptance.verify(argparse.Namespace(output=str(output))))
            verification = acceptance.load_json(output / "verification.json")
            self.assertTrue(any("missing or changed artifact" in error for error in verification["errors"]))

    def test_example_provider_configuration_is_rejected(self):
        config, errors = acceptance.provider_config(
            acceptance.ROOT / "docs/m1/acceptance/provider-config.example.json", "stage"
        )
        self.assertIsNotNone(config)
        self.assertGreaterEqual(len(errors), 5)


if __name__ == "__main__":
    unittest.main()

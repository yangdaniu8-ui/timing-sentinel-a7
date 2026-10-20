import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import __version__
from scripts import build_release as release


class ReleaseAuditTests(unittest.TestCase):
    def make_host(self, root):
        host = root / "host"
        for folder in ("app", "protocol", "transport", "simulator", "tests", "scripts", "docs", "packaging"):
            (host / folder).mkdir(parents=True)
        (host / "app/main.py").write_text("original", encoding="utf-8")
        (host / "tests/test_main.py").write_text("test", encoding="utf-8")
        return host

    def test_versioned_names_and_content_fingerprints(self):
        self.assertEqual(release.version_tag("1.2.3"), "v1_2_3")
        with tempfile.TemporaryDirectory() as folder:
            host = self.make_host(Path(folder))
            original = release.source_fingerprint(host)
            test_hash = release.source_fingerprint(host, verification=True)
            (host / "docs/guide.md").write_text("Updated guide", encoding="utf-8")
            self.assertEqual(release.source_fingerprint(host), original)
            (host / "tests/test_main.py").write_text("New test", encoding="utf-8")
            self.assertEqual(release.source_fingerprint(host), original)
            self.assertNotEqual(release.source_fingerprint(host, verification=True), test_hash)
            (host / "app/main.py").write_text("changed!", encoding="utf-8")
            self.assertNotEqual(release.source_fingerprint(host), original)

    def test_verification_requires_matching_sources_environment_and_success(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(release, "environment_versions", return_value={"fixture": "1"}):
            host = self.make_host(Path(folder))
            path = host.parent / "data/verification/host_v1_2_3_verification.json"
            path.parent.mkdir(parents=True)
            report = {"app_version": "1.2.3", "passed": True, "tests": 2, "failures": 0, "errors": 0,
                      "verification_sha256": release.source_fingerprint(host, verification=True),
                      "environment": {"fixture": "1"}}
            release.write_json(path, report)
            self.assertEqual(release.read_verification(host, "1.2.3")[0], report)
            (host / "app/main.py").write_text("changed!", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "current sources"):
                release.read_verification(host, "1.2.3")

    def test_repackage_rejects_changed_runtime_or_new_code(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(release, "environment_versions", return_value={}):
            root = Path(folder)
            host = self.make_host(root)
            app = root / "bundle"
            app.mkdir()
            exe = app / "TimingSentinelA7.exe"
            exe.write_bytes(b"test executable")
            manifest = {"app_version": "1.2.3", "environment": {},
                        "source_sha256": release.source_fingerprint(host),
                        "runtime_files": {exe.name: release.file_hash(exe)}}
            release.validate_existing_bundle(app, manifest, host, "1.2.3")
            (app / "team_quick_start.md").write_text("Docs only", encoding="utf-8")
            release.validate_existing_bundle(app, manifest, host, "1.2.3")
            (app / "untracked.dll").write_bytes(b"unexpected")
            with self.assertRaisesRegex(RuntimeError, "Untracked"):
                release.validate_existing_bundle(app, manifest, host, "1.2.3")
            (app / "untracked.dll").unlink()
            exe.write_bytes(b"changed executable")
            with self.assertRaisesRegex(RuntimeError, "changed since build"):
                release.validate_existing_bundle(app, manifest, host, "1.2.3")
            (host / "app/main.py").write_text("new source", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "rebuild without"):
                release.validate_existing_bundle(app, manifest, host, "1.2.3")

    def test_zero_exit_cannot_reuse_stale_smoke(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "smoke.json"
            release.write_json(report, {"passed": True})
            with patch.object(release.subprocess, "run") as run:
                run.return_value.returncode = 0
                with self.assertRaisesRegex(RuntimeError, "fresh smoke"):
                    release.run_packaged_smoke(root, report, root / "data")
            self.assertFalse(report.exists())

    def test_smoke_requires_frozen_version_and_expected_mock_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "smoke.json"
            evidence = {"app_version": "1.2.3", "passed": True, "frozen": True, "origin": "mock",
                        "hardware_validation": False, "samples": 65536, "result": "PASS"}
            def emit(*args, **kwargs):
                release.write_json(report, evidence)
                return type("Exit", (), {"returncode": 0})()
            with patch.object(release.subprocess, "run", side_effect=emit):
                self.assertEqual(release.run_packaged_smoke(root, report, root / "data", "1.2.3"), evidence)
                evidence["frozen"] = False
                with self.assertRaisesRegex(RuntimeError, "unexpected version"):
                    release.run_packaged_smoke(root, report, root / "data", "1.2.3")

    def test_installer_manifest_records_actual_artifact_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            host = Path(folder)
            out = host / "dist/1.2.3"
            out.mkdir(parents=True)
            portable = out / "TimingSentinelA7-1.2.3-win64-portable.zip"
            portable.write_bytes(b"portable fixture")
            installer = out / "TimingSentinelA7-1.2.3-setup.exe"
            installer.write_bytes(b"installer fixture")
            release.write_json(out / "release_manifest.json", {
                "app_version": "1.2.3", "sha256": release.file_hash(portable), "installer_built": False})
            evidence = release.record_installer(installer, host, "1.2.3")
            self.assertTrue(evidence["installer_built"])
            self.assertEqual(evidence["installer_sha256"], release.file_hash(installer))
            portable.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "manifest does not match"):
                release.record_installer(installer, host, "1.2.3")

    def test_publish_failure_restores_previous_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            out, stage = root / "out", root / "stage"
            out.mkdir()
            stage.mkdir()
            (out / "TimingSentinelA7").mkdir()
            (out / "TimingSentinelA7/old.txt").write_text("previous", encoding="utf-8")
            (stage / "TimingSentinelA7").mkdir()
            (stage / "TimingSentinelA7/new.txt").write_text("candidate", encoding="utf-8")
            # Missing smoke_data simulates publication failure after first move.
            with self.assertRaises(FileNotFoundError):
                release.publish_staged(stage, out)
            self.assertEqual((out / "TimingSentinelA7/old.txt").read_text("utf-8"), "previous")
            self.assertTrue((stage / "TimingSentinelA7/new.txt").exists())


if __name__ == "__main__":
    unittest.main()

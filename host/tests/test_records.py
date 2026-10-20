from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from app.services.record_store import export_csv, export_json, load_record, save_record
from tests.helpers import frozen_capture


class RecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture, _, _ = frozen_capture(scenario="late")

    def test_complete_save_replay_and_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.tscap"
            save_record(self.capture, path)
            restored = load_record(path)
            self.assertEqual(restored.metadata, self.capture.metadata)
            self.assertEqual(restored.samples, self.capture.samples)
            self.assertEqual(restored.events, self.capture.events)
            self.assertEqual(restored.metadata["origin"], "mock")
            export_json(restored, path.with_suffix(".json"))
            export_csv(restored, path.with_suffix(".csv"))
            self.assertEqual(json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["metadata"]["result"], "TIMEOUT")
            self.assertTrue(path.with_suffix(".csv").read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_partial_prefix_preserves_time_axis(self):
        metadata = {**self.capture.metadata, "transfer_complete": False}
        partial = replace(self.capture, metadata=metadata, samples=self.capture.samples[:1004], events=b"")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "partial.tscap"
            save_record(partial, path)
            restored = load_record(path)
            self.assertFalse(restored.complete)
            self.assertEqual(len(restored.samples), 1004)
            self.assertEqual(restored.times_ms().tolist(), self.capture.times_ms()[:1004].tolist())
            with zipfile.ZipFile(path) as archive:
                self.assertIn("segments/capture_0.bin", archive.namelist())
                self.assertNotIn("samples.bin", archive.namelist())

    def test_hash_failure_and_unknown_schema_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "original.tscap"
            save_record(self.capture, original)
            with zipfile.ZipFile(original) as archive:
                items = {name: archive.read(name) for name in archive.namelist()}
            for name in ("samples.bin", "manifest.json"):
                broken = dict(items)
                if name == "samples.bin":
                    broken[name] = b"bad"
                else:
                    manifest = json.loads(broken[name]); manifest["schema_version"] = 99
                    broken[name] = json.dumps(manifest).encode()
                path = Path(temp) / f"broken_{name}.tscap"
                with zipfile.ZipFile(path, "w") as archive:
                    for key, value in broken.items():
                        archive.writestr(key, value)
                with self.assertRaises(ValueError):
                    load_record(path)

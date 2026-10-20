from dataclasses import replace
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app.services.diagnostics import export_diagnostics
from app.services.record_store import export_csv, export_json, export_vcd, load_record, save_record
from tests.helpers import frozen_capture


class RecordAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture, _, _ = frozen_capture()

    def changed_archive(self, folder, transform):
        source = Path(folder) / "source.tscap"
        save_record(self.capture, source)
        with zipfile.ZipFile(source) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(files.pop("manifest.json"))
        transform(manifest, files)
        # Mutate with valid hashes to exercise semantic validation, not just CRC/SHA.
        manifest["files"] = {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                             for name, data in files.items()}
        target = Path(folder) / "changed.tscap"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for name, data in files.items():
                archive.writestr(name, data)
        return target

    def test_strict_completeness_and_record_layout(self):
        def extra(manifest, files):
            files["segments/capture_0.bin"] = b"unexplained alternate samples"
        def mismatched_note(manifest, files):
            files["observations.json"] = json.dumps({"algorithm": "mvp1", "note": "wrong"}).encode()
        for transform in (lambda m, f: m.update(transfer_complete="true"),
                          lambda m, f: m.update(schema_version=True), extra, mismatched_note,
                          lambda m, f: f.update({"metadata.json": b"[]"})):
            with self.subTest(transform=transform), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    load_record(self.changed_archive(folder, transform))

    def test_broken_zip_and_manifest_produce_validation_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.tscap"
            path.write_bytes(b"not a zip")
            with self.assertRaises(ValueError):
                load_record(path)
            for body in (b"null", b"[]", b'{"schema_version":1,"profile":"mvp1","transfer_complete":true}'):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("manifest.json", body)
                with self.assertRaises(ValueError):
                    load_record(path)

    def test_missing_display_identity_fields_rejected_before_gui_load(self):
        for field in ("firmware", "record_id", "observation", "app_version"):
            def remove_field(manifest, files):
                metadata = json.loads(files["metadata.json"])
                metadata.pop(field)
                files["metadata.json"] = json.dumps(metadata).encode()
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    load_record(self.changed_archive(folder, remove_field))

    def test_failed_export_preserves_existing_target_and_removes_temporary(self):
        for export, suffix in ((export_json, "json"), (export_csv, "csv"), (export_vcd, "vcd"),
                               (save_record, "tscap")):
            with self.subTest(export=export.__name__), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / ("existing." + suffix)
                path.write_bytes(b"existing user data")
                with patch("app.services.record_store.os.replace", side_effect=OSError("disk unavailable")):
                    with self.assertRaises(OSError):
                        export(self.capture, path)
                self.assertEqual(path.read_bytes(), b"existing user data")
                self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_csv_validates_before_publishing(self):
        capture = replace(self.capture, metadata={**self.capture.metadata, "origin": "bad"})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "record.csv"
            path.write_bytes(b"old")
            with self.assertRaises(ValueError):
                export_csv(capture, path)
            self.assertEqual(path.read_bytes(), b"old")

    def test_csv_preserves_uart_error_evidence(self):
        first = int.from_bytes(self.capture.events[:16], "little") | (1 << 81)
        capture = replace(self.capture, events=first.to_bytes(16, "little") + self.capture.events[16:])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.csv"
            export_csv(capture, path)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["uart_error"], "True")

    def test_diagnostic_failure_does_not_replace_existing_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.zip"
            path.write_bytes(b"old diagnostic")
            with patch("app.services.record_store.os.replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    export_diagnostics(path, folder, {"connected": False})
            self.assertEqual(path.read_bytes(), b"old diagnostic")
            self.assertEqual(list(Path(folder).iterdir()), [path])

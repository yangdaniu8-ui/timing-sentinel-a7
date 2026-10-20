import copy
import csv
from dataclasses import replace
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.models import TICK_MASK
from mvp5.export_checks import verify_csv, verify_vcd
from mvp5.exports import export_csv, export_vcd
from mvp5.models import Capture, Config
from mvp5.tests.test_mvp4 import fixture


def sample_capture(samples, *, divisor=5, first=1000, triggered=False, expected=None, events_expected=0):
    m = copy.deepcopy(fixture().metadata)
    count = len(samples) if expected is None else expected
    cfg = replace(Config(), divisor=divisor)
    m.update(valid_count=count, sample_divisor=divisor, config=cfg.as_dict(),
             first_sample_timestamp=first, event_count=events_expected,
             transfer_complete=count == len(samples) and events_expected == 0)
    if triggered:
        index = min(3, count - 1)
        anchor = (first + index * divisor) & TICK_MASK
        m.update(trigger_valid=True, primary_rule=0, trigger_rule_mask=1,
                 trigger_sample_index=index, capture_anchor_timestamp=anchor,
                 decision_timestamp=(anchor - (1 if divisor > 1 else 0)) & TICK_MASK)
    return Capture(m, bytes(samples), b"").validate()


class ExportTests(unittest.TestCase):
    def roundtrip(self, cap):
        with tempfile.TemporaryDirectory() as directory:
            csv_path, vcd_path = Path(directory) / "wave.csv", Path(directory) / "wave.vcd"
            before = copy.deepcopy(cap)
            export_csv(cap, csv_path); export_vcd(cap, vcd_path)
            self.assertTrue(verify_csv(cap, csv_path)["passed"])
            self.assertTrue(verify_vcd(cap, vcd_path)["passed"])
            self.assertEqual(before, cap)
            return csv_path.read_bytes(), vcd_path.read_text(encoding="ascii")

    def test_all_256_channel_combinations(self):
        self.roundtrip(sample_capture(range(256)))

    def test_all_sample_divisors(self):
        for d in (1, 5, 10, 50):
            with self.subTest(divisor=d):
                self.roundtrip(sample_capture([0, 255, 0, 128, 1, 1], divisor=d, triggered=True))

    def test_static_low_duration_retained(self):
        _, vcd = self.roundtrip(sample_capture([0] * 64))
        self.assertTrue(vcd.endswith("#6300\n"))

    def test_static_high_duration_retained(self):
        self.roundtrip(sample_capture([255] * 64))

    def test_single_sample(self):
        _, vcd = self.roundtrip(sample_capture([173]))
        self.assertEqual(vcd.count("#0\n"), 1)

    def test_timestamp_wrap(self):
        self.roundtrip(sample_capture([0, 1, 255, 15, 32, 32], first=TICK_MASK-12, triggered=True))

    def test_negative_relative_csv_and_nonnegative_vcd(self):
        csv_data, vcd = self.roundtrip(sample_capture(range(64), triggered=True))
        self.assertIn(b",-280,", csv_data)
        self.assertNotIn("#-", vcd)

    def test_partial_samples_not_padded(self):
        csv_data, vcd = self.roundtrip(sample_capture(range(20), expected=64, triggered=True))
        self.assertIn(b",False,20,64,", csv_data)
        self.assertIn('"transfer_complete": false', vcd)
        self.assertTrue(vcd.endswith("#1900\n") or "#1900\n" in vcd)

    def test_missing_events_marks_incomplete(self):
        csv_data, vcd = self.roundtrip(sample_capture(range(64), events_expected=1))
        self.assertIn(b",False,64,64,0,1,", csv_data)
        self.assertIn('"transfer_complete": false', vcd)

    def test_manual_stop_preserved(self):
        cap = sample_capture(range(64))
        cap.metadata["manual_stop"] = True
        _, vcd = self.roundtrip(cap)
        self.assertIn('"manual_stop": true', vcd)

    def test_vcd_comment_delimiter_cannot_be_injected(self):
        cap = sample_capture(range(10))
        cap.metadata["firmware"] = "mock $end\n#999999 injected"
        _, vcd = self.roundtrip(cap)
        self.assertIn("\\u0024end", vcd)

    def test_zero_samples_rejected(self):
        cap = sample_capture([], expected=64)
        with tempfile.TemporaryDirectory() as d:
            for writer, suffix in ((export_csv, ".csv"), (export_vcd, ".vcd")):
                p = Path(d) / ("empty" + suffix)
                with self.assertRaises(ValueError): writer(cap, p)
                self.assertFalse(p.exists())

    def test_bad_capture_never_replaces_destination(self):
        cap = sample_capture(range(10)); cap.metadata["clock_hz"] = 100
        with tempfile.TemporaryDirectory() as d:
            for writer, suffix in ((export_csv, ".csv"), (export_vcd, ".vcd")):
                p = Path(d) / ("existing" + suffix); p.write_bytes(b"keep")
                with self.assertRaises(ValueError): writer(cap, p)
                self.assertEqual(p.read_bytes(), b"keep")

    def test_tscap_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "original.tscap"; p.write_bytes(b"keep")
            for writer in (export_csv, export_vcd):
                with self.assertRaises(ValueError): writer(sample_capture(range(10)), p)
            self.assertEqual(p.read_bytes(), b"keep")

    def test_uppercase_extension(self):
        cap = sample_capture(range(10))
        with tempfile.TemporaryDirectory() as d:
            export_csv(cap, Path(d)/"WAVE.CSV"); export_vcd(cap, Path(d)/"WAVE.VCD")

    def test_atomic_csv_failure_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"wave.csv"; p.write_bytes(b"keep")
            with patch("mvp5.exports.csv.writer", side_effect=OSError("injected write failure")):
                with self.assertRaises(OSError): export_csv(sample_capture(range(10)), p)
            self.assertEqual(p.read_bytes(), b"keep")
            self.assertEqual(list(Path(d).glob(".ts_export_*")), [])

    def test_atomic_vcd_failure_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"wave.vcd"; p.write_bytes(b"keep")
            with patch.object(Path, "open", side_effect=OSError("injected write failure")):
                with self.assertRaises(OSError): export_vcd(sample_capture(range(10)), p)
            self.assertEqual(p.read_bytes(), b"keep")
            self.assertEqual(list(Path(d).glob(".ts_export_*")), [])

    def test_seeded_full_depth(self):
        rng = random.Random(0xA743)
        self.roundtrip(sample_capture(bytes(rng.randrange(256) for _ in range(65536)), triggered=True))

    def test_csv_tamper_detected(self):
        cap = sample_capture(range(10))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"wave.csv"; export_csv(cap, p)
            with p.open(encoding="utf-8-sig", newline="") as f: rows = list(csv.reader(f))
            rows[1][-1] = "1"
            with p.open("w", encoding="utf-8-sig", newline="") as f: csv.writer(f).writerows(rows)
            with self.assertRaises(ValueError): verify_csv(cap, p)

    def test_vcd_tamper_detected(self):
        cap = sample_capture(range(10))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"wave.vcd"; export_vcd(cap, p)
            p.write_text(p.read_text(encoding="ascii").replace("0!\n", "1!\n", 1), encoding="ascii")
            with self.assertRaises(ValueError): verify_vcd(cap, p)


class ExportGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mvp5.ui import MainWindow
        self.window = MainWindow(); self.window.timer.stop()
        self.window.capture = sample_capture(range(64))
        self.window.saved = False; self.window.replay = False
        self.window.device_state = "FROZEN"
        self.window.client = Mock()
        self.window.client.session_id = self.window.client.config_id = 1
        self.window.client.uncertain = False
        self.window.start = self.inline

    def inline(self, operation, success, failure=None):
        try: result = operation(Mock())
        except Exception as exc:
            if failure: failure(str(exc))
            else: raise
        else: success(result)

    def tearDown(self):
        self.window.capture=None; self.window.client=None; self.window.busy=False
        self.window.saved=True; self.window.close(); self.window.deleteLater(); self.app.processEvents()

    def test_three_menu_actions(self):
        self.assertEqual([a.text() for a in self.window.export_menu.actions()], ["JSON元数据", "CSV逐点波形", "VCD数字波形"])

    def test_each_menu_action_exports_without_device_calls(self):
        with tempfile.TemporaryDirectory() as d:
            for action, suffix in zip(self.window.export_menu.actions(), ("json", "csv", "vcd")):
                p = Path(d)/("wave."+suffix)
                with patch("mvp5.ui.QFileDialog.getSaveFileName", return_value=(str(p), "")):
                    action.trigger()
                self.assertTrue(p.exists())
                self.assertFalse(self.window.saved); self.assertFalse(self.window.replay)
                self.assertEqual(self.window.device_state, "FROZEN")
            self.assertEqual(self.window.client.method_calls, [])

    def test_cancel_no_write_or_state_change(self):
        self.window.start=Mock()
        with patch("mvp5.ui.QFileDialog.getSaveFileName", return_value=("", "")):
            self.window.export("csv")
        self.window.start.assert_not_called(); self.assertFalse(self.window.saved)

    def test_export_failure_preserves_device_state_and_unsaved_protection(self):
        with patch("mvp5.ui.QFileDialog.getSaveFileName", return_value=("bad.tscap", "")), patch("mvp5.ui.QMessageBox.warning"):
            self.window.export("vcd")
        self.assertEqual(self.window.device_state, "FROZEN")
        self.assertFalse(self.window.client.uncertain); self.assertFalse(self.window.saved)
        self.assertEqual(self.window.client.method_calls, [])

    def test_worker_exclusion(self):
        self.window.busy=True; self.window.start=Mock()
        with patch("mvp5.ui.QFileDialog.getSaveFileName") as dialog:
            self.window.export("csv")
        dialog.assert_not_called(); self.window.start.assert_not_called()


if __name__ == "__main__": unittest.main()

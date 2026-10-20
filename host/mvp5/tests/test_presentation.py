import copy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from PySide6.QtWidgets import QApplication, QAbstractItemView
from app.models import TICK_MASK
from mvp5.models import Capture, unpack_events
from mvp5.store import load_record
from mvp5.presentation import identify_scene, relative_ms, reference_tick, event_description
from mvp5.tests.test_mvp4 import fixture
from mvp5.waveform import TimeAxis, FourWaveform

RECORDS = (Path(__file__).resolve().parents[3] / 'fixtures/hardware_six')


class PresentationTests(unittest.TestCase):
    def test_all_six_real_records_verified(self):
        self.assertEqual(len(list(RECORDS.glob("*.tscap"))), 6)
        for index, path in enumerate(sorted(RECORDS.glob("*.tscap"))):
            cap = load_record(path); before = copy.deepcopy(cap)
            info = identify_scene(cap)
            self.assertEqual(info["marker"], 0xd0 + index)
            self.assertTrue(info["verified"])
            self.assertEqual(before, cap)

    def test_unknown_never_guessed(self):
        info = identify_scene(fixture())
        self.assertIsNone(info["scene"]); self.assertFalse(info["verified"])

    def test_partial_with_marker_not_verified(self):
        cap = load_record(next(RECORDS.glob("round_1*")))
        partial = replace(cap, samples=cap.samples[:41])
        info = identify_scene(partial)
        self.assertEqual(info["marker"], 0xd0); self.assertFalse(info["verified"])
        self.assertIn("不完整", info["status"])

    def test_simulated_marker_not_hardware_pass(self):
        cap = copy.deepcopy(load_record(next(RECORDS.glob("round_1*"))))
        cap.metadata.update(origin="mock", firmware="mock-test")
        info = identify_scene(cap)
        self.assertFalse(info["verified"]); self.assertIn("模拟", info["status"])

    def test_sample_marker_conflict_not_verified(self):
        cap = load_record(next(RECORDS.glob("round_1*")))
        raw = bytearray(cap.events)
        for i, e in enumerate(unpack_events(cap.events)):
            if e["uart"] == 0xd0:
                word = int.from_bytes(raw[i*16:i*16+16], "little")
                word = (word & ~(255 << 73)) | (0xd1 << 73)
                raw[i*16:i*16+16] = word.to_bytes(16, "little")
        info = identify_scene(replace(cap, events=bytes(raw)))
        self.assertEqual(info["marker"], 0xd1); self.assertFalse(info["verified"])

    def test_missing_events_unknown(self):
        cap = load_record(next(RECORDS.glob("round_1*")))
        self.assertIsNone(identify_scene(replace(cap, events=b""))["scene"])

    def test_raw_corruption_not_pass(self):
        cap = load_record(next(RECORDS.glob("round_1*")))
        info = identify_scene(replace(cap, samples=bytes([0x11])*len(cap.samples)))
        self.assertFalse(info["verified"]); self.assertIn("未通过", info["status"])

    def test_overwritten_event_ring_not_pass(self):
        cap = copy.deepcopy(load_record(next(RECORDS.glob("round_1*"))))
        cap.metadata["event_overwrites"] = 1
        self.assertFalse(identify_scene(cap)["verified"])

    def test_multiple_accepted_not_identified(self):
        cap = copy.deepcopy(load_record(next(RECORDS.glob("round_1*"))))
        cap.metadata["rules"][0]["counts"]["accepted"] = 2
        self.assertIsNone(identify_scene(cap)["scene"])

    def test_relative_timestamp_wrap(self):
        self.assertAlmostEqual(relative_ms(4, TICK_MASK-5), .0002)

    def test_fallback_missing_a(self):
        self.assertEqual(reference_tick(fixture().metadata)[0], 1000)

    def test_ms_us_exact_tick_labels(self):
        app = QApplication.instance() or QApplication([])
        axis = TimeAxis()
        self.assertEqual(axis.tickStrings([.2, 2.22314], 1, .01), ["0.20", "2.22"])
        axis.unit = "us"
        self.assertEqual(axis.tickStrings([.2, 2.22314], 1, .001), ["200", "2223"])
        self.assertFalse(axis.autoSIPrefix)


class PresentationGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mvp5.ui import MainWindow
        self.w = MainWindow(); self.w.timer.stop()
        self.cap = load_record(next(RECORDS.glob("round_3*")))
        self.w.replay = self.w.saved = True
        self.w.show_capture(self.cap)

    def tearDown(self):
        self.w.capture = None; self.w.client = None; self.w.close(); self.w.deleteLater()
        self.app.processEvents()

    def test_late_axis_zero_and_observed_delay_agree(self):
        view = self.w.wave; a = self.cap.metadata["rules"][0]["a_tick"]
        observed = self.cap.metadata["rules"][0]["observed_b_tick"]
        self.assertEqual(view.reference, a)
        self.assertAlmostEqual(view._pos(observed), float(self.w.result_table.item(0,3).text()))
        self.assertIn("延迟响应", self.w.scene_banner.text())

    def test_reference_change_keeps_cursor_distance_and_record(self):
        view = self.w.wave; distance = abs(view.cursors[1].value() - view.cursors[0].value())
        before = copy.deepcopy(self.cap)
        view.reference_combo.setCurrentIndex(1)
        self.assertEqual(view.reference, self.cap.metadata["decision_timestamp"])
        self.assertAlmostEqual(abs(view.cursors[1].value()-view.cursors[0].value()), distance)
        self.assertEqual(self.cap, before)
        for row, event in enumerate(self.w.shown_events):
            self.assertAlmostEqual(float(self.w.event_table.item(row,0).text()), view._pos(event["tick"]), places=6)

    def test_explicit_us_cursor_conversion(self):
        view = self.w.wave
        distance = abs(view.cursors[1].value()-view.cursors[0].value())
        view.unit_combo.setCurrentIndex(1)
        self.assertIn(f"{distance*1000:.6f} μs", view.measurement.text())
        self.assertFalse(view.axis.autoSIPrefix)
        self.assertEqual(view.axis.autoSIPrefixScale, 1.0)

    def test_spi_zoom_inside_received_samples(self):
        view = self.w.wave; view.zoom_spi()
        low, high = view.plot.viewRange()[0]
        self.assertLess(high-low, .1)
        self.assertEqual(view.unit_combo.currentData(), "us")

    def test_spi_missing_zoom_disabled(self):
        self.w.show_capture(load_record(next(RECORDS.glob("round_4*"))))
        self.assertFalse(self.w.wave.spi_zoom.isEnabled())

    def test_collapsible_config_and_resizable_events(self):
        self.assertFalse(self.w.config_toggle.isChecked())
        self.assertEqual(self.w.data_splitter.count(), 2)
        self.assertGreaterEqual(self.w.tabs.minimumHeight(), 180)
        self.assertGreater(self.w.tabs.maximumHeight(), 160)
        self.w.config_toggle.setChecked(True)
        self.assertFalse(self.w.config_panel.isHidden())

    def test_evidence_tables_are_read_only(self):
        for table in (self.w.event_table, self.w.result_table):
            self.assertEqual(table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)

    def test_event_click_moves_cursor(self):
        row = next(i for i,e in enumerate(self.w.shown_events) if e["uart"]==0xa5)
        self.w.focus_event(row,0)
        self.assertAlmostEqual(self.w.wave.cursors[0].value(), 0)

    def test_stale_live_status_never_renames_shown_scene(self):
        before = self.w.scene_banner.text()
        self.w.set_status(dict(state="FROZEN",session_id=999999,config_id=99))
        self.assertEqual(self.w.scene_banner.text(), before)
        self.assertIn("旧记录", self.w.summary.text())

    def test_partial_never_navigates_missing_trigger(self):
        partial = replace(self.cap, samples=self.cap.samples[:41])
        self.w.show_capture(partial)
        self.assertFalse(self.w.wave.response_zoom.isEnabled())
        self.assertFalse(self.w.wave.locate.isEnabled())
        self.assertFalse(self.w.scene_info["verified"])

    def test_unused_channels_can_be_shown(self):
        view = self.w.wave
        self.assertFalse(view.curves[7].isVisible())
        view.active_only.setChecked(False)
        self.assertTrue(view.curves[7].isVisible())

    def test_event_filter_retains_marker_and_a(self):
        shown=[e["uart"] for e in self.w.shown_events if e["uart"] is not None]
        self.assertEqual(shown,[0xd2,0xa5])
        self.assertLess(self.w.event_table.rowCount(),self.cap.metadata["event_count"])
        self.w.key_events.setChecked(False)
        self.assertEqual(self.w.event_table.rowCount(),self.cap.metadata["event_count"])

    def test_presentation_no_device_calls(self):
        self.w.client = Mock(); self.w.client.uncertain = False
        self.w.device_state = "FROZEN"
        self.w.show_capture(self.cap)
        self.w.wave.reference_combo.setCurrentIndex(2)
        self.w.wave.unit_combo.setCurrentIndex(1)
        self.w.wave.zoom_spi()
        self.assertEqual(self.w.client.method_calls, [])


if __name__ == "__main__": unittest.main()

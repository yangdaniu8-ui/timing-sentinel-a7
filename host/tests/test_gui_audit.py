import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import time
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from app.models import Capture
from app.services.device_client import Cancelled, DeviceFailure
from app.ui.main_window import MainWindow
from tests.helpers import frozen_capture


class GuiAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.fixture, _, _ = frozen_capture()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow(auto_connect=False, root=self.temp.name)
        self.window.timer.stop()
        self.window.connect_device()
        self.wait_idle()

    def tearDown(self):
        if self.window.task:
            self.window.task.cancelled.set()
            self.wait_idle()
        self.window.saved_path = "test_cleanup"
        # Test cleanup is not a user disconnect decision; never leave dialogs.
        self.window.connected = False
        self.window.close()
        self.application.processEvents()
        self.temp.cleanup()

    def wait_idle(self):
        deadline = time.monotonic() + 10
        while self.window.task is not None:
            self.application.processEvents()
            if time.monotonic() > deadline:
                self.fail("GUI worker did not finish: " + self.window.feedback.text())
            time.sleep(.002)
        self.application.processEvents()

    def show_previous(self):
        self.window.present(self.fixture)
        self.window.saved_path = "previous.tscap"
        self.window.render_capture_results()

    def test_failed_arm_and_demo_keep_previous_capture_and_saved_state(self):
        for method in (self.window.arm, self.window.demo):
            with self.subTest(method=method.__name__):
                self.show_previous()
                previous_results = self.window.results.toPlainText()
                previous_events = self.window.events.rowCount()
                with patch.object(self.window.client, "configure", side_effect=DeviceFailure("test rejection")):
                    method()
                    self.wait_idle()
                self.assertIs(self.window.capture, self.fixture)
                self.assertEqual(self.window.saved_path, "previous.tscap")
                self.assertEqual(self.window.results.toPlainText(), previous_results)
                self.assertEqual(self.window.events.rowCount(), previous_events)

    def test_successful_arm_clears_all_old_capture_views(self):
        self.show_previous()
        self.window.arm()
        self.wait_idle()
        self.assertIsNone(self.window.capture)
        self.assertIsNone(self.window.saved_path)
        self.assertIsNone(self.window.wave.capture)
        self.assertEqual(self.window.events.rowCount(), 0)
        self.assertIn("本次采集尚未读取", self.window.results.toPlainText())
        self.assertNotIn(self.fixture.metadata["record_id"], self.window.device_info.toPlainText())

    def test_saved_and_replayed_results_do_not_claim_unsaved(self):
        self.show_previous()
        path = str(Path(self.temp.name) / "capture.tscap")
        with patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(path, "")):
            self.window.save()
        self.wait_idle()
        self.assertIn(path, self.window.results.toPlainText())
        self.assertNotIn("尚未保存", self.window.results.toPlainText())
        with patch("app.ui.main_window.QFileDialog.getOpenFileName", return_value=(path, "")):
            self.window.open_record()
        self.wait_idle()
        self.assertTrue(self.window.replay)
        self.assertIn(path, self.window.results.toPlainText())
        self.assertNotIn("尚未保存", self.window.results.toPlainText())
        self.window.mode.setCurrentIndex(1)
        self.assertIn("离线回放", self.window.source.text())

    def test_active_disconnect_cancel_or_stop_preserves_connection(self):
        self.window.arm()
        self.wait_idle()
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel) as ask:
            self.assertFalse(self.window.disconnect_device())
        ask.assert_called_once()
        self.assertTrue(self.window.connected)
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.assertFalse(self.window.disconnect_device())
        self.wait_idle()
        self.assertTrue(self.window.connected)
        self.assertEqual(self.window.status["state"], "FROZEN")
        self.assertTrue(self.window.read_btn.isEnabled())

    def test_active_close_requires_explicit_choice(self):
        self.window.arm()
        self.wait_idle()
        event = QCloseEvent()
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())

    def test_switch_to_replay_during_active_capture_can_be_cancelled(self):
        self.window.arm()
        self.wait_idle()
        with patch("app.ui.main_window.QFileDialog.getOpenFileName", return_value=("unused.tscap", "")), \
                patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel), \
                patch("app.ui.main_window.load_record") as load:
            self.window.open_record()
        load.assert_not_called()
        self.assertTrue(self.window.connected)
        self.assertFalse(self.window.replay)

    def test_compound_wait_cancellation_and_timeout_require_resync(self):
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                self.window.client.uncertain = False
                with patch.object(self.window, "run_task") as run:
                    self.window.demo()
                operation = run.call_args.args[1]
                job = SimpleNamespace(cancelled=Event(), message=Mock(), progress=Mock())
                if cancel:
                    job.cancelled.set()
                with patch.object(self.window.client, "status", side_effect=[{"state": "IDLE"}, {"ready": False}]), \
                        patch.object(self.window.client, "configure"), patch.object(self.window.client, "arm"), \
                        patch("app.ui.main_window.time.monotonic", side_effect=[0, 4]):
                    with self.assertRaises(Cancelled if cancel else TimeoutError):
                        operation(job)
                self.assertTrue(self.window.client.uncertain)
                self.window.error("Cancelled" if cancel else "TimeoutError: waiting")
                self.assertEqual(self.window.status["state"], "UNKNOWN")
                self.assertFalse(self.window.arm_btn.isEnabled())

    def test_partial_prefix_cannot_locate_unreceived_decision(self):
        partial = Capture(dict(self.fixture.metadata, transfer_complete=False), self.fixture.samples[:100], b"")
        self.window.present(partial)
        self.assertFalse(self.window.wave.locate.isEnabled())
        self.assertIn("A在已收到波形内：False", self.window.results.toPlainText())
        self.assertTrue(partial.metadata["a_in_waveform"])  # Preserve original legacy metadata.
        self.window.present(self.fixture)
        self.assertTrue(self.window.wave.locate.isEnabled())

    def test_uart_error_is_visible_in_event_table(self):
        word = int.from_bytes(self.fixture.events[:16], "little") | (1 << 81)
        # Use the actual waveform and mutate only a valid event's UART error bit.
        capture = Capture(self.fixture.metadata, self.fixture.samples,
                          word.to_bytes(16, "little") + self.fixture.events[16:])
        self.window.present(capture)
        self.assertIn("帧错误", self.window.events.item(0, 2).text())

    def test_unknown_state_disconnect_requires_confirmation(self):
        self.window.client.uncertain = True
        self.window.status["state"] = "UNKNOWN"
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(self.window.disconnect_device())
        self.assertTrue(self.window.connected)
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.window.disconnect_device())
        self.assertFalse(self.window.connected)

    def test_png_encoding_failure_keeps_existing_target(self):
        self.show_previous()
        path = Path(self.temp.name) / "previous.png"
        path.write_bytes(b"previous picture")
        with patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(str(path), "")), \
                patch.object(self.window, "grab", return_value=Mock(save=Mock(return_value=False))):
            self.window.export_png()
        self.assertEqual(path.read_bytes(), b"previous picture")
        self.assertIn("原文件未替换", self.window.feedback.text())

    def test_busy_actions_do_not_discard_current_record(self):
        self.show_previous()
        self.window.task = object()
        try:
            with patch.object(self.window, "protect_record") as protect:
                self.window.arm()
                self.window.demo()
                self.window.open_record()
                self.window.save()
                self.window.read()
            protect.assert_not_called()
            self.assertIs(self.window.capture, self.fixture)
        finally:
            self.window.task = None


if __name__ == "__main__":
    unittest.main()

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(auto_connect=False)
        self.window.timer.stop()
        self.window.show()
        self.window.connect_device()
        self.wait_idle()

    def tearDown(self):
        self.window.saved_path = "test_cleanup"
        if self.window.task:
            self.window.task.cancelled.set()
            self.window.task.wait(1800)
        self.application.processEvents()
        self.window.close()
        self.application.processEvents()

    def wait_idle(self):
        deadline = time.monotonic() + 10
        while self.window.task is not None:
            self.application.processEvents()
            if time.monotonic() > deadline:
                self.fail("GUI worker did not finish: " + self.window.feedback.text())
            time.sleep(.002)
        self.application.processEvents()

    def test_visible_workflow_four_scenarios(self):
        self.assertTrue(self.window.connected)
        for index, expected in enumerate(("PASS", "EARLY", "TIMEOUT", "TIMEOUT")):
            self.window.saved_path = "test_previous_record"
            self.window.scenario.setCurrentIndex(index)
            self.window.demo_btn.click()
            self.wait_idle()
            self.assertIsNotNone(self.window.capture, self.window.feedback.text())
            self.assertEqual(self.window.capture.metadata["result"], expected)
            self.assertTrue(self.window.capture.complete)
            self.assertEqual(self.window.events.rowCount(), self.window.capture.metadata["event_count"])
            self.assertFalse(self.window.arm_btn.isEnabled())
            self.assertTrue(self.window.save_btn.isEnabled())

    def test_save_and_replay_through_ui(self):
        self.window.demo_btn.click()
        self.wait_idle()
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "gui.tscap")
            with patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(path, "")):
                self.window.save_btn.click()
            self.wait_idle()
            self.assertEqual(self.window.saved_path, path)
            samples = self.window.capture.samples
            with patch("app.ui.main_window.QFileDialog.getOpenFileName", return_value=(path, "")):
                self.window.open_btn.click()
            self.wait_idle()
            self.assertTrue(self.window.replay)
            self.assertFalse(self.window.connected)
            self.assertIn("模拟", self.window.source.text())
            self.assertEqual(self.window.capture.samples, samples)
            self.assertFalse(self.window.arm_btn.isEnabled())

    def test_stop_without_trigger_and_anomaly_pass(self):
        self.window.action.setCurrentIndex(1)
        self.window.demo_btn.click()
        self.wait_idle()
        self.assertIsNone(self.window.capture)
        self.assertEqual(self.window.status["state"], "ARMED")
        self.window.stop_btn.click()
        self.wait_idle()
        self.window.read_btn.click()
        self.wait_idle()
        self.assertFalse(self.window.capture.metadata["trigger_valid"])
        self.assertFalse(self.window.wave.locate.isEnabled())

    def test_serial_mode_failed_open_never_falls_back(self):
        self.window.disconnect_device()
        self.window.mode.setCurrentIndex(1)
        self.window.port.setEditText("COM99999")
        self.window.connect_btn.click()
        self.wait_idle()
        self.assertFalse(self.window.connected)
        self.assertIsNone(self.window.client)
        self.assertFalse(self.window.demo_btn.isEnabled())
        self.assertFalse(self.window.arm_btn.isEnabled())
        self.assertIn("未确认", self.window.source.text())

    def test_invalid_replay_does_not_disconnect_or_clear_data(self):
        self.window.demo_btn.click()
        self.wait_idle()
        before = self.window.capture
        self.window.saved_path = "previous_saved"
        with patch("app.ui.main_window.QFileDialog.getOpenFileName", return_value=("Z:/missing_record.tscap", "")):
            self.window.open_btn.click()
        self.wait_idle()
        self.assertTrue(self.window.connected)
        self.assertFalse(self.window.replay)
        self.assertIs(self.window.capture, before)

    def test_partial_release_requires_confirmation(self):
        from app.models import Capture
        self.window.demo_btn.click()
        self.wait_idle()
        full = self.window.capture
        self.window.capture = Capture(dict(full.metadata, transfer_complete=False), full.samples[:100], b"")
        self.window.saved_path = "saved_partial"
        from PySide6.QtWidgets import QMessageBox
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as ask:
            self.window.release_btn.click()
        ask.assert_called_once()
        self.assertEqual(self.window.client.status()["state"], "FROZEN")

    def test_vcd_png_and_diagnostic_export_actions(self):
        import zipfile
        self.window.demo_btn.click()
        self.wait_idle()
        with tempfile.TemporaryDirectory() as temp:
            vcd = str(Path(temp) / "capture.vcd")
            png = str(Path(temp) / "capture.png")
            diag = str(Path(temp) / "diag.zip")
            with patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(vcd, "")):
                self.window.vcd_btn.trigger()
            self.wait_idle()
            with patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(png, "")):
                self.window.png_btn.trigger()
            self.assertTrue(Path(png).exists())
            self.assertIn("$timescale 1 ns", Path(vcd).read_text("ascii"))
            with patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(diag, "")):
                self.window.diag_btn.click()
            self.wait_idle()
            with zipfile.ZipFile(diag) as archive:
                self.assertIn("diagnostics.json", archive.namelist())

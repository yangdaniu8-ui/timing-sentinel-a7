"""Storage failure injection, with no real serial or board operations."""
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from app.services.capture_storage import read_preference
from mvp5.store import load_record
from mvp5.tests.test_workload_experiment import FixtureClient, records


class StorageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mvp5.ui import MainWindow
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.guard = patch('mvp5.ui.SerialTransport', side_effect=AssertionError('No real serial'))
        self.guard.start()
        self.window = MainWindow(root=self.root / 'captures', settings_path=self.root / 'settings.json')
        self.window.timer.stop()
        self.client = FixtureClient(records())
        self.window.client = self.client
        self.window.device_state = 'IDLE'
        self.window.gate()

    def tearDown(self):
        self.window.capture = self.window.client = self.window.transport = None
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.guard.stop()
        self.temp.cleanup()

    def wait(self):
        deadline = time.monotonic() + 15
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertFalse(self.window.busy)

    def test_directory_choice_is_checked_persisted_and_never_moves_old_record(self):
        cap = records()[0]
        self.window.capture = cap
        before = self.window.root
        selected = self.root / 'another' / '中文目录'
        with patch('mvp5.ui.QFileDialog.getExistingDirectory', return_value=str(selected)):
            self.window.choose_capture_root()
        self.assertEqual(self.window.root, selected.resolve())
        self.assertEqual(read_preference(self.window.storage_settings), selected.resolve())
        self.assertTrue(self.window.storage_ready)
        self.assertIs(self.window.capture, cap)
        self.assertFalse(self.window.saved)
        self.assertFalse(before.exists())
        self.assertEqual(self.client.actions, [])

    def test_cancel_choice_keeps_root_and_preferences_untouched(self):
        original = self.window.root
        with patch('mvp5.ui.QFileDialog.getExistingDirectory', return_value=''):
            self.window.choose_capture_root()
        self.assertEqual(self.window.root, original)
        self.assertFalse(self.window.storage_settings.exists())

    def test_unusable_choice_preserves_original_root(self):
        original = self.window.root
        with patch('mvp5.ui.QFileDialog.getExistingDirectory', return_value=str(self.root / 'bad')), \
             patch('mvp5.ui.probe_directory', side_effect=OSError('injected')), \
             patch('mvp5.ui.QMessageBox.warning') as warning:
            self.window.choose_capture_root()
        warning.assert_called_once()
        self.assertEqual(self.window.root, original)
        self.assertFalse(self.window.storage_settings.exists())

    def test_setting_write_failure_keeps_valid_session_root_and_warns(self):
        selected = self.root / 'good'
        with patch('mvp5.ui.QFileDialog.getExistingDirectory', return_value=str(selected)), \
             patch('mvp5.ui.write_preference', side_effect=OSError('settings denied')), \
             patch('mvp5.ui.QMessageBox.warning') as warning:
            self.window.choose_capture_root()
        self.assertEqual(self.window.root, selected.resolve())
        self.assertTrue(self.window.storage_ready)
        warning.assert_called_once()

    def test_busy_directory_choice_cannot_change_active_experiment_root(self):
        self.window.busy = True
        try:
            with patch('mvp5.ui.QFileDialog.getExistingDirectory') as dialog:
                self.window.choose_capture_root()
            dialog.assert_not_called()
        finally:
            self.window.busy = False

    def test_failed_preflight_blocks_task_and_all_device_mutations(self):
        self.client.status = Mock(side_effect=AssertionError('Must not touch board'))
        with patch('mvp5.ui.probe_directory', side_effect=OSError('atomic publication denied')), \
             patch('mvp5.ui.QMessageBox.warning') as warning:
            self.window.workload_batch()
        self.assertFalse(self.window.busy)
        self.assertIsNone(self.window.task)
        self.assertFalse(self.window.storage_ready)
        self.assertIn('不可用', self.window.storage_label.text())
        self.assertEqual(self.client.actions, [])
        self.client.status.assert_not_called()
        warning.assert_called_once()

    def test_record_and_report_write_failures_keep_received_capture_for_resave(self):
        with patch('mvp5.workload_experiment.save_record', side_effect=OSError('disk full')), \
             patch('mvp5.workload_experiment.publish', side_effect=OSError('report disk full')), \
             patch('mvp5.ui.QMessageBox.warning'):
            self.window.workload_batch()
            self.wait()
        self.assertEqual(self.window.capture.samples, self.client.captures[0].samples)
        self.assertEqual(self.window.capture.events, self.client.captures[0].events)
        self.assertFalse(self.window.saved or self.window.replay)
        self.assertEqual(self.window.device_state, 'UNKNOWN')
        self.assertEqual(self.client.actions, ['configure', 'arm'])
        self.assertTrue(self.window.buttons['save'].isEnabled())
        self.assertFalse(self.window.buttons['release'].isEnabled())
        destination = self.root / 'resaved.tscap'
        with patch('mvp5.ui.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
            self.assertTrue(self.window.save())
        self.assertEqual(load_record(destination), self.window.capture)

    def test_report_failure_after_save_keeps_saved_capture_not_previous_record(self):
        with patch('mvp5.workload_experiment.publish', side_effect=OSError('report denied')), \
             patch('mvp5.ui.QMessageBox.warning'):
            self.window.workload_batch()
            self.wait()
        self.assertTrue(self.window.saved)
        self.assertFalse(self.window.replay)
        self.assertEqual(self.window.capture.samples, self.client.captures[0].samples)
        self.assertEqual(self.client.actions, ['configure', 'arm'])
        self.assertEqual(len(list(self.window.root.rglob('*.tscap'))), 1)

    def test_save_to_broken_default_root_can_still_choose_other_destination(self):
        obstruction = self.root / 'not_a_directory'
        obstruction.write_text('preserve', encoding='utf-8')
        self.window.root = obstruction
        self.window.capture = records()[0]
        path = self.root / 'elsewhere.tscap'
        with patch('mvp5.ui.QFileDialog.getSaveFileName', return_value=(str(path), '')):
            self.assertTrue(self.window.save())
        self.assertEqual(obstruction.read_text(encoding='utf-8'), 'preserve')
        self.assertEqual(load_record(path), self.window.capture)

    def test_failed_manual_save_keeps_old_destination_and_unsaved_flag(self):
        self.window.capture = records()[0]
        original = self.window.capture
        target = self.root / 'existing.tscap'
        target.write_bytes(b'old evidence')
        with patch('mvp5.ui.QFileDialog.getSaveFileName', return_value=(str(target), '')), \
             patch('app.services.record_store.os.replace', side_effect=OSError('publication denied')), \
             patch('mvp5.ui.QMessageBox.warning') as warning:
            self.assertFalse(self.window.save())
        self.assertEqual(target.read_bytes(), b'old evidence')
        self.assertIs(self.window.capture, original)
        self.assertFalse(self.window.saved)
        warning.assert_called_once()

    def test_saved_partial_prefix_cannot_release_complete_board_buffer_after_reconnect(self):
        cap = records()[0]
        self.window.capture = replace(cap, samples=cap.samples[:41])
        self.window.saved = True
        self.window.replay = False
        self.client.session_id = cap.metadata['session_id']
        self.client.config_id = cap.metadata['config_id']
        self.window.device_state = 'FROZEN'
        self.window.gate()
        self.assertFalse(self.window.buttons['release'].isEnabled())

    def test_direct_release_slot_rejects_saved_partial_even_if_called_programmatically(self):
        cap = records()[0]
        self.window.capture = replace(cap, samples=cap.samples[:41])
        self.window.saved = True
        self.client.session_id = cap.metadata['session_id']
        self.client.config_id = cap.metadata['config_id']
        self.window.device_state = 'FROZEN'
        self.window.start = Mock()
        with patch('mvp5.ui.QMessageBox.question') as question:
            self.window.release()
        self.window.start.assert_not_called()
        question.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""GUI safety boundaries only: no serial, ARM, release or firmware writes."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch
from PySide6.QtWidgets import QApplication,QFileDialog,QMessageBox
from mvp5.store import load_record,save_record,digest

FIXTURES=(Path(__file__).resolve().parents[3] / 'fixtures/usr43')

class SafetyModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from mvp5.ui import MainWindow
        self.temp=TemporaryDirectory()
        self.port=patch('mvp5.ui.SerialTransport',side_effect=AssertionError('Unexpected serial open'))
        self.port.start();self.w=MainWindow(root=self.temp.name);self.w.timer.stop()
    def tearDown(self):
        self.w.capture=self.w.client=None;self.w.busy=False;self.w.close();self.w.deleteLater()
        self.app.processEvents();self.port.stop();self.temp.cleanup()
    def consent(self,answer=QMessageBox.StandardButton.Yes):
        with patch.object(QMessageBox,'question',return_value=answer) as prompt:
            self.w.legacy_mode.setChecked(True)
        return prompt
    def idle(self):
        self.w.client=Mock(uncertain=False,session_id=1,config_id=1)
        self.w.device_state='IDLE';self.w.gate()
    def live_capture(self):
        cap=load_record(next(FIXTURES.glob('wide_load2_*')))
        self.w.saved=False;self.w.replay=False;self.w.show_capture(cap);return cap

    def test_unknown_device_idle_does_not_enable_or_show_legacy(self):
        self.idle()
        self.assertFalse(self.w.legacy_debug_enabled)
        self.assertFalse(self.w.buttons['batch'].isEnabled())
        self.assertTrue(self.w.buttons['batch'].isHidden())
        self.assertTrue(self.w.workload_run.isEnabled())
    def test_declining_confirmation_does_not_enter_legacy(self):
        self.consent(QMessageBox.StandardButton.No)
        self.assertFalse(self.w.legacy_mode.isChecked());self.assertFalse(self.w.legacy_debug_enabled)
        self.assertTrue(self.w.buttons['batch'].isHidden())
    def test_confirmation_defaults_to_no_and_warns_no_firmware_identity(self):
        prompt=self.consent()
        self.assertEqual(prompt.call_args.args[-1],QMessageBox.StandardButton.No)
        self.assertIn('不代表固件身份已验证',prompt.call_args.args[2])
    def test_explicit_confirmation_only_changes_gui_state(self):
        self.w.start=Mock();self.consent()
        self.assertTrue(self.w.legacy_debug_enabled);self.assertFalse(self.w.buttons['batch'].isHidden())
        self.assertIsNone(self.w.client);self.w.start.assert_not_called()
    def test_legacy_mode_enables_old_button_only_when_idle(self):
        self.consent();self.idle();self.assertTrue(self.w.buttons['batch'].isEnabled())
        for state in ('FROZEN','ARMED','PREFILL','POST_TRIGGER','UNKNOWN'):
            self.w.device_state=state;self.w.gate();self.assertFalse(self.w.buttons['batch'].isEnabled())
    def test_legacy_mode_blocks_real_task_button_and_direct_slot(self):
        self.consent();self.idle();self.w.start=Mock()
        self.assertFalse(self.w.workload_run.isEnabled())
        with patch.object(QMessageBox,'warning'):self.w.workload_batch()
        self.w.start.assert_not_called()
    def test_exit_legacy_restores_normal_mode(self):
        self.consent();self.w.legacy_mode.setChecked(False);self.idle()
        self.assertTrue(self.w.workload_run.isEnabled());self.assertTrue(self.w.buttons['batch'].isHidden())
    def test_direct_legacy_slot_without_opt_in_cannot_start_worker(self):
        self.idle();self.w.start=Mock()
        with patch.object(QMessageBox,'warning'):self.w.batch()
        self.w.start.assert_not_called()
    def test_legacy_direct_slot_requires_a_connected_idle_device(self):
        self.consent();self.w.start=Mock();self.w.batch();self.w.start.assert_not_called()
        self.idle();self.w.device_state='FROZEN';self.w.batch();self.w.start.assert_not_called()
    def test_legacy_direct_slot_remains_available_after_consent(self):
        self.consent();self.idle();self.w.start=Mock();self.w.batch()
        self.w.start.assert_called_once()  # Worker stub: run_four and hardware are never executed.
    def test_mode_change_while_connected_is_rejected(self):
        self.idle()
        with patch.object(QMessageBox,'question') as prompt:self.w.change_legacy_mode(True)
        self.assertFalse(self.w.legacy_debug_enabled);prompt.assert_not_called()
    def test_mode_change_during_worker_is_rejected(self):
        self.w.busy=True
        with patch.object(QMessageBox,'question') as prompt:self.w.change_legacy_mode(True)
        self.assertFalse(self.w.legacy_debug_enabled);prompt.assert_not_called();self.w.busy=False
    def test_unsaved_live_capture_blocks_legacy_mode(self):
        cap=self.live_capture();before=copy.deepcopy(cap)
        with patch.object(QMessageBox,'question') as prompt:self.w.change_legacy_mode(True)
        self.assertFalse(self.w.legacy_debug_enabled);self.assertEqual(self.w.capture,before);prompt.assert_not_called()
    def test_identified_real_workload_revokes_legacy_and_hides_old_button(self):
        self.consent();self.idle();self.w.live_workload_seen=True;self.w.gate()
        self.assertFalse(self.w.legacy_debug_enabled);self.assertFalse(self.w.legacy_mode.isChecked())
        self.assertTrue(self.w.buttons['batch'].isHidden());self.assertFalse(self.w.buttons['batch'].isEnabled())
    def test_known_workload_cannot_opt_in_after_disconnect(self):
        self.w.live_workload_seen=True
        with patch.object(QMessageBox,'question') as prompt:self.w.change_legacy_mode(True)
        self.assertFalse(self.w.legacy_debug_enabled);prompt.assert_not_called()
    def test_unsaved_record_blocks_direct_legacy_batch(self):
        self.consent();self.idle();self.live_capture();self.w.start=Mock();self.w.batch()
        self.w.start.assert_not_called()
    def test_rejected_old_slot_does_not_change_record_or_draft(self):
        cap=self.live_capture();before=copy.deepcopy(cap);draft=self.w.read_draft().to_bytes();self.w.start=Mock()
        with patch.object(QMessageBox,'warning'):self.w.batch()
        self.assertEqual(self.w.capture,before);self.assertEqual(self.w.read_draft().to_bytes(),draft)
        self.w.start.assert_not_called()
    def test_save_failure_preserves_destination_and_unsaved_gate(self):
        cap=self.live_capture();path=Path(self.temp.name)/'existing.tscap';save_record(cap,path)
        before=digest(path.read_bytes());self.w.device_state='FROZEN'
        with patch.object(QFileDialog,'getSaveFileName',return_value=(str(path),'')),\
             patch('app.services.record_store.os.replace',side_effect=PermissionError('test: publish denied')),\
             patch.object(QMessageBox,'warning'):
            self.assertFalse(self.w.save())
        self.assertFalse(self.w.saved);self.assertEqual(digest(path.read_bytes()),before)
        self.assertEqual(load_record(path),cap);self.assertFalse(self.w.buttons['release'].isEnabled())
        self.assertFalse(list(Path(self.temp.name).glob('.ts_export_*')))

if __name__=='__main__':unittest.main(verbosity=2)

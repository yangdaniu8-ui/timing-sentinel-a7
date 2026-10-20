"""Practical hints/save destinations are grounded in the displayed record, not the next preset."""
import copy
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication
from mvp5.store import load_record
from mvp5.models import Config
from mvp5.user_guidance import demo_expectation,suggested_record_name

FIXTURES=(Path(__file__).resolve().parents[3] / 'fixtures/usr43')

class UserGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from mvp5.ui import MainWindow
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.guard=patch('mvp5.ui.SerialTransport',side_effect=AssertionError('No real serial in UX tests'));self.guard.start()
        self.w=MainWindow(root=self.root/'next_save',settings_path=self.root/'settings.json');self.w.timer.stop()
    def tearDown(self):
        self.w.client=self.w.capture=self.w.transport=None
        self.w.close();self.w.deleteLater();self.app.processEvents();self.guard.stop();self.temp.cleanup()
    def load(self,pattern='wide_load2_*'):
        path=next(FIXTURES.glob(pattern));cap=load_record(path)
        self.w.saved=self.w.replay=True;self.w.show_capture(cap,path=path);return cap
    def connect_frozen(self):
        self.w.client=Mock(uncertain=False,session_id=1,config_id=2)
        self.w.set_status(dict(state='FROZEN',session_id=1,config_id=2))
    def test_disconnected_recommends_connection_without_commands(self):
        self.assertIn('先连接',self.w.next_step_hint.text())
        self.assertTrue(self.w.connect_button.property('recommended'))
        self.assertFalse(self.w.workload_run.property('recommended'))
    def test_frozen_empty_recommends_read_not_start_over(self):
        self.connect_frozen()
        self.assertEqual(self.w.metric_labels['result'][0].text(),'待读取记录')
        self.assertIn('读取板端记录',self.w.empty_wave_hint.text())
        self.assertTrue(self.w.buttons['read'].property('recommended'))
        self.assertFalse(self.w.workload_run.property('recommended'))
        self.w.client.arm.assert_not_called();self.w.client.release.assert_not_called()
    def test_idle_recommends_experiment(self):
        self.connect_frozen();self.w.set_status(dict(state='IDLE',session_id=1,config_id=2))
        self.assertTrue(self.w.workload_run.property('recommended'))
        self.assertIn('已就绪',self.w.next_step_hint.text())
    def test_unsaved_real_record_recommends_save_first(self):
        self.load();self.w.saved=self.w.replay=False;self.w.gate()
        self.assertTrue(self.w.buttons['save'].property('recommended'))
        self.assertFalse(self.w.connect_button.property('recommended'))
        self.assertFalse(self.w.buttons['release'].isEnabled())
    def test_uncertain_connection_never_recommends_new_measurement(self):
        self.connect_frozen();self.w.client.uncertain=True;self.w.gate()
        self.assertIn('待确认',self.w.next_step_hint.text())
        self.assertFalse(self.w.workload_run.property('recommended'))
        self.assertFalse(self.w.buttons['arm'].isEnabled())
    def test_only_one_primary_action(self):
        self.connect_frozen()
        buttons=[self.w.connect_button,self.w.workload_run,self.w.buttons['read'],self.w.buttons['save'],self.w.storage_button]
        self.assertEqual(sum(bool(b.property('recommended')) for b in buttons),1)
    def test_expectation_does_not_replace_timeout_or_use_next_setting(self):
        cap=self.load('narrow_load2_*');before=copy.deepcopy(cap)
        self.assertEqual(self.w.metric_labels['result'][0].text(),'响应超期')
        self.assertIn('符合演示预期',self.w.metric_labels['result'][1].text())
        self.w.workload_deadline.setCurrentIndex(1);self.w.gate()
        self.assertEqual(self.w.metric_labels['result'][0].text(),'响应超期')
        self.assertEqual(demo_expectation(cap,self.w.scene_info)['deadline_ms'],3.5)
        self.assertEqual(cap,before)
    def test_custom_rule_has_no_predicted_verdict(self):
        cap=copy.deepcopy(self.load());identity=self.w.scene_info
        cap.metadata['config']['rules'][0]['max_ticks']+=1
        self.assertIsNone(demo_expectation(cap,identity))
    def test_partial_or_mock_cannot_claim_matches_demo(self):
        cap=self.load();identity=self.w.scene_info
        self.assertIsNone(demo_expectation(replace(cap,samples=cap.samples[:20]),identity))
        fake=copy.deepcopy(cap);fake.metadata['origin']='mock'
        self.assertIsNone(demo_expectation(fake,identity))
    def test_default_tabs_are_short_and_advanced_remain_accessible(self):
        self.assertEqual([self.w.tabs.tabText(i) for i in range(self.w.tabs.count()) if self.w.tabs.isTabVisible(i)],
                         ['关键事件','规则详情','批量统计'])
        self.w.config_toggle.setChecked(True)
        self.assertTrue(all(self.w.tabs.isTabVisible(i) for i in self.w.compact_advanced_indices))
        self.w.tabs.setCurrentIndex(6);self.w.config_toggle.setChecked(False)
        self.assertEqual(self.w.tabs.currentIndex(),0)
    def test_saved_location_uses_actual_selected_file_not_default_root(self):
        cap=self.load();self.w.saved=self.w.replay=False
        target=self.root/'different_folder'/'my_record.tscap'
        with patch('mvp5.ui.QFileDialog.getSaveFileName',return_value=(str(target),'')):
            self.assertTrue(self.w.save())
        self.assertEqual(self.w.capture_path,target.resolve())
        self.assertEqual(load_record(target),cap)
        self.assertTrue(self.w.record_folder_button.isEnabled())
        self.assertEqual(Path(self.w.record_folder_button.toolTip()).resolve(),target.parent.resolve())
        with patch('mvp5.ui.QDesktopServices.openUrl',return_value=True) as opened:
            self.w.record_folder_button.click()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()).resolve(),target.parent.resolve())
    def test_cancel_or_failed_save_never_announces_new_destination(self):
        self.load();original=self.w.capture_path;self.w.saved=False
        with patch('mvp5.ui.QFileDialog.getSaveFileName',return_value=('','')):
            self.assertFalse(self.w.save())
        self.assertEqual(self.w.capture_path,original)
        with patch('mvp5.ui.QFileDialog.getSaveFileName',return_value=(str(self.root/'fail.tscap'),'')),\
             patch('mvp5.ui.save_record',side_effect=OSError('injected full disk')),\
             patch('mvp5.ui.QMessageBox.warning'):
            self.assertFalse(self.w.save())
        self.assertFalse(self.w.saved);self.assertEqual(self.w.capture_path,original)
        self.assertFalse(self.w.record_folder_button.isEnabled())
    def test_new_capture_clears_old_saved_path(self):
        self.load();self.assertIsNotNone(self.w.capture_path)
        self.w.saved=self.w.replay=False;self.w.show_capture(load_record(next(FIXTURES.glob('narrow_load2_*'))))
        self.assertIsNone(self.w.capture_path);self.assertFalse(self.w.record_folder_button.isEnabled())
    def test_filename_uses_record_deadline_and_load(self):
        cap=self.load('narrow_load2_*');self.w.workload_deadline.setCurrentIndex(1)
        name=suggested_record_name(cap,self.w.scene_info,'abcdef12')
        self.assertIn('_heavy_3p5ms_task',name);self.assertTrue(name.endswith('_abcdef12.tscap'))

    def test_read_does_not_overwrite_unsaved_real_record(self):
        cap=self.load();self.w.saved=self.w.replay=False;self.connect_frozen()
        with patch('mvp5.ui.QMessageBox.information') as warning:
            self.w.read_capture();warning.assert_called_once()
        self.assertIs(self.w.capture,cap);self.w.client.read_capture.assert_not_called()
        self.w.client.release.assert_not_called()

    def test_successful_six_task_save_tracks_the_real_last_file(self):
        from mvp5.tests.test_workload_experiment import FixtureClient,records
        self.w.client=FixtureClient(records());self.w.device_state='IDLE';self.w.gate()
        self.w.workload_batch();deadline=time.monotonic()+10
        while self.w.busy and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.002)
        self.assertFalse(self.w.busy)
        self.assertTrue(self.w.saved)
        self.assertIsNotNone(self.w.capture_path)
        self.assertTrue(self.w.capture_path.is_file())
        self.assertEqual(load_record(self.w.capture_path),self.w.capture)
        self.assertTrue(self.w.record_folder_button.isEnabled())

if __name__=='__main__':unittest.main()

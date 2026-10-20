import copy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import threading
import time
from unittest.mock import Mock,patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QAbstractItemView,QFileDialog
from mvp5.models import Config
from mvp5.store import load_record,save_record
from mvp5.compact_layout import update,populate_events

ROOT=(Path(__file__).resolve().parents[3] / 'fixtures/usr43')

class CompactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from mvp5.ui import MainWindow
        self.temp=TemporaryDirectory();self.port_guard=patch('mvp5.ui.SerialTransport',side_effect=AssertionError('unexpected serial open'))
        self.port_guard.start();self.w=MainWindow(root=self.temp.name);self.w.timer.stop()

    def tearDown(self):
        self.w.capture=None;self.w.client=None;self.w.close();self.w.deleteLater();self.app.processEvents()
        self.port_guard.stop();self.temp.cleanup()

    def show(self,pattern='wide_load2_*'):
        cap=load_record(next(ROOT.glob(pattern)));self.w.saved=self.w.replay=True;self.w.show_capture(cap);return cap

    def test_no_record_never_shows_pass_or_zero(self):
        self.assertEqual(self.w.metric_labels['result'][0].text(),'等待采集')
        self.assertEqual(self.w.metric_labels['gpio'][0].text(),'—');self.assertFalse(self.w.buttons['release'].isEnabled())

    def test_approved_summary_full_pass(self):
        self.show();self.assertEqual(self.w.metric_labels['result'][0].text(),'全部通过')
        self.assertIn('历史回放',self.w.compact_source.text());self.assertIn('ms',self.w.metric_labels['gpio'][0].text())

    def test_timeout_keeps_observed_delay_and_late_label(self):
        self.show('narrow_load2_*');self.assertEqual(self.w.metric_labels['result'][0].text(),'响应超期')
        self.assertIn('已迟到',self.w.metric_labels['gpio'][1].text());self.assertNotEqual(self.w.metric_labels['gpio'][0].text(),'—')

    def test_partial_record_never_green_pass(self):
        cap=self.show();self.w.show_capture(replace(cap,samples=cap.samples[:41]))
        self.assertEqual(self.w.metric_labels['result'][0].text(),'数据不完整');self.assertIn('不完整',self.w.compact_record_hint.text())

    def test_corrupted_full_waveform_never_green_pass(self):
        cap=self.show();self.w.show_capture(replace(cap,samples=bytes([0x11])*len(cap.samples)))
        self.assertEqual(self.w.metric_labels['result'][0].text(),'判定待核验')

    def test_mock_source_explicit_and_not_green(self):
        cap=copy.deepcopy(self.show());cap.metadata.update(origin='mock',firmware='mock-test')
        self.w.show_capture(cap);self.assertIn('模拟',self.w.compact_source.text())
        self.assertNotEqual(self.w.metric_labels['result'][0].text(),'全部通过')

    def test_missing_response_never_becomes_zero(self):
        cap=copy.deepcopy(self.show());cap.metadata['rules'][0]['observed_b_tick']=None
        self.w.show_capture(cap);self.assertEqual(self.w.metric_labels['gpio'][0].text(),'—')

    def test_disabled_rules_not_displayed_as_missing_failure(self):
        self.show();self.assertIn('2 条',self.w.metric_labels['result'][1].text())
        self.assertEqual(self.w.result_table.rowCount(),4)

    def test_detail_and_compact_tables_read_only(self):
        for table in (self.w.compact_events,self.w.event_table,self.w.result_table):
            self.assertEqual(table.editTriggers(),QAbstractItemView.EditTrigger.NoEditTriggers)

    def test_simple_events_hide_identity_nibbles_but_raw_retained(self):
        cap=self.show();self.assertGreater(self.w.event_table.rowCount(),self.w.compact_events.rowCount())
        self.assertEqual(len(self.w.shown_events),self.w.event_table.rowCount())
        self.assertGreater(self.w.compact_events.rowCount(),2)

    def test_reference_changes_compact_times_without_record_mutation(self):
        cap=self.show();before=copy.deepcopy(cap);self.w.wave.reference_combo.setCurrentIndex(1)
        for row,event in enumerate(self.w._compact_events):
            self.assertAlmostEqual(float(self.w.compact_events.item(row,0).text()),self.w.wave._pos(event['tick']),places=6)
        self.assertEqual(cap,before)

    def test_units_explicit_and_no_auto_si(self):
        self.show();distance=abs(self.w.wave.cursors[1].value()-self.w.wave.cursors[0].value())
        self.w.wave.unit_combo.setCurrentIndex(1)
        self.assertIn(f'{distance*1000:.6f} μs',self.w.wave.compact_measurement.text())
        self.assertFalse(self.w.wave.axis.autoSIPrefix)

    def test_simple_event_click_uses_its_actual_tick(self):
        self.show();self.w.compact_events.cellClicked.emit(0,0)
        self.assertAlmostEqual(self.w.wave.cursors[0].value(),self.w.wave._pos(self.w._compact_events[0]['tick']))

    def test_view_change_never_opens_serial_or_mutates_config(self):
        cap=self.show();before=copy.deepcopy(cap)
        for i in range(self.w.tabs.count()):self.w.tabs.setCurrentIndex(i)
        self.w.config_toggle.setChecked(True);self.w.workload_deadline.setCurrentIndex(0);self.w.gate()
        self.assertEqual(cap,before);self.assertIsNone(self.w.client)

    def test_advanced_config_is_accessible_and_original_editors_kept(self):
        self.w.config_toggle.setChecked(True)
        self.assertEqual(self.w.tabs.currentIndex(),self.w.compact_config_index)
        self.assertEqual(len(self.w.editors),4);self.assertEqual(len(self.w.read_draft().to_bytes()),100)

    def test_unsaved_live_record_warning_and_release_block(self):
        self.show();self.w.replay=False;self.w.saved=False;self.w.gate()
        self.assertIn('尚未保存',self.w.compact_save_state.text());self.assertFalse(self.w.buttons['release'].isEnabled())

    def test_replay_cannot_release_live_buffer(self):
        cap=self.show();m=cap.metadata
        self.w.client=Mock(uncertain=False,session_id=m['session_id'],config_id=m['config_id'])
        self.w.device_state='FROZEN';self.w.applied=Config.from_dict(m['config']);self.w.gate()
        self.assertFalse(self.w.buttons['release'].isEnabled())

    def test_unknown_link_status_does_not_look_ready(self):
        self.show();self.w.device_state='UNKNOWN';self.w.gate()
        self.assertIn('状态未确认',self.w.compact_state.text());self.assertFalse(self.w.buttons['arm'].isEnabled())

    def test_raw_save_cancel_preserves_unsaved_flag(self):
        self.show();self.w.saved=False;self.w.replay=False
        with patch.object(QFileDialog,'getSaveFileName',return_value=('','')):self.assertFalse(self.w.save())
        self.assertFalse(self.w.saved)

    def test_raw_save_real_roundtrip_via_retained_handler(self):
        cap=self.show();self.w.saved=False;path=Path(self.temp.name)/'saved.tscap'
        with patch.object(QFileDialog,'getSaveFileName',return_value=(str(path),'')):self.assertTrue(self.w.save())
        self.assertEqual(load_record(path),cap);self.assertTrue(self.w.saved)

    def test_action_control_labels_short_but_all_safety_controls_retained(self):
        self.assertEqual(self.w.workload_run.text(),'开始实验')
        self.assertEqual(self.w.connect_button.text(),'连接设备')
        self.assertEqual(set(self.w.buttons),{'defaults','configure','arm','stop','read','save','export','release','batch'})

    def test_failed_operation_message_not_replaced_by_friendly_status(self):
        self.show();self.w.device_state='UNKNOWN';self.w.summary.setText('未完成：test · 板端记录未自动释放');self.w.gate()
        self.assertIn('未完成：test',self.w.compact_state.text())

    def test_frozen_friendly_label_still_explains_retention(self):
        self.w.device_state='FROZEN';self.w.gate();self.assertIn('板端记录保留',self.w.compact_state.text())

    def test_small_window_keeps_usable_events_and_four_statistics_rows(self):
        self.show();self.w.resize(1366,900);self.w.show();self.app.processEvents()
        self.assertGreaterEqual(self.w.tabs.height(),220);self.assertGreater(self.w.wave.width(),800)
        self.assertTrue(self.w.compact_events.isVisible())

    def test_only_display_modules_changed_backend_safety_used(self):
        self.assertIsNotNone(self.w.buttons['export'].menu());self.assertEqual(self.w.data_splitter.count(),2)
        self.assertTrue(self.w.compact_ready)

    def test_empty_placeholder_hidden_after_capture(self):
        self.assertFalse(self.w.empty_wave_hint.isHidden());self.show()
        self.assertTrue(self.w.empty_wave_hint.isHidden())

    def test_stale_live_record_warning_is_not_hidden(self):
        cap=self.show();self.w.client=Mock(uncertain=False,session_id=cap.metadata['session_id']+1,config_id=cap.metadata['config_id'])
        self.w.set_status(dict(state='FROZEN',session_id=cap.metadata['session_id']+1,config_id=cap.metadata['config_id']))
        self.assertIn('旧记录',self.w.compact_state.text())

    def test_advanced_controls_do_not_overlap_scroll_view(self):
        self.show();self.w.resize(1366,900);self.w.show();self.w.config_toggle.setChecked(True);self.app.processEvents()
        panel=self.w.config_panel
        self.assertLessEqual(panel.y()+panel.height(),self.w.draft.y())

    def test_port_labels_short_but_original_description_preserved(self):
        self.w.port.addItem('COM99 · USB-SERIAL CH340 (COM99)','COM99');self.w.gate()
        i=self.w.port.findData('COM99');self.assertEqual(self.w.port.itemText(i),'COM99 · CH340')
        self.assertIn('USB-SERIAL',self.w.port.itemData(i,Qt.ItemDataRole.ToolTipRole))

    def test_queued_disconnect_during_worker_does_not_close_transport(self):
        self.w.client=Mock(uncertain=False);self.w.transport=Mock();self.w.busy=True
        self.w.toggle_connection();self.w.transport.close.assert_not_called();self.assertIsNotNone(self.w.client)
        self.w.busy=False

    def test_queued_connect_during_worker_does_not_open_transport(self):
        self.w.busy=True;self.w.toggle_connection();self.assertIsNone(self.w.client);self.w.busy=False

    def test_actual_worker_finishes_before_disconnect_closes_handle(self):
        self.w.client=Mock(uncertain=False);self.w.transport=Mock();transport=self.w.transport;self.w.device_state='FROZEN'
        done=threading.Event();self.w.start(lambda t:done.wait(2),lambda result:None)
        self.assertTrue(self.w.busy);self.w.toggle_connection();transport.close.assert_not_called()
        done.set();until=time.monotonic()+3
        while self.w.busy and time.monotonic()<until:self.app.processEvents();time.sleep(.005)
        self.assertFalse(self.w.busy);self.w.toggle_connection();transport.close.assert_called_once()
        self.assertIsNone(self.w.client)

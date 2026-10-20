"""Always-visible diagnostics do not open a port or change the captured evidence."""
import copy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch
from PySide6.QtWidgets import QApplication
from mvp5.store import load_record

FIXTURE=(Path(__file__).resolve().parents[3] / 'fixtures/ui049/native_saved_job25.tscap')

class DiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from mvp5.ui import MainWindow
        self.temp=TemporaryDirectory();self.guard=patch('mvp5.ui.SerialTransport',side_effect=AssertionError('No serial in UI tests'))
        self.guard.start();self.w=MainWindow(root=self.temp.name);self.w.timer.stop()
    def tearDown(self):
        self.w.capture=self.w.client=None;self.w.close();self.w.deleteLater();self.app.processEvents();self.guard.stop();self.temp.cleanup()
    def load(self):
        cap=load_record(FIXTURE);self.w.saved=self.w.replay=True;self.w.show_capture(cap);return cap
    def test_panel_visible_when_switching_tabs(self):
        self.load();self.w.show()
        for i in range(self.w.tabs.count()):
            self.w.tabs.setCurrentIndex(i);self.app.processEvents();self.assertTrue(self.w.device_diagnostics.isVisible())
        self.assertNotIn('设备与诊断',[self.w.tabs.tabText(i) for i in range(self.w.tabs.count())])
        self.assertIn('原始元数据',[self.w.tabs.tabText(i) for i in range(self.w.tabs.count())])
    def test_disconnected_frozen_does_not_look_like_live_state(self):
        self.load();self.w.device_state='FROZEN';self.w.gate()
        self.assertIn('未确认（未连接）',self.w.diagnostic_fields['board'].text())
        self.assertIn('回放',self.w.diagnostic_fields['record'].text())
    def test_empty_does_not_fabricate_zero_errors(self):
        self.assertEqual(self.w.diagnostic_fields['transfer'].text(),'传输：等待记录')
    def test_connected_frozen_and_unknown_are_distinct(self):
        self.w.client=Mock(uncertain=False);self.w.device_state='FROZEN';self.w.gate()
        self.assertIn('板端记录保留',self.w.diagnostic_fields['board'].text())
        self.w.client.uncertain=True;self.w.gate();self.assertIn('状态未确认',self.w.diagnostic_fields['board'].text())
    def test_raw_retained_while_diagnostics_update(self):
        cap=self.load();before=copy.deepcopy(cap);self.w.tabs.setCurrentIndex(4);self.w.gate()
        self.assertEqual(cap,before);self.assertIn('CRC 0',self.w.diagnostic_fields['transfer'].text())
    def test_partial_and_missing_counters_are_explicit(self):
        cap=copy.deepcopy(self.load());cap.metadata.pop('crc_errors',None);cap.metadata.pop('transport_retries',None)
        self.w.show_capture(replace(cap,samples=cap.samples[:41]))
        self.assertIn('数据不完整',self.w.diagnostic_fields['transfer'].text());self.assertIn('CRC —',self.w.diagnostic_fields['transfer'].text())
    def test_small_window_keeps_four_cards_and_table(self):
        self.load();self.w.resize(1366,900);self.w.show();self.app.processEvents()
        self.assertGreaterEqual(self.w.tabs.height(),220);self.assertGreater(self.w.device_diagnostics.width(),180)
        self.assertLess(self.w.device_diagnostics.geometry().right(),self.w.width())

if __name__=='__main__':unittest.main()

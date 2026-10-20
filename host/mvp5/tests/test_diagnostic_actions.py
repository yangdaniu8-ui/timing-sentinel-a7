"""Targeted task/export logging checks; serial opens are forbidden."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from app.diagnostics import SessionDiagnostics
from mvp5.store import load_record

FIXTURE=(Path(__file__).resolve().parents[3] / 'fixtures/ui049/native_saved_job25.tscap')

class DiagnosticActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from mvp5.ui import MainWindow
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.guard=patch('mvp5.ui.SerialTransport',side_effect=AssertionError('No serial in offline diagnostics test'))
        self.guard.start();self.window=MainWindow(root=self.root/'captures');self.window.timer.stop()
        self.capture=load_record(FIXTURE)
        self.window.replay=self.window.saved=True;self.window.show_capture(self.capture)
        self.window.show();self.app.processEvents()
    def tearDown(self):
        self.window.close();self.window.deleteLater();self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.app.processEvents();self.guard.stop();self.temp.cleanup()
    def wait(self):
        deadline=time.monotonic()+15
        while self.window.busy and time.monotonic()<deadline:self.app.processEvents();time.sleep(.002)
        self.assertFalse(self.window.busy)
        self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()
    def entries(self,session):
        return [json.loads(line) for line in (session.directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    def export_to(self,path):
        with patch('mvp5.ui.QFileDialog.getSaveFileName',return_value=(str(path),'')):
            self.window.export_menu.actions()[1 if path.suffix=='.csv' else 2].trigger()
        self.wait()
    def assert_protected(self):
        self.assertEqual(self.window.capture,self.capture)
        self.assertTrue(self.window.saved and self.window.replay)
        self.assertIsNone(self.window.client);self.assertIsNone(self.window.transport)

    def test_csv_vcd_record_all_task_stages_without_changing_capture(self):
        from mvp5.export_checks import verify_csv,verify_vcd
        with SessionDiagnostics(self.root/'logs') as session:
            for kind,verify in (('csv',verify_csv),('vcd',verify_vcd)):
                dest=self.root/('wave.'+kind);self.export_to(dest);verify(self.capture,dest)
                self.assert_protected()
        entries=self.entries(session);names=[e['event'] for e in entries]
        for stage in ('export_requested','export_destination_selected','task_create_begin','task_created',
                      'task_qobject_init_begin','task_qobject_init_complete',
                      'task_connect_progress','task_connect_message','task_connect_success','task_connect_failure',
                      'task_connect_finished','task_start','task_worker_enter','task_worker_success',
                      'task_worker_exit','export_completed','task_finished','task_delete_scheduled'):
            self.assertEqual(names.count(stage),2,stage)
        self.assertNotIn('task_worker_failure',names)
        self.assertNotIn('uncaught_callback_exception',names)
        gui_thread=next(e['thread'] for e in entries if e['event']=='session_start')
        self.assertTrue(all(e['thread']==gui_thread for e in entries if e['event'] in ('export_completed','task_finished')))
        self.assertTrue(all(e['thread']!=gui_thread for e in entries if e['event']=='task_worker_enter'))

    def test_cancel_does_not_create_task_or_modify_evidence(self):
        with SessionDiagnostics(self.root/'logs') as session,patch('mvp5.ui.QFileDialog.getSaveFileName',return_value=('','')):
            self.window.export('csv')
        self.assertEqual([e['event'] for e in self.entries(session)].count('export_requested'),1)
        self.assertNotIn('task_create_begin',[e['event'] for e in self.entries(session)])
        self.assertFalse(self.window.busy);self.assert_protected()

    def test_export_failure_preserves_original_and_logs_no_false_completion(self):
        with SessionDiagnostics(self.root/'logs') as session,patch('mvp5.ui.QFileDialog.getSaveFileName',return_value=(str(self.root/'wrong.tscap'),'')),patch('mvp5.ui.QMessageBox.warning') as warning:
            self.window.export('csv');self.wait();warning.assert_called_once()
        names=[e['event'] for e in self.entries(session)]
        self.assertIn('task_worker_failure',names);self.assertIn('task_finished',names)
        self.assertNotIn('export_completed',names)
        self.assertFalse((self.root/'wrong.tscap').exists());self.assert_protected()

if __name__=='__main__':unittest.main()

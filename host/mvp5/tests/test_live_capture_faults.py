"""Offline live-UI fault injection. No serial, board, ARM, or release."""
import copy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock,patch
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from app.diagnostics import SessionDiagnostics
from app.services.device_client import DeviceFailure,Cancelled
from mvp5.client import Client
from mvp5.store import save_record,load_record
from mvp5.tests.test_mvp4 import OracleTransport,fixture
from protocol.messages import Command,READ


class PrefixFaultTests(unittest.TestCase):
    def test_actual_reader_preserves_valid_prefix_on_transfer_failure(self):
        for error in (DeviceFailure('injected USB disconnect'),Cancelled(),ValueError('injected bad chunk')):
            with self.subTest(error=type(error).__name__):
                cap=fixture();metadata=copy.deepcopy(cap.metadata);metadata['valid_count']=4096
                cap=replace(cap,metadata=metadata,samples=b'\x01'*4096).validate()
                client=Client(OracleTransport());client.connect();client.session_id=client.config_id=1
                def request(command,body=b''):
                    if command==Command.META:return b'fixture-only'
                    session,offset,count=READ.unpack(body)
                    if offset:raise error
                    return READ.pack(session,offset,count)+cap.samples[:count]
                client.request=Mock(side_effect=request)
                with patch('mvp5.client.decode_meta',return_value=copy.deepcopy(cap.metadata)):
                    partial=client.read_capture()
                self.assertTrue(client.uncertain);self.assertFalse(partial.complete)
                self.assertEqual(partial.samples,cap.samples[:1004]);self.assertIn('transfer_error',partial.metadata)


class LiveCaptureFaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from mvp5.ui import MainWindow
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.guard=patch('mvp5.ui.SerialTransport',side_effect=AssertionError('Real serial access forbidden'))
        self.guard.start();self.window=MainWindow(root=self.root);self.window.timer.stop()
        self.client=Client(OracleTransport());self.client.connect();self.client.session_id=self.client.config_id=1
        self.client.arm=Mock(side_effect=AssertionError('No ARM'))
        self.client.release=Mock(side_effect=AssertionError('No release'))
        self.transport=Mock();self.window.client=self.client;self.window.transport=self.transport
        self.window.device_state='FROZEN';self.cap=fixture()
        self.client.status=Mock(return_value=dict(state='FROZEN',session_id=1,config_id=1))
        self.session=SessionDiagnostics(self.root/'logs').__enter__()
    def tearDown(self):
        self.client.arm.assert_not_called();self.client.release.assert_not_called()
        self.session.__exit__(None,None,None)
        self.window.saved=True;self.window.capture=self.window.client=self.window.transport=None
        self.window.close();self.window.deleteLater();self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.app.processEvents();self.guard.stop();self.temp.cleanup()
    def wait(self):
        deadline=time.monotonic()+10
        while self.window.busy and time.monotonic()<deadline:self.app.processEvents();time.sleep(.002)
        self.assertFalse(self.window.busy)
    def records(self):
        return [json.loads(line) for line in (self.session.directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_post_read_status_disconnect_keeps_completed_capture(self):
        self.client.read_capture=Mock(return_value=self.cap)
        def disconnect():
            self.client.uncertain=True
            raise DeviceFailure('injected disconnect after all waveform bytes arrived')
        self.client.status.side_effect=disconnect
        with patch('mvp5.ui.QMessageBox.warning'):
            self.window.read_capture();self.wait()
        self.assertIs(self.window.capture,self.cap)
        self.assertFalse(self.window.saved or self.window.replay)
        self.assertEqual(self.window.device_state,'UNKNOWN')
        self.assertTrue(self.window.buttons['save'].isEnabled())

    def test_partial_capture_is_visible_saveable_and_not_released(self):
        metadata=copy.deepcopy(self.cap.metadata);metadata['transfer_complete']=False
        partial=replace(self.cap,metadata=metadata,samples=self.cap.samples[:20])
        def interrupted(progress):
            progress(20,64);self.client.uncertain=True;return partial
        self.client.read_capture=Mock(side_effect=interrupted)
        self.window.read_capture();self.wait()
        self.assertIs(self.window.capture,partial);self.assertEqual(self.window.device_state,'UNKNOWN')
        self.assertFalse(self.window.saved);self.client.status.assert_not_called()
        path=self.root/'partial.tscap';save_record(self.window.capture,path)
        self.assertEqual(load_record(path),partial)

    def test_other_followup_status_failures_also_preserve_received_bytes(self):
        self.client.read_capture=Mock(return_value=self.cap)
        for error in (Cancelled(),ConnectionError('unplug'),OSError('driver'),TimeoutError('timeout'),ValueError('invalid status')):
            with self.subTest(error=type(error).__name__):
                self.client.uncertain=False;self.window.capture=None
                self.client.status.side_effect=error
                self.window.read_capture();self.wait()
                self.assertIs(self.window.capture,self.cap)
                self.assertTrue(self.client.uncertain)
                self.assertEqual(self.window.device_state,'UNKNOWN')
                self.assertTrue(self.window.buttons['save'].isEnabled())
                self.assertFalse(self.window.buttons['arm'].isEnabled() or self.window.buttons['release'].isEnabled())

    def test_read_error_before_capture_preserves_old_record(self):
        old=self.cap;self.window.saved=True;self.window.replay=True;self.window.show_capture(old)
        self.client.read_capture=Mock(side_effect=DeviceFailure('injected META disconnect'))
        with patch('mvp5.ui.QMessageBox.warning') as warning:
            self.window.read_capture();self.wait();warning.assert_called_once()
        self.assertIs(self.window.capture,old);self.assertTrue(self.window.saved and self.window.replay)
        self.assertEqual(self.window.device_state,'UNKNOWN')

    def test_busy_read_blocks_disconnect_and_overlapping_poll(self):
        entered=Event();finish=Event()
        def slow_read(progress):
            entered.set()
            if not finish.wait(3):raise TimeoutError('probe gate was not released')
            progress(64,64);return self.cap
        self.client.read_capture=Mock(side_effect=slow_read)
        self.window.read_capture();self.assertTrue(entered.wait(2));original_task=self.window.task
        try:
            for _ in range(6):self.window.toggle_connection();self.window.poll();self.app.processEvents()
            self.assertIs(self.window.task,original_task);self.assertTrue(self.window.busy)
            self.transport.close.assert_not_called();self.client.read_capture.assert_called_once()
        finally:finish.set();self.wait()
        self.assertIs(self.window.capture,self.cap)

    def test_progress_and_render_callbacks_stay_on_gui_thread(self):
        def read(progress):
            for point in (0,16,32,48,64):progress(point,64)
            return self.cap
        self.client.read_capture=Mock(side_effect=read)
        self.window.read_capture();self.wait()
        entries=self.records();gui_thread=next(e['thread'] for e in entries if e['event']=='session_start')
        for event in ('capture_apply_begin','capture_render_begin','capture_wave_rendered','capture_events_rendered','capture_render_complete','task_finished'):
            selected=[e for e in entries if e['event']==event]
            self.assertEqual(len(selected),1,event);self.assertEqual(selected[0]['thread'],gui_thread,event)
        self.assertTrue(all(e['thread']!=gui_thread for e in entries if e['event']=='capture_read_begin'))

if __name__=='__main__':unittest.main()

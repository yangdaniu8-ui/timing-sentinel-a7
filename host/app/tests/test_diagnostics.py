"""Local diagnostics must preserve behavior, handlers, and prior evidence."""
import faulthandler
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import Mock, patch
from app.diagnostics import SessionDiagnostics, record_event


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def read(self,session):
        return [json.loads(line) for line in (session.directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_start_events_and_normal_exit_restore_state(self):
        previous=sys.excepthook;previous_fault=faulthandler.is_enabled()
        with SessionDiagnostics(self.root) as session:
            record_event('example',kind='csv')
        entries=self.read(session)
        self.assertEqual(entries[0]['event'],'session_start')
        self.assertEqual(entries[-1]['event'],'session_end')
        self.assertEqual(entries[-1]['exit_code'],0)
        self.assertIn('example',[e['event'] for e in entries])
        self.assertIs(sys.excepthook,previous)
        self.assertEqual(faulthandler.is_enabled(),previous_fault)

    def test_unique_sessions_do_not_overwrite_prior_logs(self):
        with SessionDiagnostics(self.root) as one:record_event('first')
        original=(one.directory/'events.jsonl').read_bytes()
        with SessionDiagnostics(self.root) as two:record_event('second')
        self.assertNotEqual(one.directory,two.directory)
        self.assertEqual(original,(one.directory/'events.jsonl').read_bytes())

    def test_uncaught_main_exception_is_logged_and_not_swallowed(self):
        with self.assertRaisesRegex(ValueError,'probe'):
            with SessionDiagnostics(self.root) as session:raise ValueError('probe')
        error=next(e for e in self.read(session) if e['event']=='uncaught_main_exception')
        self.assertEqual(error['exception_type'],'ValueError')
        self.assertIn('ValueError: probe',error['traceback'])

    def test_callback_hook_chains_to_existing_handler(self):
        old=Mock()
        with patch.object(sys,'excepthook',old):
            with SessionDiagnostics(self.root) as session:
                try:raise RuntimeError('callback probe')
                except RuntimeError:sys.excepthook(*sys.exc_info())
            self.assertIs(sys.excepthook,old)
        old.assert_called_once()
        self.assertIn('uncaught_callback_exception',[e['event'] for e in self.read(session)])

    def test_preexisting_fault_handler_is_not_replaced_or_disabled(self):
        with patch('app.diagnostics.faulthandler.is_enabled',return_value=True),patch('app.diagnostics.faulthandler.enable') as enable,patch('app.diagnostics.faulthandler.disable') as disable:
            with SessionDiagnostics(self.root) as session:pass
            enable.assert_not_called();disable.assert_not_called()
        entry=next(e for e in self.read(session) if e['event']=='fault_handler')
        self.assertFalse(entry['owned'])

    def test_unwritable_log_directory_does_not_break_app(self):
        with patch('app.diagnostics.Path.mkdir',side_effect=PermissionError('read only')),patch.object(sys,'stderr',None):
            with SessionDiagnostics(self.root) as session:record_event('still_running')
        self.assertIsNone(session.directory);self.assertIn('PermissionError',session.error)

    def test_bad_diagnostic_payload_does_not_change_task_behavior(self):
        with SessionDiagnostics(self.root) as session:
            record_event('non_json_value',value=object())
            record_event('still_running')
        self.assertIn('still_running',[e['event'] for e in self.read(session)])

    def test_worker_thread_logging_is_complete_json(self):
        with SessionDiagnostics(self.root) as session:
            threads=[Thread(target=lambda:[record_event('worker_probe',index=i) for i in range(25)]) for _ in range(4)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
        entries=[e for e in self.read(session) if e['event']=='worker_probe']
        self.assertEqual(len(entries),100)

    def test_system_exit_code_is_retained(self):
        with self.assertRaises(SystemExit):
            with SessionDiagnostics(self.root) as session:raise SystemExit(7)
        self.assertEqual(self.read(session)[-1]['exit_code'],7)

    def test_nested_session_rejected_without_damaging_outer(self):
        with SessionDiagnostics(self.root) as session:
            with self.assertRaises(RuntimeError):
                with SessionDiagnostics(self.root):pass
            record_event('outer_continues')
        self.assertIn('outer_continues',[e['event'] for e in self.read(session)])

if __name__=='__main__':unittest.main()

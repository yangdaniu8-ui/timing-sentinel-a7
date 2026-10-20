"""Background STATUS polling must not bounce the dashboard or weaken serial guards."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock,patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mvp5.ui import MainWindow
from mvp5.models import Config
from mvp5.store import load_record

FIXTURE=(Path(__file__).resolve().parents[3] / 'fixtures/ui049/protected_frozen.tscap')

class PollLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=TemporaryDirectory()
        self.guard=patch('mvp5.ui.SerialTransport',side_effect=AssertionError('UI test must not open serial'))
        self.guard.start()
        self.w=MainWindow(root=self.temp.name,settings_path=Path(self.temp.name)/'settings.json')
        self.w.timer.stop()
        self.w.client=Mock(uncertain=False,session_id=1,config_id=2)
        self.w.transport=Mock()
        self.status=dict(state='FROZEN',session_id=1,config_id=2)
        self.w.set_status(self.status)
        self.w.show()
        self.settle()
        self.finish_worker=None

    def tearDown(self):
        if self.finish_worker:self.finish_worker.set()
        self.wait_idle()
        self.w.capture=self.w.client=None;self.w.transport=None
        self.w.close();self.w.deleteLater();self.app.processEvents()
        self.guard.stop();self.temp.cleanup()

    def settle(self):
        for _ in range(4):self.app.processEvents();QTest.qWait(5)

    def geometry(self):
        widgets=[self.w,self.w.data_splitter,self.w.device_diagnostics,self.w.tabs,
                 self.w.workload_run,self.w.connect_button,*[p[0].parentWidget() for p in self.w.metric_labels.values()]]
        return [widget.geometry().getRect() for widget in widgets]

    def wait_idle(self):
        deadline=time.monotonic()+3
        while self.w.busy and time.monotonic()<deadline:
            self.app.processEvents();QTest.qWait(5)
        self.assertFalse(self.w.busy,'Worker did not finish')
        self.settle()

    def begin_poll(self):
        entered=Event();self.finish_worker=Event()
        def observe():
            entered.set()
            if not self.finish_worker.wait(2):raise TimeoutError('Test did not release worker')
            return dict(status=self.status,config=None)
        self.w.client.observe.side_effect=observe
        self.w.poll();self.assertTrue(entered.wait(1));self.settle()

    def test_background_poll_keeps_geometry_without_loaded_record(self):
        before=self.geometry()
        self.begin_poll()
        self.assertEqual(self.geometry(),before)
        self.assertFalse(self.w.progress.isVisible())
        self.assertFalse(self.w.cancel_button.isVisible())
        self.assertNotIn('操作中',self.w.diagnostic_fields['board'].text())

    def test_repeated_poll_keeps_geometry_with_saved_record(self):
        cap=load_record(FIXTURE);self.w.saved=self.w.replay=True
        self.w.show_capture(cap);self.w.applied=Config.from_dict(cap.metadata['config'])
        self.w.resize(1366,900);self.settle();before=self.geometry()
        for _ in range(4):
            self.begin_poll();self.assertEqual(self.geometry(),before)
            self.finish_worker.set();self.wait_idle();self.assertEqual(self.geometry(),before)
        self.assertIs(self.w.capture,cap)
        self.w.client.arm.assert_not_called();self.w.client.configure.assert_not_called()
        self.w.client.release.assert_not_called();self.w.transport.close.assert_not_called()

    def test_background_poll_keeps_worker_exclusion(self):
        self.begin_poll();task=self.w.task
        self.w.toggle_connection();self.w.poll()
        self.assertIs(self.w.task,task);self.assertTrue(self.w.busy)
        self.w.client.observe.assert_called_once();self.w.transport.close.assert_not_called()
        self.assertFalse(self.w.buttons['arm'].isEnabled())
        self.assertFalse(self.w.buttons['release'].isEnabled())
        self.finish_worker.set();self.wait_idle()
        self.assertFalse(self.w.background_observe)

    def test_foreground_task_keeps_progress_and_reserved_geometry(self):
        before=self.geometry();self.finish_worker=Event()
        def foreground(task):
            if not self.finish_worker.wait(2):raise TimeoutError('Test gate')
        self.w.start(foreground,lambda value:None);self.settle()
        self.assertTrue(self.w.progress.isVisible());self.assertTrue(self.w.cancel_button.isVisible())
        self.assertEqual(self.geometry(),before)
        self.finish_worker.set();self.wait_idle();self.assertEqual(self.geometry(),before)

    def test_poll_failure_not_disguised_as_connected_ready(self):
        self.w.client.observe.side_effect=TimeoutError('injected STATUS timeout')
        with patch('mvp5.ui.QMessageBox.warning') as warning:
            self.w.poll();self.wait_idle();warning.assert_called_once()
        self.assertEqual(self.w.device_state,'UNKNOWN')
        self.assertIn('未完成',self.w.compact_state.text())
        self.assertFalse(self.w.buttons['arm'].isEnabled())

    def button_states(self):
        return [button.isEnabled() for button in (self.w.connect_button,self.w.workload_run,
                self.w.buttons['read'],self.w.open_button,self.w.storage_button)]

    def test_buttons_do_not_flash_disabled_during_background_poll(self):
        before=self.button_states();self.begin_poll()
        self.assertEqual(self.button_states(),before)
        self.finish_worker.set();self.wait_idle();self.assertEqual(self.button_states(),before)

    def test_disconnect_click_waits_for_poll_then_closes_once(self):
        transport=self.w.transport
        self.begin_poll();task=self.w.task
        self.w.connect_button.click();self.w.connect_button.click()
        self.assertIs(self.w.task,task);transport.close.assert_not_called()
        self.finish_worker.set();self.wait_idle()
        transport.close.assert_called_once();self.assertIsNone(self.w.client)
        self.assertIsNone(self.w._pending_user_action)

    def test_explicit_foreground_action_waits_for_worker_and_runs_once(self):
        calls=[];self.begin_poll()
        def operation(task):calls.append('foreground');return 42
        done=Mock()
        self.w.start(operation,done);self.w.start(operation,done)
        self.assertEqual(calls,[])
        self.finish_worker.set();self.wait_idle()
        self.assertEqual(calls,['foreground']);done.assert_called_once_with(42)

    def test_changed_config_cancels_queued_device_action(self):
        self.begin_poll();operation=Mock()
        self.w.start(operation,Mock());self.w.client.config_id=3
        self.finish_worker.set();self.wait_idle()
        operation.assert_not_called();self.assertIn('未执行',self.w.log.toPlainText())

    def test_failed_observation_does_not_run_queued_device_action(self):
        self.begin_poll();operation=Mock()
        self.w.start(operation,Mock());self.w.client.uncertain=True
        self.finish_worker.set();self.wait_idle();operation.assert_not_called()

    def test_save_click_waits_without_losing_original_handler(self):
        self.begin_poll()
        with patch('mvp5.ui.QFileDialog.getSaveFileName') as dialog:
            self.w.save();dialog.assert_not_called()
            self.finish_worker.set();self.wait_idle()
            dialog.assert_not_called()  # No record loaded, so the real save gate still applies.

if __name__=='__main__':unittest.main()

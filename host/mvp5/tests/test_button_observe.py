import unittest
from unittest.mock import Mock
from mvp5.client import Client
from mvp5.examples import spi_example
from app.services.device_client import DeviceFailure
from PySide6.QtWidgets import QApplication
from mvp5.ui import MainWindow
from mvp5.tests.test_mvp4 import OracleTransport,fixture

class ButtonObserveTests(unittest.TestCase):
    def client(self):
        c=Client(Mock());c.config_id=1;c.session_id=0
        c.configure=Mock(side_effect=AssertionError('No config writes'))
        c.arm=Mock(side_effect=AssertionError('No automatic ARM'))
        c.release=Mock(side_effect=AssertionError('No discard'))
        return c
    def test_same_config_status_only(self):
        c=self.client();s=dict(state='FROZEN',session_id=8,config_id=1)
        c.status=Mock(return_value=s);c.get_config=Mock()
        self.assertEqual(c.observe(),dict(status=s,config=None));c.get_config.assert_not_called()
    def test_external_config_rechecked(self):
        c=self.client();before=dict(state='IDLE',session_id=0,config_id=2)
        after=dict(state='FROZEN',session_id=1,config_id=2)
        c.status=Mock(side_effect=[before,after])
        def config():c.config_id=2;return spi_example()
        c.get_config=Mock(side_effect=config)
        self.assertEqual(c.observe(),dict(status=after,config=spi_example().as_dict()))
        self.assertEqual(c.status.call_count,2)
        c.configure.assert_not_called();c.arm.assert_not_called();c.release.assert_not_called()
    def test_changing_config_not_silently_accepted(self):
        c=self.client();c.status=Mock(side_effect=[dict(config_id=2),dict(config_id=3)])
        def config():c.config_id=2;return spi_example()
        c.get_config=Mock(side_effect=config)
        with self.assertRaises(DeviceFailure):c.observe()
        self.assertTrue(c.uncertain)
    def test_external_same_config_session_still_observed(self):
        c=self.client();c.status=Mock(return_value=dict(state='FROZEN',session_id=33,config_id=1))
        self.assertEqual(c.observe()['status']['session_id'],33)

class ButtonObserveGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.w=MainWindow();self.w.timer.stop()
        self.w.client=Client(OracleTransport());self.w.client.connect()
        self.w.applied=spi_example();self.w.start=Mock()
    def tearDown(self):
        self.w.busy=False;self.w.capture=None;self.w.client=None
        self.w.device_state='DISCONNECTED';self.w.close()
    def test_idle_and_frozen_polled_without_auto_arm(self):
        for state in ('IDLE','FROZEN'):
            self.w.device_state=state;self.w.poll()
        self.assertEqual(self.w.start.call_count,2)
    def test_observer_disabled_does_not_poll_idle(self):
        self.w.device_state='IDLE';self.w.observe_auto.setChecked(False);self.w.poll()
        self.w.start.assert_not_called()
    def test_worker_exclusion(self):
        self.w.device_state='IDLE';self.w.busy=True;self.w.poll()
        self.w.start.assert_not_called()
    def test_external_session_keeps_old_unsaved_record(self):
        cap=fixture();self.w.capture=cap;self.w.saved=False;self.w.replay=False
        self.w.client.session_id=2;self.w.client.config_id=2
        self.w.set_status(dict(state='FROZEN',session_id=2,config_id=2))
        self.assertIs(self.w.capture,cap);self.assertFalse(self.w.saved)
        self.assertIn('保留的旧记录',self.w.summary.text())
        self.assertFalse(self.w.buttons['release'].isEnabled())

if __name__=='__main__':unittest.main()

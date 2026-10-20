"""Profile-0 host safety checks; these fixtures are not physical evidence."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from app.models import Config
from app.services.device_client import DeviceClient, DeviceFailure
from app.ui.main_window import MainWindow
from protocol.messages import Command, INFO, PREFIX, STATUS_BODY, Status
from simulator.device import DeviceError, SimulatedDevice
from transport.mock import MockTransport


class BringupFixture(SimulatedDevice):
    def __init__(self):
        super().__init__()
        self.config_id = 0

    def _execute(self, cmd, body):
        if cmd == Command.INFO:
            return INFO.pack(0, 50_000_000, 0, 0, 0, 0, 0, 1024, b"link-a7-0.1.0")
        if cmd == Command.GET_CONFIG:
            return struct.pack("<I", 0) + Config().to_bytes()
        if cmd == Command.STATUS:
            return STATUS_BODY.pack(0, 0, 0, 0, 0, 0, 0, 0, 0)
        if cmd == Command.PING:
            return body
        raise DeviceError(Status.UNSUPPORTED)


class BringupTests(unittest.TestCase):
    def test_handshake_declares_no_capture_and_no_mock_fallback(self):
        client = DeviceClient(MockTransport(BringupFixture()))
        result = client.connect()
        self.assertEqual(result["info"]["profile"], "link_bringup")
        self.assertFalse(client.can_capture)
        self.assertFalse(result["info"]["capture_supported"])
        self.assertEqual(result["info"]["depth"], 0)
        self.assertEqual(result["status"]["config_id"], 0)
        self.assertEqual(result["info"]["origin"], "mock")

    def test_capture_commands_blocked_before_any_write_or_request_id_change(self):
        client = DeviceClient(MockTransport(BringupFixture()))
        client.connect()
        next_id = client.next_id
        with patch.object(client.transport, "write") as write:
            for command in (Command.WRITE_CONFIG, Command.COMMIT, Command.ARM,
                            Command.DISARM, Command.META, Command.EVENTS,
                            Command.SAMPLES, Command.RELEASE):
                with self.subTest(command=command), self.assertRaisesRegex(DeviceFailure, "通信联调"):
                    client.request(command)
            with self.assertRaisesRegex(DeviceFailure, "通信联调"):
                client.configure(Config())
            write.assert_not_called()
        self.assertEqual(client.next_id, next_id)
        self.assertFalse(client.uncertain)
        self.assertEqual(client.status()["state"], "IDLE")

    def test_mixed_or_fake_bringup_capabilities_rejected(self):
        for field, bad_value in ((2,65536),(3,8),(4,1),(5,1),(6,15),(8,b"mvp1-fake")):
            fixture = BringupFixture()
            execute = fixture._execute
            def malformed(cmd, body):
                data = execute(cmd, body)
                if cmd == Command.INFO:
                    fields = list(INFO.unpack(data)); fields[field] = bad_value
                    return INFO.pack(*fields)
                return data
            fixture._execute = malformed
            client = DeviceClient(MockTransport(fixture))
            with self.subTest(field=field), self.assertRaises(DeviceFailure):
                client.connect()
            self.assertTrue(client.uncertain)

    def test_bringup_cannot_claim_session_or_ready_or_counts(self):
        for status in (STATUS_BODY.pack(2,1,0,0,0,0,0,0,0),
                       STATUS_BODY.pack(0,0,1,0,0,0,0,0,0),
                       STATUS_BODY.pack(0,0,0,0,1,0,0,0,0)):
            fixture = BringupFixture()
            execute = fixture._execute
            fixture._execute = lambda cmd, body: status if cmd == Command.STATUS else execute(cmd, body)
            client = DeviceClient(MockTransport(fixture))
            with self.assertRaisesRegex(DeviceFailure, "采集状态"):
                client.connect()

    def test_gui_connects_bringup_with_all_capture_actions_disabled(self):
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            window = MainWindow(auto_connect=False, root=temp)
            window.timer.stop()
            window.device = BringupFixture()
            window.connect_device()
            deadline = time.monotonic()+5
            while window.task is not None:
                application.processEvents()
                if time.monotonic()>deadline: self.fail("GUI handshake timed out")
                time.sleep(.002)
            application.processEvents()
            try:
                self.assertTrue(window.connected, window.feedback.text())
                self.assertIsNone(window.capture)
                self.assertIn("通信联调", window.source.text())
                for button in (window.commit_btn,window.arm_btn,window.stop_btn,
                               window.read_btn,window.release_btn,window.demo_btn,window.stim_btn):
                    self.assertFalse(button.isEnabled())
                self.assertTrue(window.connect_btn.isEnabled())
                self.assertIn("只读规划", window.config_label.text())
                self.assertFalse(window.rate.isEnabled())
                self.assertFalse(window.pre.isEnabled())
            finally:
                window.disconnect_device(confirmed=True)
                window.close()
                application.processEvents()

    def test_later_status_or_configuration_cannot_turn_bringup_into_capture(self):
        fixture = BringupFixture()
        client = DeviceClient(MockTransport(fixture))
        client.connect()
        execute = fixture._execute
        fixture._execute = lambda cmd, body: STATUS_BODY.pack(0,0,0,0,1,0,0,0,0) if cmd == Command.STATUS else execute(cmd,body)
        with self.assertRaisesRegex(DeviceFailure,"采集状态"):
            client.status()
        self.assertTrue(client.uncertain)
        for reply in (struct.pack("<I",1)+Config().to_bytes(),
                      struct.pack("<I",0)+replace(Config(),max_ticks=99999).to_bytes()):
            fixture = BringupFixture()
            client = DeviceClient(MockTransport(fixture))
            client.connect()
            execute = fixture._execute
            fixture._execute = lambda cmd, body: reply if cmd == Command.GET_CONFIG else execute(cmd,body)
            with self.assertRaisesRegex(DeviceFailure,"规划默认值"):
                client.get_config()
            self.assertTrue(client.uncertain)

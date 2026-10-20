from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from app import __version__
from app.models import Capture, Config
from app.runtime import data_root
from app.services.device_client import Cancelled, DeviceClient, DeviceFailure
from app.services.capture_reader import read_capture
from app.services.diagnostics import export_diagnostics
from app.services.record_store import export_vcd, load_record, save_record
from protocol.messages import Command, INFO, PREFIX, READ
from tests.helpers import frozen_capture
from tests.serial_emulator import SerialEmulator
from transport.serial_port import SerialTransport


class V2Tests(unittest.TestCase):
    def test_real_pyserial_adapter_handshake_and_frozen_read(self):
        capture, device, _ = frozen_capture()
        with SerialEmulator(device, line_baud=115200) as emulator:
            transport = SerialTransport(emulator.url, allow_test_url=True)
            try:
                client = DeviceClient(transport)
                result = client.connect()
                self.assertEqual(result["status"]["state"], "FROZEN")
                recovered = read_capture(client)
                self.assertTrue(recovered.complete, recovered.metadata.get("transfer_error"))
                self.assertEqual(recovered.samples, capture.samples)
                self.assertEqual(recovered.events, capture.events)
                self.assertEqual(recovered.metadata["origin"], "mock")
                self.assertEqual(recovered.metadata["transport"], "serial_test")
                self.assertEqual(client.retries, 0)
            finally:
                transport.close()

    def test_incompatible_info_and_truncated_status(self):
        def wrong(response):
            if response.command == (Command.INFO | 0x80):
                fields = list(INFO.unpack(response.payload[PREFIX.size:]))
                fields[5] = 4
                return replace(response, payload=response.payload[:PREFIX.size] + INFO.pack(*fields))
            return response
        with SerialEmulator(transform=wrong) as emulator:
            transport = SerialTransport(emulator.url, allow_test_url=True)
            try:
                with self.assertRaisesRegex(DeviceFailure, "不兼容"):
                    DeviceClient(transport).connect()
            finally:
                transport.close()
        _, _, client = frozen_capture()
        with patch.object(client, "request", return_value=b"bad"):
            with self.assertRaisesRegex(DeviceFailure, "长度"):
                client.status()
        self.assertTrue(client.uncertain)

    def test_open_closed_or_bad_port_is_not_mock_fallback(self):
        with self.assertRaises(ValueError):
            SerialTransport("not-a-port")
        with self.assertRaises(ConnectionError):
            SerialTransport("COM99999")
        _, _, client = frozen_capture()
        client.transport.close()
        with self.assertRaises(DeviceFailure):
            client.status()
        self.assertTrue(client.uncertain)

    def test_read_disconnect_keeps_received_prefix_then_reconnect(self):
        original, device, client = frozen_capture()
        def progress(done, total):
            if done > 2000:
                client.transport.close()
        partial = read_capture(client, progress)
        self.assertFalse(partial.complete)
        self.assertTrue(client.uncertain)
        self.assertEqual(partial.samples, original.samples[:len(partial.samples)])
        self.assertIn("transfer_error", partial.metadata)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prefix.tscap"
            save_record(partial, path)
            self.assertEqual(load_record(path).samples, partial.samples)
        from transport.mock import MockTransport
        new = DeviceClient(MockTransport(device))
        self.assertEqual(new.connect()["status"]["state"], "FROZEN")
        self.assertEqual(read_capture(new).samples, original.samples)

    def test_hardware_origin_roundtrip_and_old_mock_record(self):
        # Schema test only: not evidence of physical measurement.
        original, _, _ = frozen_capture()
        m = dict(original.metadata, origin="hardware", validation_level="test_fixture")
        capture = Capture(m, original.samples, original.events)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.tscap"
            save_record(capture, path)
            self.assertEqual(load_record(path).metadata["origin"], "hardware")
        legacy = Path(__file__).resolve().parents[2] / "data/captures/mock_normal_v0_1.tscap"
        if legacy.exists():
            self.assertEqual(load_record(legacy).metadata["app_version"], "0.1.0")

    def test_vcd_reconstructs_every_received_sample(self):
        full, _, _ = frozen_capture()
        m = dict(full.metadata, transfer_complete=False)
        capture = Capture(m, full.samples[:8000], b"")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "waves.vcd"
            export_vcd(capture, path)
            lines = path.read_text("ascii").splitlines()
            self.assertIn("origin=mock", "\n".join(lines))
            value, changes, tick = 0, {}, None
            for line in lines:
                if line.startswith("#"):
                    tick = int(line[1:])
                    changes[tick] = value
                elif tick is not None and len(line) == 2 and line[0] in "01":
                    channel = ord(line[1]) - 33
                    value = (value & ~(1 << channel)) | (int(line[0]) << channel)
                    changes[tick] = value
            actual, value = bytearray(), 0
            for i in range(len(capture.samples)):
                value = changes.get(i * 100, value)
                actual.append(value)
            self.assertEqual(bytes(actual), capture.samples)
            self.assertEqual(max(changes), (len(capture.samples)-1) * 100)

    def test_anchor_integrity_and_capability_before_write(self):
        capture, _, client = frozen_capture()
        with self.assertRaisesRegex(ValueError, "锚点"):
            Capture(dict(capture.metadata, capture_anchor_timestamp=capture.metadata["capture_anchor_timestamp"] + 1), capture.samples, capture.events).validate()
        client.info["divisor_mask"] = 1
        with patch.object(client, "request") as request:
            with self.assertRaisesRegex(DeviceFailure, "采样率"):
                client.configure(Config())
            request.assert_not_called()

    def test_cancel_after_side_effect_requires_resync(self):
        _, _, client = frozen_capture()
        client.cancel = lambda: True
        with self.assertRaises(Cancelled):
            client.status()
        self.assertTrue(client.uncertain)
        client.cancel = lambda: False
        with self.assertRaisesRegex(DeviceFailure, "重新连接"):
            client.arm()

    def test_writable_packaged_paths_and_diagnostic_privacy(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("sys.frozen", True, create=True), patch.dict("os.environ", {"LOCALAPPDATA": temp}):
                self.assertEqual(data_root(), Path(temp) / "TimingSentinelA7")
            path = Path(temp) / "diagnostics.zip"
            export_diagnostics(path, temp, {"connected": False})
            with zipfile.ZipFile(path) as archive:
                report = json.loads(archive.read("diagnostics.json"))
                self.assertEqual(report["app_version"], __version__)
                self.assertFalse(report["hardware_validation"])
                self.assertFalse(any("samples" in name for name in archive.namelist()))

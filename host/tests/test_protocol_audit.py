"""Regression tests for malformed wire evidence and partial-read coverage."""
from dataclasses import replace
import struct
import unittest
from unittest.mock import patch

from app.models import Capture, Config
from app.services.capture_reader import read_capture
from app.services.device_client import Cancelled, DeviceFailure
from protocol.codec import Frame
from protocol.messages import Command, State, decode_meta, encode_meta, pack_event, unpack_events
from tests.helpers import frozen_capture


class ProtocolAuditTests(unittest.TestCase):
    def test_invalid_meta_inhibits_further_commands_before_data_read(self):
        _, device, client = frozen_capture()
        cases = ({"flags": device.meta["flags"] | 0x80},
                 {"session_id": device.meta["session_id"] + 1},
                 {"config_id": device.meta["config_id"] + 1},
                 {"capture_anchor_timestamp": device.meta["capture_anchor_timestamp"] + 1},
                 {"result_code": 255})
        for changes in cases:
            with self.subTest(changes=changes):
                client.uncertain = False
                raw = encode_meta(dict(device.meta, **changes), device.config.to_bytes())
                with patch.object(client, "request", return_value=raw) as request:
                    with self.assertRaisesRegex(DeviceFailure, "元数据非法"):
                        read_capture(client)
                    request.assert_called_once()
                self.assertTrue(client.uncertain)
                with self.assertRaisesRegex(DeviceFailure, "重新连接"):
                    client.release()

    def test_absent_wire_timestamps_cannot_hide_nonzero_data(self):
        _, device, _ = frozen_capture(scenario="missing")
        # TIMEOUT has no matched B. Reject its nonzero wire value, rather than
        # silently normalizing contradictory evidence to null.
        invalid = dict(device.meta, b_tick=123)
        with self.assertRaisesRegex(ValueError, "absent timestamp"):
            decode_meta(encode_meta(invalid, device.config.to_bytes()))

    def test_invalid_config_does_not_publish_id_and_requires_resync(self):
        _, _, client = frozen_capture()
        old_id = client.config_id
        raw = bytearray(Config().to_bytes())
        raw[21] = 1
        with patch.object(client, "request", return_value=struct.pack("<I", old_id + 1) + raw):
            with self.assertRaisesRegex(DeviceFailure, "配置内容非法"):
                client.get_config()
        self.assertEqual(client.config_id, old_id)
        self.assertTrue(client.uncertain)

    def test_cancel_between_precheck_and_first_attempt_requires_resync(self):
        _, _, client = frozen_capture()
        cancel_checks = iter((False, True))
        client.cancel = lambda: next(cancel_checks)
        with self.assertRaises(Cancelled):
            client.status()
        self.assertTrue(client.uncertain)

    def test_stale_request_response_requires_resync(self):
        _, device, client = frozen_capture()
        device.handle(Frame(Command.PING, client.next_id + 1, struct.pack("<Q", client.token)))
        with self.assertRaisesRegex(DeviceFailure, "STALE_REQUEST"):
            client.status()
        self.assertTrue(client.uncertain)

    def test_disarm_ack_must_confirm_stopped_state(self):
        _, _, client = frozen_capture()
        with patch.object(client, "request", return_value=bytes([State.ARMED])):
            with self.assertRaisesRegex(DeviceFailure, "未确认设备停止"):
                client.disarm()
        self.assertTrue(client.uncertain)

    def test_partial_coverage_uses_received_samples_not_device_size(self):
        _, _, client = frozen_capture(scenario="late")
        def stop_after_first_chunk(done, total):
            client.cancel = lambda: True
        partial = read_capture(client, stop_after_first_chunk)
        m = partial.metadata
        self.assertEqual(len(partial.samples), 1004)
        self.assertTrue(m["a_in_device_window"])
        self.assertFalse(m["a_in_waveform"])
        self.assertFalse(m["decision_in_waveform"])
        self.assertFalse(m["observed_b_in_waveform"])
        self.assertEqual(m["received_sample_count"], 1004)
        self.assertIn("未被本地已收到波形覆盖", m["observation"])
        # Cancellation between completed read requests needs no link repair.
        self.assertFalse(client.uncertain)

    def test_event_reserved_bits_and_other_rule_slots_are_rejected(self):
        base = int.from_bytes(pack_event(10, state=State.ARMED), "little")
        for bit in (86, 97, 99, 103, 112, 127):
            with self.subTest(bit=bit), self.assertRaisesRegex(ValueError, "MVP-1事件"):
                unpack_events((base | (1 << bit)).to_bytes(16, "little"))
        self.assertTrue(unpack_events((base | (1 << 81)).to_bytes(16, "little"))[0]["uart_error"])

    def test_invalid_event_chunk_is_not_added_to_verified_prefix(self):
        original, device, client = frozen_capture()
        device.events = (int.from_bytes(device.events[:16], "little") | (1 << 112)).to_bytes(16, "little") + device.events[16:]
        partial = read_capture(client)
        self.assertEqual(partial.samples, original.samples)
        self.assertEqual(partial.events, b"")
        self.assertFalse(partial.complete)
        self.assertTrue(client.uncertain)

    def test_metadata_types_required_fields_and_result_are_validated(self):
        capture, _, _ = frozen_capture()
        cases = ({"valid_count": "65536"}, {"event_count": True}, {"first_sample_timestamp": None},
                 {"result": "NOT_A_RESULT"}, {"trigger_valid": 1}, {"flags": 0},
                 {"config": {}}, {"accepted": False}, {"deadline_tick": capture.metadata["deadline_tick"] + 1})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(capture, metadata=dict(capture.metadata, **changes)).validate()
        missing = dict(capture.metadata)
        del missing["a_tick"]
        with self.assertRaisesRegex(ValueError, "缺少必要字段"):
            replace(capture, metadata=missing).validate()
        with self.assertRaises(ValueError):
            Capture([], b"", b"").validate()
        with self.assertRaises(ValueError):
            replace(Config(), divisor=True).validate()


if __name__ == "__main__":
    unittest.main()

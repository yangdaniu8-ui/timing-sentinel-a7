from dataclasses import replace
import unittest
import random
import struct
from app.models import Capture, Config, CLOCK_HZ, delta_tick
from app.services.capture_reader import read_capture
from app.services.device_client import DeviceClient, DeviceFailure
from protocol.messages import Action, Command, READ, Result, State, unpack_events
from tests.helpers import frozen_capture, setup_device
from transport.mock import MockTransport


class CaptureTests(unittest.TestCase):
    def test_four_scenarios_and_saved_evidence(self):
        for scenario, result in (("normal", "PASS"), ("early", "EARLY"), ("late", "TIMEOUT"), ("missing", "TIMEOUT")):
            config = replace(Config(), min_ticks=25000 if scenario == "early" else 0)
            capture, _, _ = frozen_capture(config, scenario)
            m = capture.metadata
            self.assertEqual(m["result"], result)
            self.assertTrue(capture.complete)
            self.assertEqual(len(capture.samples), 65536)
            self.assertEqual(m["actual_pre_count"], config.pre)
            self.assertEqual(m["actual_post_count"], config.post)
            self.assertTrue(m["a_in_waveform"])
            self.assertIn(result, [e["result"] for e in unpack_events(capture.events)])
            if result == "TIMEOUT":
                self.assertIsNone(m["b_tick"])
                self.assertEqual(delta_tick(m["decision_timestamp"], m["deadline_tick"]), 1)
                self.assertEqual(m["observed_b_tick"] is not None, scenario == "late")

    def test_closed_boundaries(self):
        config = replace(Config(), min_ticks=100, max_ticks=200)
        for delay, expected in ((99, "EARLY"), (100, "PASS"), (200, "PASS"), (201, "TIMEOUT")):
            capture, _, _ = frozen_capture(config, delay=delay)
            self.assertEqual(capture.metadata["result"], expected)
        capture, _, _ = frozen_capture(replace(Config(), min_ticks=0), delay=0)
        self.assertEqual(capture.metadata["result"], "PASS")

    def test_all_sample_rates_alignment(self):
        for divisor in (1, 5, 10, 50):
            capture, _, _ = frozen_capture(replace(Config(), divisor=divisor), delay=10003)
            m = capture.metadata
            anchor = m["first_sample_timestamp"] + m["trigger_sample_index"] * divisor
            self.assertEqual(anchor, m["capture_anchor_timestamp"])
            self.assertTrue(0 <= delta_tick(anchor, m["decision_timestamp"]) < divisor)

    def test_normal_in_anomaly_mode_does_not_freeze(self):
        clock, device, _, client = setup_device(replace(Config(), action=int(Action.ANOMALY)))
        device.stimulate("normal")
        clock.advance(.2)
        status = client.status()
        self.assertEqual(status["state"], "ARMED")
        self.assertEqual(status["counts"]["pass"], 1)
        self.assertEqual(client.status()["counts"]["pass"], 1)
        with self.assertRaises(DeviceFailure):
            client.request(Command.META, struct.pack("<I", client.session_id))
        client.disarm()
        capture = read_capture(client)
        self.assertFalse(capture.metadata["trigger_valid"])
        self.assertTrue(capture.metadata["manual_stop"])

    def test_partial_read_then_reread_no_release(self):
        _, _, client = frozen_capture()
        def progress(done, total):
            if done > 2000:
                client.cancel = lambda: True
        partial = read_capture(client, progress)
        self.assertFalse(partial.complete)
        self.assertLess(len(partial.samples), partial.metadata["valid_count"])
        client.cancel = lambda: False
        self.assertEqual(client.status()["state"], "FROZEN")
        full = read_capture(client)
        self.assertTrue(full.complete)
        self.assertEqual(partial.samples, full.samples[:len(partial.samples)])

    def test_stale_session_and_reconnect_preserves_frozen(self):
        full, device, client = frozen_capture()
        with self.assertRaises(DeviceFailure):
            client.request(Command.SAMPLES, READ.pack(client.session_id + 1, 0, 10))
        reconnect = DeviceClient(MockTransport(device, fragment_size=4096), timeout=.02)
        self.assertEqual(reconnect.connect()["status"]["state"], "FROZEN")
        recovered = read_capture(reconnect)
        self.assertEqual(full.samples, recovered.samples)
        self.assertEqual(full.events, recovered.events)
        reconnect.release()
        self.assertEqual(reconnect.status()["state"], "IDLE")

    def test_manual_stop_without_trigger(self):
        _, _, _, client = setup_device()
        client.disarm()
        capture = read_capture(client)
        self.assertFalse(capture.metadata["trigger_valid"])
        self.assertIsNone(capture.metadata["decision_timestamp"])
        self.assertEqual(capture.metadata["result"], "NONE")
        self.assertEqual(capture.metadata["timeout"], 0)

    def test_same_tick_snapshot_and_falling_edge(self):
        capture, _, _ = frozen_capture(replace(Config(), edge=1), delay=0)
        a = capture.metadata["a_tick"]
        simultaneous = [e for e in unpack_events(capture.events) if e["tick"] == a]
        self.assertEqual(len(simultaneous), 1)
        self.assertEqual(simultaneous[0]["result"], "PASS")
        self.assertEqual(simultaneous[0]["fall"] & 2, 2)
        self.assertEqual(simultaneous[0]["accepted_mask"], 1)

    def test_random_rule_outcomes_independent_expected(self):
        rng = random.Random(2307)
        for _ in range(100):
            low, high = sorted((rng.randrange(1000), rng.randrange(1000)))
            delay = rng.randrange(1200)
            clock, device, _, _ = setup_device(replace(Config(), min_ticks=low, max_ticks=high))
            result = device.stimulate("normal", delay_ticks=delay)
            expected = "PASS" if low <= delay <= high else "EARLY" if delay < low else "TIMEOUT"
            self.assertEqual(result, expected)

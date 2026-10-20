import binascii
import random
import struct
import unittest
from dataclasses import replace
from app.models import Config, delta_tick, ms_ticks
from app.services.device_client import DeviceFailure
from protocol.codec import Decoder, Frame, crc16, encode
from protocol.messages import Command, PREFIX, Status
from tests.helpers import Clock, setup_device


class ProtocolTests(unittest.TestCase):
    def test_crc_and_independent_wire_vector(self):
        self.assertEqual(crc16(b"123456789"), 0x29B1)
        body = bytes.fromhex("0101040302010300616263")
        raw = b"TS" + body + binascii.crc_hqx(body, 0xFFFF).to_bytes(2, "little")
        frame = Frame(1, 0x01020304, b"abc")
        self.assertEqual(encode(frame), raw)
        self.assertEqual(Decoder().feed(raw), [frame])

    def test_every_split_and_magic_inside_payload(self):
        frame = Frame(0x31, 9, b"TS\0TS" * 20)
        raw = encode(frame)
        for cut in range(len(raw) + 1):
            decoder = Decoder()
            self.assertEqual(decoder.feed(raw[:cut]) + decoder.feed(raw[cut:]), [frame])
        decoder = Decoder()
        got = []
        for byte in raw:
            got += decoder.feed(bytes([byte]))
        self.assertEqual(got, [frame])

    def test_bad_crc_noise_length_and_recovery(self):
        good = encode(Frame(1, 2, b"ok"))
        bad = bytearray(good); bad[-1] ^= 1
        invalid = b"TS" + struct.pack("<BBIH", 1, 1, 1, 65535)
        decoder = Decoder()
        got = decoder.feed(b"junkT" + bytes(bad) + invalid + good + good)
        self.assertEqual(got, [Frame(1, 2, b"ok")] * 2)
        self.assertGreater(decoder.crc_errors, 0)
        self.assertGreater(decoder.length_errors, 0)
        self.assertLessEqual(len(decoder.buffer), 1036)

    def test_partial_expiry(self):
        clock = Clock(); decoder = Decoder(timeout=.1, clock=clock)
        decoder.feed(encode(Frame(1, 1, b"abc"))[:11])
        clock.advance(.2)
        decoder.feed()
        self.assertGreater(decoder.expired, 0)
        self.assertEqual(decoder.feed(encode(Frame(1, 2))), [Frame(1, 2)])

    def test_random_roundtrips_independent_crc(self):
        rng = random.Random(7401)
        for _ in range(100):
            payload = rng.randbytes(rng.randrange(1025))
            frame = Frame(0x31, rng.randrange(1, 2**32), payload)
            raw = encode(frame)
            self.assertEqual(int.from_bytes(raw[-2:], "little"), binascii.crc_hqx(raw[2:-2], 0xFFFF))
            self.assertEqual(Decoder().feed(raw), [frame])

    def test_config_and_exact_tick_conversion(self):
        self.assertEqual(Config.from_bytes(Config().to_bytes()), Config())
        self.assertEqual(ms_ticks("0.00002"), 1)
        with self.assertRaises(ValueError):
            ms_ticks("0.00001")
        with self.assertRaises(ValueError):
            replace(Config(), min_ticks=100001).validate()
        self.assertTrue(replace(Config(), divisor=1).coverage_warning())
        self.assertEqual(delta_tick(2, (1 << 48)-3), 5)

    def test_retries_execute_side_effect_once(self):
        clock, device, transport, client = setup_device()
        client.disarm(); client.release()
        before = device.executions.get(Command.ARM, 0)
        transport.drop_next = True
        client.arm()
        self.assertEqual(device.executions[Command.ARM], before + 1)
        clock.advance(.1); client.disarm()
        before = device.executions.get(Command.RELEASE, 0)
        transport.corrupt_next = True
        client.release()
        self.assertEqual(device.executions[Command.RELEASE], before + 1)
        before = device.executions.get(Command.COMMIT, 0)
        client.request(Command.WRITE_CONFIG, Config().to_bytes())
        transport.drop_next = True
        client.request(Command.COMMIT)
        self.assertEqual(device.executions[Command.COMMIT], before + 1)

    def test_id_conflict_sync_retransmission_and_stale_link(self):
        _, device, _, client = setup_device()
        token = client.token
        request = Frame(Command.PING, 500, struct.pack("<Q", token) + b"a")
        original = device.handle(request)
        device.handle(Frame(Command.SYNC, 0, struct.pack("<Q", token)))
        self.assertEqual(device.handle(request), original)
        conflict = device.handle(replace(request, payload=struct.pack("<Q", token)+b"b"))
        self.assertEqual(PREFIX.unpack(conflict.payload[:10])[1], Status.ID_CONFLICT)
        device.handle(Frame(Command.SYNC, 0, struct.pack("<Q", token+1)))
        self.assertEqual(PREFIX.unpack(device.handle(request).payload[:10])[1], Status.STALE_LINK)

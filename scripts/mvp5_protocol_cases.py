"""Independent byte oracle for profile1 transport/config/session operations.

Raw input in tb_mvp5_protocol is constant CH0 high. A long explicit simulator
wait fills the real65536-depth ring before DISARM; the oracle never fabricates
physical evidence. Capture/rule timing is tested separately in tb_mvp5_capture.
"""
import argparse
from dataclasses import replace
from pathlib import Path
import random
import struct
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from mvp5.models import Config, Rule
from protocol.codec import Frame, encode
from protocol.messages import Command as C, INFO, META_FIELDS, PREFIX, READ, STATUS_BODY, Status as S
from protocol_link_cases import TOKEN_A, TOKEN_B, Case


class Oracle:
    def __init__(self):
        self.token = 0
        self.last = self.cached = None
        self.config = Config(enabled_mask=1, rules=(Rule(action=2,min_ticks=25000,max_ticks=100000),Rule(),Rule(),Rule()))
        self.shadow = None
        self.config_id = 1
        self.session = 0
        self.state = 0

    def handle(self, req):
        token = int.from_bytes(req.payload[:8], "little") if len(req.payload) >= 8 else 0

        def reply(status, body=b""):
            return encode(Frame(req.command | 128, req.request_id, PREFIX.pack(token, int(status)) + body))

        if len(req.payload) < 8:
            return reply(S.BAD_LENGTH)
        if req.command == C.SYNC:
            if req.request_id or len(req.payload) != 8 or not token:
                return reply(S.INVALID_ARGUMENT)
            if token != self.token:
                self.token, self.last, self.cached = token, None, None
            return reply(S.OK)
        if not self.token:
            return reply(S.NOT_SYNCED)
        if token != self.token:
            return reply(S.STALE_LINK)
        if not req.request_id:
            return reply(S.INVALID_ARGUMENT)
        if self.last and req.request_id == self.last.request_id:
            return self.cached if req == self.last else reply(S.ID_CONFLICT)
        if self.last and req.request_id < self.last.request_id:
            return reply(S.STALE_REQUEST)
        body = req.payload[8:]
        status, result = S.OK, b""
        if req.command == C.PING:
            if len(body) > 1014:
                status = S.BAD_LENGTH
            else:
                result = body
        elif req.command in (C.INFO, C.GET_CONFIG, C.STATUS, C.COMMIT):
            if body:
                status = S.BAD_LENGTH
            elif req.command == C.INFO:
                result = INFO.pack(5, 50_000_000, 65536, 8, 1, 4, 15, 1024, b"mvp5-a7-0.1.0")
            elif req.command == C.GET_CONFIG:
                result = struct.pack("<I", self.config_id) + self.config.to_bytes()
            elif req.command == C.STATUS:
                result = struct.pack("<BBIIBBBB", self.state, self.state == 2, self.session, self.config_id, self.config.enabled_mask, 255, 0, 0) + bytes(112)
            elif self.state != 0:
                status = S.BUSY
            elif self.shadow is None:
                status = S.INVALID_ARGUMENT
            else:
                self.config, self.shadow = self.shadow, None
                self.config_id += 1
                result = struct.pack("<I", self.config_id)
        elif req.command == C.WRITE_CONFIG:
            if len(body) != 100:
                status = S.BAD_LENGTH
            elif self.state != 0:
                status = S.BUSY
            else:
                try:
                    self.shadow = Config.from_bytes(body)
                except ValueError:
                    status = S.INVALID_ARGUMENT
        elif req.command == C.ARM:
            if len(body) != 4:
                status = S.BAD_LENGTH
            elif self.state != 0:
                status = S.BUSY
            elif int.from_bytes(body, "little") != self.config_id:
                status = S.CONFIG_MISMATCH
            else:
                assert self.config.pre == 0
                self.state = 2
                self.session += 1
                result = struct.pack("<II", self.session, self.config_id)
        elif req.command in (C.DISARM, C.META, C.RELEASE):
            if len(body) != 4:
                status = S.BAD_LENGTH
            elif int.from_bytes(body, "little") != self.session:
                status = S.STALE_SESSION
            elif req.command == C.DISARM:
                if self.state in (1, 2, 3):
                    self.state = 4
                result = bytes((self.state,))
            elif self.state != 4:
                status = S.NOT_READY
            elif req.command == C.RELEASE:
                self.state = 0
            else:
                raw = bytearray(412)
                raw[0:4] = bytes((5, 2, 255, self.config.enabled_mask))
                struct.pack_into("<IIIIHHI", raw, 4, self.session, self.config_id, 50000000, 65536, self.config.divisor, 0, 0)
                struct.pack_into("<I", raw, 46, 0xffffffff)
                raw[312:] = self.config.to_bytes()
                result = bytes(raw)
        elif req.command in (C.SAMPLES, C.EVENTS):
            if len(body) != 10:
                status = S.BAD_LENGTH
            else:
                session, offset, count = READ.unpack(body)
                total, cap = (65536, 1004) if req.command == C.SAMPLES else (0, 992)
                if session != self.session:
                    status = S.STALE_SESSION
                elif self.state != 4:
                    status = S.NOT_READY
                elif not 0 < count <= cap or offset > total or (req.command == C.EVENTS and (offset % 16 or count % 16)):
                    status = S.INVALID_ARGUMENT
                else:
                    part = bytes((1,)) * min(count, total - offset)
                    result = READ.pack(session, offset, len(part)) + part
        else:
            status = S.UNKNOWN_COMMAND
        self.last, self.cached = req, reply(status, result)
        return self.cached


def cases():
    oracle, result = Oracle(), []
    rid = 0

    def add(name, command, body=b"", request_id=None, token=TOKEN_A):
        nonlocal rid
        if request_id is None:
            rid += 1
            request_id = rid
        req = Frame(command, request_id, struct.pack("<Q", token) + body)
        result.append(Case(name, encode(req), oracle.handle(req)))
        return request_id

    add("before_sync", C.INFO)
    add("SYNC", C.SYNC, request_id=0)
    for cmd in (C.INFO, C.GET_CONFIG, C.STATUS):
        add("initial_" + cmd.name, cmd)
        add("extra_body_" + cmd.name, cmd, b"x")
    add("commit_without_shadow", C.COMMIT)
    add("old_diagnostic_not_supported", 6, b"\0")
    add("bad_arm_config", C.ARM, struct.pack("<I", 999))
    add("meta_no_capture", C.META, struct.pack("<I", 0))
    rng = random.Random(20261009)
    for size in (0, 1, 127, 1014, 1015):
        add(f"ping_{size}", C.PING, rng.randbytes(size))
    valid = replace(Config(), pre=0, post=65535)
    for field, value in (("uart_channel", 8), ("edge", 2), ("baud", 0), ("divisor", 7),
                         ("pre", 65536), ("post", 0), ("action", 3), ("min_ticks", 100001)):
        raw = bytearray(valid.to_bytes())
        offset, width = {"uart_channel": (1, 1), "edge": (21, 1), "baud": (4, 4), "divisor": (8, 2),
                         "pre": (12, 4), "post": (16, 4), "action": (24, 1), "min_ticks": (28, 6)}[field]
        raw[offset:offset + width] = value.to_bytes(width, "little")
        add("reject_config_" + field, C.WRITE_CONFIG, bytes(raw))
    commit_rid = 0
    for baud in (1200, 9600, 115200, 460800, 999999, 1000000):
        c = replace(valid, baud=baud)
        add(f"shadow_{baud}", C.WRITE_CONFIG, c.to_bytes())
        add(f"shadow_is_not_active_{baud}", C.GET_CONFIG)
        commit_rid = add(f"commit_{baud}", C.COMMIT)
        add(f"duplicate_commit_{baud}", C.COMMIT, request_id=commit_rid)
        add(f"committed_{baud}", C.GET_CONFIG)
    # Restore the MCU's actual115200 configuration and keep PREFILL=0 for
    # deterministic state response bytes in this transport-only testbench.
    add("shadow_default_baud", C.WRITE_CONFIG, valid.to_bytes())
    add("commit_default_baud", C.COMMIT)
    sid = oracle.session + 1
    arm_rid = add("arm", C.ARM, struct.pack("<I", oracle.config_id))
    add("duplicate_arm", C.ARM, struct.pack("<I", oracle.config_id), request_id=arm_rid)
    add("arm_conflicting_contents", C.ARM, struct.pack("<I", oracle.config_id + 1), request_id=arm_rid)
    add("status_one_session", C.STATUS)
    add("write_busy", C.WRITE_CONFIG, valid.to_bytes())
    add("commit_busy", C.COMMIT)
    add("arm_busy", C.ARM, struct.pack("<I", oracle.config_id))
    add("release_active", C.RELEASE, struct.pack("<I", sid))
    result.append(Case("fill_real_ring_wait", b"", None))
    stop_rid = add("disarm", C.DISARM, struct.pack("<I", sid))
    add("duplicate_disarm", C.DISARM, struct.pack("<I", sid), request_id=stop_rid)
    add("frozen_status", C.STATUS)
    add("meta_dynamic_first_tick", C.META, struct.pack("<I", sid))
    for offset, count in ((0, 1004), (1, 33), (65530, 20), (65536, 10), (65537, 10), (0, 0), (0, 1005)):
        read_rid = add(f"read_{offset}_{count}", C.SAMPLES, READ.pack(sid, offset, count))
        add(f"cached_read_{offset}_{count}", C.SAMPLES, READ.pack(sid, offset, count), request_id=read_rid)
    for offset, count in ((0, 16), (1, 16), (0, 15), (0, 1008)):
        add(f"event_read_{offset}_{count}", C.EVENTS, READ.pack(sid, offset, count))
    add("stale_session_read", C.SAMPLES, READ.pack(sid + 1, 0, 10))
    add("stale_session_release", C.RELEASE, struct.pack("<I", sid + 1))
    add("same_token_sync_preserves_frozen", C.SYNC, request_id=0)
    add("frozen_after_sync", C.STATUS)
    release_rid = add("release", C.RELEASE, struct.pack("<I", sid))
    add("duplicate_release", C.RELEASE, struct.pack("<I", sid), request_id=release_rid)
    add("read_after_release", C.SAMPLES, READ.pack(sid, 0, 10))
    add("status_after_release", C.STATUS)
    add("new_token", C.SYNC, request_id=0, token=TOKEN_B)
    add("old_link_rejected", C.INFO)
    rid = 0
    for _ in range(20):
        add("random_ping", C.PING, rng.randbytes(rng.randrange(129)), token=TOKEN_B)
    # Invalid stream candidates expire and must not reach the application.
    bad = bytearray(encode(Frame(C.PING, rid + 1, struct.pack("<Q", TOKEN_B) + b"invalid")))
    bad[-1] ^= 1
    result.append(Case("bad_crc", bytes(bad), None))
    bad[2] = 2
    result.append(Case("bad_version", bytes(bad), None))
    result.append(Case("truncated_frame", b"TS\x01\x10\x01", None))
    add("recovered_ping", C.PING, b"after-expiry", token=TOKEN_B)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    vectors, tests = [], cases()
    for case in tests:
        vectors.append("// " + case.name)
        vectors.extend(f"010000{b:02x}" for b in case.request)
        if case.response is not None:
            for index, byte in enumerate(case.response):
                # Unknown free-running first tick; CRC is independently checked
                # against the received packet in the TB, not skipped globally.
                skip = case.name == "meta_dynamic_first_tick" and (48 <= index < 54 or index >= len(case.response) - 2)
                vectors.append(f"{'04' if skip else '02'}0000{byte:02x}")
            if case.name.startswith("committed_"):
                baud = int.from_bytes(case.response[28:32], "little")
                vectors.append(f"05{50_000_000 // baud:06x}")
        else:
            vectors.append(f"03{400000 if case.name == 'fill_real_ring_wait' else 25010:06x}")
    vectors.append("ff000000")
    args.output.write_text("\n".join(vectors) + "\n", encoding="ascii")
    print(f"MVP5_PROTOCOL_VECTOR_CASES={len(tests)}")


if __name__ == "__main__":
    main()



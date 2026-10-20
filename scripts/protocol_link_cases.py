"""Independent Python wire oracle for profile-0 RTL and physical UART checks.

Generated data are protocol test vectors, never samples or hardware evidence.
"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import random
import struct
import sys

HOST = Path(__file__).resolve().parents[1] / "host"
sys.path.insert(0, str(HOST))
from app.models import Config
from protocol.codec import Frame, crc16, encode
from protocol.messages import Command, INFO, PREFIX, STATUS_BODY, Status

TOKEN_A = 0x1020304050607080
TOKEN_B = 0x8877665544332211
FIRMWARE = b"link-a7-0.1.0"
LINK_INFO = INFO.pack(0, 50_000_000, 0, 0, 0, 0, 0, 1024, FIRMWARE)


class LinkOracle:
    def __init__(self):
        self.token = 0
        self.last = self.cached = None

    def handle(self, request):
        token = int.from_bytes(request.payload[:8], "little") if len(request.payload) >= 8 else 0
        def reply(status, body=b""):
            return encode(Frame(request.command | 0x80, request.request_id, PREFIX.pack(token, status) + body))
        if len(request.payload) < 8:
            return reply(Status.BAD_LENGTH)
        if request.command == Command.SYNC:
            if request.request_id or len(request.payload) != 8 or not token:
                return reply(Status.INVALID_ARGUMENT)
            if self.token != token:
                self.token, self.last, self.cached = token, None, None
            return reply(Status.OK)
        if not self.token:
            return reply(Status.NOT_SYNCED)
        if token != self.token:
            return reply(Status.STALE_LINK)
        if not request.request_id:
            return reply(Status.INVALID_ARGUMENT)
        if self.last:
            if request.request_id == self.last.request_id:
                return self.cached if request == self.last else reply(Status.ID_CONFLICT)
            if request.request_id < self.last.request_id:
                return reply(Status.STALE_REQUEST)
        body = request.payload[8:]
        if request.command == Command.PING:
            response = reply(Status.OK, body) if len(body) <= 1014 else reply(Status.BAD_LENGTH)
        elif request.command in (Command.INFO, Command.GET_CONFIG, Command.STATUS):
            data = {Command.INFO: LINK_INFO,
                    Command.GET_CONFIG: struct.pack("<I", 0) + Config().to_bytes(),
                    Command.STATUS: STATUS_BODY.pack(0, 0, 0, 0, 0, 0, 0, 0, 0)}
            response = reply(Status.BAD_LENGTH) if body else reply(Status.OK, data[request.command])
        elif request.command in set(Command) - {Command.SYNC}:
            response = reply(Status.UNSUPPORTED)
        else:
            response = reply(Status.UNKNOWN_COMMAND)
        self.last, self.cached = request, response
        return response


@dataclass
class Case:
    name: str
    request: bytes
    response: bytes | None
    expire_partial: bool = False


def cases():
    oracle, result = LinkOracle(), []
    def add(name, command, rid, body=b"", token=TOKEN_A, payload=None):
        frame = Frame(command, rid, struct.pack("<Q", token) + body if payload is None else payload)
        result.append(Case(name, encode(frame), oracle.handle(frame)))
    add("before_sync", Command.PING, 1)
    add("short_token", Command.PING, 1, payload=b"bad")
    add("sync_zero_token", Command.SYNC, 0, token=0)
    add("sync_wrong_id", Command.SYNC, 1)
    add("sync_extra_body", Command.SYNC, 0, b"x")
    add("sync", Command.SYNC, 0)
    add("ping", Command.PING, 1, b"sentinel")
    add("exact_replay", Command.PING, 1, b"sentinel")
    add("same_token_sync", Command.SYNC, 0)
    add("replay_after_sync", Command.PING, 1, b"sentinel")
    add("same_id_changed_content", Command.PING, 1, b"Sentinel")
    add("same_id_changed_command", Command.INFO, 1)
    add("cache_survives_conflict", Command.PING, 1, b"sentinel")
    add("ordinary_id_zero", Command.STATUS, 0)
    add("info_honest_zero_capabilities", Command.INFO, 2)
    add("stale_id", Command.PING, 1, b"sentinel")
    add("read_only_planned_config", Command.GET_CONFIG, 3)
    add("idle_not_ready_status", Command.STATUS, 4)
    add("unknown_command", 0x7F, 5)
    add("info_bad_length", Command.INFO, 6, b"x")
    rng = random.Random(0x2026A735)
    add("maximum_ping", Command.PING, 7, rng.randbytes(1014))
    add("ping_response_would_exceed_limit", Command.PING, 8, rng.randbytes(1016))
    for rid, cmd in enumerate((Command.WRITE_CONFIG, Command.COMMIT, Command.ARM,
                              Command.DISARM, Command.META, Command.EVENTS,
                              Command.SAMPLES, Command.RELEASE), 9):
        add(f"unsupported_{cmd.name.lower()}", cmd, rid, Config().to_bytes() if cmd == Command.WRITE_CONFIG else b"")
    fault_frame = encode(Frame(Command.PING, 20, struct.pack("<Q", TOKEN_A) + b"crc-test"))
    result.append(Case("bad_crc_drop", fault_frame[:-1] + bytes([fault_frame[-1] ^ 1]), None))
    add("valid_after_bad_crc", Command.PING, 20, b"recovered")
    wrong_version = bytearray(encode(Frame(Command.PING, 21, struct.pack("<Q", TOKEN_A))))
    wrong_version[2] = 2
    wrong_version[-2:] = struct.pack("<H", crc16(wrong_version[2:-2]))
    result.append(Case("bad_version_drop", bytes(wrong_version), None))
    result.append(Case("oversize_header_drop", b"TS\x01\x01" + struct.pack("<IH", 21, 1025), None))
    partial = encode(Frame(Command.PING, 21, struct.pack("<Q", TOKEN_A) + b"partial"))[:13]
    result.append(Case("partial_frame_timeout", partial, None, True))
    add("valid_after_partial_timeout", Command.PING, 22, b"after-timeout")
    # Equal CRC does not mean equal request: explicitly find a collision.
    seen = {}
    for _ in range(5000):
        value = rng.randbytes(3)
        raw = encode(Frame(Command.PING, 24, struct.pack("<Q", TOKEN_A) + value))
        key = raw[-2:]
        if key in seen and seen[key] != value:
            first, second = seen[key], value
            break
        seen[key] = value
    else:
        raise AssertionError("Deterministic CRC collision search failed")
    add("crc_collision_original", Command.PING, 24, first)
    add("crc_collision_not_exact_replay", Command.PING, 24, second)
    add("crc_collision_original_replay", Command.PING, 24, first)
    add("new_token_sync", Command.SYNC, 0, token=TOKEN_B)
    add("old_token_rejected", Command.STATUS, 25)
    add("new_link_ids_restart", Command.PING, 1, b"new-link", token=TOKEN_B)
    for rid in range(2, 52):
        add(f"random_ping_{rid}", Command.PING, rid, rng.randbytes(rng.randrange(129)), token=TOKEN_B)
    add("final_status", Command.STATUS, 52, token=TOKEN_B)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert crc16(b"123456789") == 0x29B1
    vectors, test_cases = [], cases()
    for case in test_cases:
        vectors.append(f"// {case.name}")
        vectors.extend(f"010000{byte:02x}" for byte in case.request)
        if case.response is not None:
            vectors.extend(f"020000{byte:02x}" for byte in case.response)
        else:
            vectors.append("030061b2")  # 25010 cycles, exceeds TB's 20000 bound.
    vectors.append("ff000000")
    args.output.write_text("\n".join(vectors) + "\n", encoding="ascii")
    print(f"LINK_VECTOR_CASES={len(test_cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

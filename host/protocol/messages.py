from enum import IntEnum
import struct


class Command(IntEnum):
    PING = 0x01
    INFO = 0x02
    SYNC = 0x03
    WRITE_CONFIG = 0x10
    COMMIT = 0x11
    GET_CONFIG = 0x12
    ARM = 0x20
    DISARM = 0x21
    META = 0x22
    STATUS = 0x23
    EVENTS = 0x30
    SAMPLES = 0x31
    RELEASE = 0x32


class Status(IntEnum):
    OK = 0
    BAD_VERSION = 1
    UNKNOWN_COMMAND = 2
    BAD_LENGTH = 3
    INVALID_ARGUMENT = 4
    BUSY = 5
    STALE_SESSION = 6
    ID_CONFLICT = 7
    NOT_READY = 8
    NOT_SYNCED = 9
    STALE_LINK = 10
    UNSUPPORTED = 11
    INTERNAL_ERROR = 12
    STALE_REQUEST = 13
    CONFIG_MISMATCH = 14


class State(IntEnum):
    IDLE = 0
    PREFILL = 1
    ARMED = 2
    POST_TRIGGER = 3
    FROZEN = 4


class Result(IntEnum):
    NONE = 0
    PASS = 1
    EARLY = 2
    TIMEOUT = 3
    CANCELLED_DISARM = 4
    INCOMPLETE_CAPTURE_END = 5


class Action(IntEnum):
    RECORD = 0
    ANOMALY = 1
    EACH = 2


SCENARIOS = ("normal", "early", "late", "missing")
PREFIX = struct.Struct("<QH")
READ = struct.Struct("<IIH")
CONFIG = struct.Struct("<BBBBIH IIBBBB6s6s")
INFO = struct.Struct("<BIIBBBIH16s")
STATUS_BODY = struct.Struct("<BBII5I")
COUNTERS = ("accepted", "pass", "early", "timeout", "cancelled")


def u48(value):
    if not 0 <= value < (1 << 48):
        raise ValueError("Invalid 48-bit value")
    return value.to_bytes(6, "little")


def pack_event(tick, value=0, rise=0, fall=0, uart=None, result=0, state=0, accepted=False):
    word = tick | (value << 48) | (rise << 56) | (fall << 64)
    if uart is not None:
        word |= (1 << 72) | (uart << 73)
    word |= int(result) << 82
    word |= int(accepted) << 102
    word |= int(state) << 106
    return word.to_bytes(16, "little")


def unpack_events(data):
    if len(data) % 16:
        raise ValueError("Event data is not aligned")
    out = []
    for offset in range(0, len(data), 16):
        word = int.from_bytes(data[offset:offset + 16], "little")
        if word >> 112 or (word >> 86) & 0xFFF or (word >> 99) & 7 or (word >> 103) & 7:
            raise ValueError("MVP-1事件包含保留位或未支持的规则槽")
        out.append({"tick": word & ((1 << 48) - 1), "value": (word >> 48) & 255,
                    "rise": (word >> 56) & 255, "fall": (word >> 64) & 255,
                    "uart": ((word >> 73) & 255) if (word >> 72) & 1 else None,
                    "uart_error": bool((word >> 81) & 1),
                    "result": Result((word >> 82) & 15).name,
                    "overlap_mask": (word >> 98) & 15,
                    "accepted_mask": (word >> 102) & 15,
                    "state": State((word >> 106) & 63).name})
    return out


# Fixed MVP-1 metadata layout. Field order is the wire order.
META_FIELDS = (("schema", 1), ("flags", 1), ("session_id", 4), ("config_id", 4),
               ("clock_hz", 4), ("valid_count", 4), ("sample_divisor", 2),
               ("first_sample_timestamp", 6), ("capture_anchor_timestamp", 6),
               ("decision_timestamp", 6), ("trigger_sample_index", 4),
               ("a_tick", 6), ("b_tick", 6), ("deadline_tick", 6),
               ("observed_b_tick", 6), ("result_code", 1), ("event_count", 2),
               ("event_overwrites", 4), ("accepted", 4), ("pass", 4),
               ("early", 4), ("timeout", 4), ("cancelled", 4),
               ("seed", 4), ("scenario_id", 1))


def encode_meta(meta, config_bytes):
    out = bytearray()
    for name, size in META_FIELDS:
        value = meta[name]
        out.extend((0 if value is None else value).to_bytes(size, "little"))
    return bytes(out) + config_bytes


def decode_meta(body):
    expected = sum(size for _, size in META_FIELDS) + CONFIG.size
    if len(body) != expected:
        raise ValueError("Metadata length mismatch")
    offset, meta = 0, {}
    for name, size in META_FIELDS:
        meta[name] = int.from_bytes(body[offset:offset + size], "little")
        offset += size
    if meta["schema"] != 1 or meta["flags"] & ~0x1F:
        raise ValueError("Unsupported metadata schema/flags")
    for name, bit in (("capture_anchor_timestamp", 0), ("decision_timestamp", 0),
                      ("a_tick", 1), ("b_tick", 2), ("observed_b_tick", 3)):
        if not meta["flags"] & (1 << bit):
            if meta[name] != 0:
                raise ValueError(f"Invalid absent timestamp: {name}")
            meta[name] = None
    if meta["a_tick"] is None:
        if meta["deadline_tick"] != 0:
            raise ValueError("Invalid absent deadline timestamp")
        meta["deadline_tick"] = None
    if not meta["flags"] & 1:
        if meta["trigger_sample_index"] != 0xFFFFFFFF:
            raise ValueError("Invalid absent trigger index")
        meta["trigger_sample_index"] = None
    meta["trigger_valid"] = bool(meta["flags"] & 1)
    meta["manual_stop"] = bool(meta["flags"] & 16)
    meta["result"] = Result(meta["result_code"]).name
    return meta, body[offset:]

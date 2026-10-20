from dataclasses import dataclass
import struct
import time

MAGIC = b"TS"
VERSION = 1
MAX_PAYLOAD = 1024
HEADER = struct.Struct("<BBIH")


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Frame:
    command: int
    request_id: int
    payload: bytes = b""


def encode(frame: Frame) -> bytes:
    if len(frame.payload) > MAX_PAYLOAD:
        raise ValueError("Payload exceeds 1024 bytes")
    body = HEADER.pack(VERSION, frame.command, frame.request_id, len(frame.payload)) + frame.payload
    return MAGIC + body + struct.pack("<H", crc16(body))


class Decoder:
    """Bounded incremental decoder. Expiry is measured from frame start."""
    def __init__(self, timeout=0.5, clock=time.monotonic):
        self.buffer = bytearray()
        self.timeout = timeout
        self.clock = clock
        self.started = None
        self.crc_errors = self.length_errors = self.version_errors = self.expired = 0

    def feed(self, data=b""):
        frames = []
        self.buffer.extend(data)
        while self.buffer:
            index = self.buffer.find(MAGIC)
            if index < 0:
                self.buffer[:] = b"T" if self.buffer[-1:] == b"T" else b""
                self.started = None
                break
            if index:
                del self.buffer[:index]
                self.started = None
            now = self.clock()
            if self.started is None:
                self.started = now
            if now - self.started > self.timeout:
                del self.buffer[0]
                self.started = None
                self.expired += 1
                continue
            if len(self.buffer) < 10:
                break
            version, cmd, rid, length = HEADER.unpack(self.buffer[2:10])
            if length > MAX_PAYLOAD or version != VERSION:
                self.length_errors += length > MAX_PAYLOAD
                self.version_errors += version != VERSION
                del self.buffer[0]
                self.started = None
                continue
            size = 12 + length
            if len(self.buffer) < size:
                break
            body = bytes(self.buffer[2:size - 2])
            expected = int.from_bytes(self.buffer[size - 2:size], "little")
            if crc16(body) != expected:
                self.crc_errors += 1
                del self.buffer[0]
                self.started = None
                continue
            frames.append(Frame(cmd, rid, bytes(self.buffer[10:size - 2])))
            del self.buffer[:size]
            self.started = None
        return frames

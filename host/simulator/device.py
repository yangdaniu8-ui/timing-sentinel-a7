from dataclasses import asdict
import struct
import time
import numpy as np
from app.models import CLOCK_HZ, DEPTH, TICK_MASK, Config
from protocol.codec import Frame
from protocol.messages import (Action, Command, COUNTERS, INFO, PREFIX, READ, SCENARIOS,
                               STATUS_BODY, Result, State, Status, encode_meta, pack_event)


class DeviceError(Exception):
    def __init__(self, status):
        self.status = status


class SimulatedDevice:
    """One UART decoder and one rule. Virtual transactions advance atomically.

    GUI wall-time post-trigger delay is presentation only. Logical event timing
    and all samples are constructed from integer ticks, not host arrival times.
    """
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.config = Config()
        self.shadow = None
        self.config_id = 1
        self.session = 0
        self.state = State.IDLE
        self.token = 0
        self.last_request = None
        self.last_response = None
        self.executions = {}
        self.counts = dict.fromkeys(COUNTERS, 0)
        self.frames = []
        self.edges = []
        self.log = []
        self.overwrites = 0
        self.last = None
        self.meta = None
        self.samples = self.events = b""
        self.arm_wall = self.clock()
        self.post_wall = None
        self.logical_end = 0
        self.seed = 1

    def _update(self):
        if self.state == State.PREFILL:
            if (self.clock() - self.arm_wall) * CLOCK_HZ >= self.config.pre * self.config.divisor:
                self.state = State.ARMED
        elif self.state == State.POST_TRIGGER and self.clock() - self.post_wall >= 0.12:
            self.state = State.FROZEN

    def _tick(self):
        return max(self.logical_end, int((self.clock() - self.arm_wall) * CLOCK_HZ))

    def _require(self, condition, error):
        if not condition:
            raise DeviceError(error)

    def _reply(self, request, token, status, body=b""):
        return Frame(request.command | 0x80, request.request_id, PREFIX.pack(token, int(status)) + body)

    def handle(self, request):
        self._update()
        if len(request.payload) < 8:
            return self._reply(request, 0, Status.BAD_LENGTH)
        token = int.from_bytes(request.payload[:8], "little")
        if request.command == Command.SYNC:
            if request.request_id != 0 or len(request.payload) != 8 or token == 0:
                return self._reply(request, token, Status.INVALID_ARGUMENT)
            if token != self.token:
                self.token = token
                self.last_request = self.last_response = None
            return self._reply(request, token, Status.OK)
        if not self.token or token != self.token:
            return self._reply(request, token, Status.STALE_LINK if self.token else Status.NOT_SYNCED)
        if request.request_id == 0:
            return self._reply(request, token, Status.INVALID_ARGUMENT)
        if self.last_request:
            if request.request_id == self.last_request.request_id:
                return self.last_response if request == self.last_request else self._reply(request, token, Status.ID_CONFLICT)
            if request.request_id < self.last_request.request_id:
                return self._reply(request, token, Status.STALE_REQUEST)
        try:
            cmd = Command(request.command)
        except ValueError:
            response = self._reply(request, token, Status.UNKNOWN_COMMAND)
            self.last_request, self.last_response = request, response
            return response
        try:
            body = self._execute(cmd, request.payload[8:])
            response = self._reply(request, token, Status.OK, body)
        except DeviceError as exc:
            response = self._reply(request, token, exc.status)
        except (ValueError, struct.error):
            response = self._reply(request, token, Status.INVALID_ARGUMENT)
        self.last_request, self.last_response = request, response
        return response

    def _execute(self, cmd, body):
        self.executions[cmd] = self.executions.get(cmd, 0) + 1
        c = self.config
        if cmd == Command.PING:
            return body
        if cmd == Command.INFO:
            self._require(not body, Status.BAD_LENGTH)
            return INFO.pack(1, CLOCK_HZ, DEPTH, 8, 1, 1, 0xF, 1024, b"mock-mvp1-0.1.0")
        if cmd == Command.STATUS:
            self._require(not body, Status.BAD_LENGTH)
            return STATUS_BODY.pack(self.state, self.state == State.ARMED, self.session,
                                    self.config_id, *(self.counts[k] for k in COUNTERS))
        if cmd == Command.GET_CONFIG:
            self._require(not body, Status.BAD_LENGTH)
            return struct.pack("<I", self.config_id) + c.to_bytes()
        if cmd == Command.WRITE_CONFIG:
            self._require(self.state == State.IDLE, Status.BUSY)
            self.shadow = Config.from_bytes(body)
            return b""
        if cmd == Command.COMMIT:
            self._require(not body, Status.BAD_LENGTH)
            self._require(self.state == State.IDLE, Status.BUSY)
            self._require(self.shadow is not None, Status.INVALID_ARGUMENT)
            self.config = self.shadow
            self.shadow = None
            self.config_id += 1
            return struct.pack("<I", self.config_id)
        if cmd == Command.ARM:
            self._require(len(body) == 4, Status.BAD_LENGTH)
            self._require(self.state == State.IDLE, Status.BUSY)
            self._require(int.from_bytes(body, "little") == self.config_id, Status.CONFIG_MISMATCH)
            self.session += 1
            self.arm_wall = self.clock()
            self.logical_end = 0
            self.state = State.PREFILL
            self.counts = dict.fromkeys(COUNTERS, 0)
            self.frames, self.edges, self.log = [], [], []
            self.overwrites = 0
            self.last = self.meta = None
            self.samples = self.events = b""
            return struct.pack("<II", self.session, self.config_id)
        if cmd in (Command.DISARM, Command.META, Command.RELEASE):
            self._require(len(body) == 4, Status.BAD_LENGTH)
            self._require(int.from_bytes(body, "little") == self.session, Status.STALE_SESSION)
            if cmd == Command.DISARM:
                if self.state in (State.PREFILL, State.ARMED):
                    self._freeze(self._tick(), None, manual=True)
                    self.state = State.FROZEN
                elif self.state == State.POST_TRIGGER:
                    fraction = min(1, max(0, (self.clock() - self.post_wall) / 0.12))
                    end = self.meta["capture_anchor_timestamp"] + int(c.post * fraction) * c.divisor
                    self._freeze(end, self.meta["decision_timestamp"], manual=True)
                    self.state = State.FROZEN
                return bytes([self.state])
            self._require(self.state == State.FROZEN, Status.NOT_READY)
            if cmd == Command.META:
                return encode_meta(self.meta, c.to_bytes())
            self.state = State.IDLE
            return b""
        if cmd in (Command.EVENTS, Command.SAMPLES):
            self._require(self.state == State.FROZEN, Status.NOT_READY)
            self._require(len(body) == READ.size, Status.BAD_LENGTH)
            session, offset, count = READ.unpack(body)
            self._require(session == self.session, Status.STALE_SESSION)
            data = self.samples if cmd == Command.SAMPLES else self.events
            limit = 1004 if cmd == Command.SAMPLES else 992
            self._require(0 < count <= limit and offset <= len(data), Status.INVALID_ARGUMENT)
            if cmd == Command.EVENTS:
                self._require(offset % 16 == 0 and count % 16 == 0, Status.INVALID_ARGUMENT)
            part = data[offset:offset + count]
            return READ.pack(session, offset, len(part)) + part
        raise DeviceError(Status.UNKNOWN_COMMAND)

    def stimulate(self, scenario, seed=1, delay_ticks=None):
        """Mock-only stimulus port. No equivalent FPGA/MCU command is claimed."""
        self._update()
        self._require(self.state == State.ARMED, Status.NOT_READY)
        if scenario not in SCENARIOS or not 0 <= seed <= 0xFFFFFFFF:
            raise ValueError("Unknown scenario/seed")
        c = self.config
        self.seed = seed
        start = self._tick() + c.divisor * 2
        a = start + (19 * CLOCK_HZ) // (2 * c.baud)
        delays = {"normal": CLOCK_HZ // 1000, "early": CLOCK_HZ // 5000,
                  "late": CLOCK_HZ * 22 // 10000, "missing": None}
        delay = delays[scenario] if delay_ticks is None else delay_ticks
        if delay is not None and delay < 0:
            raise ValueError("Negative stimulus delay")
        b = None if delay is None else a + delay
        self.frames.append((start, c.value))
        if b is not None:
            self.edges.extend(((b, 1 if c.edge == 0 else 0), (b + CLOCK_HZ // 2000, c.edge)))
        self.counts["accepted"] = min(0xFFFFFFFF, self.counts["accepted"] + 1)
        # Independent device-side transaction semantics, not a GUI classifier.
        if b is not None and b <= a + c.max_ticks:
            result = Result.EARLY if b < a + c.min_ticks else Result.PASS
            decision = b
        else:
            result, decision = Result.TIMEOUT, a + c.max_ticks + 1
        key = result.name.lower()
        self.counts[key] = min(0xFFFFFFFF, self.counts[key] + 1)
        self.last = {"a": a, "b": b if result != Result.TIMEOUT else None,
                     "observed_b": b, "deadline": a + c.max_ticks,
                     "decision": decision, "result": result, "scenario": scenario}
        # UART/GPIO selected events only; auxiliary square waves are not logged.
        previous = 1
        for bit in range(11):
            level = 0 if bit == 0 else ((c.value >> (bit - 1)) & 1) if bit <= 8 else 1
            tick = start + (bit * CLOCK_HZ) // c.baud
            if level != previous:
                mask = 1 << c.uart_channel
                self.log.append((tick, pack_event(tick & TICK_MASK, self._level(tick),
                                                 rise=mask if level else 0, fall=0 if level else mask,
                                                 state=State.ARMED)))
            previous = level
        self.log.append((a, pack_event(a & TICK_MASK, self._level(a), uart=c.value,
                                      state=State.ARMED, accepted=True)))
        if b is not None:
            mask = 1 << c.target_channel
            self.log.append((b, pack_event(b & TICK_MASK, self._level(b),
                                          rise=mask if c.edge == 0 else 0,
                                          fall=mask if c.edge else 0, state=State.ARMED)))
        trigger = c.action == Action.EACH or (c.action == Action.ANOMALY and result != Result.PASS)
        self.log.append((decision, pack_event(decision & TICK_MASK, self._level(decision),
                                             result=result, state=State.POST_TRIGGER if trigger else State.ARMED)))
        if trigger:
            anchor = ((decision + c.divisor - 1) // c.divisor) * c.divisor
            end = anchor + c.post * c.divisor
            self._freeze(end, decision)
            self.state = State.POST_TRIGGER
            self.post_wall = self.clock()
            self.logical_end = end
        else:
            self.logical_end = max(decision, b or 0) + CLOCK_HZ // 2000
        return result.name

    def _waveform(self, ticks):
        c = self.config
        out = np.zeros(len(ticks), dtype=np.uint8)
        for channel in range(8):
            if channel in (c.uart_channel, c.target_channel):
                continue
            phase = (self.seed * (channel + 3)) % 500
            out |= ((((ticks + phase) // (500 * (channel + 1))) & 1).astype(np.uint8) << channel)
        uart = np.ones(len(ticks), dtype=np.uint8)
        for start, value in self.frames:
            active = (ticks >= start) & (ticks < start + (10 * CLOCK_HZ + c.baud - 1) // c.baud)
            bits = np.full(len(ticks), -1, dtype=np.int64)
            bits[active] = ((ticks[active] - start) * c.baud) // CLOCK_HZ
            uart[bits == 0] = 0
            for bit in range(8):
                uart[bits == bit + 1] = (value >> bit) & 1
        gpio = np.full(len(ticks), c.edge, dtype=np.uint8)
        for tick, level in sorted(self.edges):
            gpio[ticks >= tick] = level
        out |= uart << c.uart_channel
        out |= gpio << c.target_channel
        return out

    def _level(self, tick):
        return int(self._waveform(np.array([tick], dtype=np.int64))[0])

    def _freeze(self, end, decision, manual=False):
        c = self.config
        count = min(DEPTH, end // c.divisor + 1)
        first = end - (count - 1) * c.divisor if count else 0
        ticks = first + np.arange(count, dtype=np.int64) * c.divisor
        self.samples = self._waveform(ticks).tobytes()
        records = []
        for tick, data in sorted(((t, e) for t, e in self.log if t <= end), key=lambda item: item[0]):
            if records and records[-1][0] == tick:
                previous = int.from_bytes(records[-1][1], "little")
                current = int.from_bytes(data, "little")
                state_mask = 63 << 106
                merged = ((previous | current) & ~state_mask) | (current & state_mask)
                records[-1] = (tick, merged.to_bytes(16, "little"))
            else:
                records.append((tick, data))
        self.overwrites = max(0, len(records) - 1024)
        self.events = b"".join(e for _, e in records[-1024:])
        last = self.last
        a = last["a"] if last else None
        b = last["b"] if last else None
        observed = last["observed_b"] if last and last["observed_b"] is not None and last["observed_b"] <= end else None
        anchor = ((decision + c.divisor - 1) // c.divisor) * c.divisor if decision is not None else None
        trigger_index = (anchor - first) // c.divisor if anchor is not None else None
        flags = int(decision is not None) | (int(a is not None) << 1) | (int(b is not None) << 2)
        flags |= (int(observed is not None) << 3) | (int(manual) << 4)
        self.meta = {"schema": 1, "flags": flags, "session_id": self.session,
                     "config_id": self.config_id, "clock_hz": CLOCK_HZ,
                     "valid_count": count, "sample_divisor": c.divisor,
                     "first_sample_timestamp": first & TICK_MASK,
                     "capture_anchor_timestamp": None if anchor is None else anchor & TICK_MASK,
                     "decision_timestamp": None if decision is None else decision & TICK_MASK,
                     "trigger_sample_index": trigger_index if trigger_index is not None else 0xFFFFFFFF,
                     "a_tick": None if a is None else a & TICK_MASK,
                     "b_tick": None if b is None else b & TICK_MASK,
                     "deadline_tick": None if a is None else (a + c.max_ticks) & TICK_MASK,
                     "observed_b_tick": None if observed is None else observed & TICK_MASK,
                     "result_code": int(last["result"]) if last else int(Result.NONE),
                     "event_count": len(self.events) // 16, "event_overwrites": self.overwrites,
                     "seed": self.seed, "scenario_id": SCENARIOS.index(last["scenario"]) if last else 255,
                     **self.counts}

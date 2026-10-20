from dataclasses import asdict
import secrets
import struct
import time
from app.models import Config
from protocol.codec import Decoder, Frame, encode
from protocol.messages import Command, CONFIG, COUNTERS, INFO, PREFIX, STATUS_BODY, State, Status


class Cancelled(Exception):
    pass


class DeviceFailure(Exception):
    pass


class DeviceClient:
    """Blocking bounded client: only call from CLI or communication worker."""
    def __init__(self, transport, timeout=0.5, logger=None):
        self.transport = transport
        self.decoder = Decoder(timeout=timeout)
        self.timeout = timeout
        self.token = secrets.randbits(64) or 1
        self.next_id = 1
        self.config_id = 0
        self.session_id = 0
        self.info = None
        self.log = logger or (lambda _: None)
        self.cancel = lambda: False
        self.retries = 0
        self.uncertain = False
        self.origin = getattr(transport, "origin", "mock")

    def _body(self, body, size, command):
        if len(body) != size:
            self.uncertain = True
            raise DeviceFailure(f"{command}响应长度不一致；请重新连接同步")
        return body

    @property
    def can_capture(self):
        return self.info is not None and self.info.get("profile") == "mvp1"

    def request(self, command, body=b""):
        if self.cancel():
            # The enclosing workflow may already have committed/armed before
            # cancellation reached its next request. Resync conservatively.
            self.uncertain = True
            raise Cancelled()
        if self.uncertain:
            raise DeviceFailure("上一请求结果未确认，请重新连接并同步")
        if self.info and self.info.get("profile") == "link_bringup" and command not in (
                Command.SYNC, Command.PING, Command.INFO, Command.GET_CONFIG, Command.STATUS):
            raise DeviceFailure("当前固件仅支持通信联调，尚未实现采集/规则；未发送控制命令")
        rid = 0 if command == Command.SYNC else self.next_id
        if rid >= 0xFFFFFFFF:
            raise DeviceFailure("请求ID即将耗尽，请重新连接")
        if rid:
            self.next_id += 1
        payload = struct.pack("<Q", self.token) + body
        raw = encode(Frame(int(command), rid, payload))
        timeout = max(self.timeout, self.transport.request_timeout(len(raw), 1036)) if hasattr(self.transport, "request_timeout") else self.timeout
        self.decoder.timeout = timeout
        for attempt in range(3):
            if self.cancel():
                # The enclosing workflow may already have side effects even
                # before this request's first attempt.
                self.uncertain = True
                raise Cancelled()
            try:
                self.transport.write(raw)
            except (ConnectionError, OSError) as exc:
                self.uncertain = True
                raise DeviceFailure(f"链路写入失败，命令结果待确认：{exc}") from exc
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                # Finish this bounded request before honouring read cancellation.
                try:
                    frames = self.decoder.feed(self.transport.read())
                except (ConnectionError, OSError) as exc:
                    self.uncertain = True
                    raise DeviceFailure(f"链路断开，命令结果待确认：{exc}") from exc
                for frame in frames:
                    if frame.command != (int(command) | 0x80) or frame.request_id != rid or len(frame.payload) < PREFIX.size:
                        continue
                    token, status = PREFIX.unpack(frame.payload[:PREFIX.size])
                    if token != self.token:
                        continue
                    if status != Status.OK:
                        label = Status(status).name if status in Status._value2member_map_ else f"UNKNOWN_STATUS_{status}"
                        if status in (Status.STALE_LINK, Status.NOT_SYNCED, Status.INTERNAL_ERROR,
                                      Status.ID_CONFLICT, Status.STALE_REQUEST) or status not in Status._value2member_map_:
                            self.uncertain = True
                        raise DeviceFailure(f"{Command(command).name}: {label}")
                    if command not in (Command.STATUS, Command.SAMPLES, Command.EVENTS):
                        self.log(f"{Command(command).name} #{rid}: OK" + (f"（重试{attempt}）" if attempt else ""))
                    return frame.payload[PREFIX.size:]
                if not frames:
                    time.sleep(0.001)
            self.retries += 1
            self.log(f"{Command(command).name} #{rid}: 超时，同ID重试")
        self.uncertain = True
        raise DeviceFailure(f"{Command(command).name}超时，结果待确认；请重新连接同步")

    def connect(self):
        try:
            return self._connect()
        except (DeviceFailure, ValueError, struct.error) as exc:
            self.uncertain = True
            if isinstance(exc, DeviceFailure):
                raise
            raise DeviceFailure(f"握手数据非法；请重新连接同步：{exc}") from exc

    def _connect(self):
        self._body(self.request(Command.SYNC), 0, "SYNC")
        nonce = b"sentinel"
        if self.request(Command.PING, nonce) != nonce:
            raise DeviceFailure("PING内容不匹配")
        raw = self._body(self.request(Command.INFO), INFO.size, "INFO")
        profile, clock, depth, channels, uart_count, rules, divisors, payload, firmware = INFO.unpack(raw)
        capture = (profile, clock, depth, channels, uart_count, rules, payload) == (1, 50_000_000, 65536, 8, 1, 1, 1024)
        bringup = (profile, clock, depth, channels, uart_count, rules, divisors, payload) == (0, 50_000_000, 0, 0, 0, 0, 0, 1024)
        if not (capture or bringup):
            raise DeviceFailure("设备能力与MVP-1不兼容；串口打开不等于协议握手成功")
        if capture and (not divisors or divisors & ~0xF):
            raise DeviceFailure("不支持的采样率能力掩码")
        firmware = firmware.rstrip(b"\0").decode("ascii", errors="strict")
        if bringup and not firmware.startswith("link-"):
            raise DeviceFailure("通信联调固件标识不合法")
        # Simulator sent through a serial test adapter is still simulated data.
        if firmware.startswith("mock-"):
            self.origin = "mock"
        self.info = {"profile": "mvp1" if capture else "link_bringup", "clock_hz": clock, "depth": depth,
                     "channels": channels, "rules": rules, "divisor_mask": divisors,
                     "capture_supported": capture, "config_writable": capture,
                     "firmware": firmware, "origin": self.origin,
                     "transport": getattr(self.transport, "kind", "unknown"),
                     "link": getattr(self.transport, "description", "unknown")}
        config = self.get_config()
        status = self.status()
        if bringup and (self.config_id != 0 or status["state"] != "IDLE" or status["ready"] or
                        status["session_id"] or status["config_id"] or any(status["counts"].values())):
            raise DeviceFailure("通信联调固件不应声明有效采集状态或配置")
        return {"info": self.info, "status": status, "config": asdict(config)}

    def get_config(self):
        body = self._body(self.request(Command.GET_CONFIG), CONFIG.size + 4, "GET_CONFIG")
        try:
            config = Config.from_bytes(body[4:])
        except ValueError as exc:
            self.uncertain = True
            raise DeviceFailure(f"GET_CONFIG配置内容非法；请重新连接同步：{exc}") from exc
        self.config_id = int.from_bytes(body[:4], "little")
        if self.info and self.info.get("profile") == "link_bringup" and (self.config_id != 0 or config != Config()):
            self.uncertain = True
            raise DeviceFailure("通信联调固件只应返回config_id=0的规划默认值；请重新连接同步")
        return config

    def configure(self, config):
        if self.info and not self.can_capture:
            raise DeviceFailure("当前固件仅支持通信联调，配置为只读规划值，尚未实现采集/规则")
        config.validate()
        bit = (1, 5, 10, 50).index(config.divisor)
        if self.info and not self.info["divisor_mask"] & (1 << bit):
            raise DeviceFailure("设备未声明支持所选采样率，请选择受支持的采样率")
        self._body(self.request(Command.WRITE_CONFIG, config.to_bytes()), 0, "WRITE_CONFIG")
        config_id = int.from_bytes(self._body(self.request(Command.COMMIT), 4, "COMMIT"), "little")
        actual = self.get_config()
        if actual != config or self.config_id != config_id:
            self.uncertain = True
            raise DeviceFailure("配置回读不一致")
        return {"config_id": config_id, "config": asdict(actual)}

    def status(self):
        raw = self._body(self.request(Command.STATUS), STATUS_BODY.size, "STATUS")
        state, ready, session, config_id, *counts = STATUS_BODY.unpack(raw)
        if state not in State._value2member_map_ or ready not in (0, 1) or bool(ready) != (state == State.ARMED):
            self.uncertain = True
            raise DeviceFailure("设备状态/READY组合非法")
        if self.info and self.info.get("profile") == "link_bringup" and (state or ready or session or config_id or any(counts)):
            self.uncertain = True
            raise DeviceFailure("通信联调固件不应声明有效采集状态或计数；请重新连接同步")
        self.session_id = session
        return {"state": State(state).name, "ready": bool(ready), "session_id": session,
                "config_id": config_id, "counts": dict(zip(COUNTERS, counts))}

    def arm(self):
        raw = self._body(self.request(Command.ARM, struct.pack("<I", self.config_id)), 8, "ARM")
        self.session_id, config_id = struct.unpack("<II", raw)
        if config_id != self.config_id:
            self.uncertain = True
            raise DeviceFailure("ARM配置ID不一致")
        return self.status()

    def disarm(self):
        state = self._body(self.request(Command.DISARM, struct.pack("<I", self.session_id)), 1, "DISARM")[0]
        if state not in (State.IDLE, State.FROZEN):
            self.uncertain = True
            raise DeviceFailure("DISARM未确认设备停止；请重新连接同步")
        return self.status()

    def release(self):
        self._body(self.request(Command.RELEASE, struct.pack("<I", self.session_id)), 0, "RELEASE")
        return self.status()

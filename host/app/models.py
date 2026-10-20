from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import uuid4
import numpy as np
from app import __version__
from protocol.messages import Action, CONFIG, Result, u48, unpack_events

CLOCK_HZ = 50_000_000
DEPTH = 65536
TICK_MASK = (1 << 48) - 1


def delta_tick(a, b):
    """Signed modular a-b for differences smaller than half a wrap."""
    return ((a - b + (1 << 47)) & TICK_MASK) - (1 << 47)


def ms_ticks(text):
    ticks = Decimal(str(text)) * Decimal(CLOCK_HZ) / 1000
    if not ticks.is_finite() or ticks != ticks.to_integral_value():
        raise ValueError("时间必须为20ns的整数倍")
    return int(ticks)


@dataclass(frozen=True)
class Config:
    uart_channel: int = 0
    target_channel: int = 1
    edge: int = 0
    baud: int = 115200
    divisor: int = 5
    pre: int = 45874
    post: int = 19661
    value: int = 0xA5
    mask: int = 0xFF
    action: int = int(Action.EACH)
    min_ticks: int = 0
    max_ticks: int = 100000

    def validate(self):
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in asdict(self).values()):
            raise ValueError("配置字段必须为整数")
        if not 0 <= self.uart_channel < 8 or not 0 <= self.target_channel < 8:
            raise ValueError("通道编号必须为0到7")
        if self.uart_channel == self.target_channel:
            raise ValueError("UART与响应GPIO请选择不同通道")
        if self.edge not in (0, 1) or self.action not in tuple(Action):
            raise ValueError("未知边沿或动作")
        if not 1200 <= self.baud <= 1_000_000:
            raise ValueError("首版监听波特率范围1200到1000000")
        if self.divisor not in (1, 5, 10, 50):
            raise ValueError("不支持的采样率")
        if self.pre < 0 or self.post < 1 or self.pre + 1 + self.post != DEPTH:
            raise ValueError("前触发+1+后触发必须为65536，后触发至少1点")
        if not 0 <= self.min_ticks <= self.max_ticks < (1 << 47):
            raise ValueError("时间窗不合法")
        if not 0 <= self.value <= 255 or not 0 <= self.mask <= 255 or self.value & ~self.mask:
            raise ValueError("value/mask不合法或value含mask外位")
        return self

    def coverage_warning(self):
        need = self.max_ticks + 1 + 2 * self.divisor + (10 * CLOCK_HZ + self.baud - 1) // self.baud
        return "前窗不足以保留完整UART帧及最大时间窗；建议10/5MS/s或缩短max" if self.pre * self.divisor < need else ""

    def to_bytes(self):
        self.validate()
        return CONFIG.pack(1, self.uart_channel, self.target_channel, self.edge,
                           self.baud, self.divisor, self.pre, self.post,
                           self.value, self.mask, self.action, 0,
                           u48(self.min_ticks), u48(self.max_ticks))

    @classmethod
    def from_bytes(cls, data):
        if len(data) != CONFIG.size:
            raise ValueError("配置字节长度不匹配")
        fields = CONFIG.unpack(data)
        if fields[0] != 1 or fields[11] != 0:
            raise ValueError("配置schema或保留字段不支持")
        return cls(*fields[1:11], int.from_bytes(fields[12], "little"),
                   int.from_bytes(fields[13], "little")).validate()


@dataclass(frozen=True)
class Capture:
    metadata: dict
    samples: bytes
    events: bytes

    @property
    def complete(self):
        return (len(self.samples) == self.metadata["valid_count"] and
                len(self.events) == self.metadata["event_count"] * 16)

    def validate(self):
        m = self.metadata
        if not isinstance(m, dict):
            raise ValueError("记录元数据必须为对象")
        if not isinstance(self.samples, bytes) or not isinstance(self.events, bytes):
            raise ValueError("原始采样/事件数据必须为字节串")
        required = ("config", "trigger_valid", "manual_stop", "trigger_sample_index", "result",
                    "first_sample_timestamp", "capture_anchor_timestamp", "decision_timestamp",
                    "a_tick", "b_tick", "deadline_tick", "observed_b_tick")
        if any(name not in m for name in required):
            raise ValueError("记录元数据缺少必要字段")
        for name, maximum in (("valid_count", DEPTH), ("event_count", 1024),
                              ("session_id", 0xFFFFFFFF), ("config_id", 0xFFFFFFFF),
                              ("sample_divisor", 50), ("event_overwrites", 0xFFFFFFFF),
                              ("accepted", 0xFFFFFFFF), ("pass", 0xFFFFFFFF),
                              ("early", 0xFFFFFFFF), ("timeout", 0xFFFFFFFF), ("cancelled", 0xFFFFFFFF)):
            if type(m.get(name)) is not int or not 0 <= m[name] <= maximum:
                raise ValueError(f"记录字段类型/范围非法: {name}")
        if type(m["trigger_valid"]) is not bool or type(m["manual_stop"]) is not bool:
            raise ValueError("触发/停止标志必须为布尔值")
        if m.get("origin") not in ("mock", "hardware"):
            raise ValueError("不支持的记录来源")
        if type(m.get("record_schema")) is not int or m["record_schema"] != 1 or m.get("profile") != "mvp1":
            raise ValueError("不支持的记录schema/profile")
        if type(m.get("clock_hz")) is not int or m["clock_hz"] != CLOCK_HZ:
            raise ValueError("首版要求50MHz标称时钟")
        if not 0 <= m["valid_count"] <= DEPTH or not 0 <= m["event_count"] <= 1024:
            raise ValueError("记录计数超出容量")
        if len(self.samples) > m["valid_count"] or len(self.events) > m["event_count"] * 16 or len(self.events) % 16:
            raise ValueError("原始数据长度不匹配")
        if not isinstance(m["config"], dict) or set(m["config"]) != set(Config.__dataclass_fields__):
            raise ValueError("记录配置必须包含完整配置字段")
        try:
            config = Config(**m["config"]).validate()
        except TypeError as exc:
            raise ValueError("记录配置字段非法") from exc
        if m["sample_divisor"] != config.divisor:
            raise ValueError("元数据采样率与配置不一致")
        if m["trigger_valid"]:
            index = m["trigger_sample_index"]
            if type(index) is not int or not 0 <= index < m["valid_count"]:
                raise ValueError("触发索引无效")
        for name in ("first_sample_timestamp", "capture_anchor_timestamp", "decision_timestamp",
                     "a_tick", "b_tick", "deadline_tick", "observed_b_tick"):
            value = m.get(name)
            if value is not None and (type(value) is not int or not 0 <= value <= TICK_MASK):
                raise ValueError(f"无效时间戳: {name}")
        if m["first_sample_timestamp"] is None:
            raise ValueError("缺少首采样时间戳")
        if not m["trigger_valid"] and any(m[name] is not None for name in
                ("capture_anchor_timestamp", "decision_timestamp", "trigger_sample_index")):
            raise ValueError("无触发时判定/锚点/索引必须为空")
        if (m["a_tick"] is None) != (m["deadline_tick"] is None):
            raise ValueError("A与截止时间有效性不一致")
        if m["a_tick"] is not None and m["deadline_tick"] != ((m["a_tick"] + config.max_ticks) & TICK_MASK):
            raise ValueError("截止时间与A及规则上限不一致")
        if m["b_tick"] is not None and m["a_tick"] is None:
            raise ValueError("匹配B缺少有效A")
        if "flags" in m:
            flags = (int(m["trigger_valid"]) | (int(m["a_tick"] is not None) << 1) |
                     (int(m["b_tick"] is not None) << 2) | (int(m["observed_b_tick"] is not None) << 3) |
                     (int(m["manual_stop"]) << 4))
            if type(m["flags"]) is not int or m["flags"] != flags:
                raise ValueError("元数据flags与有效字段不一致")
        if m["trigger_valid"]:
            if m.get("capture_anchor_timestamp") is None or m.get("decision_timestamp") is None:
                raise ValueError("有效触发缺少判定/锚点时间")
            anchor = (m["first_sample_timestamp"] + m["trigger_sample_index"] * config.divisor) & TICK_MASK
            if anchor != m["capture_anchor_timestamp"] or not 0 <= delta_tick(anchor, m["decision_timestamp"]) < config.divisor:
                raise ValueError("触发样点、缓存锚点与逻辑判定时间不一致")
        if not isinstance(m["result"], str) or m["result"] not in Result.__members__:
            raise ValueError("未知规则结果")
        if "result_code" in m and (type(m["result_code"]) is not int or m["result_code"] != int(Result[m["result"]])):
            raise ValueError("规则结果与结果代码不一致")
        if m["result"] in ("PASS", "EARLY") and (m["a_tick"] is None or m["b_tick"] is None):
            raise ValueError("PASS/EARLY缺少有效A/B")
        if m["result"] == "TIMEOUT" and (m["a_tick"] is None or m["b_tick"] is not None):
            raise ValueError("TIMEOUT必须有A且无匹配B")
        unpack_events(self.events)
        return self

    def times_ms(self):
        m = self.metadata
        reference = m.get("decision_timestamp")
        if reference is None:
            reference = m["first_sample_timestamp"]
        first = delta_tick(m["first_sample_timestamp"], reference)
        return (first + np.arange(len(self.samples), dtype=np.int64) * m["sample_divisor"]) * 1000 / m["clock_hz"]

    def covers_tick(self, tick):
        """Local received waveform coverage; never trust a legacy derived flag."""
        return (tick is not None and bool(self.samples) and
                0 <= delta_tick(tick, self.metadata["first_sample_timestamp"]) <=
                (len(self.samples) - 1) * self.metadata["sample_divisor"])


def record_identity(origin="mock"):
    return {"record_schema": 1, "profile": "mvp1", "record_id": str(uuid4()),
            "origin": origin, "app_version": __version__}

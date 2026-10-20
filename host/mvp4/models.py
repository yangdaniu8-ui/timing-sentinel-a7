"""Strict independent profile4 schema. Legacy profile1 files stay untouched."""
from dataclasses import asdict, dataclass, field
import struct
from uuid import uuid4, UUID
import numpy as np
from app.models import delta_tick, ms_ticks, TICK_MASK
from protocol.messages import Action, Result, State, u48
from mvp4 import __version__

HEADER = struct.Struct("<BBBBIHHII")
RULE = struct.Struct("<BBBBB3s6s6s")
COUNTERS = ("accepted", "pass", "early", "timeout", "cancelled", "incomplete", "overlap")


def require(condition, message):
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class Rule:
    target_channel: int = 1
    edge: int = 0
    value: int = 0xa5
    mask: int = 0xff
    action: int = int(Action.RECORD)
    min_ticks: int = 0
    max_ticks: int = 150000

    def validate(self, uart_channel):
        require(all(type(x) is int for x in asdict(self).values()), "规则字段必须为整数")
        require(0 <= self.target_channel < 8 and self.target_channel != uart_channel, "GPIO不能与UART同通道")
        require(self.edge in (0, 1) and self.action in (0, 1, 2), "未知边沿或动作")
        require(0 <= self.value <= 255 and 0 <= self.mask <= 255 and not self.value & ~self.mask, "value/mask不合法")
        require(0 <= self.min_ticks <= self.max_ticks < (1 << 47), "规则时间窗不合法")
        return self

    def to_bytes(self):
        return RULE.pack(self.target_channel, self.edge, self.value, self.mask, self.action,
                         b"\0" * 3, u48(self.min_ticks), u48(self.max_ticks))


def default_rules():
    return (Rule(action=2, min_ticks=25000, max_ticks=100000), Rule(max_ticks=25000),
            Rule(), Rule(edge=1, min_ticks=55000))


@dataclass(frozen=True)
class Config:
    uart_channel: int = 0
    baud: int = 115200
    divisor: int = 5
    pre: int = 32767
    post: int = 32768
    enabled_mask: int = 15
    rules: tuple = field(default_factory=default_rules)

    def validate(self):
        require(all(type(getattr(self, k)) is int for k in ("uart_channel", "baud", "divisor", "pre", "post", "enabled_mask")), "全局配置类型非法")
        require(0 <= self.uart_channel < 8 and 1200 <= self.baud <= 1000000, "UART配置不合法")
        require(self.divisor in (1, 5, 10, 50) and self.pre >= 0 and self.post >= 1
                and self.pre + 1 + self.post == 65536, "采样/前后窗不合法")
        require(1 <= self.enabled_mask <= 15 and isinstance(self.rules, tuple) and len(self.rules) == 4, "必须至少启用一条、固定四槽")
        for i, rule in enumerate(self.rules):
            require(isinstance(rule, Rule), "规则对象类型非法")
            if self.enabled_mask & (1 << i):
                rule.validate(self.uart_channel)
        return self

    def to_bytes(self):
        self.validate()
        head = HEADER.pack(4, self.uart_channel, self.enabled_mask, 0, self.baud, self.divisor, 0, self.pre, self.post)
        return head + b"".join(rule.to_bytes() if self.enabled_mask & (1 << i) else b"\0" * 20 for i, rule in enumerate(self.rules))

    @classmethod
    def from_bytes(cls, raw):
        require(len(raw) == 100, "CONFIG4必须100字节")
        schema, uart, mask, reserved, baud, divisor, reserved2, pre, post = HEADER.unpack(raw[:20])
        require(schema == 4 and reserved == reserved2 == 0, "CONFIG4 schema/保留字段非法")
        rules = []
        for i in range(4):
            block = raw[20+i*20:40+i*20]
            channel, edge, value, byte_mask, action, zero, minimum, maximum = RULE.unpack(block)
            require(zero == b"\0" * 3, "规则保留字段非法")
            if not mask & (1 << i):
                require(block == b"\0" * 20, "禁用规则必须全零")
            rules.append(Rule(channel, edge, value, byte_mask, action, int.from_bytes(minimum, "little"), int.from_bytes(maximum, "little")))
        return cls(uart, baud, divisor, pre, post, mask, tuple(rules)).validate()

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        require(isinstance(data, dict) and set(data) == set(cls.__dataclass_fields__), "配置字段不完整")
        values = dict(data)
        require(isinstance(values["rules"], (list, tuple)) and len(values["rules"]) == 4, "规则槽字段非法")
        values["rules"] = tuple(Rule(**item) for item in values["rules"])
        return cls(**values).validate()


def unpack_events(raw):
    require(len(raw) % 16 == 0, "事件未16字节对齐")
    rows = []
    for i in range(0, len(raw), 16):
        word = int.from_bytes(raw[i:i+16], "little")
        require(word >> 112 == 0, "事件保留高位非零")
        results = [Result((word >> (82 + r*4)) & 15).name for r in range(4)]
        state = (word >> 106) & 63
        require(state in State._value2member_map_, "事件状态非法")
        rows.append(dict(tick=word & TICK_MASK, value=(word >> 48) & 255, rise=(word >> 56) & 255,
                         fall=(word >> 64) & 255, uart=(word >> 73) & 255 if word >> 72 & 1 else None,
                         uart_error=bool(word >> 81 & 1), results=results, overlap_mask=(word >> 98) & 15,
                         accepted_mask=(word >> 102) & 15, state=State(state).name))
    return rows


def decode_meta(raw):
    require(len(raw) == 412, "META4必须412字节")
    number = lambda offset, size: int.from_bytes(raw[offset:offset+size], "little")
    require(raw[0] == 4 and raw[1] & ~3 == 0 and raw[52:56] == b"\0"*4, "META4 schema/保留位非法")
    triggered = bool(raw[1] & 1)
    config = Config.from_bytes(raw[312:])
    require(raw[3] == config.enabled_mask and raw[50] <= 15 and raw[51] <= 15, "META4配置掩码不一致")
    meta = dict(record_schema=2, profile="mvp4", record_id=str(uuid4()), app_version=__version__,
                trigger_valid=triggered, manual_stop=bool(raw[1] & 2), primary_rule=raw[2] if triggered else None,
                enabled_mask=raw[3], session_id=number(4,4), config_id=number(8,4), clock_hz=number(12,4),
                valid_count=number(16,4), sample_divisor=number(20,2), event_count=number(22,2), event_overwrites=number(24,4),
                first_sample_timestamp=number(28,6), capture_anchor_timestamp=number(34,6) if triggered else None,
                decision_timestamp=number(40,6) if triggered else None, trigger_sample_index=number(46,4) if triggered else None,
                trigger_rule_mask=raw[50], incomplete_mask=raw[51], config=config.as_dict(), rules=[])
    if not triggered:
        require(raw[2] == 255 and number(34,6) == number(40,6) == raw[50] == 0 and number(46,4) == 0xffffffff, "无触发字段非空")
    for r in range(4):
        offset = 56 + r * 64
        flags, result = raw[offset], raw[offset+1]
        require(flags & ~15 == 0 and raw[offset+2:offset+4] == b"\0"*2 and raw[offset+34:offset+36] == b"\0"*2, "规则元数据保留位非法")
        entry = dict(rule_id=r, flags=flags, result=Result(result).name)
        for field, pos, bit in (("a_tick",4,0),("b_tick",10,1),("observed_b_tick",22,2),("result_tick",28,3)):
            value = number(offset+pos,6)
            require(bool(flags & (1 << bit)) or value == 0, "无效规则时间戳必须全零")
            entry[field] = value if flags & (1 << bit) else None
        deadline = number(offset+16,6)
        require(entry["a_tick"] is not None or deadline == 0, "无A却声明deadline")
        entry["deadline_tick"] = deadline if entry["a_tick"] is not None else None
        entry["counts"] = {name: number(offset+36+4*i,4) for i,name in enumerate(COUNTERS)}
        meta["rules"].append(entry)
    return meta


@dataclass(frozen=True)
class Capture:
    metadata: dict
    samples: bytes
    events: bytes

    @property
    def complete(self):
        return len(self.samples) == self.metadata["valid_count"] and len(self.events) == self.metadata["event_count"] * 16

    def validate(self):
        m = self.metadata
        require(m.get("profile") == "mvp4" and m.get("record_schema") == 2 and m.get("origin") in ("hardware", "mock"), "记录profile/schema/来源不支持")
        config = Config.from_dict(m["config"])
        require(str(UUID(m["record_id"])) == m["record_id"], "记录UUID非法")
        require(m.get("validation_level") == "protocol_only_not_independently_verified", "不得虚报独立精度认证")
        if m["origin"] == "hardware":
            require(m.get("firmware") == "mvp4-a7-0.1.0", "实物固件来源不一致")
        require(m["clock_hz"] == 50000000 and m["sample_divisor"] == config.divisor and m["enabled_mask"] == config.enabled_mask, "时钟/配置不一致")
        for name, maximum in (("valid_count",65536),("event_count",1024),("session_id",0xffffffff),
                              ("config_id",0xffffffff),("event_overwrites",0xffffffff),("incomplete_mask",15),("trigger_rule_mask",15)):
            require(type(m[name]) is int and 0 <= m[name] <= maximum, "元数据整数字段非法: " + name)
        require(type(m["trigger_valid"]) is bool and type(m["manual_stop"]) is bool and isinstance(self.samples,bytes) and isinstance(self.events,bytes), "记录字段类型非法")
        require(len(self.samples) <= m["valid_count"] and len(self.events) <= m["event_count"]*16, "原始记录超长")
        require(type(m["first_sample_timestamp"]) is int and 0 <= m["first_sample_timestamp"] <= TICK_MASK, "首采样时间戳非法")
        require(not (m["trigger_rule_mask"] | m["incomplete_mask"]) & ~config.enabled_mask, "禁用槽声明触发/截断")
        if m["trigger_valid"]:
            require(type(m["primary_rule"]) is int and m["primary_rule"] in range(4) and m["trigger_rule_mask"] & (1 << m["primary_rule"]), "主触发编号/掩码非法")
            require(m["primary_rule"] == next(i for i in range(4) if m["trigger_rule_mask"] & (1 << i)), "并发触发未选最低编号")
            require(type(m["trigger_sample_index"]) is int and 0 <= m["trigger_sample_index"] < m["valid_count"], "触发索引非法")
            require(all(type(m[k]) is int and 0 <= m[k] <= TICK_MASK for k in ("capture_anchor_timestamp","decision_timestamp")), "触发tick非法")
            require(m["capture_anchor_timestamp"] == ((m["first_sample_timestamp"] + m["trigger_sample_index"]*config.divisor) & TICK_MASK)
                    and 0 <= delta_tick(m["capture_anchor_timestamp"],m["decision_timestamp"]) < config.divisor, "锚点几何不一致")
        else:
            require(all(m[k] is None for k in ("primary_rule","trigger_sample_index","capture_anchor_timestamp","decision_timestamp")) and m["trigger_rule_mask"] == 0, "无触发却有触发数据")
        require(isinstance(m["rules"],list) and len(m["rules"]) == 4, "规则元数据不完整")
        for i, entry in enumerate(m["rules"]):
            rule = config.rules[i]
            require(entry["rule_id"] == i and entry["result"] in Result.__members__, "规则编号/结果非法")
            require(type(entry["flags"]) is int and 0 <= entry["flags"] <= 15, "规则标志非法")
            for name in ("a_tick","b_tick","deadline_tick","observed_b_tick","result_tick"):
                value=entry[name]
                require(value is None or type(value) is int and 0 <= value <= TICK_MASK, "规则tick非法")
            require(set(entry["counts"]) == set(COUNTERS) and all(type(v) is int and 0 <= v <= 0xffffffff for v in entry["counts"].values()), "规则计数非法")
            a,b=entry["a_tick"],entry["b_tick"]
            for name,bit in (("a_tick",0),("b_tick",1),("observed_b_tick",2),("result_tick",3)):
                require(bool(entry["flags"] & (1 << bit)) == (entry[name] is not None), "规则有效位不一致")
            require((entry["result"] == "NONE") == (entry["result_tick"] is None), "结果有效性不一致")
            require((a is None) == (entry["deadline_tick"] is None), "A/deadline有效性不一致")
            if a is not None:
                require(entry["deadline_tick"] == ((a+rule.max_ticks)&TICK_MASK), "规则deadline错误")
            if entry["result"] in ("PASS","EARLY"):
                require(a is not None and b is not None and entry["result_tick"] == b, "终端结果缺少匹配A/B")
                delay=delta_tick(b,a)
                require(0 <= delay <= rule.max_ticks and entry["result"] == ("EARLY" if delay < rule.min_ticks else "PASS"), "闭区间分类错误")
            elif entry["result"] == "TIMEOUT":
                require(a is not None and b is None and delta_tick(entry["result_tick"],a) == rule.max_ticks+1, "TIMEOUT不是max+1")
            elif entry["result"] in ("CANCELLED_DISARM","INCOMPLETE_CAPTURE_END"):
                require(a is not None and b is None and entry["result_tick"] is not None, "取消/截断缺少A或误配B")
            require(bool(m["incomplete_mask"] & (1 << i)) == (entry["result"] == "INCOMPLETE_CAPTURE_END"), "截断掩码不一致")
            if not config.enabled_mask & (1 << i):
                require(entry["result"] == "NONE" and a is None and all(v==0 for v in entry["counts"].values()), "禁用槽产生了结果")
        unpack_events(self.events)
        return self

    def times_ms(self):
        m=self.metadata
        reference=m["decision_timestamp"] if m["decision_timestamp"] is not None else m["first_sample_timestamp"]
        return (delta_tick(m["first_sample_timestamp"],reference)+np.arange(len(self.samples),dtype=np.int64)*m["sample_divisor"])/50000

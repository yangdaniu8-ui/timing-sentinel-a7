from dataclasses import asdict
import struct
from app.models import Capture, Config, delta_tick, record_identity
from app.services.device_client import Cancelled, DeviceFailure
from protocol.messages import Command, READ, decode_meta, unpack_events


def read_capture(client, progress=None):
    progress = progress or (lambda a, b: None)
    raw = client.request(Command.META, struct.pack("<I", client.session_id))
    try:
        meta, config = decode_meta(raw)
        meta.update(record_identity(client.origin))
        meta["config"] = asdict(Config.from_bytes(config))
        if meta["session_id"] != client.session_id or meta["config_id"] != client.config_id:
            raise ValueError("元数据session/config不匹配")
        Capture(meta, b"", b"").validate()  # reject malformed geometry before reads
    except (ValueError, TypeError, KeyError) as exc:
        client.uncertain = True
        raise DeviceFailure(f"元数据非法，停止读取并等待重新连接：{exc}") from exc
    meta["firmware"] = client.info["firmware"]
    meta["transport"] = client.info.get("transport", "mock")
    meta["link"] = client.info.get("link", "unknown")
    meta["validation_level"] = "simulation" if client.origin == "mock" else "protocol_only_not_independently_verified"
    sample_bytes, event_bytes = bytearray(), bytearray()
    total = meta["valid_count"] + meta["event_count"] * 16
    try:
        for cmd, target, size, chunk in ((Command.SAMPLES, sample_bytes, meta["valid_count"], 1004),
                                          (Command.EVENTS, event_bytes, meta["event_count"] * 16, 992)):
            while len(target) < size:
                if client.cancel():
                    raise Cancelled()
                offset = len(target)
                body = client.request(cmd, READ.pack(client.session_id, offset, min(chunk, size - offset)))
                if len(body) < READ.size:
                    raise DeviceFailure("分片头过短")
                session, actual_offset, count = READ.unpack(body[:READ.size])
                if session != client.session_id or actual_offset != offset or count != len(body) - READ.size:
                    raise DeviceFailure("分片身份/偏移/长度不一致")
                if not 0 < count <= min(chunk, size - offset) or (cmd == Command.EVENTS and count % 16):
                    raise DeviceFailure("分片长度非法")
                if cmd == Command.EVENTS:
                    unpack_events(body[READ.size:])  # reject invalid event enums in worker
                target.extend(body[READ.size:])
                progress(len(sample_bytes) + len(event_bytes), total)
    except Cancelled:
        meta["transfer_error"] = "用户取消读取，保留已收到的连续前缀"
    except (DeviceFailure, ConnectionError, OSError, ValueError) as exc:
        client.uncertain = True
        meta["transfer_error"] = str(exc)
    meta["transport_retries"] = client.retries
    meta["crc_errors"] = client.decoder.crc_errors
    meta["transfer_complete"] = len(sample_bytes) == meta["valid_count"] and len(event_bytes) == meta["event_count"] * 16
    meta["actual_pre_count"] = meta["trigger_sample_index"] if meta["trigger_valid"] else None
    meta["actual_post_count"] = (meta["valid_count"] - meta["trigger_sample_index"] - 1) if meta["trigger_valid"] else None
    meta["counter_saturated"] = any(meta[k] == 0xFFFFFFFF for k in ("accepted", "pass", "early", "timeout", "cancelled"))
    def in_window(tick, count):
        return tick is not None and count > 0 and 0 <= delta_tick(tick, meta["first_sample_timestamp"]) <= (count - 1) * meta["sample_divisor"]

    meta["received_sample_count"] = len(sample_bytes)
    meta["received_event_count"] = len(event_bytes) // 16
    meta["a_in_device_window"] = in_window(meta["a_tick"], meta["valid_count"])
    for key, name in (("a_tick", "a_in_waveform"), ("observed_b_tick", "observed_b_in_waveform"),
                      ("decision_timestamp", "decision_in_waveform")):
        # Local evidence coverage is based only on received points, never the
        # device's promised size. Missing suffixes remain unknown.
        meta[name] = in_window(meta[key], len(sample_bytes))
    observed = meta.get("observed_b_tick")
    if meta["result"] == "TIMEOUT":
        note = "设备元数据报告LATE_OBSERVED：冻结观察窗内有迟到边沿" if observed is not None else "设备元数据报告冻结观察窗内未见响应（不代表永远无响应）"
        if observed is not None and not meta["observed_b_in_waveform"]:
            note += "；该边沿时间未被本地已收到波形覆盖"
    elif meta["manual_stop"]:
        note = "手动停止并保留；取消不计规则失败"
    else:
        note = ("设备模拟结果" if client.origin == "mock" else "设备报告结果") + "；原始tick与波形共用标称50MHz时间基准"
    if not meta["transfer_complete"]:
        note += "；传输不完整，缺失数据不能用于证明事件未发生"
    meta["observation"] = note
    return Capture(meta, bytes(sample_bytes), bytes(event_bytes)).validate()

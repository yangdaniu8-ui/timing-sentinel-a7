"""Read-only E2 task identity; no filename, verdict or annotation inference."""
from app.models import delta_tick
from mvp5.analysis import audit_capture
from mvp5.experiment import decode_uart
from mvp5.models import unpack_events, require

LOAD_NAMES = ("无中断", "轻度中断竞争", "重度中断竞争")
PERIOD_US = (0, 1000, 125)


def parse_packet(values):
    """E2 + three ID nibbles + profile + phase + checksum + A5."""
    require(len(values) == 10 and values[0] == 0xe2 and values[-1] == 0xa5,
            "需要唯一完整 E2 → A5 十字节任务标记")
    require(all(values[i] & 0xf0 == tag for i, tag in
                ((1, 0x40), (2, 0x40), (3, 0x40), (4, 0x60),
                 (5, 0x50), (6, 0x50), (7, 0x70), (8, 0x70))), "任务标记字段标签错误")
    job = ((values[1] & 15) << 8) | ((values[2] & 15) << 4) | (values[3] & 15)
    profile = values[4] & 15
    phase = ((values[5] & 15) << 4) | (values[6] & 15)
    checksum = ((values[7] & 15) << 4) | (values[8] & 15)
    require(1 <= job <= 4095 and profile < 3, "任务编号或负载档位越界")
    require(checksum == (job ^ (job >> 8) ^ profile ^ phase ^ 0x96) & 255,
            "任务标记校验字节错误")
    return dict(id=job, profile=profile, phase=phase, packet_hex=bytes(values).hex(),
                name=LOAD_NAMES[profile], nominal_period_us=PERIOD_US[profile])


def identify_workload(cap):
    rows = unpack_events(cap.events)
    uart = [e for e in rows if e["uart"] is not None]
    if not any(e["uart"] == 0xe2 for e in uart):
        return None
    result = dict(kind="workload", scene=None, marker=0xe2, workload=None, name="负载身份未识别",
                  verified=False, status="E2标记不完整或无效", detail="不根据文件名、编号顺序或规则结果猜负载。")
    try:
        identity = parse_packet([e["uart"] for e in uart])
        require(not any(e["uart_error"] for e in rows), "事件含UART帧错误")
        m = cap.metadata
        enabled = [r for i, r in enumerate(m["rules"]) if m["enabled_mask"] & (1 << i)]
        require(enabled and all(r["counts"]["accepted"] == 1 for r in enabled)
                and {r["a_tick"] for r in enabled} == {uart[-1]["tick"]},
                "非唯一A5单事务，不自动关联负载身份")
        result.update(workload=identity, name=identity["name"],
                      status="负载标记已识别 · 待核验",
                      detail="身份来自本记录E2标记，不等同于任务正确或规则PASS；标称中断周期不是独立测量值。")
        if not cap.complete:
            result["status"] = "负载标记已识别 · 数据不完整，未验证"
        elif m["origin"] != "hardware":
            result["status"] = "模拟负载标记 · 非实物验收"
        else:
            frames = decode_uart(bytes((v >> m["config"]["uart_channel"]) & 1 for v in cap.samples),
                                 m["first_sample_timestamp"], m["sample_divisor"],
                                 m["clock_hz"] // m["config"]["baud"])
            require([f["value"] for f in frames] == [e["uart"] for e in uart],
                    "波形与事件的完整任务标记不一致或不在观察窗内")
            require(all(0 <= delta_tick(f["nominal_stop_center"], e["tick"]) < m["sample_divisor"]
                        for f, e in zip(frames, uart)), "波形与事件任务标记时间不一致")
            audit = audit_capture(cap)
            require(audit["status"] == "verified", "；".join(audit["reasons"]) or "规则未核验")
            result.update(verified=True, status="负载身份 / 样点 / 事件 / 规则一致")
    except (ValueError, AssertionError) as exc:
        result.update(status="负载标记识别 / 核验未通过", detail=str(exc))
    return result


def workload_label(identity):
    period = identity["nominal_period_us"]
    rate = "IRQ关闭" if not period else f"标称 {1_000_000 // period} Hz"
    return f"{identity['name']} · 任务 #{identity['id']} · {rate} · 相位 {identity['phase']} μs（标记值）"

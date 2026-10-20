"""Read-only geometry and conservative single-demo recognition.

Display offsets never change metadata, protocol, or export references.
Scene markers describe stimulus, not a successful hardware verdict.
"""
from app.models import delta_tick
from mvp5.models import unpack_events
from mvp5.experiment import SCENES, verify_capture
from mvp5.workload import identify_workload

SCENE_NAMES = dict(zip(SCENES, ("正常响应", "提前响应", "延迟响应", "无响应", "SPI错误字节", "SPI传输截断")))


def reference_tick(meta, kind="a"):
    enabled = [r for i, r in enumerate(meta["rules"]) if meta["enabled_mask"] & (1 << i)]
    starts = {r["a_tick"] for r in enabled if r["a_tick"] is not None}
    if kind == "a" and len(starts) == 1:
        return starts.pop(), "A事件（UART合法字节）"
    if kind != "first" and meta["decision_timestamp"] is not None:
        reason = "主判定" if kind == "decision" else "主判定（无唯一A，自动回退）"
        return meta["decision_timestamp"], reason
    return meta["first_sample_timestamp"], "首个已收到样点"


def relative_ms(tick, reference, clock_hz=50000000):
    return delta_tick(tick, reference) * 1000 / clock_hz


def identify_scene(cap):
    """Recognize exact Dn,A5 single demo, never a filename/result heuristic."""
    workload = identify_workload(cap)
    if workload is not None:
        return workload
    meta = cap.metadata
    rows = unpack_events(cap.events)
    uart = [e for e in rows if e["uart"] is not None]
    result = dict(scene=None, marker=None, name="未识别", verified=False,
                  status="未找到唯一 D0–D5 或 E2 → A5 标记", detail="不根据轮号或文件名猜场景。")
    if (len(uart) != 2 or not 0xd0 <= uart[0]["uart"] <= 0xd5 or uart[1]["uart"] != 0xa5
            or any(e["uart_error"] for e in rows)):
        return result
    starts = {r["a_tick"] for i, r in enumerate(meta["rules"])
              if meta["enabled_mask"] & (1 << i) and r["a_tick"] is not None}
    if len(starts) != 1 or uart[1]["tick"] not in starts or any(
            r["counts"]["accepted"] != 1 for i, r in enumerate(meta["rules"])
            if meta["enabled_mask"] & (1 << i)):
        result["status"] = "非唯一单事务，不自动套用六场景"
        return result
    marker = uart[0]["uart"]
    scene = SCENES[marker - 0xd0]
    result.update(scene=scene, marker=marker, name=SCENE_NAMES[scene],
                  status="标记识别，待核验", detail="场景来自STM32标记；不等同于实测通过。")
    if not cap.complete:
        result.update(status="标记识别 · 数据不完整，未验证", detail="仅显示已收到的证据，不填补缺失样点。")
    elif meta["origin"] != "hardware":
        result.update(status="模拟数据标记 · 非实物验收", detail="不能把模拟回放当作实物采集。")
    else:
        try:
            check = verify_capture(cap)
            if check["scene"] != scene:
                raise ValueError("事件标记与样点标记不一致")
            result.update(verified=True, status="样点 / 事件 / 规则一致",
                          detail="按记录内的实际规则核验；场景标记本身不是判定结果。")
        except (ValueError, AssertionError) as exc:
            result.update(status="标记识别 · 核验未通过", detail=str(exc))
    return result


def event_description(event, uart_channel):
    text = []
    if event["uart"] is not None:
        value = event["uart"]
        if 0xd0 <= value <= 0xd5:
            text.append("场景标记：" + SCENE_NAMES[SCENES[value - 0xd0]])
        elif value == 0xe2:
            text.append("E2 · 任务负载标记头（身份见波形上方）")
        elif value == 0xa5:
            text.append("A · UART A5触发")
        else:
            text.append(f"UART字节 {value:02X}")
    if event["spi"] is not None:
        text.append(f"SPI完成 {event['spi']:02X}（第8采样沿）")
    if event["spi_error"]:
        text.append("SPI截断 / 错误")
    if event["uart_error"]:
        text.append("UART帧错误")
    for i, verdict in enumerate(event["results"]):
        if verdict != "NONE":
            text.append(f"R{i} {verdict}")
    for ch in range(8):
        if ch == uart_channel:
            continue
        if event["rise"] & (1 << ch):
            text.append(f"CH{ch} ↑")
        if event["fall"] & (1 << ch):
            text.append(f"CH{ch} ↓")
    return " · ".join(text) or "UART位跳变"

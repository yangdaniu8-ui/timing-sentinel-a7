"""Read-only waveform: explicit reference, units, decoded tags and zoom."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QCheckBox, QPushButton
from app.ui.waveform_view import WaveformView, COLORS
from mvp5.models import unpack_events
from mvp5.presentation import reference_tick, relative_ms, SCENE_NAMES
from mvp5.experiment import SCENES
from mvp5.workload import parse_packet


class TimeAxis(pg.AxisItem):
    """Internal x stays ms; explicit us is display-only, never automatic SI."""
    def __init__(self):
        self.unit = "ms"
        super().__init__(orientation="bottom")
        self.enableAutoSIPrefix(False)

    def tickStrings(self, values, scale, spacing):
        factor = 1000 if self.unit == "us" else 1
        step = abs(spacing * factor)
        digits = max(0, int(np.ceil(-np.log10(step)))) if step > 0 else 0
        return [f"{v * factor:.{min(digits, 9)}f}" if abs(v * factor) >= step * 1e-6 else "0"
                for v in values]


class FourWaveform(WaveformView):
    referenceChanged = Signal()

    def __init__(self):
        super().__init__()
        self.axis = TimeAxis()
        self.plot.setAxisItems({"bottom": self.axis})
        self.reference = None
        self.reference_label = "等待记录"
        self.x_ms = np.array([])
        self.bits = None
        self.protocol_tags = []
        self._changing = False
        bar = self.toolbar_layout
        self.reference_combo = QComboBox()
        for name, kind in (("零点：A事件", "a"), ("零点：主判定", "decision"), ("零点：首样点", "first")):
            self.reference_combo.addItem(name, kind)
        self.reference_combo.setToolTip("只移动显示零点；不改变原始tick、规则结果或CSV/VCD导出参考。")
        self.unit_combo = QComboBox()
        self.unit_combo.addItem("单位：ms", "ms")
        self.unit_combo.addItem("单位：μs", "us")
        self.active_only = QCheckBox("仅已配置通道")
        self.active_only.setChecked(True)
        bar.insertWidget(1, self.reference_combo)
        bar.insertWidget(2, self.unit_combo)
        bar.insertWidget(3, self.active_only)
        self.response_zoom = QPushButton("A→响应")
        self.spi_zoom = QPushButton("放大SPI")
        bar.addWidget(self.response_zoom)
        bar.addWidget(self.spi_zoom)
        self.reference_combo.currentIndexChanged.connect(self._reference_changed)
        self.unit_combo.currentIndexChanged.connect(self._unit_changed)
        self.active_only.toggled.connect(self._channels_changed)
        self.response_zoom.clicked.connect(self.zoom_response)
        self.spi_zoom.clicked.connect(self.zoom_spi)
        self.response_zoom.setEnabled(False)
        self.spi_zoom.setEnabled(False)
        self.plot.getAxis("left").setWidth(135)
        self.plot.setMinimumHeight(210)
        self.plot.getViewBox().sigXRangeChanged.connect(self._reposition_tags)
        self._unit_changed()

    def clear(self):
        super().clear()
        self.x_ms = np.array([])
        self.bits = None
        self.protocol_tags = []
        if hasattr(self, "response_zoom"):
            self.response_zoom.setEnabled(False)
            self.spi_zoom.setEnabled(False)

    def _reference_changed(self):
        if self.capture:
            self.set_capture(self.capture)
        self.referenceChanged.emit()

    def _unit_changed(self):
        self.axis.unit = self.unit_combo.currentData()
        unit = "μs" if self.axis.unit == "us" else "ms"
        # No units metadata: pyqtgraph must not apply a second prefix transform.
        self.axis.setLabel(f"相对 {self.reference_label} 的时间（{unit}）", units=None)
        self.axis.picture = None
        self.axis.update()
        self.update_measurement()

    def _channels_changed(self):
        if self.capture:
            self._channel_layout(self.capture.metadata["config"])

    def _channel_layout(self, cfg):
        channels = {cfg["uart_channel"]}
        gpio = {}
        for i, rule in enumerate(cfg["rules"]):
            if cfg["enabled_mask"] & (1 << i) and not rule["target_kind"]:
                channels.add(rule["target_channel"])
                gpio.setdefault(rule["target_channel"], []).append(i)
        spi = {cfg["spi_clk"]: "SCK", cfg["spi_mosi"]: "MOSI", cfg["spi_cs"]: "CS_n"} if cfg["spi_enabled"] else {}
        channels.update(spi)
        if not self.active_only.isChecked():
            channels = set(range(8))
        names = []
        for ch, curve in enumerate(self.curves):
            curve.setVisible(ch in channels)
            name = "UART" if ch == cfg["uart_channel"] else spi.get(ch, "GPIO · R" + ",".join(map(str, gpio[ch])) if ch in gpio else "未配置")
            if ch in channels:
                names.append((14 - 2 * ch + .35, f"CH{ch} · {name}"))
        self.plot.getAxis("left").setTicks([names])
        self.plot.setYRange(14 - 2 * max(channels) - .7, 14 - 2 * min(channels) + 1.7, padding=0)

    def set_capture(self, cap):
        same_record = self.capture is cap
        old_ref = self.reference
        old_cursors = [c.value() for c in self.cursors]
        self.clear()
        self.capture = cap
        meta = cap.metadata
        self.reference, self.reference_label = reference_tick(meta, self.reference_combo.currentData())
        self.x_ms = (relative_ms(meta["first_sample_timestamp"], self.reference, meta["clock_hz"])
                     + np.arange(len(cap.samples), dtype=np.int64) * meta["sample_divisor"] * 1000 / meta["clock_hz"])
        self.bits = np.frombuffer(cap.samples, dtype=np.uint8)
        x = self.x_ms
        for ch, curve in enumerate(self.curves):
            bits = (self.bits >> ch) & 1
            if not len(bits):
                continue
            changes = np.flatnonzero(bits[1:] != bits[:-1]) + 1
            xx, yy = [x[0]], [float(bits[0])]
            for i in changes:
                xx.extend((x[i], x[i]))
                yy.extend((float(bits[i-1]), float(bits[i])))
            xx.append(x[-1]); yy.append(float(bits[-1]))
            curve.setData(np.array(xx), 14 - ch * 2 + np.array(yy) * .7)
        cfg = meta["config"]
        self._channel_layout(cfg)
        self._unit_changed()
        if len(x):
            self._add_rule_guides(meta)
            self._add_protocol_tags(meta)
        self.locate.setEnabled(self._tick_visible(meta["decision_timestamp"]))
        a, _ = reference_tick(meta, "a")
        has_unique_a = len({r["a_tick"] for i, r in enumerate(meta["rules"])
                            if meta["enabled_mask"] & (1 << i) and r["a_tick"] is not None}) == 1
        self.response_zoom.setEnabled(bool(has_unique_a and self._tick_visible(a)))
        self.spi_zoom.setEnabled(bool(cfg["spi_enabled"] and len(x) and np.any(((self.bits >> cfg["spi_cs"]) & 1) == 0)))
        self._changing = True
        if same_record and old_ref is not None:
            shift = relative_ms(old_ref, self.reference, meta["clock_hz"])
            for c, value in zip(self.cursors, old_cursors):
                c.setValue(value + shift)
        elif has_unique_a and self._tick_visible(a):
            self.cursors[0].setValue(self._pos(a))
            end = next((r["observed_b_tick"] for i, r in enumerate(meta["rules"])
                        if cfg["enabled_mask"] & (1 << i) and not cfg["rules"][i]["target_kind"]
                        and r["observed_b_tick"] is not None), None)
            self.cursors[1].setValue(self._pos(end) if self._tick_visible(end) else self._pos(a))
        else:
            for c, value in zip(self.cursors, (float(x[0]), float(x[-1])) if len(x) else (0, 0)):
                c.setValue(value)
        self._changing = False
        self.fit()
        self.update_measurement()

    def _pos(self, tick):
        return relative_ms(tick, self.reference, self.capture.metadata["clock_hz"])

    def _tick_visible(self, tick):
        return bool(tick is not None and len(self.x_ms) and self.x_ms[0] <= self._pos(tick) <= self.x_ms[-1])

    def _add(self, item):
        self.plot.addItem(item)
        self.markers.append(item)

    def _add_rule_guides(self, meta):
        groups = {}
        first_gpio = next((i for i,r in enumerate(meta["config"]["rules"])
                           if meta["enabled_mask"] & (1 << i) and not r["target_kind"]), None)
        for i, entry in enumerate(meta["rules"]):
            if not meta["enabled_mask"] & (1 << i):
                continue
            a = entry["a_tick"]
            if a is not None:
                groups.setdefault(a, []).append("A · UART合法字节")
            if self._tick_visible(entry["result_tick"]):
                groups.setdefault(entry["result_tick"], []).append(f"R{i} {entry['result']}")
            if entry["observed_b_tick"] is not None and i == first_gpio:
                groups.setdefault(entry["observed_b_tick"], []).append("GPIO响应" + ("（迟到）" if entry["result"] == "TIMEOUT" else ""))
        enabled_gpio = [(i, r) for i, r in enumerate(meta["config"]["rules"])
                        if meta["enabled_mask"] & (1 << i) and not r["target_kind"]]
        if enabled_gpio:
            i, rule = enabled_gpio[0]
            a = meta["rules"][i]["a_tick"]
            if a is not None:
                low = self._pos(a) + rule["min_ticks"] * 1000 / meta["clock_hz"]
                high = self._pos(a) + rule["max_ticks"] * 1000 / meta["clock_hz"]
                if high >= self.x_ms[0] and low <= self.x_ms[-1]:
                    region = pg.LinearRegionItem((max(low, self.x_ms[0]), min(high, self.x_ms[-1])), movable=False,
                                                 brush=pg.mkBrush(65, 145, 110, 30), pen=pg.mkPen(None))
                    region.setZValue(-10)
                    self._add(region)
                    text = pg.TextItem(f"R{i} GPIO合格窗 {rule['min_ticks']/50000:g}–{rule['max_ticks']/50000:g} ms",
                                       color="#8fcaac", anchor=(.5, 0))
                    text.setPos((max(low, self.x_ms[0]) + min(high, self.x_ms[-1]))/2,
                                14 - 2 * rule["target_channel"] - .25)
                    self._add(text)
        for index, (tick, labels) in enumerate(sorted(groups.items(), key=lambda p: self._pos(p[0]))):
            if not self._tick_visible(tick):
                continue
            label = " / ".join(dict.fromkeys(labels))
            color = "#ff9c9c" if "TIMEOUT" in label or "EARLY" in label else "#89dce8"
            line = pg.InfiniteLine(pos=self._pos(tick), pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine),
                                   label=label, labelOpts={"position": .88 - .10 * (index % 5), "color": color,
                                                        "fill": pg.mkBrush(16, 25, 37, 225)})
            self._add(line)

    def _add_protocol_tags(self, meta):
        events = unpack_events(self.capture.events)
        uart = [e for e in events if e["uart"] is not None]
        identity = None
        try:
            identity = parse_packet([e["uart"] for e in uart])
        except ValueError:
            pass
        for event in events:
            if not self._tick_visible(event["tick"]):
                continue
            if event["uart"] is not None:
                value = event["uart"]
                if identity and event in uart[1:-1]:
                    continue  # Group the tagged packet instead of overlapping eight nibble labels.
                if value == 0xa5 and event["accepted_mask"]:
                    continue  # The exact A marker already identifies this byte.
                label = f"{value:02X}"
                if 0xd0 <= value <= 0xd5:
                    label += " · 场景标记"
                elif value == 0xa5:
                    label += " · 触发字节"
                elif value == 0xe2:
                    label += " · 负载标记"
                channel, duration = meta["config"]["uart_channel"], 10 * 1000 / meta["config"]["baud"]
                if identity and event is uart[0]:
                    label = f"E2标记 · #{identity['id']} · {identity['name']}"
            elif event["spi"] is not None or event["spi_error"]:
                label = "SPI截断 / 错误" if event["spi_error"] else f"SPI {event['spi']:02X} · 8位完成"
                channel, duration = meta["config"]["spi_mosi"], 0
            else:
                continue
            text = pg.TextItem(label, color=COLORS[channel], fill=pg.mkBrush(16, 25, 37, 230), anchor=(.5, 1))
            end = self._pos(event["tick"])
            low = max(float(self.x_ms[0]), end - duration)
            if identity and event is uart[0]:
                end = self._pos(uart[-2]["tick"])
            if event["spi"] is not None or event["spi_error"]:
                cs = (self.bits >> meta["config"]["spi_cs"]) & 1
                starts = np.flatnonzero((cs[:-1] == 1) & (cs[1:] == 0)) + 1
                starts = starts[self.x_ms[starts] <= end]
                low = float(self.x_ms[starts[-1]]) if len(starts) else float(self.x_ms[0])
            self.protocol_tags.append((text, low, end, 14 - 2 * channel + .9))
            self._add(text)

    def _reposition_tags(self, *args):
        low, high = self.plot.viewRange()[0]
        for text, start, end, y in self.protocol_tags:
            visible = end >= low and start <= high
            text.setVisible(visible)
            if visible:
                text.setPos((max(start, low) + min(end, high))/2, y)

    def _range(self, low, high):
        if not len(self.x_ms):
            return
        low, high = max(float(self.x_ms[0]), low), min(float(self.x_ms[-1]), high)
        if high <= low:
            self.fit()
        else:
            self.plot.setXRange(low, high, padding=.025)
            self._reposition_tags()

    def fit(self):
        if len(self.x_ms):
            self.plot.setXRange(float(self.x_ms[0]), float(self.x_ms[-1]) if len(self.x_ms) > 1 else float(self.x_ms[0] + .001), padding=.025)
            self._reposition_tags()

    def locate_decision(self):
        if self.capture and self.locate.isEnabled():
            pos = self._pos(self.capture.metadata["decision_timestamp"])
            self._range(pos - .2, pos + .4)

    def zoom_response(self):
        if not self.capture or not self.response_zoom.isEnabled():
            return
        meta = self.capture.metadata
        a, _ = reference_tick(meta, "a")
        ticks = [a] + [t for i, r in enumerate(meta["rules"]) if meta["enabled_mask"] & (1 << i)
                        for t in (r["deadline_tick"], r["observed_b_tick"]) if t is not None]
        positions = [self._pos(t) for t in ticks]
        self._range(min(positions) - .15, max(positions) + .2)

    def zoom_spi(self):
        if not self.capture or not self.spi_zoom.isEnabled():
            return
        cfg = self.capture.metadata["config"]
        selected = np.flatnonzero(((self.bits >> cfg["spi_cs"]) & 1) == 0)
        if len(selected):
            self.unit_combo.setCurrentIndex(1)
            self._range(float(self.x_ms[selected[0]]) - .01, float(self.x_ms[selected[-1]]) + .01)

    def update_measurement(self):
        if not getattr(self, "capture", None) or getattr(self, "_changing", False):
            return
        unit = getattr(self, "unit_combo", None)
        us = unit is not None and unit.currentData() == "us"
        factor, name = (1000, "μs") if us else (1, "ms")
        distance = abs(self.cursors[1].value() - self.cursors[0].value()) * factor
        ns = self.capture.metadata["sample_divisor"] * 1e9 / self.capture.metadata["clock_hz"]
        self.measurement.setText(f"游标 Δt = {distance:.6f} {name} | {ns:g} ns/点 · 20 ns/tick（标称）")

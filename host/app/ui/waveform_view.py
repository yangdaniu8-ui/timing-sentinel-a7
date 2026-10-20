import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from app.models import delta_tick

COLORS = ("#67d7ff", "#ffcc66", "#a0e8a3", "#c8a1ff", "#ffa3bc", "#70dfd2", "#e4d583", "#a5bcdf")


class WaveformView(QWidget):
    def __init__(self):
        super().__init__()
        self.capture = None
        self.markers = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.toolbar_layout = bar
        self.measurement = QLabel("8路数字输入 · 等待捕获")
        bar.addWidget(self.measurement)
        bar.addStretch()
        full = QPushButton("全览")
        self.full_button = full
        full.clicked.connect(self.fit)
        bar.addWidget(full)
        self.locate = QPushButton("定位判定")
        self.locate.clicked.connect(self.locate_decision)
        self.locate.setEnabled(False)
        bar.addWidget(self.locate)
        layout.addLayout(bar)
        self.plot = pg.PlotWidget(background="#101925")
        self.plot.showGrid(x=True, y=False, alpha=0.18)
        self.plot.setLabel("bottom", "相对逻辑判定时间", units="ms")
        self.plot.setYRange(-0.5, 15.5, padding=0)
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.getPlotItem().hideButtons()
        self.plot.getAxis("left").setTicks([[(14 - 2 * c + 0.35, f"CH{c}") for c in range(8)]])
        self.plot.getAxis("left").setWidth(100)
        self.plot.setMinimumHeight(260)
        self.curves = [self.plot.plot(pen=pg.mkPen(color, width=1.4)) for color in COLORS]
        self.cursors = [pg.InfiniteLine(pos=p, movable=True, pen=pg.mkPen("#c8d5e2", style=Qt.PenStyle.DashLine))
                        for p in (-0.2, 0.2)]
        for cursor in self.cursors:
            self.plot.addItem(cursor)
            cursor.sigPositionChanged.connect(self.update_measurement)
        layout.addWidget(self.plot)

    def clear(self):
        self.capture = None
        for curve in self.curves:
            curve.clear()
        for item in self.markers:
            self.plot.removeItem(item)
        self.markers.clear()
        self.locate.setEnabled(False)
        self.measurement.setText("8路数字输入 · 等待捕获")

    def set_capture(self, capture):
        self.clear()
        self.capture = capture
        m = capture.metadata
        x = capture.times_ms()
        values = np.frombuffer(capture.samples, dtype=np.uint8)
        for channel, curve in enumerate(self.curves):
            # Transition-only coordinates preserve every digital edge exactly.
            bits = (values >> channel) & 1
            if not len(bits):
                continue
            changes = np.flatnonzero(bits[1:] != bits[:-1]) + 1
            xx, yy = [x[0]], [float(bits[0])]
            for i in changes:
                xx.extend((x[i], x[i]))
                yy.extend((float(bits[i - 1]), float(bits[i])))
            xx.append(x[-1]); yy.append(float(bits[-1]))
            curve.setData(np.array(xx), 14 - channel * 2 + np.array(yy) * 0.7)
        names = [f"CH{c}" + (" · UART" if c == m["config"]["uart_channel"] else
                            " · GPIO" if c == m["config"]["target_channel"] else "") for c in range(8)]
        self.plot.getAxis("left").setTicks([[(14 - 2 * c + 0.35, names[c]) for c in range(8)]])
        reference = m.get("decision_timestamp")
        if reference is None:
            reference = m["first_sample_timestamp"]
        self.plot.setLabel("bottom", "相对逻辑判定时间" if m.get("decision_timestamp") is not None else "相对首个有效样点", units="ms")
        if m.get("a_tick") is not None and len(x):
            a = delta_tick(m["a_tick"], reference) * 1000 / m["clock_hz"]
            low = a + m["config"]["min_ticks"] * 1000 / m["clock_hz"]
            high = a + m["config"]["max_ticks"] * 1000 / m["clock_hz"]
            if low <= x[-1] and high >= x[0]:
                region = pg.LinearRegionItem((max(low, x[0]), min(high, x[-1])), movable=False,
                                             brush=pg.mkBrush(61, 129, 112, 35), pen=pg.mkPen(None))
                region.setZValue(-10)
                self.plot.addItem(region)
                self.markers.append(region)
        positions = (("a_tick", "A · UART valid", "#67d7ff"), ("b_tick", "B · GPIO", "#a0e8a3"),
                     ("deadline_tick", "截止", "#ffcc66"), ("decision_timestamp", "判定", "#ff8fa4"),
                     ("capture_anchor_timestamp", "缓存锚点", "#c8a1ff"),
                     ("observed_b_tick", "观察到B", "#70dfd2"))
        used = []
        for field, label, color in positions:
            tick = m.get(field)
            if tick is None or (field == "observed_b_tick" and tick == m.get("b_tick")):
                continue
            pos = delta_tick(tick, reference) * 1000 / m["clock_hz"]
            if len(x) and x[0] <= pos <= x[-1]:
                # Keep exact position, stagger labels when two meanings coincide.
                tolerance = max(1e-6, (x[-1] - x[0]) * .06)
                neighbours = sum(abs(pos - previous) < tolerance for previous in used)
                label_position = .97 - .13 * neighbours
                used.append(pos)
                line = pg.InfiniteLine(pos=pos, pen=pg.mkPen(color, style=Qt.PenStyle.DashLine),
                                       label=label, labelOpts={"position": label_position, "color": color})
                self.plot.addItem(line)
                self.markers.append(line)
        # A received prefix can end before the trigger. Do not navigate to
        # missing evidence or show a blank range as if the decision were read.
        anchor = m.get("capture_anchor_timestamp")
        anchor_offset = delta_tick(anchor, m["first_sample_timestamp"]) if anchor is not None else None
        self.locate.setEnabled(bool(m["trigger_valid"] and len(x) and anchor_offset is not None
                                    and 0 <= anchor_offset <= (len(x) - 1) * m["sample_divisor"]))
        self.fit()
        self.update_measurement()

    def locate_decision(self):
        if not self.capture or not self.locate.isEnabled():
            return
        x = self.capture.times_ms()
        low, high = max(float(x[0]), -0.2), min(float(x[-1]), 0.4)
        if high <= low:
            self.fit()
        else:
            self.plot.setXRange(low, high, padding=0.025)

    def fit(self):
        if self.capture and len(self.capture.samples):
            x = self.capture.times_ms()
            self.plot.setXRange(float(x[0]), float(x[-1]) if len(x) > 1 else float(x[0] + .001), padding=0.025)

    def update_measurement(self):
        distance = abs(self.cursors[1].value() - self.cursors[0].value())
        if self.capture:
            ns = self.capture.metadata["sample_divisor"] * 20
            self.measurement.setText(f"游标 Δt = {distance:.6f} ms  |  波形 {ns} ns/点 · 逻辑 20 ns/tick（标称）")

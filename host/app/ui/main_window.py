from dataclasses import asdict
from pathlib import Path
import json
import logging
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                              QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                              QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
                              QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
                              QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)
from app.models import Config, delta_tick, ms_ticks
from app import __version__
from app.runtime import data_root
from app.services.capture_reader import read_capture
from app.services.device_client import Cancelled, DeviceClient
from app.services.record_store import atomic_output, export_csv, export_json, export_vcd, load_record, save_record
from app.services.diagnostics import export_diagnostics
from app.ui.waveform_view import WaveformView
from app.ui.fonts import configure_fonts
from app.workers import Task
from protocol.messages import Action, SCENARIOS, unpack_events
from simulator.device import SimulatedDevice
from transport.mock import MockTransport
from transport.serial_port import SerialTransport, enumerate_ports

STATES = {"IDLE": "空闲", "PREFILL": "预填充，尚未READY", "ARMED": "READY · 监测中",
          "POST_TRIGGER": "已触发，等待后窗", "FROZEN": "证据已冻结", "UNKNOWN": "设备状态未知"}

STYLE = """
QMainWindow,QWidget { background:#101925; color:#dce7f2; font-family:'Microsoft YaHei UI'; font-size:9pt; }
QGroupBox { border:1px solid #30435b; border-radius:7px; margin-top:13px; padding:12px 8px 8px; }
QGroupBox::title { subcontrol-origin:margin; left:10px; color:#8ebce6; }
QPushButton { background:#23354b; border:1px solid #3b5571; border-radius:5px; padding:6px 11px; }
QPushButton:hover { background:#304d6b; }
QPushButton:disabled { color:#657488; border-color:#26374a; background:#172333; }
QPushButton#primary { background:#147a9b; color:white; font-weight:bold; }
QPushButton#primary:hover { background:#2295ba; }
QPushButton#primary:disabled { color:#657488; border-color:#26374a; background:#172333; }
QLineEdit,QComboBox,QSpinBox { background:#172738; border:1px solid #344b64; border-radius:4px; padding:4px; }
QComboBox QAbstractItemView { background:#172738; selection-background-color:#28597c; }
QPlainTextEdit,QTableWidget { background:#111e2c; border:1px solid #2c4058; }
QHeaderView::section { background:#23354b; color:#c5d8eb; border:0; padding:5px; }
QTabWidget::pane { border:1px solid #2c4058; }
QTabBar::tab { background:#172738; padding:7px 15px; }
QTabBar::tab:selected { background:#2a4663; }
QProgressBar { border:1px solid #344b64; border-radius:4px; background:#172738; text-align:center; }
QProgressBar::chunk { background:#1d90af; }
QSplitter::handle { background:#263a50; }
QMenu { background:#172738; border:1px solid #344b64; padding:4px; }
QMenu::item:selected { background:#28597c; }
"""


class MainWindow(QMainWindow):
    def __init__(self, auto_connect=True, demo_scenario=None, root=None):
        super().__init__()
        configure_fonts(QApplication.instance())
        self.setWindowTitle(f"Timing Sentinel A7 · 串口联调版 {__version__}")
        self.data_root = data_root(root)
        self.capture_dir = self.data_root / "captures"
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.resize(1360, 760)
        self.setMinimumSize(1100, 680)
        self.setStyleSheet(STYLE)
        self.client = None
        self.device = SimulatedDevice()
        self.connected = False
        self.connecting = False
        self.replay = False
        self.task = None
        self.capture = None
        self.saved_path = None
        self.last_read_session = None
        self.close_pending = False
        self.auto_demo = demo_scenario
        self.status = {"state": "UNKNOWN", "ready": False, "session_id": 0, "counts": {}}
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.buttons()
        if auto_connect:
            QTimer.singleShot(30, self.connect_device)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 10)
        top = QHBoxLayout()
        title = QLabel("TIMING SENTINEL  /  A7")
        title.setFont(QFont("Segoe UI", 17, QFont.Weight.Bold))
        title.setStyleSheet("font-size:15pt;font-weight:600;color:#dce7f2")
        top.addWidget(title)
        top.addStretch()
        self.source = QLabel("模拟数据 · 未连接")
        self.source.setStyleSheet("color:#ffcc66;font-weight:bold;padding:6px")
        top.addWidget(self.source)
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self.connect_device)
        top.addWidget(self.connect_btn)
        self.quick_demo_btn = QPushButton("运行所选场景")
        self.quick_demo_btn.setObjectName("primary")
        self.quick_demo_btn.clicked.connect(self.demo)
        top.addWidget(self.quick_demo_btn)
        self.open_btn = QPushButton("打开记录")
        self.open_btn.clicked.connect(self.open_record)
        top.addWidget(self.open_btn)
        root.addLayout(top)
        connection = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("模拟设备", "mock")
        self.mode.addItem("真实串口 · 需协议握手", "serial")
        self.mode.currentIndexChanged.connect(self.connection_mode_changed)
        connection.addWidget(QLabel("数据源"))
        connection.addWidget(self.mode)
        self.port = QComboBox(); self.port.setEditable(True); self.port.setMinimumWidth(235)
        self.port.setToolTip("以设备管理器中的USB_UART端口为准；蓝牙COM口不是FPGA")
        connection.addWidget(self.port)
        self.link_baud = QComboBox()
        for value in (115200, 230400, 460800, 921600):
            self.link_baud.addItem(str(value), value)
        self.link_baud.setToolTip("PC↔FPGA控制串口，不是左侧被监听UART的波特率")
        connection.addWidget(QLabel("控制链路 baud"))
        connection.addWidget(self.link_baud)
        self.refresh_btn = QPushButton("刷新端口")
        self.refresh_btn.clicked.connect(self.refresh_ports)
        connection.addWidget(self.refresh_btn)
        connection.addStretch()
        self.diag_btn = QPushButton("导出诊断包")
        self.diag_btn.clicked.connect(self.diagnostics)
        connection.addWidget(self.diag_btn)
        root.addLayout(connection)
        self.refresh_ports()
        note = QLabel("模拟/回放：单规则与8路波形；真实模式按固件能力开放。打开COM不等于协议确认；时间按记录内标称时基换算。")
        note.setStyleSheet("color:#91a7bf;padding-bottom:4px")
        root.addWidget(note)
        body = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(body, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(280)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(3, 0, 9, 0)
        group = QGroupBox("采样与监听")
        form = QFormLayout(group)
        self.rate = QComboBox()
        for name, divisor in (("1 MS/s", 50), ("5 MS/s", 10), ("10 MS/s", 5), ("50 MS/s", 1)):
            self.rate.addItem(name, divisor)
        self.rate.setCurrentIndex(2)
        form.addRow("波形采样率", self.rate)
        self.uart_ch, self.gpio_ch = QComboBox(), QComboBox()
        for i in range(8):
            self.uart_ch.addItem(f"CH{i}", i); self.gpio_ch.addItem(f"CH{i}", i)
        self.gpio_ch.setCurrentIndex(1)
        form.addRow("UART监听通道", self.uart_ch)
        form.addRow("响应GPIO通道", self.gpio_ch)
        self.baud = QComboBox()
        for baud in (9600, 19200, 57600, 115200, 230400, 460800, 921600):
            self.baud.addItem(str(baud), baud)
        self.baud.setCurrentIndex(3)
        form.addRow("监听波特率 · 8N1", self.baud)
        self.pre = QSpinBox(); self.pre.setRange(0, 65534); self.pre.setValue(45874)
        form.addRow("前触发样点", self.pre)
        self.capacity = QLabel()
        self.capacity.setWordWrap(True)
        form.addRow(self.capacity)
        left_layout.addWidget(group)
        rule = QGroupBox("规则 0 · UART_BYTE_VALID → GPIO")
        form = QFormLayout(rule)
        self.value = QLineEdit("A5"); self.mask = QLineEdit("FF")
        form.addRow("字节value · 十六进制", self.value)
        form.addRow("字节mask · 十六进制", self.mask)
        self.edge = QComboBox(); self.edge.addItems(["上升沿", "下降沿"])
        form.addRow("目标事件", self.edge)
        self.minimum = QLineEdit("0"); self.maximum = QLineEdit("2")
        form.addRow("最小延时 · ms", self.minimum)
        form.addRow("最大延时 · ms", self.maximum)
        self.action = QComboBox()
        self.action.addItem("每事务触发 · 单次验证", int(Action.EACH))
        self.action.addItem("异常触发 · 等待异常", int(Action.ANOMALY))
        self.action.addItem("仅记录", int(Action.RECORD))
        form.addRow("动作", self.action)
        self.commit_btn = QPushButton("提交并回读配置")
        self.commit_btn.clicked.connect(self.commit)
        form.addRow(self.commit_btn)
        self.config_label = QLabel("本地草稿 · 默认配置")
        self.config_label.setWordWrap(True)
        form.addRow(self.config_label)
        left_layout.addWidget(rule)
        simulation = QGroupBox("模拟激励 · 可重复场景")
        form = QFormLayout(simulation)
        self.scenario = QComboBox()
        for label, key in (("正常 · 1.0ms", "normal"), ("提前 · 0.2ms", "early"),
                           ("迟到 · 2.2ms", "late"), ("观察窗内无响应", "missing")):
            self.scenario.addItem(label, key)
        self.scenario.currentIndexChanged.connect(self.preset)
        form.addRow("选择场景", self.scenario)
        self.seed = QSpinBox(); self.seed.setRange(0, 2_147_483_647); self.seed.setValue(1)
        form.addRow("随机seed · 辅助波形", self.seed)
        self.demo_btn = QPushButton("运行所选场景（完整流程）")
        self.demo_btn.setObjectName("primary")
        self.demo_btn.clicked.connect(self.demo)
        form.addRow(self.demo_btn)
        self.stim_btn = QPushButton("仅发送激励 · 需READY")
        self.stim_btn.clicked.connect(self.stimulate)
        form.addRow(self.stim_btn)
        left_layout.addWidget(simulation)
        left_layout.insertWidget(0, simulation)
        help_text = QLabel("A参考点：停止位中心校验通过。\n提前场景自动预设min=0.5ms。\nCH编号是逻辑编号，接线须另查原理图。\n四规则与FT232H尚未启用。")
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color:#8da5bd;padding:6px")
        left_layout.addWidget(help_text)
        left_layout.addStretch()
        scroll.setWidget(left)
        body.addWidget(scroll)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)
        self.summary = QLabel("等待连接")
        self.summary.setStyleSheet("background:#1c3045;border-radius:6px;padding:10px;font-size:14px")
        right_layout.addWidget(self.summary)
        vertical = QSplitter(Qt.Orientation.Vertical)
        self.wave = WaveformView()
        vertical.addWidget(self.wave)
        tabs = QTabWidget()
        self.results = QPlainTextEdit(); self.results.setReadOnly(True)
        self.results.setPlainText("点击左侧“运行所选场景”，即可查看完整模拟流程。\n\n异常触发模式中，正常PASS继续监测，不会冻结。")
        tabs.addTab(self.results, "规则结果")
        self.events = QTableWidget(0, 6)
        self.events.setHorizontalHeaderLabels(["序号", "tick", "UART", "GPIO边沿", "规则结果", "状态"])
        self.events.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.events.verticalHeader().setVisible(False)
        self.events.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tabs.addTab(self.events, "事件列表")
        self.device_info = QPlainTextEdit(); self.device_info.setReadOnly(True)
        tabs.addTab(self.device_info, "设备 / 捕获配置")
        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumBlockCount(500)
        tabs.addTab(self.logs, "通信日志")
        vertical.addWidget(tabs)
        vertical.setSizes([420, 210])
        right_layout.addWidget(vertical, 1)
        body.addWidget(right)
        body.setSizes([340, 1000])
        controls = QHBoxLayout()
        self.arm_btn = QPushButton("ARM · 准备监测"); self.arm_btn.clicked.connect(self.arm)
        self.stop_btn = QPushButton("停止并保留"); self.stop_btn.clicked.connect(self.disarm)
        self.read_btn = QPushButton("读取冻结数据"); self.read_btn.clicked.connect(self.read)
        self.cancel_btn = QPushButton("停止当前任务"); self.cancel_btn.clicked.connect(self.cancel_task)
        self.save_btn = QPushButton("保存记录"); self.save_btn.clicked.connect(self.save)
        self.export_btn = QPushButton("导出 ▾")
        menu = QMenu(self)
        self.json_btn = menu.addAction("JSON 元数据 / 事件", lambda: self.export("json"))
        self.csv_btn = menu.addAction("CSV 事件列表", lambda: self.export("csv"))
        self.vcd_btn = menu.addAction("VCD 数字波形", lambda: self.export("vcd"))
        self.png_btn = menu.addAction("PNG 当前界面", self.export_png)
        self.export_btn.setMenu(menu)
        self.release_btn = QPushButton("释放板端记录"); self.release_btn.clicked.connect(self.release)
        for button in (self.arm_btn, self.stop_btn, self.read_btn, self.cancel_btn, self.save_btn,
                       self.export_btn, self.release_btn):
            controls.addWidget(button)
        root.addLayout(controls)
        bottom = QHBoxLayout()
        self.feedback = QLabel("准备就绪")
        bottom.addWidget(self.feedback, 1)
        self.progress = QProgressBar(); self.progress.setMaximumWidth(280); self.progress.setValue(0)
        bottom.addWidget(self.progress)
        root.addLayout(bottom)
        for control in (self.rate, self.uart_ch, self.gpio_ch, self.baud, self.edge, self.action):
            control.currentIndexChanged.connect(self.draft_changed)
        for control in (self.value, self.mask, self.minimum, self.maximum):
            control.textChanged.connect(self.draft_changed)
        self.pre.valueChanged.connect(self.draft_changed)
        self.update_capacity()

    def log(self, message):
        self.logs.appendPlainText(time.strftime("%H:%M:%S ") + message)
        logging.getLogger("timing_sentinel").info(message)

    def refresh_ports(self):
        previous = self.port.currentData() or self.port.currentText().split(" · ")[0]
        self.port.clear()
        for entry in enumerate_ports():
            self.port.addItem(f"{entry['port']} · {entry['description']}", entry["port"])
        index = self.port.findData(previous)
        if index >= 0:
            self.port.setCurrentIndex(index)
        elif previous:
            self.port.setEditText(previous)
        if not self.port.count():
            self.port.setPlaceholderText("未识别端口，可输入COM编号")

    def connection_mode_changed(self):
        if not self.connected and not self.replay:
            self.source.setText("串口待确认 · 未连接" if self.mode.currentData() == "serial" else "模拟数据 · 未连接")
        if hasattr(self, "arm_btn"):
            self.buttons()

    def draft_changed(self):
        self.config_label.setText("本地草稿已修改 · 尚未提交")
        self.update_capacity()

    def update_capacity(self):
        divisor = self.rate.currentData()
        pre = self.pre.value()
        self.capacity.setText(f"后触发 {65535-pre} 点 / 共65536点\n前窗 {pre*divisor/50000:.4f} ms · 后窗 {(65535-pre)*divisor/50000:.4f} ms")

    def preset(self):
        self.minimum.setText("0.5" if self.scenario.currentData() == "early" else "0")

    def config(self):
        return Config(uart_channel=self.uart_ch.currentData(), target_channel=self.gpio_ch.currentData(),
                      edge=self.edge.currentIndex(), baud=self.baud.currentData(), divisor=self.rate.currentData(),
                      pre=self.pre.value(), post=65535-self.pre.value(), value=int(self.value.text(), 16),
                      mask=int(self.mask.text(), 16), action=self.action.currentData(),
                      min_ticks=ms_ticks(self.minimum.text()), max_ticks=ms_ticks(self.maximum.text())).validate()

    def checked_config(self):
        try:
            config = self.config()
            warning = config.coverage_warning()
            if warning:
                answer = QMessageBox.question(self, "证据覆盖提示", warning + "\n是否作为短窗实验继续？")
                if answer != QMessageBox.StandardButton.Yes:
                    return None
            return config
        except (ValueError, ArithmeticError) as exc:
            self.error(str(exc))
            return None

    def run_task(self, name, operation, callback=None):
        if self.task:
            return
        job = Task(operation, self)
        self.task = job
        if self.client:
            self.client.cancel = job.cancelled.is_set
            self.client.log = job.message.emit
        job.message.connect(self.log)
        job.progress.connect(lambda done, total: self.progress.setValue(int(100 * done / max(1, total))))
        job.succeeded.connect(callback or (lambda _: None))
        job.failed.connect(self.error)
        job.finished.connect(lambda: self.task_finished(job))
        if name:
            self.feedback.setText(name)
        self.buttons()
        job.start()

    def task_finished(self, job):
        if self.task is job:
            self.task = None
        self.connecting = False
        if self.client:
            self.client.cancel = lambda: False
            self.client.log = lambda _: None
        job.deleteLater()
        self.buttons()
        if self.close_pending:
            self.close_pending = False
            QTimer.singleShot(0, self.close)
        elif self.connected and self.auto_demo:
            scenario = self.auto_demo
            self.auto_demo = None
            self.scenario.setCurrentIndex(SCENARIOS.index(scenario))
            QTimer.singleShot(0, self.demo)

    def buttons(self):
        busy = self.task is not None
        available = (self.connected and not self.replay and not busy and self.client
                     and not self.client.uncertain and self.client.can_capture)
        state = self.status["state"]
        self.connect_btn.setEnabled(not busy)
        self.connect_btn.setText("断开" if self.connected else "连接并同步")
        for control in (self.mode, self.port, self.link_baud, self.refresh_btn):
            control.setEnabled(not busy and not self.connected)
        if not self.connected and not busy:
            for control in (self.port, self.link_baud, self.refresh_btn):
                control.setEnabled(self.mode.currentData() == "serial")
        self.diag_btn.setEnabled(not busy)
        self.open_btn.setEnabled(not busy)
        draft_supported = not (self.connected and self.client and not self.client.can_capture)
        for control in (self.rate,self.uart_ch,self.gpio_ch,self.baud,self.pre,
                        self.value,self.mask,self.edge,self.minimum,self.maximum,self.action):
            control.setEnabled(bool(draft_supported))
        self.commit_btn.setEnabled(bool(available and state == "IDLE"))
        self.arm_btn.setEnabled(bool(available and state == "IDLE"))
        self.stop_btn.setEnabled(bool(available and state in ("PREFILL", "ARMED", "POST_TRIGGER")))
        simulated = self.mode.currentData() == "mock"
        self.scenario.setEnabled(simulated and not busy)
        self.seed.setEnabled(simulated and not busy)
        self.stim_btn.setEnabled(bool(available and simulated and state == "ARMED"))
        self.read_btn.setEnabled(bool(available and state == "FROZEN"))
        self.release_btn.setEnabled(bool(available and state == "FROZEN"))
        self.demo_btn.setEnabled(bool(available and simulated and state in ("IDLE", "FROZEN")))
        self.quick_demo_btn.setEnabled(self.demo_btn.isEnabled())
        self.cancel_btn.setEnabled(busy)
        for button in (self.save_btn, self.export_btn, self.json_btn, self.csv_btn, self.vcd_btn, self.png_btn):
            button.setEnabled(self.capture is not None and not busy)

    def error(self, message):
        self.feedback.setText("流程已停止" if message.startswith("Cancelled") else message)
        self.log(message)
        if self.connecting:
            self.source.setText("设备未确认 · 连接失败（没有自动切换模拟数据）")
            self.summary.setText("设备连接未确认 · 本地记录不受影响")
        if self.client and self.client.uncertain:
            self.status["state"] = "UNKNOWN"
            self.summary.setText("命令结果待确认 · 请断开并重新同步")
            self.source.setText("链路结果未知 · 断开后重连；不会自动重放ARM")
        self.buttons()

    def protect_record(self):
        if self.capture is None or self.saved_path:
            return True
        answer = QMessageBox.question(self, "尚未保存", "当前记录尚未保存。是否先保存？",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self.save()
            return False  # async save; user can retry after it completes
        return True

    def confirm_leave_acquisition(self, action):
        """Disconnecting the host does not stop the device or save its buffer."""
        if self.connected and (self.client.uncertain or self.status["state"] == "UNKNOWN"):
            return QMessageBox.question(
                self, "设备状态未确认", f"设备状态未知，可能仍在采集。{action}不会自动停止设备。\n"
                "重新连接后可查询状态；尚未读取的证据仍需设备保持供电。\n是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        if not self.connected or self.status["state"] not in ("PREFILL", "ARMED", "POST_TRIGGER"):
            return True
        answer = QMessageBox.question(
            self, "采集仍在运行", f"设备仍在采集，{action}不会自动停止或保存设备数据。\n\n"
            "是：先停止并保留板端证据，留在当前页面读取/保存。\n"
            f"否：仍然{action}，设备可能继续采集。\n取消：留在当前页面。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self.disarm()
            return False
        return answer == QMessageBox.StandardButton.No

    def clear_capture(self):
        self.capture = None
        self.saved_path = None
        self.last_read_session = None
        self.wave.clear()
        self.events.setRowCount(0)
        self.results.setPlainText("本次采集尚未读取；此前记录已从当前视图移除。")
        self.device_info.setPlainText(json.dumps({"device": self.client.info if self.client else None,
                                                 "config_id": self.client.config_id if self.client else None,
                                                 "capture": None}, ensure_ascii=False, indent=2))
        self.progress.setValue(0)

    def connect_device(self):
        if self.task:
            return
        if self.connected:
            self.disconnect_device()
            return
        if not self.protect_record():
            return
        mode = self.mode.currentData()
        port = self.port.currentData() if self.port.currentIndex() >= 0 and self.port.currentText() == self.port.itemText(self.port.currentIndex()) else self.port.currentText().strip()
        baud = self.link_baud.currentData()
        self.client = None
        def operation(job):
            transport = MockTransport(self.device) if mode == "mock" else SerialTransport(port or "", baud)
            candidate = DeviceClient(transport)
            candidate.log = job.message.emit
            candidate.cancel = job.cancelled.is_set
            try:
                return candidate, candidate.connect()
            except Exception:
                transport.close()
                raise
        def success(value):
            self.client, value = value
            self.connected = True
            self.replay = False
            self.clear_capture()
            self.results.setPlainText("已同步设备；ARM后等待READY，再施加外部激励。")
            self.source.setText("模拟数据 · MOCK / 1规则" if self.client.origin == "mock" else f"设备协议已确认 · {port}")
            self.device_info.setPlainText(json.dumps(value["info"], ensure_ascii=False, indent=2))
            self.mark_committed(Config(**value["config"]))
            self.update_status(value["status"])
            for i in range(self.rate.count()):
                divisor = self.rate.itemData(i)
                self.rate.model().item(i).setEnabled(bool(value["info"]["divisor_mask"] & (1 << (1, 5, 10, 50).index(divisor))))
            self.feedback.setText("模拟设备已确认，可运行场景" if mode == "mock" else "串口协议握手完成；需外部激励，不自动清除冻结证据")
            if not self.client.can_capture:
                label = "模拟通信联调设备" if self.client.origin == "mock" else "真实板卡通信已确认"
                self.source.setText(f"{label} · {port} / 通信联调固件，无采集能力")
                self.results.setPlainText("SYNC/PING/INFO/CONFIG/STATUS握手通过。\n当前固件仅用于通信联调，采集、规则及上传尚未实现；不会生成模拟波形冒充实测。")
                self.config_label.setText("只读规划默认值 · config_id=0；不是已提交的采集配置")
                self.summary.setText("通信联调已连接 · 尚无采集引擎 · ARM/配置提交/读取禁用")
                self.feedback.setText("板卡握手成功；下一阶段实现BRAM采集，当前无需连接STM32")
        self.source.setText("正在确认设备 · SYNC / PING / INFO / CONFIG / STATUS")
        self.connecting = True
        self.run_task("连接并校验协议（不会自动ARM）", operation, success)

    def disconnect_device(self, confirmed=False):
        if not confirmed and (self.task or not self.confirm_leave_acquisition("断开连接")):
            return False
        if self.client:
            self.client.transport.close()
        self.connected = False
        self.status["state"] = "UNKNOWN"
        self.source.setText("链路已断开 · 设备状态未知；本地记录保留")
        self.summary.setText("连接已断开；设备状态未推断为IDLE")
        self.buttons()
        return True

    def update_status(self, status):
        self.status = status
        counts = status.get("counts", {})
        self.summary.setText(f"{STATES[status['state']]}  |  session {status['session_id']}  |  "
                             f"PASS {counts.get('pass',0)} / EARLY {counts.get('early',0)} / TIMEOUT {counts.get('timeout',0)}")
        self.buttons()

    def poll(self):
        if not self.connected or self.replay or self.task or self.client.uncertain or QApplication.activeModalWidget():
            return
        if self.status["state"] not in ("PREFILL", "ARMED", "POST_TRIGGER"):
            return
        self.run_task("", lambda _: self.client.status(), self.update_status)

    def mark_committed(self, config):
        try:
            same = self.config() == config
        except (ValueError, ArithmeticError):
            same = False
        suffix = "与草稿一致" if same else "当前草稿与设备配置不同，尚未提交"
        self.config_label.setText(f"设备config_id={self.client.config_id} · {suffix}")

    def commit(self):
        config = self.checked_config()
        if config:
            def success(value):
                self.mark_committed(Config(**value["config"]))
                self.feedback.setText("配置提交及回读成功")
            self.run_task("提交并回读配置", lambda _: self.client.configure(config), success)

    def arm(self):
        if self.task or not self.protect_record():
            return
        config = self.checked_config()
        if config is None:
            return
        def operation(_):
            self.client.configure(config)
            return self.client.arm()
        def success(value):
            self.clear_capture()
            self.mark_committed(config)
            self.update_status(value)
        self.run_task("提交当前配置并ARM（ACK后仍需等待READY）", operation, success)

    def disarm(self):
        self.run_task("停止并保留", lambda _: self.client.disarm(), self.update_status)

    def release(self):
        if self.task:
            return
        if self.capture and not self.protect_record():
            return
        if not self.capture or self.capture.metadata["session_id"] != self.status["session_id"] or not self.capture.complete:
            answer = QMessageBox.question(self, "丢弃板端数据", "当前session的完整冻结证据尚未读取，释放会丢失尚未读取的数据。确定释放？",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.run_task("释放冻结证据", lambda _: self.client.release(), self.update_status)

    def stimulate(self):
        if self.mode.currentData() != "mock":
            return
        scenario, seed = self.scenario.currentData(), self.seed.value()
        def operation(_):
            result = self.client.transport.stimulate(scenario, seed)
            return result, self.client.status()
        def success(value):
            result, status = value
            self.update_status(status)
            self.log("模拟激励结果: " + result)
            self.feedback.setText("正常事务不冻结，正在等待异常" if status["state"] == "ARMED" else "等待后窗冻结后点击读取")
        self.run_task("发送模拟激励", operation, success)

    def demo(self):
        if self.task or self.mode.currentData() != "mock":
            return
        if not self.protect_record():
            return
        config = self.checked_config()
        if not config:
            return
        scenario, seed = self.scenario.currentData(), self.seed.value()
        self.progress.setValue(0)
        def check_wait(job, deadline, message):
            # ARM may already have succeeded even though this workflow has not
            # returned its final status to the UI. Never leave the old IDLE view.
            if job.cancelled.is_set():
                self.client.uncertain = True
                raise Cancelled()
            if time.monotonic() > deadline:
                self.client.uncertain = True
                raise TimeoutError(message)
        def operation(job):
            if self.client.status()["state"] == "FROZEN":
                self.client.release()
            committed = self.client.configure(config)
            self.client.arm()
            deadline = time.monotonic() + 3
            while not self.client.status()["ready"]:
                check_wait(job, deadline, "等待READY超时")
                time.sleep(0.005)
            job.message.emit("READY：开始模拟激励")
            result = self.client.transport.stimulate(scenario, seed)
            status = self.client.status()
            if status["state"] == "ARMED":
                return {"capture": None, "status": status, "committed": committed, "result": result}
            while status["state"] != "FROZEN":
                check_wait(job, deadline, "等待冻结超时")
                time.sleep(.01)
                status = self.client.status()
            capture = read_capture(self.client, job.progress.emit)
            return {"capture": capture, "status": status, "committed": committed, "result": result}
        def success(value):
            self.update_status(value["status"])
            self.mark_committed(Config(**value["committed"]["config"]))
            if value["capture"]:
                self.present(value["capture"])
            else:
                self.clear_capture()
                self.results.setPlainText(f"{value['result']} · 本次事务未触发冻结，设备仍在监测。")
                self.feedback.setText(f"{value['result']} · 当前动作不触发，仍在监测；可停止并保留")
        self.run_task("配置 → ARM → READY → 激励 → 读取", operation, success)

    def read(self):
        if self.task or not self.protect_record():
            return
        self.progress.setValue(0)
        self.run_task("读取冻结数据", lambda job: read_capture(self.client, job.progress.emit), self.present)

    def cancel_task(self):
        if self.task:
            self.task.cancelled.set()
            self.feedback.setText("正在有界结束当前请求；不会释放冻结证据")

    def present(self, capture):
        self.capture, self.saved_path = capture, None
        self.last_read_session = capture.metadata["session_id"]
        self.wave.set_capture(capture)
        m = capture.metadata
        self.render_capture_results()
        events = unpack_events(capture.events)
        self.events.setRowCount(len(events))
        for row, event in enumerate(events):
            uart = "—" if event["uart"] is None else f"0x{event['uart']:02X}"
            if event["uart_error"]:
                uart += " · 帧错误"
            values = [str(row), str(event["tick"]), uart,
                      f"↑{event['rise']:02X} ↓{event['fall']:02X}", event["result"], event["state"]]
            for col, value in enumerate(values):
                self.events.setItem(row, col, QTableWidgetItem(value))
        self.device_info.setPlainText(json.dumps(m, ensure_ascii=False, indent=2))
        complete = "传输完整" if capture.complete else "部分记录"
        self.feedback.setText(f"{m['result']} · {complete} · 尚未保存，设备冻结数据保留")
        if m.get("transfer_error"):
            self.feedback.setText("已保留部分记录，可保存；" + m["transfer_error"])
            if self.client and self.client.uncertain:
                self.status["state"] = "UNKNOWN"
                self.source.setText("链路结果未知 · 部分记录保留；重连后从0重读")
                self.summary.setText("读取中断 · 不推断设备IDLE · 请重连同步")
        self.log(f"捕获 {m['record_id']}：{m['result']}，{len(capture.samples)}点")
        self.buttons()

    def render_capture_results(self):
        if self.capture is None:
            return
        capture, m = self.capture, self.capture.metadata
        delay = "—" if m["a_tick"] is None or m["b_tick"] is None else f"{delta_tick(m['b_tick'],m['a_tick'])/50000:.6f} ms"
        complete = "传输完整" if capture.complete else "部分记录（缺失尾部，未收到的数据不绘制）"
        origin = "模拟数据" if m["origin"] == "mock" else "设备数据"
        observation = m["observation"]
        if m.get("observed_b_tick") is not None and not capture.covers_tick(m["observed_b_tick"]):
            observation += "\n该边沿来自设备元数据；本地已收到波形未覆盖该时间。"
        self.results.setPlainText(f"规则0结果：{m['result']}\nA→B 延时：{delay}\n\n{observation}\n\n"
                                  f"来源：{origin} / {m['firmware']}\nsession={m['session_id']}  config_id={m['config_id']}\n"
                                  f"{complete} · 收到 {len(capture.samples)}/{m['valid_count']} 点\n"
                                  f"事件 {len(capture.events)//16}/{m['event_count']} 条 · 覆盖 {m['event_overwrites']} 条\n"
                                  f"trigger_valid={m['trigger_valid']} · 手动停止={m['manual_stop']}\n"
                                  f"逻辑判定tick={m['decision_timestamp']}\n缓存锚点tick={m['capture_anchor_timestamp']}\n"
                                  f"A在已收到波形内：{capture.covers_tick(m['a_tick'])}\n\n" +
                                  (f"原始记录已保存：{self.saved_path}" if self.saved_path else "原始记录尚未保存。"))

    def save(self):
        if self.task or self.capture is None:
            return
        default = self.capture_dir / (f"{self.capture.metadata['origin']}_{self.capture.metadata['record_id'][:8]}.tscap")
        path, _ = QFileDialog.getSaveFileName(self, "保存原始记录", str(default), "Timing Sentinel (*.tscap)")
        if path:
            capture = self.capture
            def success(value):
                self.saved_path = value
                self.render_capture_results()
                self.feedback.setText("记录已保存并回读校验：" + value)
                self.log("保存完成: " + value)
            self.run_task("保存及哈希校验", lambda _: save_record(capture, path), success)

    def open_record(self):
        if self.task or not self.protect_record():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开原始记录", str(self.capture_dir), "Timing Sentinel (*.tscap)")
        if not path or not self.confirm_leave_acquisition("切换离线回放并断开连接"):
            return
        def success(capture):
            if self.connected:
                self.disconnect_device(confirmed=True)
            self.replay = True
            self.present(capture)
            self.saved_path = path
            self.render_capture_results()
            self.source.setText("离线回放 · 原始来源：" + ("模拟数据" if capture.metadata["origin"] == "mock" else "设备数据"))
            self.summary.setText(f"离线回放  |  {capture.metadata['result']}  |  session {capture.metadata['session_id']}")
            self.feedback.setText("回放校验通过，设备控制已禁用")
        self.run_task("加载及校验记录", lambda _: load_record(path), success)

    def export(self, kind):
        if not self.capture:
            return
        path, _ = QFileDialog.getSaveFileName(self, f"导出{kind.upper()}", str(self.capture_dir / f"{self.capture.metadata['origin']}_export.{kind}"), f"{kind.upper()} (*.{kind})")
        if path:
            capture = self.capture
            fn = {"json": export_json, "csv": export_csv, "vcd": export_vcd}[kind]
            self.run_task("导出" + kind.upper(), lambda _: fn(capture, path), lambda value: self.feedback.setText("已导出：" + value))

    def export_png(self):
        if self.capture is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存当前界面截图（含来源标签）", str(self.capture_dir / "capture_view.png"), "PNG (*.png)")
        if path:
            try:
                with atomic_output(path) as temporary:
                    if not self.grab().save(str(temporary), "PNG"):
                        raise OSError("PNG编码失败")
            except (OSError, ValueError) as exc:
                self.error(f"PNG保存失败，原文件未替换：{exc}")
            else:
                self.feedback.setText("PNG已保存；截图不能替代原始.tscap记录")

    def diagnostics(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出诊断包（不包含原始采样，发送前可审阅）", str(self.data_root / "host_diagnostics.zip"), "ZIP (*.zip)")
        if path:
            snapshot = {"connected": self.connected, "replay": self.replay, "status": dict(self.status),
                        "info": dict(self.client.info or {}) if self.client else None,
                        "uncertain": self.client.uncertain if self.client else None,
                        "origin": self.capture.metadata["origin"] if self.capture else None}
            self.run_task("导出诊断包", lambda _: export_diagnostics(path, self.data_root, snapshot),
                          lambda value: self.feedback.setText("诊断包已导出（包含端口信息和本地日志）：" + value))

    def closeEvent(self, event):
        if self.task:
            self.task.cancelled.set()
            self.close_pending = True
            self.feedback.setText("等待当前有界任务结束，再处理未保存记录")
            event.ignore()
            return
        if not self.protect_record():
            event.ignore()
            return
        if not self.confirm_leave_acquisition("退出上位机"):
            event.ignore()
            return
        self.timer.stop()
        if self.client:
            self.client.transport.close()
        event.accept()

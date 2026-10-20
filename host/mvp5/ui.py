"""Real-only four-rule UI. One worker owns serial; no automatic ARM or discard."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import json
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtGui import QColor,QBrush,QDesktopServices
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
 QSpinBox,QLineEdit,QCheckBox,QTableWidget,QTableWidgetItem,QHeaderView,QTabWidget,QPlainTextEdit,QFileDialog,QMessageBox,QProgressBar,QMenu,QSplitter,QAbstractItemView,QScrollArea)
from app.models import ms_ticks,delta_tick
from app.ui.main_window import STYLE
from app.ui.fonts import configure_fonts
from app.workers import Task
from app.diagnostics import record_event
from app.services.capture_storage import preference_path, probe_directory, write_preference
from app.services.device_client import DeviceFailure,Cancelled
from transport.serial_port import SerialTransport,enumerate_ports
from mvp5.models import Config,Rule,unpack_events
from mvp5.client import Client
from mvp5.store import save_record,load_record,export_json
from mvp5.exports import export_csv,export_vcd
from mvp5.compact_waveform import CompactWaveform as FourWaveform
from mvp5.experiment import run_four,new_experiment_dir
from mvp5 import __version__
from mvp5.examples import spi_example
from mvp5.presentation import identify_scene,relative_ms,event_description
from mvp5.analysis import analyze_records,summary_text,export_analysis
from mvp5.workload import workload_label
from mvp5.workload_experiment import workload_config,run_workload

def combo(items,selected=0):
 w=QComboBox();w.addItems(items);w.setCurrentIndex(selected);return w

class MainWindow(QMainWindow):
 def __init__(self,root=None,port=None,settings_path=None):
  super().__init__();configure_fonts(QApplication.instance());self.setStyleSheet(STYLE)
  self.setWindowTitle("Timing Sentinel A7 · GPIO/SPI跨协议版 "+__version__);self.resize(1580,980)
  self.root=Path(root or Path(__file__).resolve().parents[2]/"data"/"mvp5")
  self.storage_settings=Path(settings_path) if settings_path is not None else preference_path()
  self.storage_ready=None;self.storage_error=''
  self.client=None;self.transport=None;self.task=None;self.busy=False;self.background_observe=False;self._pending_user_action=None;self.device_state="DISCONNECTED"
  self.capture=None;self.capture_path=None;self.saved=False;self.applied=None;self.replay=False;self.scene_info=None
  self.analysis_report=None
  self.live_workload_seen=False
  self.legacy_debug_enabled=False
  self.last_workload_directory=None
  base=QWidget();self.setCentralWidget(base);layout=QVBoxLayout(base)
  top=QHBoxLayout();top.addWidget(QLabel("TIMING SENTINEL / A7 · 4 PARALLEL RULES"));top.addStretch()
  self.port=combo([]);top.addWidget(self.port);self.refresh_button=QPushButton("刷新串口");top.addWidget(self.refresh_button)
  self.connect_button=QPushButton("连接 / 确认SPI固件");top.addWidget(self.connect_button)
  self.observe_button=QPushButton("更新板端状态");top.addWidget(self.observe_button)
  self.observe_auto=QCheckBox("监听板载按键");self.observe_auto.setChecked(True);top.addWidget(self.observe_auto)
  self.legacy_mode=QCheckBox("旧六场景固件调试（高级）")
  self.legacy_mode.setToolTip("默认关闭。仅在断开设备、没有未保存实物记录且本窗口未识别过真实任务时可启用；不会刷写或识别STM32固件。")
  self.legacy_mode.toggled.connect(self.change_legacy_mode);top.addWidget(self.legacy_mode)
  self.open_button=QPushButton("打开GPIO/SPI记录");top.addWidget(self.open_button);layout.addLayout(top)
  self.analysis_button=QPushButton("批量统计 / 报告");top.addWidget(self.analysis_button)
  self.summary=QLabel("未连接 · 需要profile5固件，打开串口不等于设备握手");layout.addWidget(self.summary)
  self.note=QLabel("3.3V被动监听 · UART→GPIO/SPI · 硬件判定与场景标记分开显示 · 50 MHz标称时基")
  self.note.setToolTip("STM32 A9→FPGA R17；B0→N15；A8←P20；A5→R16；A7→P15；A4→R14；共地、独立供电。SPI mode0/250kHz，B=第8采样沿，不是CS结束。")
  self.note.setWordWrap(True);layout.addWidget(self.note)
  self.config_toggle=QPushButton("展开规则与采样配置 ▾");self.config_toggle.setCheckable(True)
  layout.addWidget(self.config_toggle)
  workload_bar=QHBoxLayout();workload_bar.addWidget(QLabel("真实负载实验"))
  self.workload_deadline=combo(["3.5 ms · 观察超期","8 ms · 宽窗对照","3.5 / 8 ms · 仅波形"])
  self.workload_deadline.setItemData(2,"同一次任务四规则对照。正式USR43不对该配置启用判定灯或蜂鸣器；声光演示请选择原3.5ms或8ms模式。",Qt.ItemDataRole.ToolTipRole)
  self.workload_preset=QPushButton("载入任务预设")
  self.workload_run=QPushButton("六任务实验＋自动保存")
  self.workload_report=QPushButton("打开实验报告")
  self.workload_run.setToolTip("适用于STM32 FIR/IRQ任务固件。先保护现有冻结记录，再采集三档各两次；全部保存后才释放，末轮保留冻结。不会烧录两板。")
  self.workload_hint=QLabel("需要STM32任务固件；3.5ms为演示期限")
  for widget in (self.workload_deadline,self.workload_preset,self.workload_run,self.workload_report,self.workload_hint):workload_bar.addWidget(widget)
  workload_bar.addStretch();layout.addLayout(workload_bar)
  self.workload_preset.clicked.connect(lambda:self.set_draft(workload_config(self.selected_workload_deadline())))
  self.workload_run.clicked.connect(self.workload_batch)
  self.workload_report.clicked.connect(self.open_workload_report)
  self.config_panel=QScrollArea();self.config_panel.setWidgetResizable(True);self.config_panel.setMaximumHeight(145)
  config_content=QWidget();self.config_panel.setWidget(config_content)
  config_layout=QVBoxLayout(config_content);config_layout.setContentsMargins(0,0,0,0)
  layout.addWidget(self.config_panel);self.config_panel.setVisible(False)
  self.config_toggle.toggled.connect(self.toggle_config_panel)
  settings=QHBoxLayout()
  self.uart=combo(["CH"+str(i) for i in range(8)]);self.baud=QSpinBox();self.baud.setRange(1200,1000000);self.baud.setValue(115200)
  self.rate=combo(["50 MS/s","10 MS/s","5 MS/s","1 MS/s"],1)
  self.pre=QSpinBox();self.pre.setRange(0,65534);self.pre.setValue(32767)
  for title,widget in (("UART监听",self.uart),("监听baud",self.baud),("采样率",self.rate),("前触发点",self.pre)):settings.addWidget(QLabel(title));settings.addWidget(widget)
  self.window_text=QLabel();settings.addWidget(self.window_text);settings.addStretch();config_layout.addLayout(settings)
  spi_settings=QHBoxLayout();self.spi_enable=QCheckBox("启用SPI监听")
  self.spi_mode=combo(["mode0","mode1","mode2","mode3"])
  self.spi_clk=combo(["CH"+str(i) for i in range(8)],2);self.spi_mosi=combo(["CH"+str(i) for i in range(8)],3);self.spi_cs=combo(["CH"+str(i) for i in range(8)],4)
  for title,widget in (("",self.spi_enable),("SPI mode",self.spi_mode),("SCK",self.spi_clk),("MOSI",self.spi_mosi),("CS低有效",self.spi_cs)):
   spi_settings.addWidget(QLabel(title));spi_settings.addWidget(widget)
  spi_settings.addWidget(QLabel("被动监听 · 无MISO · 无总线驱动"));spi_settings.addStretch();config_layout.addLayout(spi_settings)
  self.config_table=QTableWidget(4,12);self.config_table.setHorizontalHeaderLabels(["启用","GPIO","边沿","A UART值","A mask","min ms","max ms","动作","B事件类型","B SPI值","B SPI mask","用途"])
  self.config_table.verticalHeader().setDefaultSectionSize(37);self.config_table.setFixedHeight(184)
  self.config_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.editors=[]
  for i,rule in enumerate(Config().rules):
   enable=QCheckBox("R"+str(i));enable.setChecked(True)
   widgets=[enable,combo(["CH"+str(j) for j in range(8)],rule.target_channel),combo(["上升沿","下降沿"],rule.edge),
    QLineEdit(f"{rule.value:02X}"),QLineEdit(f"{rule.mask:02X}"),QLineEdit(str(rule.min_ticks/50000)),
    QLineEdit(str(rule.max_ticks/50000)),combo(["仅记录","异常触发","每事务触发"],rule.action),
    combo(["GPIO边沿","SPI字节"],rule.target_kind),QLineEdit(f"{rule.spi_value:02X}"),QLineEdit(f"{rule.spi_mask:02X}")]
   self.editors.append(widgets)
   for col,w in enumerate(widgets):self.config_table.setCellWidget(i,col,w)
   self.config_table.setItem(i,11,QTableWidgetItem(["GPIO响应","严格GPIO窗","SPI C3到达","SPI 3C识别"][i]))
  config_layout.addWidget(self.config_table)
  actions=QHBoxLayout();self.buttons={}
  for key,title in (("defaults","恢复GPIO/SPI示例"),("configure","提交＋回读规则"),("arm","ARM · 采集一次"),("stop","DISARM · 保留"),
   ("read","读取冻结波形"),("save","保存记录"),("export","导出 ▾"),("release","释放已保存记录"),("batch","自动六轮SPI验收＋保存")):
   w=QPushButton(title);self.buttons[key]=w;actions.addWidget(w)
  layout.addLayout(actions)
  self.export_menu=QMenu(self.buttons["export"])
  for kind,title in (("json","JSON元数据"),("csv","CSV逐点波形"),("vcd","VCD数字波形")):
   action=self.export_menu.addAction(title)
   action.triggered.connect(lambda checked=False,fmt=kind:self.export(fmt))
  self.buttons["export"].setMenu(self.export_menu)
  self.draft=QLabel();layout.addWidget(self.draft)
  self.result_table=QTableWidget(4,8);self.result_table.setHorizontalHeaderLabels(["规则","实际结果","A→B ms","观察到B ms","主触发","accepted","PASS/EARLY/TIMEOUT","取消/截断/overlap"])
  self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.result_table.verticalHeader().setDefaultSectionSize(27);self.result_table.setFixedHeight(149);layout.addWidget(self.result_table)
  self.scene_banner=QLabel("场景：等待读取或打开记录");self.scene_banner.setWordWrap(True)
  self.scene_banner.setStyleSheet("QLabel {font-size:16px;padding:9px;background:#1b3043;border-radius:5px;}")
  layout.addWidget(self.scene_banner)
  self.wave=FourWaveform()
  self.wave.referenceChanged.connect(self.refresh_event_times)
  tabs=QTabWidget();self.detail=QPlainTextEdit();self.detail.setReadOnly(True)
  self.event_table=QTableWidget(0,10);self.event_table.setHorizontalHeaderLabels(["相对时间 ms","原始tick","UART","SPI/error","rise/fall","R0","R1","R2","R3","事件说明"])
  self.event_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
  self.event_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
  self.event_table.setAlternatingRowColors(True);self.event_table.verticalHeader().setDefaultSectionSize(30)
  self.event_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
  for col,width in enumerate((115,145,65,90,85,90,90,90,90,360)):self.event_table.setColumnWidth(col,width)
  self.event_table.horizontalHeader().setStretchLastSection(True)
  self.event_table.cellClicked.connect(self.focus_event)
  self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(1000)
  event_panel=QWidget();event_layout=QVBoxLayout(event_panel);event_layout.setContentsMargins(0,0,0,0)
  filter_bar=QHBoxLayout();self.key_events=QCheckBox("仅关键事件（不修改原始记录）");self.key_events.setChecked(True)
  self.event_count=QLabel();filter_bar.addWidget(self.key_events);filter_bar.addWidget(self.event_count);filter_bar.addStretch()
  event_layout.addLayout(filter_bar);event_layout.addWidget(self.event_table)
  self.key_events.toggled.connect(self.populate_events)
  self.shown_events=[]
  tabs.addTab(self.detail,"元数据 / 验收报告");tabs.addTab(event_panel,"并行事件（点行定位波形）");tabs.addTab(self.log,"通信日志")
  analysis_panel=QWidget();analysis_layout=QVBoxLayout(analysis_panel)
  analysis_bar=QHBoxLayout();self.analysis_select=QPushButton("选择多份tscap并分析")
  self.analysis_html=QPushButton("导出HTML报告");self.analysis_json=QPushButton("导出JSON统计")
  for w in (self.analysis_select,self.analysis_html,self.analysis_json):analysis_bar.addWidget(w)
  analysis_bar.addStretch();analysis_layout.addLayout(analysis_bar)
  self.analysis_summary=QPlainTextEdit();self.analysis_summary.setReadOnly(True);self.analysis_summary.setMinimumHeight(70);self.analysis_summary.setMaximumHeight(85)
  self.analysis_summary.setPlainText("离线分析已保存的记录，不读取/ARM/释放板端。\n按完整配置分组，重复采集内容只计一次；异常捕获不代表全流分布。\n完整单事务实物波形重判一致才纳入延迟统计；时间按记录内标称时基换算。")
  analysis_layout.addWidget(self.analysis_summary)
  self.analysis_table=QTableWidget(0,10)
  self.analysis_table.setHorizontalHeaderLabels(["配置组","规则","N","PASS/EARLY/TIMEOUT","匹配均值 ms","观测B均值 ms","观测B σ ms","观测B P95 ms","观测B P99 ms","观测B N"])
  self.analysis_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
  self.analysis_table.verticalHeader().setDefaultSectionSize(28);self.analysis_table.setMinimumHeight(164)
  self.analysis_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
  self.analysis_table.horizontalHeader().setStretchLastSection(True)
  analysis_layout.addWidget(self.analysis_table,1)
  self.analysis_index=tabs.addTab(analysis_panel,"批量统计 / 报告（离线）")
  self.analysis_button.clicked.connect(self.analyze_saved_records);self.analysis_select.clicked.connect(self.analyze_saved_records)
  self.analysis_html.clicked.connect(lambda:self.export_statistics("html"))
  self.analysis_json.clicked.connect(lambda:self.export_statistics("json"))
  tabs.setCurrentIndex(1);tabs.setMinimumHeight(220);self.tabs=tabs
  self.data_splitter=QSplitter(Qt.Orientation.Vertical);self.data_splitter.setHandleWidth(9)
  self.data_splitter.setChildrenCollapsible(False);self.data_splitter.addWidget(self.wave);self.data_splitter.addWidget(tabs)
  self.data_splitter.setStretchFactor(0,3);self.data_splitter.setStretchFactor(1,3)
  self.data_splitter.setSizes([330,300]);layout.addWidget(self.data_splitter,1)
  tabs.currentChanged.connect(self.adjust_evidence_panel)
  bottom=QHBoxLayout();self.progress=QProgressBar();bottom.addWidget(self.progress);self.cancel_button=QPushButton("停止当前读取/验收（不释放）");bottom.addWidget(self.cancel_button);layout.addLayout(bottom)
  self.refresh_button.clicked.connect(self.refresh_ports);self.connect_button.clicked.connect(self.toggle_connection);self.open_button.clicked.connect(self.open_record)
  self.cancel_button.clicked.connect(lambda:self.task.cancelled.set() if self.task else None)
  self.buttons["defaults"].clicked.connect(lambda:self.set_draft(spi_example()))
  self.buttons["configure"].clicked.connect(self.configure);self.buttons["arm"].clicked.connect(self.arm)
  self.buttons["stop"].clicked.connect(lambda:self.start(lambda t:self.client.disarm(),self.set_status))
  self.buttons["read"].clicked.connect(self.read_capture);self.buttons["save"].clicked.connect(self.save)
  self.buttons["release"].clicked.connect(self.release)
  self.buttons["batch"].clicked.connect(self.batch)
  self.refresh_ports();self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.poll);self.timer.start()
  self.observe_button.clicked.connect(self.observe)
  self.set_draft(spi_example());self.gate()
  from mvp5.compact_layout import install as install_compact_layout
  install_compact_layout(self)
  self.setWindowTitle("Timing Sentinel A7 · 时序诊断工作台 "+__version__)
  self.gate()
  if port:
   idx=self.port.findData(port)
   if idx>=0:self.port.setCurrentIndex(idx);QTimer.singleShot(200,self.toggle_connection)

 def check_capture_root(self):
  try:
   checked=probe_directory(self.root)
   self.root=checked;self.storage_ready=True;self.storage_error=''
   record_event('capture_directory_checked',passed=True)
  except (OSError,ValueError) as exc:
   self.storage_ready=False;self.storage_error=f'{type(exc).__name__}: {exc}'
   self.log.appendPlainText('保存位置不可用：'+str(self.root)+' · '+self.storage_error)
   record_event('capture_directory_checked',passed=False,exception_type=type(exc).__name__)
  self.gate();return self.storage_ready

 def ensure_capture_root(self):
  if self.check_capture_root():return True
  QMessageBox.warning(self,'请更换保存位置',
   '当前目录不能可靠地写入和原子替换文件，未启动实验、未释放板端记录。\n'
   '请点击“保存位置”选择其他目录，再重试。\n'+str(self.root)+'\n'+self.storage_error)
  return False

 def choose_capture_root(self):
  if self.after_observe(self.choose_capture_root,local=True):return
  if self.busy:return
  selected=QFileDialog.getExistingDirectory(self,'选择波形与实验保存位置',str(self.root))
  if not selected:return
  try:checked=probe_directory(selected)
  except (OSError,ValueError) as exc:
   QMessageBox.warning(self,'保存位置不可用',str(exc)+'\n原保存位置和已有记录均未改变。');return
  self.root=checked;self.storage_ready=True;self.storage_error=''
  self.log.appendPlainText('保存位置已检查：'+str(self.root))
  try:write_preference(self.root,self.storage_settings)
  except (OSError,ValueError) as exc:
   self.log.appendPlainText('位置本次有效，但记忆设置未保存：'+str(exc))
   QMessageBox.warning(self,'本次位置已生效','新目录可用，但无法记住设置；下次启动请重新选择。\n'+str(exc))
  self.gate()

 def toggle_config_panel(self,checked):
  if getattr(self,"compact_ready",False):
   was_advanced=self.tabs.currentIndex() in self.compact_advanced_indices
   for index in self.compact_advanced_indices:self.tabs.setTabVisible(index,checked)
   self.config_panel.setVisible(True)
   if checked:self.tabs.setCurrentIndex(self.compact_config_index)
   elif was_advanced:self.tabs.setCurrentIndex(0)
   self.adjust_evidence_panel();return
  self.config_panel.setVisible(checked)
  self.config_toggle.setText("收起规则与采样配置 ▴" if checked else "展开规则与采样配置 ▾")
  self.wave.plot.setMinimumHeight(150 if checked else 210)
  self.tabs.setMinimumHeight(140 if checked else 220)
  self.adjust_evidence_panel()

 def adjust_evidence_panel(self,index=None):
  if not hasattr(self,"data_splitter"):return
  if getattr(self,"compact_ready",False):
   advanced=self.tabs.currentIndex()==self.compact_config_index
   stats=self.tabs.currentIndex()==self.analysis_index
   self.wave.plot.setMinimumHeight(115 if advanced or stats else 210)
   self.tabs.setMinimumHeight(380 if advanced else 330 if stats else 220)
   self.data_splitter.setSizes([200,410] if advanced else [250,350] if stats else [365,240])
   return
  stats=self.tabs.currentIndex()==self.analysis_index
  expanded=self.config_toggle.isChecked()
  self.wave.plot.setMinimumHeight(150 if stats or expanded else 210)
  self.tabs.setMinimumHeight(330 if stats else 140 if expanded else 220)
  if stats:self.data_splitter.setSizes([250,350])

 def refresh_event_times(self):
  if self.capture is None:return
  header=self.event_table.horizontalHeaderItem(0)
  if header is None:header=QTableWidgetItem();self.event_table.setHorizontalHeaderItem(0,header)
  header.setText('相对时间 ms')
  for row,e in enumerate(self.shown_events):
   item=self.event_table.item(row,0)
   if item is None:item=QTableWidgetItem();self.event_table.setItem(row,0,item)
   item.setText('%.6f'%relative_ms(e['tick'],self.wave.reference))
  header.setToolTip("随波形零点切换，单位始终为ms；原始tick不变。波形可单独切换μs。")
  if getattr(self,"compact_ready",False):
   from mvp5.compact_layout import populate_events
   populate_events(self)

 def focus_event(self,row,column):
  if self.capture is None:return
  event=self.shown_events[row]
  if not self.wave._tick_visible(event["tick"]):
   self.log.appendPlainText("事件在已收到的波形范围外；保留事件时间，不绘制不存在的样点。")
   return
  pos=self.wave._pos(event["tick"]);self.wave.cursors[0].setValue(pos)
  self.wave._range(pos-.1,pos+.1)

 def refresh_ports(self):
  if self.busy or self.client:return
  previous=self.port.currentData();self.port.clear()
  ports=enumerate_ports()
  for p in ports:self.port.addItem(p["port"]+" · "+p["description"],p["port"])
  candidates=[p["port"] for p in ports if (p["vid"],p["pid"])==(0x1a86,0x7523)]
  selected=previous if previous else candidates[0] if len(candidates)==1 else None
  if selected:self.port.setCurrentIndex(self.port.findData(selected))

 def read_draft(self):
  mask=0;rules=[]
  for i,w in enumerate(self.editors):
   if w[0].isChecked():mask|=1<<i
   kind=w[8].currentIndex()
   rules.append(Rule(0 if kind else w[1].currentIndex(),0 if kind else w[2].currentIndex(),int(w[3].text(),16),int(w[4].text(),16),
    w[7].currentIndex(),ms_ticks(w[5].text()),ms_ticks(w[6].text()),kind,int(w[9].text(),16) if kind else 0,int(w[10].text(),16) if kind else 0))
  spi=(1,self.spi_mode.currentIndex(),self.spi_clk.currentIndex(),self.spi_mosi.currentIndex(),self.spi_cs.currentIndex()) if self.spi_enable.isChecked() else (0,0,0,0,0)
  return Config(self.uart.currentIndex(),self.baud.value(),(1,5,10,50)[self.rate.currentIndex()],
   self.pre.value(),65535-self.pre.value(),mask,tuple(rules),*spi).validate()

 def set_draft(self,cfg):
  self.uart.setCurrentIndex(cfg.uart_channel);self.baud.setValue(cfg.baud);self.rate.setCurrentIndex((1,5,10,50).index(cfg.divisor));self.pre.setValue(cfg.pre)
  self.spi_enable.setChecked(bool(cfg.spi_enabled));self.spi_mode.setCurrentIndex(cfg.spi_mode)
  self.spi_clk.setCurrentIndex(cfg.spi_clk);self.spi_mosi.setCurrentIndex(cfg.spi_mosi);self.spi_cs.setCurrentIndex(cfg.spi_cs)
  for i,r in enumerate(cfg.rules):
   w=self.editors[i];w[0].setChecked(bool(cfg.enabled_mask&(1<<i)));w[1].setCurrentIndex(r.target_channel);w[2].setCurrentIndex(r.edge)
   w[3].setText(f"{r.value:02X}");w[4].setText(f"{r.mask:02X}");w[5].setText(str(r.min_ticks/50000));w[6].setText(str(r.max_ticks/50000));w[7].setCurrentIndex(r.action)
   w[8].setCurrentIndex(r.target_kind);w[9].setText(f"{r.spi_value:02X}");w[10].setText(f"{r.spi_mask:02X}")
  self.gate()

 def after_observe(self,callback,*,local=False):
  # Stable buttons during STATUS reads, but never race the serial worker.
  if not (self.busy and self.background_observe):return False
  if self._pending_user_action is None:
   self._pending_user_action=(callback,local,self.client,self.device_state,getattr(self.client,'config_id',None))
  return True

 def start(self,operation,success,failure=None,*,background=False):
  if self.busy:
   if not background:self.after_observe(lambda:self.start(operation,success,failure))
   return
  record_event("task_create_begin",operation=getattr(operation,"__qualname__",type(operation).__name__))
  self.busy=True;self.background_observe=background;self.progress.setValue(0);task=Task(operation,self);self.task=task
  record_event("task_created",task_id=id(task))
  if self.client:self.client.cancel=task.cancelled.is_set;self.client.log=task.message.emit
  record_event("task_connect_progress",task_id=id(task))
  task.progress.connect(lambda a,b:self.progress.setValue(int(a*100/max(b,1))))
  record_event("task_connect_message",task_id=id(task))
  task.message.connect(self.log.appendPlainText)
  record_event("task_connect_success",task_id=id(task))
  task.succeeded.connect(success)
  record_event("task_connect_failure",task_id=id(task))
  task.failed.connect(failure or self.failed)
  record_event("task_connect_finished",task_id=id(task))
  task.finished.connect(self.finished);self.gate()
  record_event("task_start",task_id=id(task));task.start()

 def finished(self):
  task=self.task
  pending=self._pending_user_action;self._pending_user_action=None
  record_event("task_finished",task_id=id(task) if task else None)
  if self.client:self.client.cancel=lambda:False;self.client.log=lambda _:None
  self.task=None;self.busy=False;self.background_observe=False
  if task:task.deleteLater()
  record_event("task_delete_scheduled",task_id=id(task) if task else None)
  self.gate()
  if pending:
   callback,local,client,state,config_id=pending
   if self.client is client and (local or (client is not None and not client.uncertain
       and self.device_state==state and client.config_id==config_id)):
    callback()
   else:self.log.appendPlainText('后台查询发现设备状态或配置变化，本次等待中的操作未执行，请确认后重试。')

 def failed(self,message):
  self.log.appendPlainText(message);self.summary.setText("未完成："+message+" · 板端记录未自动释放")
  if self.client:self.device_state="UNKNOWN"
  QMessageBox.warning(self,"操作未完成",message+"\n未自动释放、重新ARM或抹除板端数据。")

 def toggle_connection(self):
  # A queued click can arrive after polling started, even if the button was enabled when invoked.
  # Never close a Win32 overlapped serial handle beneath an active worker.
  if self.after_observe(self.toggle_connection,local=True):return
  if self.busy:return
  if self.client:
   if self.device_state in ("PREFILL","ARMED","POST_TRIGGER"):
    if QMessageBox.question(self,"监测尚在运行","断开只关闭电脑串口，板端继续监测，不会停止或释放。确认断开？")!=QMessageBox.StandardButton.Yes:return
   record_event("connection_close_begin")
   self.transport.close();self.transport=None;self.client=None;self.applied=None;self.device_state="DISCONNECTED";self.gate()
   record_event("connection_closed");return
  port=self.port.currentData()
  if not port:return
  def connect(t):
   record_event("connection_open_begin")
   transport=SerialTransport(port,115200);client=Client(transport,logger=t.message.emit)
   try:
    record_event("connection_handshake_begin");result=client.connect()
   except Exception:transport.close();raise
   record_event("connection_handshake_complete")
   return transport,client,result
  def accepted(value):
   record_event("connection_apply_begin")
   self.transport,self.client,result=value;self.applied=Config.from_dict(result["config"])
   # Connecting must not turn a historical replay into live saved evidence.
   # Only read_capture's successful device read can establish a live capture.
   if self.capture is None:self.replay=False
   self.set_status(result["status"]);self.log.appendPlainText("握手：真实mvp5-a7-0.1.0 / 四个硬件判定器＋被动SPI监听；未自动ARM")
   record_event("connection_apply_complete")
  self.start(connect,accepted)

 def set_status(self,status):
  self.device_state=status["state"]
  self.summary.setText("实物 · "+self.device_state+" | session "+str(status["session_id"])+" | config "+str(status["config_id"])+" | 4并行规则 | 标称时基")
  if self.capture is not None and (self.replay or self.capture.metadata["session_id"]!=status["session_id"] or self.capture.metadata["config_id"]!=status["config_id"]):
   self.summary.setText(self.summary.text()+" | 当前波形是保留的旧记录，请读取新冻结数据")
  self.gate()

 def observe(self,background=False):
  def accepted(result):
   if result["config"] is not None:
    self.applied=Config.from_dict(result["config"])
    self.log.appendPlainText("检测到板端配置变化，已只读回读；本地草稿/旧波形保留，未自动ARM或释放")
   self.set_status(result["status"])
  if self.client and not self.busy and not self.client.uncertain:
   self.start(lambda t:self.client.observe(),accepted,background=background)

 def poll(self):
  self.gate()
  active=self.device_state in ("PREFILL","ARMED","POST_TRIGGER","UNKNOWN")
  if self.client and not self.busy and not self.client.uncertain and (active or self.observe_auto.isChecked()):
   self.observe(background=True)

 def gate(self):
  if not hasattr(self,"buttons"):return
  # Background queries keep the last enabled states; handlers serialize clicks.
  if self.busy and self.background_observe:return
  free=not self.busy;connected=self.client is not None;known=connected and not self.client.uncertain
  if self.live_workload_seen and self.legacy_debug_enabled:
   self.legacy_debug_enabled=False
   self.legacy_mode.blockSignals(True);self.legacy_mode.setChecked(False);self.legacy_mode.blockSignals(False)
  self.analysis_button.setEnabled(free);self.analysis_select.setEnabled(free)
  self.analysis_html.setEnabled(free and self.analysis_report is not None)
  self.analysis_json.setEnabled(free and self.analysis_report is not None)
  idle=known and self.device_state=="IDLE";frozen=known and self.device_state=="FROZEN"
  try:
   cfg=self.read_draft();match=self.applied is not None and cfg.to_bytes()==self.applied.to_bytes()
   self.draft.setText("已提交并回读一致" if match else "本地草稿尚未提交；ARM只用已确认配置")
   self.window_text.setText("后窗 %.4f ms"%(cfg.post*cfg.divisor/50000))
  except Exception as exc:match=False;self.draft.setText("配置错误："+str(exc))
  protected=self.capture is not None and not self.saved and not self.replay
  for key,w in self.buttons.items():
   enabled={"defaults":free,"configure":free and idle,"arm":free and idle and match and not protected,
    "stop":free and known and self.device_state in ("PREFILL","ARMED","POST_TRIGGER"),
    "read":free and frozen,"save":free and self.capture is not None,"export":free and self.capture is not None,
    "release":self.can_release_capture(),
    "batch":free and idle and not protected and self.legacy_debug_enabled and not self.live_workload_seen}[key]
   w.setEnabled(enabled)
  self.connect_button.setEnabled(free)
  if not getattr(self,"compact_ready",False):self.connect_button.setText("断开" if connected else "连接 / 确认SPI固件")
  self.observe_button.setEnabled(free and known)
  self.observe_auto.setEnabled(free)
  self.refresh_button.setEnabled(free and not connected);self.port.setEnabled(free and not connected)
  self.open_button.setEnabled(free);self.cancel_button.setEnabled(self.busy)
  if hasattr(self,'storage_button'):self.storage_button.setEnabled(free)
  self.workload_deadline.setEnabled(free);self.workload_preset.setEnabled(free and (not connected or idle))
  self.workload_run.setEnabled(free and known and (idle or frozen) and not protected and not self.legacy_debug_enabled)
  self.legacy_mode.setEnabled(free and not connected and not protected and not self.live_workload_seen)
  self.buttons['batch'].setVisible(self.legacy_debug_enabled and not self.live_workload_seen)
  self.workload_report.setEnabled(free and self.last_workload_directory is not None and (self.last_workload_directory/'summary.html').is_file())
  for widget in (self.config_table,self.uart,self.baud,self.rate,self.pre,self.spi_enable,self.spi_mode,self.spi_clk,self.spi_mosi,self.spi_cs):widget.setEnabled(free and (not connected or idle))
  editable=free and (not connected or idle)
  for row in self.editors:
   kind=row[8].currentIndex()
   for i in (1,2):row[i].setEnabled(editable and not kind)
   for i in (9,10):row[i].setEnabled(editable and bool(kind))
  if getattr(self,"compact_ready",False):
   from mvp5.compact_layout import update as update_compact_layout
   update_compact_layout(self)

 def configure(self):
  try:cfg=self.read_draft()
  except Exception as exc:QMessageBox.warning(self,"配置错误",str(exc));return
  def done(result):self.applied=Config.from_dict(result["config"]);self.log.appendPlainText("配置提交＋100字节回读一致");self.gate()
  self.start(lambda t:self.client.configure(cfg),done)

 def arm(self):
  def done(status):
   self.capture=None;self.saved=False;self.replay=False;self.scene_info=None;self.wave.clear();self.clear_result_cells();self.event_table.setRowCount(0);self.scene_banner.setText("场景：等待本轮冻结数据");self.set_status(status)
  self.start(lambda t:self.client.arm(),done)

 def clear_result_cells(self):
  # Keep Qt-owned wrappers alive across captures, just as for a normal refresh.
  # Replacement/destruction was the failing path in the scoped native reproducer.
  for row in range(self.result_table.rowCount()):
   for col in range(self.result_table.columnCount()):
    item=self.result_table.item(row,col)
    if item is not None:item.setText('');item.setData(Qt.ItemDataRole.ForegroundRole,None)

 def read_capture(self):
  if self.after_observe(self.read_capture):return
  if self.capture is not None and not self.saved and not self.replay:
   QMessageBox.information(self,'先保存当前记录','请先保存已读取的记录，再读取另一份。\n不完整记录也可以先保存；不会自动释放板端数据。');return
  def operation(t):
   record_event("capture_read_begin")
   cap=self.client.read_capture(t.progress.emit)
   record_event("capture_read_return",samples=len(cap.samples),event_bytes=len(cap.events),complete=cap.complete,uncertain=self.client.uncertain)
   record_event("capture_status_begin")
   try:status=self.client.status() if not self.client.uncertain else None
   except (DeviceFailure,Cancelled,ConnectionError,OSError,TimeoutError,ValueError) as exc:
    # All acquired bytes already belong to cap. A follow-up STATUS failure must
    # not throw away that capture or report the connection as still confirmed.
    self.client.uncertain=True;status=None
    record_event("capture_status_unavailable",exception_type=type(exc).__name__)
   record_event("capture_status_return",confirmed=status is not None)
   return cap,status
  def done(value):
   record_event("capture_apply_begin")
   cap,status=value;self.saved=False;self.replay=False;self.show_capture(cap)
   if self.scene_info.get('kind')=='workload' and self.scene_info.get('verified'):self.live_workload_seen=True
   if status:self.set_status(status)
   else:
    self.device_state="UNKNOWN"
    self.summary.setText("波形已完整收到，但板端状态未确认；请先保存，再重连" if cap.complete else "传输中断，已保留收到的前缀，可保存；重连后读取原冻结记录")
    self.gate()
   record_event("capture_apply_complete",complete=cap.complete)
  self.start(operation,done)

 def show_capture(self,cap,*,path=None):
  record_event("capture_render_begin",samples=len(cap.samples),event_bytes=len(cap.events),complete=cap.complete)
  if path is not None:self.capture_path=Path(path).resolve()
  elif self.capture is not cap:self.capture_path=None
  self.capture=cap;self.wave.set_capture(cap);m=cap.metadata
  record_event("capture_wave_rendered")
  self.scene_info=identify_scene(cap)
  info=self.scene_info;marker=(f" · {info['marker']:02X}" if info['marker'] is not None else "")
  identity=info.get("workload")
  title=workload_label(identity) if identity else info['name']+marker
  self.scene_banner.setText(f"当前波形{'负载' if info.get('kind')=='workload' else '场景'}：{title} | session {m['session_id']} / config {m['config_id']}\n{info['status']}")
  self.scene_banner.setToolTip(info["detail"]+"\n场景对应当前显示记录，不随板端状态更新改变。")
  self.scene_banner.setStyleSheet("QLabel {font-size:16px;padding:9px;background:"+("#19392f" if info["verified"] else "#3c3220")+";border-radius:5px;}")
  for i,r in enumerate(m["rules"]):
   a,b,o=r["a_tick"],r["b_tick"],r["observed_b_tick"];c=r["counts"]
   values=[str(i),r["result"],"%.6f"%(delta_tick(b,a)/50000) if b is not None and a is not None else "—",
    "%.6f"%(delta_tick(o,a)/50000) if o is not None and a is not None else "—","首触发" if m["primary_rule"]==i else "",
    str(c["accepted"]),"/".join(str(c[k]) for k in ("pass","early","timeout")),"/".join(str(c[k]) for k in ("cancelled","incomplete","overlap"))]
   for col,value in enumerate(values):
    item=self.result_table.item(i,col)
    if item is None:
     item=QTableWidgetItem();self.result_table.setItem(i,col,item)
    item.setText(value)
    if col==1:item.setForeground(QBrush(QColor({"PASS":"#91e7b1","EARLY":"#ffd08a","TIMEOUT":"#ff9b9b"}.get(value,"#dce6f2"))))
  self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
  record_event("capture_rules_rendered")
  self.populate_events()
  record_event("capture_events_rendered")
  self.detail.setPlainText(json.dumps(m,ensure_ascii=False,indent=2));self.progress.setValue(100 if cap.complete else 0)
  source=("实物" if m["origin"]=="hardware" else "模拟数据")+("回放" if self.replay else "读取")
  self.summary.setText(source+" | 4规则 | "+str(len(cap.samples))+"/65536样点 | "+("完整" if cap.complete else "部分数据")+" | 标称时基")
  self.gate()
  record_event("capture_render_complete")

 def populate_events(self,*args):
  if self.capture is None:return
  m=self.capture.metadata;events=unpack_events(self.capture.events)
  cfg=m["config"];edge_mask=sum(1<<ch for ch in {r["target_channel"] for i,r in enumerate(cfg["rules"]) if cfg["enabled_mask"]&(1<<i) and not r["target_kind"]})
  if cfg["spi_enabled"]:edge_mask|=1<<cfg["spi_cs"]
  important=lambda e:e["uart"] is not None or e["spi"] is not None or e["spi_error"] or e["uart_error"] or e["overlap_mask"] or e["accepted_mask"] or any(r!="NONE" for r in e["results"]) or bool((e["rise"]|e["fall"])&edge_mask)
  self.shown_events=[e for e in events if not self.key_events.isChecked() or important(e)]
  self.event_count.setText(f"显示 {len(self.shown_events)} / 原始 {len(events)} 条 · 相对时间与波形共用零点")
  self.event_table.setRowCount(len(self.shown_events))
  for row,e in enumerate(self.shown_events):
   values=["%.6f"%relative_ms(e["tick"],self.wave.reference),str(e["tick"]),f"{e['uart']:02X}" if e["uart"] is not None else "",
    f"{e['spi']:02X}" if e["spi"] is not None else "截断" if e["spi_error"] else "",f"{e['rise']:02X}/{e['fall']:02X}",*e["results"],event_description(e,m["config"]["uart_channel"])]
   important=e["uart"] is not None or e["spi"] is not None or e["spi_error"] or any(r!="NONE" for r in e["results"])
   for col,value in enumerate(values):
    item=self.event_table.item(row,col)
    if item is None:item=QTableWidgetItem();self.event_table.setItem(row,col,item)
    item.setText(value);item.setToolTip(value)
    item.setBackground(QBrush(QColor('#203c4a')) if important else QBrush())
  self.refresh_event_times()

 def save(self):
  if self.after_observe(self.save,local=True):return False
  if self.busy or not self.capture:return False
  from mvp5.user_guidance import suggested_record_name
  suggested=self.root/suggested_record_name(self.capture,self.scene_info,uuid4().hex[:8])
  path,_=QFileDialog.getSaveFileName(self,"保存四规则证据",str(suggested),"Timing evidence (*.tscap)")
  if not path:self.gate();return False
  try:save_record(self.capture,path);self.capture_path=Path(path).resolve();self.saved=True;self.log.appendPlainText("保存＋回放核对："+path);self.gate();return True
  except Exception as exc:
   record_event('capture_save_failed',exception_type=type(exc).__name__)
   QMessageBox.warning(self,"保存失败",str(exc)+'\n已收到的波形仍保留在内存中，板端未自动释放；请选择其他位置重存。');self.gate();return False

 def export(self,kind="json"):
  if self.after_observe(lambda:self.export(kind),local=True):return
  if self.busy or self.capture is None:return
  record_event("export_requested",kind=kind)
  formats={"json":("JSON元数据",export_json),"csv":("CSV逐点波形",export_csv),"vcd":("VCD数字波形",export_vcd)}
  if kind not in formats:return
  title,writer=formats[kind]
  suggested=self.root/("capture_s"+str(self.capture.metadata["session_id"])+"_"+uuid4().hex[:8]+"."+kind)
  path,_=QFileDialog.getSaveFileName(self,"导出"+title,str(suggested),kind.upper()+" (*."+kind+")")
  if not path:return
  record_event("export_destination_selected",kind=kind)
  cap=self.capture
  def operation(t):
   if Path(path).suffix.lower()!="."+kind:raise ValueError("请使用."+kind+"扩展名，不能覆盖tscap原始记录")
   cap.validate();writer(cap,path);return path
  def done(destination):
   record_event("export_completed",kind=kind)
   self.progress.setValue(100)
   self.log.appendPlainText("导出"+title+"完成："+destination+" · "+str(len(cap.samples))+"点 · "+("传输完整" if cap.complete else "部分记录，未补造缺失数据")+" · 原始tscap仍需单独保存")
  def failed(message):
   self.log.appendPlainText("导出失败："+message)
   QMessageBox.warning(self,"导出失败",message+"\n板端状态、原始记录和保存标记均未改变。")
  self.start(operation,done,failed)

 def open_record(self):
  if self.after_observe(self.open_record,local=True):return
  if self.capture and not self.saved and not self.replay:
   QMessageBox.information(self,"先保存","当前读取的实物记录尚未保存，请先保存。");return
  path,_=QFileDialog.getOpenFileName(self,"打开四规则记录",str(self.root),"Timing evidence (*.tscap)")
  if path:
   try:cap=load_record(path);self.replay=True;self.saved=True;self.show_capture(cap,path=path)
   except Exception as exc:QMessageBox.warning(self,"回放失败",str(exc))

 def open_record_folder(self):
  if self.after_observe(self.open_record_folder,local=True):return
  path=self.capture_path
  if self.busy or not self.capture or not self.saved or path is None or not path.is_file():return
  if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))):
   QMessageBox.warning(self,'无法打开目录','请在文件管理器中打开：\n'+str(path.parent))

 def can_release_capture(self):
  return bool(not self.busy and self.client is not None and not self.client.uncertain
   and self.device_state=='FROZEN' and self.saved and not self.replay
   and self.capture is not None and self.capture.complete
   and self.capture.metadata['session_id']==self.client.session_id
   and self.capture.metadata['config_id']==self.client.config_id)

 def release(self):
  if self.after_observe(self.release):return
  # A saved transfer prefix is useful evidence, not a saved full board buffer.
  # Re-check here too: an already-queued click can outlive a button state update.
  if not self.can_release_capture():return
  if QMessageBox.question(self,"释放板端缓存","记录已保存。释放后原板端波形将无法重读，继续？")!=QMessageBox.StandardButton.Yes:return
  self.start(lambda t:self.client.release(),self.set_status)

 def change_legacy_mode(self,checked):
  protected=self.capture is not None and not self.saved and not self.replay
  allowed=not self.busy and self.client is None and not protected and not self.live_workload_seen
  enabled=self.legacy_debug_enabled
  if allowed:
   enabled=False
   if checked:
    enabled=QMessageBox.question(self,"旧固件调试确认",
     "仅适用于已刷入旧六场景刺激固件的STM32，不适用于当前FIR/中断任务固件。\n"
     "勾选不代表固件身份已验证，也不会自动换固件。启用后真实负载实验入口停用。\n确认进入旧固件调试模式？",
     QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes
  self.legacy_debug_enabled=enabled
  self.legacy_mode.blockSignals(True);self.legacy_mode.setChecked(enabled);self.legacy_mode.blockSignals(False)
  self.gate()

 def batch(self):
  if self.after_observe(self.batch):return
  if self.busy:return
  if not self.legacy_debug_enabled:
   QMessageBox.warning(self,"旧固件入口已关闭","普通模式不启动旧六场景。仅在断开设备后明确启用旧固件调试模式。");return
  if self.live_workload_seen:
   QMessageBox.warning(self,"固件用途不同","最近实物记录来自真实任务固件，请使用六任务实验按钮。旧六场景需要对应STM32演示固件。");return
  if self.client is None or self.client.uncertain or self.device_state!='IDLE':return
  if self.capture is not None and not self.saved and not self.replay:return
  if not self.ensure_capture_root():return
  self.set_draft(spi_example());directory=new_experiment_dir(self.root)
  def done(result):
   self.applied=spi_example();self.replay=False;self.saved=True;self.show_capture(result["capture"])
   self.set_status(result["status"]);self.detail.setPlainText(json.dumps(result["report"],ensure_ascii=False,indent=2))
   self.log.appendPlainText("六场景实物验收PASS，6记录＋experiment.json："+result["directory"])
   self.summary.setText("自动六轮SPI验收 PASS · 24条规则判定 · 最后一轮已保存/FROZEN")
  self.start(lambda t:run_four(self.client,directory,t.progress.emit,t.message.emit),done)

 def selected_workload_deadline(self):
  return (3.5,8,None)[self.workload_deadline.currentIndex()]

 def workload_batch(self):
  if self.after_observe(self.workload_batch):return
  if self.busy or not self.client or self.client.uncertain:return
  if self.legacy_debug_enabled:
   QMessageBox.warning(self,"当前为旧固件调试模式","请先断开设备并退出旧固件调试模式，再运行真实负载实验。");return
  if self.capture is not None and not self.saved and not self.replay:
   QMessageBox.warning(self,"先保存当前记录","请先保存电脑上尚未保存的波形，再开始实验。");return
  if not self.ensure_capture_root():return
  deadline=self.selected_workload_deadline();directory=new_experiment_dir(self.root);directory=directory.with_name("workload_"+directory.name.removeprefix("four_"))
  mode_label='同次任务 3.5 / 8 ms（仅波形）' if deadline is None else f'{deadline:g} ms'
  self.summary.setText(f"真实负载实验运行中 · {mode_label} · 自动保护、采集与保存")
  self.workload_hint.setText("正在采集／保存；保持接线与供电")
  def done(result):
   self.applied=result['config'];self.set_draft(self.applied)
   self.last_workload_directory=Path(result['directory'])
   self.replay=False;self.saved=True;self.live_workload_seen=True
   self.show_capture(result['capture'],path=Path(result['directory'])/result['report']['latest_record'])
   self.set_status(result['status']);self.detail.setPlainText(json.dumps(result['report'],ensure_ascii=False,indent=2))
   self.log.appendPlainText("六任务证据核验完成；原始记录＋experiment.json＋summary.html："+result['directory'])
   self.summary.setText(f"真实负载六任务已保存 · {mode_label} · 三档各2次 · 末轮FROZEN")
   means=[g['rules']['0']['observed']['mean_ms'] for g in result['report']['groups']]
   self.workload_hint.setText("GPIO均值："+" / ".join("—" if m is None else f"{m:.3f}" for m in means)+" ms（无/轻/重）")
   self.workload_hint.setToolTip(result['directory'])
  def failed(message):
   # The worker retains received evidence even if BOTH record and report writes fail.
   pending=getattr(self.task,'pending_capture',None)
   pending_path=getattr(self.task,'pending_capture_path',None)
   if pending is not None:
    self.replay=False;self.saved=pending_path is not None;self.show_capture(pending,path=pending_path)
   report_path=directory/'experiment.json'
   if pending is None and report_path.exists():
    try:
     report=json.loads(report_path.read_text(encoding='utf-8'));name=report.get('latest_record')
     if name and Path(name).name==name and (directory/name).is_file():
      cap=load_record(directory/name);self.replay=False;self.saved=True;self.show_capture(cap,path=directory/name)
    except (OSError,ValueError,TypeError,AttributeError) as exc:self.log.appendPlainText('中止报告不可读，原有记录保留：'+str(exc))
   self.workload_hint.setText("实验中止；记录路径见通信日志");self.workload_hint.setToolTip(str(directory))
   self.failed(message)
  def operation(t):
   def preserve(cap,path):t.pending_capture=cap;t.pending_capture_path=path
   return run_workload(self.client,directory,deadline,t.progress.emit,t.message.emit,preserve=preserve)
  self.start(operation,done,failed)

 def open_workload_report(self):
  if self.last_workload_directory is not None:
   path=self.last_workload_directory/'summary.html'
   if path.is_file():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

 def analyze_saved_records(self):
  if self.after_observe(self.analyze_saved_records,local=True):return
  if self.busy:return
  self.tabs.setCurrentIndex(self.analysis_index)
  paths,_=QFileDialog.getOpenFileNames(self,"选择已保存的原始记录（可多选）",str(self.root),"Timing evidence (*.tscap)")
  if paths:self.start(lambda t:analyze_records(paths,t.cancelled.is_set,t.progress.emit),self.show_analysis,self.analysis_failed)

 def show_analysis(self,report):
  self.analysis_report=report;self.analysis_summary.setPlainText(summary_text(report))
  rows=[(group,row) for group in report["groups"] for row in group["rules"]]
  self.analysis_table.setRowCount(len(rows))
  fmt=lambda value:"—" if value is None else "%.6f"%value
  for index,(group,row) in enumerate(rows):
   counts=row["result_counts"];obs=row["observed_response_delay"]
   values=[group["group_id"][:12],"R"+str(row["rule_id"]),row["n"],
    "/".join(str(counts.get(k,0)) for k in ("PASS","EARLY","TIMEOUT")),fmt(row["matched_delay"]["mean_ms"]),
    fmt(obs["mean_ms"]),fmt(obs["stddev_ms"]),fmt(obs["p95_ms"]),fmt(obs["p99_ms"]),obs["n"]]
   for col,value in enumerate(values):
    item=self.analysis_table.item(index,col)
    if item is None:item=QTableWidgetItem();self.analysis_table.setItem(index,col,item)
    item.setText(str(value))
  self.tabs.setCurrentIndex(self.analysis_index);self.progress.setValue(100);self.gate()
  self.log.appendPlainText("离线批量统计完成：仅处理所选历史记录，未操作板端；可导出HTML/JSON报告")

 def analysis_failed(self,message):
  self.log.appendPlainText("离线统计/报告未完成："+message)
  QMessageBox.warning(self,"离线统计/报告未完成",message+"\n未操作板端；此前成功的统计报告（若有）仍保留。")

 def export_statistics(self,kind):
  if self.after_observe(lambda:self.export_statistics(kind),local=True):return
  if self.busy or self.analysis_report is None:return
  path,_=QFileDialog.getSaveFileName(self,"导出离线统计报告",str(self.root/("analysis_report."+kind)),kind.upper()+" (*."+kind+")")
  if path:
   report=self.analysis_report
   self.start(lambda t:export_analysis(report,path),lambda p:self.log.appendPlainText("离线报告已保存："+p),self.analysis_failed)

 def closeEvent(self,event):
  if self.after_observe(self.close,local=True):event.ignore();return
  if self.busy:
   QMessageBox.information(self,"任务进行中","先停止当前读取/验收，等待有界请求结束。");event.ignore();return
  if self.capture and not self.saved and not self.replay:
   if QMessageBox.question(self,"未保存","当前数据尚未保存，关闭将丢失电脑端副本（板端未释放）。继续？")!=QMessageBox.StandardButton.Yes:event.ignore();return
  if self.client and self.device_state in ("PREFILL","ARMED","POST_TRIGGER"):
   if QMessageBox.question(self,"监测尚在运行","关闭电脑窗口不会停止板端监测，重连后可继续读取。确认关闭？")!=QMessageBox.StandardButton.Yes:event.ignore();return
  if self.transport:self.transport.close()
  # A hidden main window can outlive closeEvent; never poll a closed transport.
  # Leave the timer running if any close guard above rejected the request.
  self.timer.stop()
  record_event('window_polling_stopped')
  event.accept()


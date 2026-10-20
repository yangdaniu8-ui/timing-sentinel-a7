"""Real-only four-rule UI. One worker owns serial; no automatic ARM or discard."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import json
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
 QSpinBox,QLineEdit,QCheckBox,QTableWidget,QTableWidgetItem,QHeaderView,QTabWidget,QPlainTextEdit,QFileDialog,QMessageBox,QProgressBar)
from app.models import ms_ticks,delta_tick
from app.ui.main_window import STYLE
from app.ui.fonts import configure_fonts
from app.workers import Task
from transport.serial_port import SerialTransport,enumerate_ports
from mvp4.models import Config,Rule,unpack_events
from mvp4.client import Client
from mvp4.store import save_record,load_record,export_json
from mvp4.waveform import FourWaveform
from mvp4.experiment import run_four,new_experiment_dir
from mvp4 import __version__

def combo(items,selected=0):
 w=QComboBox();w.addItems(items);w.setCurrentIndex(selected);return w

class MainWindow(QMainWindow):
 def __init__(self,root=None,port=None):
  super().__init__();configure_fonts(QApplication.instance());self.setStyleSheet(STYLE)
  self.setWindowTitle("Timing Sentinel A7 · 四规则实物版 "+__version__);self.resize(1450,950)
  self.root=Path(root or Path(__file__).resolve().parents[2]/"data"/"mvp4")
  self.client=None;self.transport=None;self.task=None;self.busy=False;self.device_state="DISCONNECTED"
  self.capture=None;self.saved=False;self.applied=None;self.replay=False
  base=QWidget();self.setCentralWidget(base);layout=QVBoxLayout(base)
  top=QHBoxLayout();top.addWidget(QLabel("TIMING SENTINEL / A7 · 4 PARALLEL RULES"));top.addStretch()
  self.port=combo([]);top.addWidget(self.port);self.refresh_button=QPushButton("刷新串口");top.addWidget(self.refresh_button)
  self.connect_button=QPushButton("连接 / 确认四规则固件");top.addWidget(self.connect_button)
  self.open_button=QPushButton("打开四规则记录");top.addWidget(self.open_button);layout.addLayout(top)
  self.summary=QLabel("未连接 · 需要profile4固件，打开串口不等于设备握手");layout.addWidget(self.summary)
  self.note=QLabel("现有接线：CH0 UART / CH1 GPIO / READY。四规则同时判断同一事务；UART停止位中心为A。仅3.3V数字输入；50MHz标称时基。")
  self.note.setWordWrap(True);layout.addWidget(self.note)
  settings=QHBoxLayout()
  self.uart=combo(["CH"+str(i) for i in range(8)]);self.baud=QSpinBox();self.baud.setRange(1200,1000000);self.baud.setValue(115200)
  self.rate=combo(["50 MS/s","10 MS/s","5 MS/s","1 MS/s"],1)
  self.pre=QSpinBox();self.pre.setRange(0,65534);self.pre.setValue(32767)
  for title,widget in (("UART监听",self.uart),("监听baud",self.baud),("采样率",self.rate),("前触发点",self.pre)):settings.addWidget(QLabel(title));settings.addWidget(widget)
  self.window_text=QLabel();settings.addWidget(self.window_text);settings.addStretch();layout.addLayout(settings)
  self.config_table=QTableWidget(4,9);self.config_table.setHorizontalHeaderLabels(["启用","GPIO","边沿","value hex","mask hex","min ms","max ms","动作","用途"])
  self.config_table.verticalHeader().setDefaultSectionSize(37);self.config_table.setFixedHeight(184)
  self.config_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.editors=[]
  for i,rule in enumerate(Config().rules):
   enable=QCheckBox("R"+str(i));enable.setChecked(True)
   widgets=[enable,combo(["CH"+str(j) for j in range(8)],rule.target_channel),combo(["上升沿","下降沿"],rule.edge),
    QLineEdit(f"{rule.value:02X}"),QLineEdit(f"{rule.mask:02X}"),QLineEdit(str(rule.min_ticks/50000)),
    QLineEdit(str(rule.max_ticks/50000)),combo(["仅记录","异常触发","每事务触发"],rule.action)]
   self.editors.append(widgets)
   for col,w in enumerate(widgets):self.config_table.setCellWidget(i,col,w)
   self.config_table.setItem(i,8,QTableWidgetItem(["正常响应窗","快速响应窗","宽限响应窗","下降沿/脉冲末端"][i]))
  layout.addWidget(self.config_table)
  actions=QHBoxLayout();self.buttons={}
  for key,title in (("defaults","恢复四规则示例"),("configure","提交＋回读四规则"),("arm","ARM · 采集一次"),("stop","DISARM · 保留"),
   ("read","读取冻结波形"),("save","保存记录"),("export","导出元数据"),("release","释放已保存记录"),("batch","自动四轮验收＋保存")):
   w=QPushButton(title);self.buttons[key]=w;actions.addWidget(w)
  layout.addLayout(actions)
  self.draft=QLabel();layout.addWidget(self.draft)
  self.result_table=QTableWidget(4,8);self.result_table.setHorizontalHeaderLabels(["规则","实际结果","A→B ms","观察到B ms","主触发","accepted","PASS/EARLY/TIMEOUT","取消/截断/overlap"])
  self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.result_table.verticalHeader().setDefaultSectionSize(27);self.result_table.setFixedHeight(149);layout.addWidget(self.result_table)
  self.wave=FourWaveform();layout.addWidget(self.wave,1)
  tabs=QTabWidget();self.detail=QPlainTextEdit();self.detail.setReadOnly(True)
  self.event_table=QTableWidget(0,7);self.event_table.setHorizontalHeaderLabels(["tick","UART","rise/fall","R0","R1","R2","R3"])
  self.event_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
  self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(1000)
  tabs.addTab(self.detail,"元数据 / 验收报告");tabs.addTab(self.event_table,"并行事件");tabs.addTab(self.log,"通信日志");tabs.setMaximumHeight(160);layout.addWidget(tabs)
  bottom=QHBoxLayout();self.progress=QProgressBar();bottom.addWidget(self.progress);self.cancel_button=QPushButton("停止当前读取/验收（不释放）");bottom.addWidget(self.cancel_button);layout.addLayout(bottom)
  self.refresh_button.clicked.connect(self.refresh_ports);self.connect_button.clicked.connect(self.toggle_connection);self.open_button.clicked.connect(self.open_record)
  self.cancel_button.clicked.connect(lambda:self.task.cancelled.set() if self.task else None)
  self.buttons["defaults"].clicked.connect(lambda:self.set_draft(Config()))
  self.buttons["configure"].clicked.connect(self.configure);self.buttons["arm"].clicked.connect(self.arm)
  self.buttons["stop"].clicked.connect(lambda:self.start(lambda t:self.client.disarm(),self.set_status))
  self.buttons["read"].clicked.connect(self.read_capture);self.buttons["save"].clicked.connect(self.save)
  self.buttons["export"].clicked.connect(self.export);self.buttons["release"].clicked.connect(self.release)
  self.buttons["batch"].clicked.connect(self.batch)
  self.refresh_ports();self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.poll);self.timer.start()
  self.set_draft(Config());self.gate()
  if port:
   idx=self.port.findData(port)
   if idx>=0:self.port.setCurrentIndex(idx);QTimer.singleShot(200,self.toggle_connection)

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
   rules.append(Rule(w[1].currentIndex(),w[2].currentIndex(),int(w[3].text(),16),int(w[4].text(),16),
    w[7].currentIndex(),ms_ticks(w[5].text()),ms_ticks(w[6].text())))
  return Config(self.uart.currentIndex(),self.baud.value(),(1,5,10,50)[self.rate.currentIndex()],
   self.pre.value(),65535-self.pre.value(),mask,tuple(rules)).validate()

 def set_draft(self,cfg):
  self.uart.setCurrentIndex(cfg.uart_channel);self.baud.setValue(cfg.baud);self.rate.setCurrentIndex((1,5,10,50).index(cfg.divisor));self.pre.setValue(cfg.pre)
  for i,r in enumerate(cfg.rules):
   w=self.editors[i];w[0].setChecked(bool(cfg.enabled_mask&(1<<i)));w[1].setCurrentIndex(r.target_channel);w[2].setCurrentIndex(r.edge)
   w[3].setText(f"{r.value:02X}");w[4].setText(f"{r.mask:02X}");w[5].setText(str(r.min_ticks/50000));w[6].setText(str(r.max_ticks/50000));w[7].setCurrentIndex(r.action)
  self.gate()

 def start(self,operation,success):
  if self.busy:return
  self.busy=True;self.progress.setValue(0);task=Task(operation,self);self.task=task
  if self.client:self.client.cancel=task.cancelled.is_set;self.client.log=task.message.emit
  task.progress.connect(lambda a,b:self.progress.setValue(int(a*100/max(b,1))))
  task.message.connect(self.log.appendPlainText)
  task.succeeded.connect(success);task.failed.connect(self.failed)
  task.finished.connect(self.finished);self.gate();task.start()

 def finished(self):
  task=self.task
  if self.client:self.client.cancel=lambda:False;self.client.log=lambda _:None
  self.task=None;self.busy=False
  if task:task.deleteLater()
  self.gate()

 def failed(self,message):
  self.log.appendPlainText(message);self.summary.setText("未完成："+message+" · 板端记录未自动释放")
  if self.client:self.device_state="UNKNOWN"
  QMessageBox.warning(self,"操作未完成",message+"\n未自动释放、重新ARM或抹除板端数据。")

 def toggle_connection(self):
  if self.client:
   if self.device_state in ("PREFILL","ARMED","POST_TRIGGER"):
    if QMessageBox.question(self,"监测尚在运行","断开只关闭电脑串口，板端继续监测，不会停止或释放。确认断开？")!=QMessageBox.StandardButton.Yes:return
   self.transport.close();self.transport=None;self.client=None;self.applied=None;self.device_state="DISCONNECTED";self.gate();return
  port=self.port.currentData()
  if not port:return
  def connect(t):
   transport=SerialTransport(port,115200);client=Client(transport,logger=t.message.emit)
   try:result=client.connect()
   except Exception:transport.close();raise
   return transport,client,result
  def accepted(value):
   self.transport,self.client,result=value;self.applied=Config.from_dict(result["config"]);self.replay=False
   self.set_status(result["status"]);self.log.appendPlainText("握手：真实mvp4-a7-0.1.0 / 四个硬件判定器；未自动ARM")
  self.start(connect,accepted)

 def set_status(self,status):
  self.device_state=status["state"]
  self.summary.setText("实物 · "+self.device_state+" | session "+str(status["session_id"])+" | config "+str(status["config_id"])+" | 4并行规则 | 标称时基")
  self.gate()

 def poll(self):
  self.gate()
  if self.client and not self.busy and not self.client.uncertain and self.device_state in ("PREFILL","ARMED","POST_TRIGGER","UNKNOWN"):
   self.start(lambda t:self.client.status(),self.set_status)

 def gate(self):
  if not hasattr(self,"buttons"):return
  free=not self.busy;connected=self.client is not None;known=connected and not self.client.uncertain
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
    "release":free and frozen and self.saved and not self.replay and self.capture is not None and self.capture.metadata["session_id"]==self.client.session_id,
    "batch":free and idle and not protected}[key]
   w.setEnabled(enabled)
  self.connect_button.setEnabled(free);self.connect_button.setText("断开" if connected else "连接 / 确认四规则固件")
  self.refresh_button.setEnabled(free and not connected);self.port.setEnabled(free and not connected)
  self.open_button.setEnabled(free);self.cancel_button.setEnabled(self.busy)
  for widget in (self.config_table,self.uart,self.baud,self.rate,self.pre):widget.setEnabled(free and (not connected or idle))

 def configure(self):
  try:cfg=self.read_draft()
  except Exception as exc:QMessageBox.warning(self,"配置错误",str(exc));return
  def done(result):self.applied=Config.from_dict(result["config"]);self.log.appendPlainText("配置提交＋100字节回读一致");self.gate()
  self.start(lambda t:self.client.configure(cfg),done)

 def arm(self):
  def done(status):
   self.capture=None;self.saved=False;self.replay=False;self.wave.clear();self.result_table.clearContents();self.set_status(status)
  self.start(lambda t:self.client.arm(),done)

 def read_capture(self):
  def operation(t):
   cap=self.client.read_capture(t.progress.emit)
   return cap,self.client.status() if not self.client.uncertain else None
  def done(value):
   cap,status=value;self.show_capture(cap);self.saved=False;self.replay=False
   if status:self.set_status(status)
   else:self.device_state="UNKNOWN";self.summary.setText("传输中断，已保留收到的前缀，可保存；重连后读取原冻结记录")
  self.start(operation,done)

 def show_capture(self,cap):
  self.capture=cap;self.wave.set_capture(cap);m=cap.metadata
  for i,r in enumerate(m["rules"]):
   a,b,o=r["a_tick"],r["b_tick"],r["observed_b_tick"];c=r["counts"]
   values=[str(i),r["result"],"%.6f"%(delta_tick(b,a)/50000) if b is not None and a is not None else "—",
    "%.6f"%(delta_tick(o,a)/50000) if o is not None and a is not None else "—","首触发" if m["primary_rule"]==i else "",
    str(c["accepted"]),"/".join(str(c[k]) for k in ("pass","early","timeout")),"/".join(str(c[k]) for k in ("cancelled","incomplete","overlap"))]
   for col,value in enumerate(values):self.result_table.setItem(i,col,QTableWidgetItem(value))
  events=unpack_events(cap.events);self.event_table.setRowCount(len(events))
  for row,e in enumerate(events):
   values=[str(e["tick"]),f"{e['uart']:02X}" if e["uart"] is not None else "",f"{e['rise']:02X}/{e['fall']:02X}",*e["results"]]
   for col,value in enumerate(values):self.event_table.setItem(row,col,QTableWidgetItem(value))
  self.detail.setPlainText(json.dumps(m,ensure_ascii=False,indent=2));self.progress.setValue(100 if cap.complete else 0)
  source=("实物" if m["origin"]=="hardware" else "模拟数据")+("回放" if self.replay else "读取")
  self.summary.setText(source+" | 4规则 | "+str(len(cap.samples))+"/65536样点 | "+("完整" if cap.complete else "部分数据")+" | 标称时基")
  self.gate()

 def save(self):
  if not self.capture:return False
  self.root.mkdir(parents=True,exist_ok=True)
  suggested=self.root/("capture_s"+str(self.capture.metadata["session_id"])+"_"+uuid4().hex[:8]+".tscap")
  path,_=QFileDialog.getSaveFileName(self,"保存四规则证据",str(suggested),"Timing evidence (*.tscap)")
  if not path:return False
  try:save_record(self.capture,path);self.saved=True;self.log.appendPlainText("保存＋回放核对："+path);self.gate();return True
  except Exception as exc:QMessageBox.warning(self,"保存失败",str(exc));return False

 def export(self):
  path,_=QFileDialog.getSaveFileName(self,"导出四规则元数据",str(self.root/"metadata.json"),"JSON (*.json)")
  if path:
   try:export_json(self.capture,path)
   except Exception as exc:QMessageBox.warning(self,"导出失败",str(exc))

 def open_record(self):
  if self.capture and not self.saved and not self.replay:
   QMessageBox.information(self,"先保存","当前读取的实物记录尚未保存，请先保存。");return
  path,_=QFileDialog.getOpenFileName(self,"打开四规则记录",str(self.root),"Timing evidence (*.tscap)")
  if path:
   try:cap=load_record(path);self.replay=True;self.saved=True;self.show_capture(cap)
   except Exception as exc:QMessageBox.warning(self,"回放失败",str(exc))

 def release(self):
  if QMessageBox.question(self,"释放板端缓存","记录已保存。释放后原板端波形将无法重读，继续？")!=QMessageBox.StandardButton.Yes:return
  self.start(lambda t:self.client.release(),self.set_status)

 def batch(self):
  self.set_draft(Config());directory=new_experiment_dir(self.root)
  def done(result):
   self.applied=Config();self.replay=False;self.saved=True;self.show_capture(result["capture"])
   self.set_status(result["status"]);self.detail.setPlainText(json.dumps(result["report"],ensure_ascii=False,indent=2))
   self.log.appendPlainText("四场景实物验收PASS，4记录＋experiment.json："+result["directory"])
   self.summary.setText("自动四轮验收 PASS · 16条规则判定 · 最后一轮已保存/FROZEN")
  self.start(lambda t:run_four(self.client,directory,t.progress.emit,t.message.emit),done)

 def closeEvent(self,event):
  if self.busy:
   QMessageBox.information(self,"任务进行中","先停止当前读取/验收，等待有界请求结束。");event.ignore();return
  if self.capture and not self.saved and not self.replay:
   if QMessageBox.question(self,"未保存","当前数据尚未保存，关闭将丢失电脑端副本（板端未释放）。继续？")!=QMessageBox.StandardButton.Yes:event.ignore();return
  if self.client and self.device_state in ("PREFILL","ARMED","POST_TRIGGER"):
   if QMessageBox.question(self,"监测尚在运行","关闭电脑窗口不会停止板端监测，重连后可继续读取。确认关闭？")!=QMessageBox.StandardButton.Yes:event.ignore();return
  if self.transport:self.transport.close()
  event.accept()


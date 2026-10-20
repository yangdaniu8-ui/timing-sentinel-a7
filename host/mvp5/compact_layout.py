"""Approved compact presentation, reusing the original controls/handlers and safety gates."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QFont
from PySide6.QtWidgets import (QApplication,QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,
    QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QSplitter,QTabWidget,QSizePolicy,QLayout)
import shiboken6
from mvp5.presentation import relative_ms,event_description
from mvp5.user_guidance import next_step,demo_expectation

STYLE='''
QMainWindow,QWidget {background:#0c121c;color:#e9f0f7;font-family:"Microsoft YaHei UI";font-size:14px;}
QFrame#card {background:#131e2c;border:1px solid #26364a;border-radius:12px;}
QFrame#card QLabel {background:transparent;border:none;}
QLabel#muted {color:#8b9db3;font-size:12px;}
QLabel#section {font-size:16px;font-weight:600;}
QPushButton {background:#1b2a3e;border:1px solid #32465f;border-radius:7px;padding:9px 12px;color:#d9e5f2;}
QPushButton:hover {background:#263d55;border-color:#4c7295;}
QPushButton#primary {background:#168dcb;border-color:#168dcb;color:white;font-weight:600;}
QPushButton#quiet {background:transparent;border-color:#2c3d53;}
QPushButton:disabled {background:#172233;color:#61738b;border-color:#253449;}
QPushButton#primary:disabled {background:#172233;color:#61738b;border-color:#253449;}
QPushButton[recommended="true"],QPushButton#quiet[recommended="true"] {background:#168dcb;border-color:#168dcb;color:white;font-weight:600;}
QPushButton[recommended="true"]:disabled,QPushButton#quiet[recommended="true"]:disabled {background:#172233;color:#61738b;border-color:#253449;}
QComboBox,QSpinBox,QLineEdit {background:#1a293c;border:1px solid #32465f;border-radius:6px;padding:7px;color:#e9f0f7;}
QComboBox::drop-down {border:none;width:24px;}
QComboBox QAbstractItemView {background:#1a293c;selection-background-color:#254e73;color:#e9f0f7;}
QTabWidget::pane {background:#131e2c;border:none;border-top:1px solid #26364a;}
QTabBar::tab {background:transparent;color:#8b9db3;padding:11px 15px;border-bottom:2px solid transparent;}
QTabBar::tab:selected {color:#e9f0f7;border-bottom:2px solid #45b9ed;font-weight:600;}
QTableWidget {background:#131e2c;alternate-background-color:#162333;border:none;gridline-color:#26364a;selection-background-color:#244361;}
QHeaderView::section {background:#182638;color:#93a9c0;border:none;padding:9px 11px;font-size:12px;}
QTableWidget::item {padding-left:10px;border-bottom:1px solid #1e2d40;}
QPlainTextEdit,QScrollArea {background:#131e2c;border:none;color:#afc0d3;}
QProgressBar {background:#172233;border:1px solid #26364a;border-radius:5px;height:8px;text-align:center;}
QProgressBar::chunk {background:#168dcb;border-radius:4px;}
QScrollBar:vertical {background:#131e2c;width:8px;} QScrollBar::handle:vertical {background:#36516d;min-height:25px;border-radius:4px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
'''

def label(text,name='muted',size=None):
    w=QLabel(text);w.setObjectName(name)
    if size:w.setStyleSheet(f'font-size:{size}px;font-weight:600')
    return w

def card():
    w=QFrame();w.setObjectName('card');return w

def detach_widget(widget):
    """Release its old QLayoutItem explicitly before Qt reparents the QWidget.

    takeAt transfers item ownership to Python. Destroying that wrapper through
    Shiboken unregisters its address; implicit setParent removal can leave a
    borrowed QWidgetItem wrapper pointing at an already-deleted native item.
    The layout item does not own/delete the widget itself.
    """
    previous=widget.parentWidget()
    if previous is not None:
        for layout in previous.findChildren(QLayout):
            index=layout.indexOf(widget)
            if index>=0:
                item=layout.takeAt(index)
                if item is not None:shiboken6.delete(item)
                break

def reparent_widget(widget,parent):
    if widget.parentWidget() is parent:return
    detach_widget(widget)
    widget.setParent(parent)

def add_widget(layout,widget,*args):
    # addWidget also implicitly removes a widget from its former layout.
    # Use the same ownership-safe path, including moves within one parent.
    detach_widget(widget)
    layout.addWidget(widget,*args)

def install(w):
    old=w.takeCentralWidget();base=QWidget();parking=QWidget(base);parking.hide()
    # Move roots, not every child: config editors and tab internals keep ownership/connections.
    roots=[w.port,w.refresh_button,w.connect_button,w.observe_button,w.observe_auto,w.open_button,
        w.analysis_button,w.summary,w.note,w.config_toggle,w.legacy_mode,w.workload_deadline,w.workload_preset,
        w.workload_run,w.workload_report,w.workload_hint,w.config_panel,w.draft,w.result_table,
        w.scene_banner,w.wave,w.tabs,w.progress,w.cancel_button,*w.buttons.values()]
    for widget in roots:reparent_widget(widget,parking);widget.hide()
    pages=[]
    while w.tabs.count():
        page=w.tabs.widget(0);w.tabs.removeTab(0);reparent_widget(page,parking);pages.append(page)
    w.setCentralWidget(base);w._compact_parking=parking;w._old_central=old
    outer=QVBoxLayout(base);outer.setContentsMargins(24,18,24,10);outer.setSpacing(16)
    w.setStyleSheet(STYLE);w.resize(1500,1000);w.setMinimumSize(1280,870)
    head=QHBoxLayout();brand=QVBoxLayout();brand.setSpacing(4)
    brand.addWidget(label('时序诊断工作台','section',23));brand.addWidget(label('TIMING SENTINEL  /  A7'))
    head.addLayout(brand);head.addStretch();w.connection_badge=label('●  未连接');head.addWidget(w.connection_badge)
    w.port.setFixedWidth(240);add_widget(head,w.port);add_widget(head,w.refresh_button);add_widget(head,w.connect_button)
    for widget in (w.port,w.refresh_button,w.connect_button):widget.show()
    w.port.setObjectName('device_port');w.connect_button.setObjectName('connect_device')
    w.workload_deadline.setObjectName('workload_deadline');w.workload_run.setObjectName('run_workload')
    w.buttons['read'].setObjectName('read_frozen');w.buttons['save'].setObjectName('save_record')
    outer.addLayout(head)
    body=QHBoxLayout();body.setSpacing(20);outer.addLayout(body,1)
    sidebar=QVBoxLayout();sidebar.setSpacing(14);body.addLayout(sidebar)
    settings=card();settings.setFixedWidth(240);s=QVBoxLayout(settings);s.setContentsMargins(18,18,18,18);s.setSpacing(13)
    s.addWidget(label('实验设置','section'));s.addWidget(label('下一次实验的判定模式'));add_widget(s,w.workload_deadline)
    s.addWidget(label('三档负载  ×  各 2 次','section'))
    hint=label('请先连接设备，\n或打开已保存的记录。');hint.setWordWrap(True);hint.setFixedHeight(44)
    w.next_step_hint=hint;s.addWidget(hint)
    w.workload_run.setObjectName('run_workload');w.workload_run.setText('开始实验');w.workload_run.setMinimumHeight(44);add_widget(s,w.workload_run)
    add_widget(s,w.workload_report);w.workload_report.setText('查看实验报告')
    for widget in (w.workload_deadline,w.workload_run,w.workload_report):widget.show()
    sidebar.addWidget(settings)
    records=card();records.setFixedWidth(240);r=QVBoxLayout(records);r.setContentsMargins(18,18,18,18);r.setSpacing(10)
    r.addWidget(label('记录管理','section'))
    for widget,title in ((w.open_button,'打开记录'),(w.buttons['read'],'读取板端记录'),
        (w.buttons['save'],'保存记录'),(w.buttons['export'],'导出数据')):
        widget.setText(title);widget.setObjectName('quiet');add_widget(r,widget);widget.show()
    w.storage_button=QPushButton('保存位置');w.storage_button.setObjectName('capture_directory')
    w.storage_button.clicked.connect(w.choose_capture_root)
    storage_row=QHBoxLayout();add_widget(storage_row,w.storage_button)
    w.record_folder_button=QPushButton('打开目录');w.record_folder_button.setObjectName('open_record_folder')
    w.record_folder_button.clicked.connect(w.open_record_folder);storage_row.addWidget(w.record_folder_button);r.addLayout(storage_row)
    w.storage_label=label('保存位置待检查');w.storage_label.setWordWrap(True);r.addWidget(w.storage_label)
    sidebar.addWidget(records)
    w.config_toggle.setText('高级配置与诊断');add_widget(sidebar,w.config_toggle);w.config_toggle.show()
    sidebar.addStretch(1);w.compact_save_state=label('');w.compact_save_state.setWordWrap(True);sidebar.addWidget(w.compact_save_state)
    main=QVBoxLayout();main.setSpacing(14);body.addLayout(main,1)
    metrics=QHBoxLayout();metrics.setSpacing(12);w.metric_labels={}
    for key,title in (('result','本次判定'),('gpio','GPIO 响应时间'),('spi','SPI 响应时间')):
        box=card();b=QVBoxLayout(box);b.setContentsMargins(18,13,18,13);b.setSpacing(4)
        b.addWidget(label(title));value=label('—','section',30);sub=label('等待数据');b.addWidget(value);b.addWidget(sub)
        w.metric_labels[key]=(value,sub);metrics.addWidget(box,1)
    w.device_diagnostics=card();db=QVBoxLayout(w.device_diagnostics)
    db.setContentsMargins(15,12,15,12);db.setSpacing(5)
    diagnostic_head=QHBoxLayout();diagnostic_head.addWidget(label('设备与诊断','section'));diagnostic_head.addStretch()
    w.observe_button.setText('刷新');w.observe_button.setToolTip('读取板端状态；不开始实验、不释放记录')
    w.observe_button.setStyleSheet('font-size:12px;padding:4px 8px;')
    add_widget(diagnostic_head,w.observe_button);w.observe_button.show();db.addLayout(diagnostic_head)
    w.diagnostic_fields={}
    for key in ('board','record','transfer'):
        item=label('');item.setWordWrap(True);db.addWidget(item);w.diagnostic_fields[key]=item
    metrics.addWidget(w.device_diagnostics,1)
    main.addLayout(metrics)
    chart=card();c=QVBoxLayout(chart);c.setContentsMargins(16,13,16,10);c.setSpacing(7)
    charthead=QHBoxLayout();charthead.addWidget(label('时序波形','section'));charthead.addStretch()
    # Keep direct control references. Reparenting the button deletes its old
    # QWidgetItem; do not create borrowed layout-item wrappers while finding it.
    full=w.wave.full_button
    for widget,title in ((full,'全览'),(w.wave.response_zoom,'A → 响应'),(w.wave.spi_zoom,'SPI 细节')):
        reparent_widget(widget,chart);widget.setText(title);add_widget(charthead,widget);widget.show()
    for widget in (w.wave.measurement,w.wave.locate,w.wave.reference_combo,w.wave.unit_combo,w.wave.active_only):widget.hide()
    c.addLayout(charthead);w.compact_record_hint=label('连接设备或打开记录，查看波形');c.addWidget(w.compact_record_hint)
    reparent_widget(w.wave,chart);add_widget(c,w.wave,1);w.wave.show()
    w.empty_wave_hint=QLabel('连接设备或打开记录，开始查看波形',w.wave.plot)
    w.empty_wave_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    w.empty_wave_hint.setStyleSheet('background:transparent;color:#8b9db3;font-size:16px')
    w.empty_wave_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    wavefoot=QHBoxLayout();w.wave.compact_measurement=label('游标 Δt  —');wavefoot.addWidget(w.wave.compact_measurement);wavefoot.addStretch()
    for widget in (w.wave.reference_combo,w.wave.unit_combo,w.wave.active_only):
        reparent_widget(widget,chart);add_widget(wavefoot,widget);widget.show()
    c.addLayout(wavefoot)
    detail=card();d=QVBoxLayout(detail);d.setContentsMargins(0,0,0,0);add_widget(d,w.tabs);w.tabs.show()
    w.compact_events=QTableWidget(0,3);w.compact_events.setHorizontalHeaderLabels(['相对时间 / ms','事件','说明'])
    w.compact_events.verticalHeader().hide();w.compact_events.verticalHeader().setDefaultSectionSize(36)
    w.compact_events.setShowGrid(False);w.compact_events.setAlternatingRowColors(True)
    w.compact_events.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    w.compact_events.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    w.compact_events.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
    w.compact_events.setColumnWidth(0,160);w.compact_events.setColumnWidth(1,150)
    w.tabs.addTab(w.compact_events,'关键事件')
    rulepage=QWidget();rl=QVBoxLayout(rulepage);add_widget(rl,w.result_table);w.result_table.show()
    w.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    w.result_table.setMinimumHeight(180);w.result_table.setMaximumHeight(16777215)
    w.result_table.verticalHeader().setDefaultSectionSize(34);w.tabs.addTab(rulepage,'规则详情')
    configpage=QWidget();cf=QVBoxLayout(configpage);add_widget(cf,w.config_panel,1);w.config_panel.show()
    w.config_panel.setMinimumHeight(150);w.config_panel.setMaximumHeight(16777215)
    w.config_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    for col in range(12):w.config_table.setColumnWidth(col,100 if col!=11 else 130)
    for widget in (w.workload_preset,w.buttons['defaults'],w.buttons['configure'],w.buttons['arm'],w.buttons['stop'],w.buttons['release']):
        widget.show()
    add_widget(cf,w.draft);w.draft.show()
    for group in ((w.workload_preset,w.buttons['defaults'],w.buttons['configure']),
                  (w.buttons['arm'],w.buttons['stop'],w.buttons['release'])):
        row=QHBoxLayout()
        for widget in group:add_widget(row,widget)
        cf.addLayout(row)
    w.tabs.addTab(configpage,'高级配置');w.compact_config_index=2
    diagnostic=QWidget();dg=QVBoxLayout(diagnostic);tools=QHBoxLayout()
    for widget in (w.observe_auto,w.wave.locate):add_widget(tools,widget);widget.show()
    tools.addStretch();dg.addLayout(tools)
    # Technical originals stay alive for handlers/tooltips; the summary is now always visible above.
    for widget in (w.summary,w.scene_banner,w.note,w.workload_hint,w.wave.measurement):
        reparent_widget(widget,parking);widget.hide()
    add_widget(dg,w.detail,1);w.detail.show();w.tabs.addTab(diagnostic,'原始元数据')
    w.tabs.addTab(pages[1],'完整事件');w.tabs.addTab(pages[3],'批量统计');w.analysis_index=5
    logs=QWidget();ll=QVBoxLayout(logs);add_widget(ll,w.analysis_button);w.analysis_button.show()
    add_widget(ll,w.legacy_mode);w.legacy_mode.show()
    add_widget(ll,w.buttons['batch']);w.buttons['batch'].hide();add_widget(ll,w.log,1);w.log.show()
    w.buttons['batch'].setToolTip('仅在明确启用旧固件调试模式后可用；不会自动确认或切换STM32固件。')
    w.tabs.addTab(logs,'通信日志')
    w.compact_advanced_indices=(2,3,4,6)
    for index in w.compact_advanced_indices:w.tabs.setTabVisible(index,False)
    w.data_splitter=QSplitter(Qt.Orientation.Vertical);w.data_splitter.setChildrenCollapsible(False);w.data_splitter.setHandleWidth(8)
    w.data_splitter.addWidget(chart);w.data_splitter.addWidget(detail);w.data_splitter.setSizes([365,240]);main.addWidget(w.data_splitter,1)
    w.tabs.setMinimumHeight(220);w.tabs.setCurrentIndex(0)
    operation=QHBoxLayout();w.compact_state=label('未连接')
    w.compact_state.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
    w.compact_state.setMinimumWidth(0);operation.addWidget(w.compact_state,1)
    w.progress.setFixedWidth(170);w.progress.setTextVisible(False);add_widget(operation,w.progress);add_widget(operation,w.cancel_button)
    w.cancel_button.setText('停止任务');w.progress.show();w.cancel_button.show();main.addLayout(operation)
    # Keep the row's footprint when idle; repeated STATUS workers must not resize the chart.
    for widget in (w.progress,w.cancel_button):
        policy=widget.sizePolicy();policy.setRetainSizeWhenHidden(True);widget.setSizePolicy(policy)
    footer=QHBoxLayout();w.compact_source=label('未载入记录');footer.addWidget(w.compact_source);footer.addStretch()
    footer.addWidget(label('50 MHz标称时基 · 20 ns/tick'));outer.addLayout(footer)
    w.compact_events.cellClicked.connect(lambda row,col:focus_event(w,row))
    w.compact_ready=True;w._compact_cap=None
    old.deleteLater()
    update(w)

def update(w):
    if not getattr(w,'compact_ready',False):return
    storage_text={None:'保存位置待检查',True:'保存位置已检查',False:'保存位置不可用 · 请更换'}[w.storage_ready]
    w.storage_label.setText(storage_text if w.storage_ready is False else '下次保存目录：\n'+w.root.name)
    w.storage_label.setToolTip(str(w.root)+('\n'+w.storage_error if w.storage_error else '\n新文件与替换写入预检通过不保证后续磁盘空间。'))
    w.storage_label.setStyleSheet('font-size:12px;color:'+('#f3bc67' if w.storage_ready is False else '#8b9db3'))
    w.storage_button.setToolTip(str(w.root)+'\n仅影响后续保存位置，不搬移或删除旧记录；显式 --root 启动参数优先。')
    connected=w.client is not None
    visual_busy=w.busy and not getattr(w,'background_observe',False)
    states={'DISCONNECTED':'未连接','IDLE':'设备就绪','PREFILL':'正在预填充','ARMED':'等待信号',
        'POST_TRIGGER':'正在捕获','FROZEN':'采集已完成 · 板端记录保留','UNKNOWN':'状态未确认 · 请检查连接'}
    w.connection_badge.setText('●  '+('已连接' if connected else '未连接'))
    w.connect_button.setText('断开设备' if connected else '连接设备')
    text=w.summary.text()
    state='操作中 · '+text if visual_busy else (text if text.startswith(('未完成','传输中断')) else states.get(w.device_state,w.device_state))
    if '旧记录' in text:state+=' · 当前波形为旧记录'
    if w.legacy_debug_enabled:state+=' · 旧固件调试模式（身份未自动确认）'
    w.compact_state.setText(state);w.compact_state.setToolTip(text)
    board=w.diagnostic_fields['board']
    uncertain=connected and (w.device_state=='UNKNOWN' or bool(getattr(w.client,'uncertain',False)))
    board_text=('状态未确认' if uncertain else states.get(w.device_state,w.device_state)) if connected else '未确认（未连接）'
    if connected and text.startswith(('未完成','传输中断')):board_text='操作未完成 · 状态待确认'
    elif connected and visual_busy:board_text='操作中 · '+board_text
    board.setText('板端：'+board_text);board.setToolTip(text+'\n未连接时不把旧记录的FROZEN当作实时板端状态。')
    board.setStyleSheet('font-size:12px;font-weight:600;color:'+('#f3bc67' if uncertain or (connected and text.startswith(('未完成','传输中断'))) else '#e9f0f7'))
    w.cancel_button.setVisible(visual_busy);w.progress.setVisible(visual_busy)
    cap=w.capture
    protected=cap is not None and not w.saved and not w.replay
    current=bool(cap is not None and cap.complete and not w.replay and connected
        and cap.metadata['session_id']==w.client.session_id and cap.metadata['config_id']==w.client.config_id)
    action,hint=next_step(connected=connected,state=w.device_state,uncertain=uncertain,
        protected=protected,current=current,has_capture=cap is not None,busy=visual_busy,storage_ready=w.storage_ready)
    if action=='workload' and w.workload_deadline.currentIndex()==2:
        hint='同次四规则波形对照。\n本模式不启用判定声光。'
    w.next_step_hint.setText(hint);w.next_step_hint.setToolTip(hint)
    choices={'connect':w.connect_button,'read':w.buttons['read'],'save':w.buttons['save'],
        'workload':w.workload_run,'storage':w.storage_button}
    for key,button in choices.items():
        recommended=key==action and button.isEnabled()
        if button.property('recommended')!=recommended:
            button.setProperty('recommended',recommended);button.style().unpolish(button);button.style().polish(button)
    path=w.capture_path
    can_locate=bool(cap is not None and w.saved and path is not None and path.is_file())
    w.record_folder_button.setEnabled(not w.busy and can_locate)
    w.record_folder_button.setToolTip(str(path.parent) if can_locate else '保存记录或打开已有文件后，可直接打开它所在的目录。')
    w.empty_wave_hint.setGeometry(w.wave.plot.rect());w.empty_wave_hint.setVisible(cap is None)
    w.wave.plot.getAxis('left').setVisible(cap is not None)
    for cursor in w.wave.cursors:cursor.setVisible(cap is not None)
    for i in range(w.port.count()):
        original=w.port.itemData(i,Qt.ItemDataRole.ToolTipRole) or w.port.itemText(i)
        w.port.setItemData(i,original,Qt.ItemDataRole.ToolTipRole)
        if 'CH340' in original:w.port.setItemText(i,str(w.port.itemData(i))+' · CH340')
        elif '蓝牙' in original:w.port.setItemText(i,str(w.port.itemData(i))+' · 蓝牙串口')
    if cap is None:
        w.diagnostic_fields['record'].setText('记录：未载入')
        w.diagnostic_fields['transfer'].setText('传输：等待记录')
        w.diagnostic_fields['transfer'].setStyleSheet('font-size:12px;color:#8b9db3')
        for key,(value,sub) in w.metric_labels.items():
            empty_title=('待读取记录' if connected and not uncertain and w.device_state=='FROZEN' else
                         '设备已就绪' if connected and not uncertain and w.device_state=='IDLE' else '等待采集')
            value.setText(empty_title if key=='result' else '—')
            sub.setText('读取记录后显示' if key!='result' and connected else '按左侧提示操作')
            value.setStyleSheet('font-size:30px;font-weight:600;color:#e9f0f7')
        w.empty_wave_hint.setText(hint.replace('\n',''))
        w.compact_source.setText('未载入记录');w.compact_record_hint.setText('尚未载入记录')
        w.compact_save_state.setText('');w.compact_events.setRowCount(0);return
    m=cap.metadata;enabled=[(i,r,m['config']['rules'][i]) for i,r in enumerate(m['rules']) if m['enabled_mask']&(1<<i)]
    origin='实物' if m['origin']=='hardware' else '模拟'
    record=w.diagnostic_fields['record'];record.setText(f'记录：{origin}{"回放" if w.replay else ""} · S{m["session_id"]} / C{m["config_id"]}')
    record.setToolTip('当前显示记录的 session / config，不代表当前板端实时身份。\n'+w.scene_banner.text())
    crc=m.get('crc_errors');retries=m.get('transport_retries')
    transfer=w.diagnostic_fields['transfer']
    transfer.setText(('完整' if cap.complete else '数据不完整')+f' · CRC {crc if crc is not None else "—"} · 重试 {retries if retries is not None else "—"}')
    warning=not cap.complete or (isinstance(crc,int) and crc>0) or (isinstance(retries,int) and retries>0)
    transfer.setStyleSheet('font-size:12px;font-weight:600;color:'+('#f3bc67' if warning else '#8b9db3'))
    transfer.setToolTip('当前记录的传输计数，不是连接后的实时累计值；缺失计数显示—，不冒充0。\n时间按记录内标称时基换算。')
    results=[r['result'] for _,r,_ in enabled]
    if not cap.complete:verdict='数据不完整';color='#f3bc67'
    elif not results:verdict='无启用规则';color='#8b9db3'
    elif any(x=='TIMEOUT' for x in results):verdict='响应超期';color='#f3bc67'
    elif any(x=='EARLY' for x in results):verdict='响应提前';color='#f3bc67'
    elif any(x not in ('PASS','NONE') for x in results):verdict='未完成判定';color='#f3bc67'
    elif any(x=='NONE' for x in results):verdict='等待判定';color='#8b9db3'
    elif not (w.scene_info or {}).get('verified'):verdict='判定待核验';color='#f3bc67'
    else:verdict='全部通过';color='#63d8ad'
    value,sub=w.metric_labels['result'];value.setText(verdict);value.setStyleSheet(f'font-size:30px;font-weight:600;color:{color}')
    sub.setText(f'{len(enabled)} 条启用规则'+(' · 部分记录不可完整重判' if not cap.complete else ' · 模拟判定' if m['origin']!='hardware' else ''))
    expectation=demo_expectation(cap,w.scene_info or {})
    if expectation:
        sub.setText(f'{len(enabled)} 条 · '+('符合演示预期' if expectation['matches'] else '与演示预期不同'))
        sub.setToolTip(expectation['detail']+f'\n{len(enabled)} 条启用规则；大字仍显示真实硬件判定。')
    else:sub.setToolTip('响应超期表示超出本记录的规则期限，不等于采集或连接失败。\n自定义规则不推断演示预期。')
    for kind,key in ((0,'gpio'),(1,'spi')):
        entry=next(((i,r,c) for i,r,c in enabled if c['target_kind']==kind),None)
        value,sub=w.metric_labels[key]
        if not entry:value.setText('—');sub.setText('未启用对应规则');continue
        i,r,c=entry;a=r['a_tick'];b=r['observed_b_tick']
        delay=relative_ms(b,a,m['clock_hz']) if a is not None and b is not None else None
        value.setText('—' if delay is None else f'{delay:.3f} ms')
        sub.setText(f'R{i} · 截止 {c["max_ticks"]/50000:g} ms'+(' · 已迟到' if r['result']=='TIMEOUT' and b is not None else ''))
        value.setToolTip('无已观察B，不能填0' if delay is None else f'元数据观测值 {delay:.6f} ms；样点完整性见详情')
    identity=w.scene_info or {};task=identity.get('workload')
    source=('历史回放' if w.replay else ('实物记录' if m['origin']=='hardware' else '模拟数据'))
    if m['origin']!='hardware':source='模拟数据'+('回放' if w.replay else '')
    title=(f'{task["name"]} · 任务 #{task["id"]}' if task else identity.get('name','未识别场景'))
    deadlines=sorted({c['max_ticks']/50000 for _,_,c in enabled})
    window=' / '.join(f'{x:g}' for x in deadlines)
    w.compact_record_hint.setText(f'{title} · {source} · 记录期限 {window} ms'+(' · 数据不完整' if not cap.complete else ''))
    w.compact_source.setText(f'{source} · session {m["session_id"]} / config {m["config_id"]}'+(' · 当前操作不改变此旧记录' if visual_busy else ''))
    w.compact_save_state.setText('记录已保存' if w.saved else ('历史回放，不控制板端' if w.replay else '当前记录尚未保存\n请先保存，再释放板端。'))
    if w.saved and path is not None:
        w.compact_save_state.setText('记录已保存 · 点击“打开目录”查看')
        w.compact_save_state.setToolTip(str(path))
    else:w.compact_save_state.setToolTip('')
    w.compact_save_state.setStyleSheet('color:'+('#8b9db3' if w.saved or w.replay else '#f3bc67'))
    if w._compact_cap is not cap or w._compact_reference!=w.wave.reference:
        is_new=w._compact_cap is not cap
        w._compact_cap=cap;w._compact_reference=w.wave.reference;populate_events(w)
        if is_new and cap.complete and w.wave.response_zoom.isEnabled():w.wave.zoom_response()

def populate_events(w):
    if w.capture is None:return
    m=w.capture.metadata;cfg=m['config'];selected=[]
    gpio_edges=[(r['target_channel'],r['edge']) for i,r in enumerate(cfg['rules']) if m['enabled_mask']&(1<<i) and not r['target_kind']]
    for e in w.shown_events:
        matched_gpio=any((e['rise'] if edge==0 else e['fall'])&(1<<ch) for ch,edge in gpio_edges)
        if e['accepted_mask'] or e['spi'] is not None or e['spi_error'] or e['uart_error'] or e['overlap_mask'] or any(r!='NONE' for r in e['results']) or matched_gpio:selected.append(e)
    w._compact_events=selected;w.compact_events.setRowCount(len(selected))
    for row,e in enumerate(selected):
        verdicts=[f'R{i} {r}' for i,r in enumerate(e['results']) if r!='NONE']
        matched_gpio=any((e['rise'] if edge==0 else e['fall'])&(1<<ch) for ch,edge in gpio_edges)
        title='UART 命令' if e['accepted_mask'] else 'SPI 字节' if e['spi'] is not None else 'GPIO 响应' if matched_gpio else '规则判定' if verdicts else '协议异常'
        note=event_description(e,cfg['uart_channel'])
        if verdicts and not all(v in note for v in verdicts):note+=' · '+' / '.join(verdicts)
        for col,text in enumerate((f'{relative_ms(e["tick"],w.wave.reference,m["clock_hz"]):.6f}',title,note)):
            item=w.compact_events.item(row,col)
            if item is None:item=QTableWidgetItem();w.compact_events.setItem(row,col,item)
            item.setText(text)
            item.setForeground(QColor('#f3bc67' if any(r in ('EARLY','TIMEOUT') for r in e['results']) else '#e9f0f7'))

def focus_event(w,row):
    if row>=len(getattr(w,'_compact_events',[])):return
    e=w._compact_events[row]
    if w.wave._tick_visible(e['tick']):
        pos=w.wave._pos(e['tick']);w.wave.cursors[0].setValue(pos);w.wave._range(pos-.1,pos+.1)

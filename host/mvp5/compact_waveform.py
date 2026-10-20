"""Display-only simplification. Original samples, time reference and zoom stay unchanged."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
import pyqtgraph as pg
from mvp5.waveform import FourWaveform


class CompactWaveform(FourWaveform):
    def __init__(self):
        super().__init__()
        self.plot.setBackground('#131e2c')
        self.plot.setMenuEnabled(False)
        colors=('#68cef3','#efbf70','#a897df','#6ccebd','#8aafd9','#82aa91','#cfb888','#a5bcdf')
        for c,color in zip(self.curves,colors):c.setPen(pg.mkPen(color,width=1.4))
        for name in ('left','bottom'):
            axis=self.plot.getAxis(name)
            axis.setPen(pg.mkPen('#33475e'));axis.setTextPen(pg.mkPen('#a8b8cc'))
            axis.setTickFont(QFont('Microsoft YaHei UI',9))
        self.plot.showGrid(x=True,y=False,alpha=.10)
        self.plot.getAxis('left').setWidth(120)

    def _tag(self,text,tick,y,color,anchor=(0,0)):
        if not self._tick_visible(tick):return
        item=pg.TextItem(text,color=color,anchor=anchor,fill=pg.mkBrush(19,30,44,235))
        item.setFont(QFont('Microsoft YaHei UI',9));item.setPos(self._pos(tick),y);self._add(item)

    def _add_rule_guides(self,meta):
        enabled=[(i,r,meta['config']['rules'][i]) for i,r in enumerate(meta['rules']) if meta['enabled_mask']&(1<<i)]
        starts={r['a_tick'] for _,r,_ in enabled if r['a_tick'] is not None}
        if len(starts)==1:
            a=next(iter(starts))
            if self._tick_visible(a):
                self._add(pg.InfiniteLine(pos=self._pos(a),pen=pg.mkPen('#70879f',style=Qt.PenStyle.DashLine)))
                self._tag('A · UART命令',a,15.45,'#a5dff0')
        gpio=next(((i,r,c) for i,r,c in enabled if not c['target_kind']),None)
        if gpio:
            i,r,c=gpio;a=r['a_tick'];deadline=r['deadline_tick']
            if a is not None and len(self.x_ms):
                lo=self._pos(a)+c['min_ticks']/50000;hi=self._pos(a)+c['max_ticks']/50000
                if hi>=self.x_ms[0] and lo<=self.x_ms[-1]:
                    region=pg.LinearRegionItem((max(lo,float(self.x_ms[0])),min(hi,float(self.x_ms[-1]))),movable=False,
                        brush=pg.mkBrush(41,134,130,20),pen=pg.mkPen(None));region.setZValue(-10);self._add(region)
            if self._tick_visible(deadline):
                self._add(pg.InfiniteLine(pos=self._pos(deadline),pen=pg.mkPen('#f3bc67',style=Qt.PenStyle.DashLine)))
                self._tag(f'R{i} 截止 {c["max_ticks"]/50000:g} ms',deadline,13.25,'#f3bc67',(1,0))
            if self._tick_visible(r['observed_b_tick']):
                self._tag('GPIO响应'+(' · 迟到' if r['result']=='TIMEOUT' else ''),r['observed_b_tick'],14-2*c['target_channel']-.35,'#efbf70',(1,0))
        # Preserve every other rule's verdict/trigger in details; avoid stacked labels at one tick.
        for i,r,c in enabled:
            if r['result'] in ('EARLY','CANCELLED_DISARM','INCOMPLETE_CAPTURE_END'):
                self._tag(f'R{i} {r["result"]}',r['result_tick'],10.8-i*.5,'#f3bc67')

    def _add_protocol_tags(self,meta):
        # Raw decoding and all events remain in the details table. Overview avoids nibble labels.
        self.protocol_tags=[]

    def update_measurement(self):
        super().update_measurement()
        if hasattr(self,'compact_measurement') and self.capture:
            us=self.unit_combo.currentData()=='us';factor=1000 if us else 1;unit='μs' if us else 'ms'
            distance=abs(self.cursors[1].value()-self.cursors[0].value())*factor
            self.compact_measurement.setText(f'游标 Δt  {distance:.6f} {unit}')

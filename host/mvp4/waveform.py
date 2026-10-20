import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from app.ui.waveform_view import WaveformView,COLORS
from app.models import delta_tick
class FourWaveform(WaveformView):
 def set_capture(self,cap):
  self.clear();self.capture=cap;m=cap.metadata;x=cap.times_ms();values=np.frombuffer(cap.samples,dtype=np.uint8)
  for ch,curve in enumerate(self.curves):
   bits=(values>>ch)&1
   if not len(bits):continue
   changes=np.flatnonzero(bits[1:]!=bits[:-1])+1;xx=[x[0]];yy=[float(bits[0])]
   for i in changes:xx.extend((x[i],x[i]));yy.extend((float(bits[i-1]),float(bits[i])))
   xx.append(x[-1]);yy.append(float(bits[-1]));curve.setData(np.array(xx),14-ch*2+np.array(yy)*.7)
  cfg=m["config"];labels=[]
  for ch in range(8):
   rids=[str(i) for i,r in enumerate(cfg["rules"]) if cfg["enabled_mask"]&(1<<i) and r["target_channel"]==ch]
   labels.append("CH"+str(ch)+(" · UART" if ch==cfg["uart_channel"] else " · R"+",".join(rids) if rids else ""))
  self.plot.getAxis("left").setTicks([[(14-2*c+.35,labels[c]) for c in range(8)]])
  ref=m["decision_timestamp"] if m["decision_timestamp"] is not None else m["first_sample_timestamp"]
  self.plot.setLabel("bottom","相对首个主判定 / 首采样",units="ms")
  used=[]
  for r,entry in enumerate(m["rules"]):
   for field,label in (("a_tick","A"),("b_tick","B"),("result_tick",entry["result"])):
    tick=entry[field]
    if tick is None:continue
    pos=delta_tick(tick,ref)/50000
    if not len(x) or not x[0]<=pos<=x[-1]:continue
    neighbours=sum(abs(pos-v)<max(1e-6,(x[-1]-x[0])*.04) for v in used);used.append(pos)
    line=pg.InfiniteLine(pos=pos,pen=pg.mkPen(COLORS[r],style=Qt.PenStyle.DashLine),
     label="R"+str(r)+" "+label,labelOpts={"position":max(.05,.97-.08*neighbours),"color":COLORS[r]})
    self.plot.addItem(line);self.markers.append(line)
  self.locate.setEnabled(bool(len(x) and m["trigger_valid"] and x[0]<=0<=x[-1]))
  self.fit();self.update_measurement()


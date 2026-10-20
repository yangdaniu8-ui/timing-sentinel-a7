"""Four real transactions; compare sampled pins with event/metadata evidence."""
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
from app.models import delta_tick,TICK_MASK
from app.services.record_store import atomic_output
from mvp4.models import Config,unpack_events,require
from mvp4.store import save_record,load_record,blob,digest

SCENES=("normal","early","late","missing")
MATRIX={"normal":["PASS","TIMEOUT","PASS","PASS"],"early":["EARLY","PASS","PASS","EARLY"],
 "late":["TIMEOUT","TIMEOUT","PASS","PASS"],"missing":["TIMEOUT"]*4}
def decode_uart(samples,first,divisor,period):
 frames=[];i=1
 while i<len(samples):
  if samples[i-1]&1 and not samples[i]&1:
   indices=[i+(period//2+k*period)//divisor for k in range(10)]
   if indices[-1]>=len(samples):break
   if samples[indices[0]]&1 or not samples[indices[9]]&1:i+=1;continue
   value=sum((samples[indices[k+1]]&1)<<k for k in range(8))
   frames.append(dict(value=value,nominal_stop_center=(first+i*divisor+period//2+9*period)&TICK_MASK))
   i+=10*period//divisor
  else:i+=1
 return frames
def verify_capture(cap):
 cap.validate();m=cap.metadata;cfg=Config.from_dict(m["config"]);div=cfg.divisor
 require(cap.complete and m["origin"]=="hardware","必须完整实物记录")
 require(m["event_overwrites"]==0,"事件环已覆盖，保留记录但不能通过完整性验收")
 events=unpack_events(cap.events)
 require(not any(e["uart_error"] or e["overlap_mask"] for e in events),"UART/重叠异常：本场景非单事务")
 frames=decode_uart(bytes((v>>cfg.uart_channel)&1 for v in cap.samples),m["first_sample_timestamp"],div,50000000//cfg.baud)
 markers=[SCENES[f["value"]-0xd0] for f in frames if 0xd0<=f["value"]<=0xd3]
 require(len(markers)==1,"完整波形未译码出唯一STM32场景标记")
 checks=[]
 for r,entry in enumerate(m["rules"]):
  if not cfg.enabled_mask&(1<<r):continue
  rule=cfg.rules[r];a=entry["a_tick"];require(a is not None and entry["counts"]["accepted"]==1,"需要单次A事务")
  require(any(f["value"]&rule.mask==rule.value and 0<=delta_tick(f["nominal_stop_center"],a)<div for f in frames),"样点UART无法复现A")
  edges=[(m["first_sample_timestamp"]+i*div)&TICK_MASK for i in range(1,len(cap.samples))
   if ((cap.samples[i-1]>>rule.target_channel)&1,(cap.samples[i]>>rule.target_channel)&1)==((1,0) if rule.edge else (0,1))]
  observed=entry["observed_b_tick"]
  if observed is not None:require(any(0<=delta_tick(t,observed)<div for t in edges),"样点不能复现观察边沿")
  require(sum(bool(e["accepted_mask"]&(1<<r)) and e["tick"]==a for e in events)==1,"A事件数量/时间错")
  if entry["result"]!="INCOMPLETE_CAPTURE_END":
   require(sum(e["results"][r]==entry["result"] and e["tick"]==entry["result_tick"] for e in events)==1,"终端结果事件错")
  checks.append(dict(rule_id=r,result=entry["result"],delay_ms=delta_tick(entry["b_tick"],a)/50000 if entry["b_tick"] is not None else None))
 if cfg.to_bytes()==Config().to_bytes():
  require([r["result"] for r in m["rules"]]==MATRIX[markers[0]],"默认四规则矩阵错误")
 return dict(passed=True,scene=markers[0],rules=checks,samples=len(cap.samples),events=len(events),
  waveform_event_metadata_consistent=True,independently_calibrated=False)
def run_four(client,directory,progress=None,message=None):
 directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
 progress=progress or (lambda a,b:None);message=message or (lambda s:None)
 report=dict(schema=1,generated_utc=datetime.now(timezone.utc).isoformat(),passed=False,
  source="external_stm32",independent_instrument_verified=False,cases=[])
 capture=None;seen=set()
 try:
  require(client.status()["state"]=="IDLE","批量验收必须从IDLE开始，先保存并释放现有冻结记录")
  client.configure(Config())
  for i in range(4):
   message("真实事务 "+str(i+1)+"/4：四规则并行，读取65536样点")
   client.arm();client.wait_frozen(3)
   capture=client.read_capture(lambda a,b:progress(i*100+int(a*100/max(b,1)),400))
   name="round_"+str(i+1)+"_s"+str(client.session_id)+".tscap";path=directory/name
   save_record(capture,path) # Save even partial before any classification/release.
   require(load_record(path)==capture,"记录回放不一致")
   checked=verify_capture(capture);require(checked["scene"] not in seen,"场景重复")
   seen.add(checked["scene"]);report["cases"].append(dict(file=name,sha256=digest(path.read_bytes()),verification=checked))
   if i<3:client.release() # Last saved record remains FROZEN for GUI inspection.
  require(seen==set(SCENES),"四个真实场景不齐");report["passed"]=True
 except Exception as exc:
  report["error"]=str(exc);raise
 finally:
  with atomic_output(directory/"experiment.json") as temp:temp.write_bytes(blob(report))
 return dict(report=report,capture=capture,directory=str(directory),status=client.status())
def new_experiment_dir(root):return Path(root)/("four_"+datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid4().hex[:8])


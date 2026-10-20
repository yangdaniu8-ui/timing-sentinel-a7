"""Bounded schema2 records, atomic publication, verified roundtrip."""
import hashlib,json,zipfile
from pathlib import Path
from app.services.record_store import atomic_output
from mvp4.models import Capture, require

def blob(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2).encode("utf-8")
def digest(b):return hashlib.sha256(b).hexdigest()
def save_record(capture,path):
 capture.validate()
 files={"metadata.json":blob(capture.metadata),"samples.bin":capture.samples,"events.bin":capture.events}
 manifest=dict(schema_version=2,profile="mvp4",origin=capture.metadata["origin"],record_id=capture.metadata["record_id"],
  transfer_complete=capture.complete,files={n:dict(size=len(b),sha256=digest(b)) for n,b in files.items()})
 with atomic_output(path) as temp:
  with zipfile.ZipFile(temp,"w",zipfile.ZIP_DEFLATED) as z:
   z.writestr("manifest.json",blob(manifest))
   for n,b in files.items():z.writestr(n,b)
  reread=load_record(temp)
  require(blob(reread.metadata)==blob(capture.metadata) and reread.samples==capture.samples and reread.events==capture.events,"保存后回读不一致")
 return str(path)
def load_record(path):
 try:
  with zipfile.ZipFile(path) as z:
   infos=z.infolist();names=[x.filename for x in infos]
   require(len(names)==4 and set(names)=={"manifest.json","metadata.json","samples.bin","events.bin"},"记录清单非法；单规则旧记录请用旧版打开")
   require(sum(x.file_size for x in infos)<=1000000 and all(not x.flag_bits&1 for x in infos),"记录超限或加密")
   manifest=json.loads(z.read("manifest.json"))
   require(type(manifest["schema_version"]) is int and manifest["schema_version"]==2 and manifest["profile"]=="mvp4","schema/profile错误")
   require(set(manifest["files"])=={"metadata.json","samples.bin","events.bin"},"manifest清单错误")
   data={}
   for n,entry in manifest["files"].items():
    b=z.read(n);require(type(entry["size"]) is int and entry["size"]==len(b) and entry["sha256"]==digest(b),"哈希/长度错误")
    data[n]=b
   meta=json.loads(data["metadata.json"])
   from mvp4.models import Config
   meta["config"]=Config.from_dict(meta["config"]).as_dict()
   cap=Capture(meta,data["samples.bin"],data["events.bin"]).validate()
   require(manifest["origin"]==cap.metadata["origin"] and manifest["record_id"]==cap.metadata["record_id"]
    and type(manifest["transfer_complete"]) is bool and manifest["transfer_complete"]==cap.complete,"manifest/metadata不一致")
   return cap
 except (KeyError,TypeError,AttributeError,UnicodeError,zipfile.BadZipFile) as exc:raise ValueError("损坏记录："+str(exc)) from exc
def export_json(capture,path):
 with atomic_output(path) as temp:temp.write_bytes(blob(capture.metadata))


import copy,json,struct,tempfile,unittest,zipfile,time
from dataclasses import replace
from pathlib import Path
import sys
from unittest.mock import patch
from mvp4.models import Config,Rule,Capture,decode_meta,unpack_events
from mvp4.store import save_record,load_record
from mvp4.client import Client
from protocol.codec import Decoder
from protocol.messages import Command,Result
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/"scripts"))
from mvp4_protocol_cases import Oracle

def fixture():
 raw=bytearray(412);raw[:4]=bytes((4,2,255,15));struct.pack_into("<IIIIHHI",raw,4,1,1,50000000,64,5,0,0)
 struct.pack_into("<I",raw,46,0xffffffff);raw[28:34]=(1000).to_bytes(6,"little");raw[312:]=Config().to_bytes()
 m=decode_meta(bytes(raw));m.update(origin="mock",firmware="mock-test-only",validation_level="protocol_only_not_independently_verified",transfer_complete=True)
 return Capture(m,bytes([1])*64,b"").validate()
class OracleTransport:
 kind="mock";origin="mock";description="unit-test-only"
 def __init__(self):self.oracle=Oracle();self.decoder=Decoder();self.buffer=b""
 def write(self,raw):
  frames=self.decoder.feed(raw);assert len(frames)==1;self.buffer=self.oracle.handle(frames[0])
 def read(self):b=self.buffer;self.buffer=b"";return b
 def close(self):pass
class SchemaTests(unittest.TestCase):
 def test_config_length_roundtrip(self):
  c=Config();self.assertEqual(len(c.to_bytes()),100);self.assertEqual(Config.from_bytes(c.to_bytes()),c)
 def test_disabled_canonical(self):
  c=replace(Config(),enabled_mask=1);raw=c.to_bytes();self.assertEqual(raw[40:],bytes(60))
  self.assertEqual(Config.from_bytes(raw).to_bytes(),raw)
 def test_reject_no_rule(self):
  with self.assertRaises(ValueError):replace(Config(),enabled_mask=0).validate()
 def test_reject_same_uart(self):
  with self.assertRaises(ValueError):replace(Config(),rules=(Rule(target_channel=0),)*4).validate()
 def test_reject_reserved(self):
  raw=bytearray(Config().to_bytes());raw[25]=1
  with self.assertRaises(ValueError):Config.from_bytes(raw)
 def test_reject_disabled_nonzero(self):
  raw=bytearray(replace(Config(),enabled_mask=1).to_bytes());raw[40]=1
  with self.assertRaises(ValueError):Config.from_bytes(raw)
 def test_closed_interval_exact(self):
  cap=fixture();m=copy.deepcopy(cap.metadata);e=m["rules"][0];a=1100;b=a+100000
  e.update(flags=15,a_tick=a,b_tick=b,observed_b_tick=b,deadline_tick=b,result_tick=b,result="PASS")
  Capture(m,cap.samples,cap.events).validate()
  e["result"]="EARLY"
  with self.assertRaises(ValueError):Capture(m,cap.samples,cap.events).validate()
 def test_timeout_plus_one(self):
  cap=fixture();m=copy.deepcopy(cap.metadata);e=m["rules"][0];e.update(flags=9,a_tick=1100,deadline_tick=101100,result_tick=101101,result="TIMEOUT")
  Capture(m,cap.samples,b"").validate();e["result_tick"]-=1
  with self.assertRaises(ValueError):Capture(m,cap.samples,b"").validate()
 def test_incomplete_mask(self):
  cap=fixture();m=copy.deepcopy(cap.metadata);e=m["rules"][2];e.update(flags=9,a_tick=1100,deadline_tick=151100,result_tick=5000,result="INCOMPLETE_CAPTURE_END");m["incomplete_mask"]=4
  Capture(m,cap.samples,b"").validate();m["incomplete_mask"]=0
  with self.assertRaises(ValueError):Capture(m,cap.samples,b"").validate()
 def test_event_four_nibbles(self):
  word=(1<<82)|(2<<86)|(3<<90)|(4<<94)|(15<<102)|(2<<106)
  e=unpack_events(word.to_bytes(16,"little"))[0];self.assertEqual(e["results"],["PASS","EARLY","TIMEOUT","CANCELLED_DISARM"]);self.assertEqual(e["accepted_mask"],15)
 def test_bad_event_reserved(self):
  with self.assertRaises(ValueError):unpack_events((1<<112).to_bytes(16,"little"))
 def test_uuid_identity(self):
  cap=fixture();m=copy.deepcopy(cap.metadata);m["record_id"]="bad"
  with self.assertRaises(ValueError):Capture(m,cap.samples,b"").validate()
 def test_flag_consistency(self):
  cap=fixture();m=copy.deepcopy(cap.metadata);m["rules"][0]["flags"]=1
  with self.assertRaises(ValueError):Capture(m,cap.samples,b"").validate()
class StoreTests(unittest.TestCase):
 def test_full_roundtrip(self):
  with tempfile.TemporaryDirectory() as d:
   c=fixture();p=Path(d)/"x.tscap";save_record(c,p);self.assertEqual(load_record(p),c)
 def test_partial_roundtrip(self):
  with tempfile.TemporaryDirectory() as d:
   c=fixture();m=copy.deepcopy(c.metadata);m["transfer_complete"]=False;c=Capture(m,c.samples[:20],b"")
   p=Path(d)/"x.tscap";save_record(c,p);self.assertFalse(load_record(p).complete);self.assertEqual(load_record(p),c)
 def test_tamper(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"x.tscap";save_record(fixture(),p)
   with zipfile.ZipFile(p) as z:files={n:z.read(n) for n in z.namelist()}
   files["samples.bin"]=bytes(64)
   with zipfile.ZipFile(p,"w") as z:
    for n,b in files.items():z.writestr(n,b)
   with self.assertRaises(ValueError):load_record(p)
 def test_failure_preserves_destination(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"x.tscap";save_record(fixture(),p);original=p.read_bytes()
   with patch("mvp4.store.load_record",side_effect=ValueError("injected")):
    with self.assertRaises(ValueError):save_record(fixture(),p)
   self.assertEqual(p.read_bytes(),original)
class ClientTests(unittest.TestCase):
 def test_connect_profile4(self):
  c=Client(OracleTransport());r=c.connect();self.assertEqual(r["info"]["rules"],4);self.assertEqual(c.origin,"mock");self.assertTrue(c.can_capture)
 def test_four_rule_commit_readback(self):
  c=Client(OracleTransport());c.connect();cfg=Config();r=c.configure(cfg);self.assertEqual(r["config_id"],2);self.assertEqual(c.get_config(),cfg)
 def test_duplicate_commit_once(self):
  t=OracleTransport();c=Client(t);c.connect();c.configure(Config());self.assertEqual(t.oracle.config_id,2)
 def test_reject_profile1(self):
  t=OracleTransport();original=t.oracle.handle
  from protocol.codec import Frame,encode
  from protocol.messages import INFO,PREFIX
  def handler(req):
   if req.command==Command.INFO:return encode(Frame(req.command|128,req.request_id,PREFIX.pack(c.token,0)+INFO.pack(1,50000000,65536,8,1,1,15,1024,b"mvp1-a7-0.1.0")))
   return original(req)
  c=Client(t);t.oracle.handle=handler
  with self.assertRaises(Exception):c.connect()
  self.assertTrue(c.uncertain)
class GuiTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  from PySide6.QtWidgets import QApplication
  cls.app=QApplication.instance() or QApplication([])
 def setUp(self):
  from mvp4.ui import MainWindow
  self.window=MainWindow()
 def tearDown(self):
  self.window.busy=False;self.window.saved=True;self.window.close();self.window.deleteLater();self.app.processEvents()
 def test_four_widgets_defaults(self):
  w=self.window;self.assertEqual(w.config_table.rowCount(),4);self.assertEqual(w.read_draft(),Config());self.assertFalse(w.buttons["arm"].isEnabled())
 def test_draft_arm_gate(self):
  w=self.window;w.client=Client(OracleTransport());w.client.connect();w.applied=Config();w.device_state="IDLE";w.gate();self.assertTrue(w.buttons["arm"].isEnabled())
  w.editors[0][5].setText("0.8");w.gate();self.assertFalse(w.buttons["arm"].isEnabled())
 def test_saved_release_gate(self):
  w=self.window;w.client=Client(OracleTransport());w.client.connect();w.client.session_id=1;w.device_state="FROZEN";w.show_capture(fixture());w.gate()
  self.assertFalse(w.buttons["release"].isEnabled());w.saved=True;w.gate();self.assertTrue(w.buttons["release"].isEnabled())
  w.replay=True;w.gate();self.assertFalse(w.buttons["release"].isEnabled())
 def test_four_result_rows_and_wave(self):
  w=self.window;w.replay=True;w.show_capture(fixture());self.assertEqual(w.result_table.item(3,1).text(),"NONE")
  self.assertEqual(len(w.wave.curves),8);self.assertGreater(len(w.wave.curves[0].getData()[0]),0)
 def test_one_worker_serialization(self):
  w=self.window;w.busy=True;w.start(lambda t:1,lambda r:None);self.assertIsNone(w.task)
class BatchTests(unittest.TestCase):
 def fake_client(self):
  from unittest.mock import MagicMock
  c=MagicMock();c.session_id=1;c.status.return_value={"state":"IDLE","session_id":1,"config_id":1}
  c.read_capture.side_effect=[fixture() for _ in range(4)]
  return c
 def test_save_before_release_and_last_frozen(self):
  from mvp4.experiment import run_four,SCENES
  c=self.fake_client()
  with tempfile.TemporaryDirectory() as d,patch("mvp4.experiment.verify_capture",side_effect=[dict(passed=True,scene=s) for s in SCENES]):
   r=run_four(c,Path(d)/"run")
   self.assertTrue(r["report"]["passed"]);self.assertEqual(c.release.call_count,3)
   self.assertEqual(len(list((Path(d)/"run").glob("*.tscap"))),4)
 def test_failed_check_keeps_saved_evidence_no_release(self):
  from mvp4.experiment import run_four
  c=self.fake_client()
  with tempfile.TemporaryDirectory() as d,patch("mvp4.experiment.verify_capture",side_effect=ValueError("test failure")):
   with self.assertRaises(ValueError):run_four(c,Path(d)/"run")
   self.assertEqual(c.release.call_count,0);self.assertEqual(len(list((Path(d)/"run").glob("*.tscap"))),1)
   report=json.loads((Path(d)/"run"/"experiment.json").read_text());self.assertFalse(report["passed"])
 def test_preexisting_capture_not_discarded(self):
  from mvp4.experiment import run_four
  c=self.fake_client();c.status.return_value={"state":"FROZEN"}
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):run_four(c,Path(d)/"run")
   self.assertEqual(c.arm.call_count,0);self.assertEqual(c.release.call_count,0)
if __name__=="__main__":unittest.main()


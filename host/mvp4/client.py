from dataclasses import replace
import struct
import time
from app.services.device_client import DeviceClient, DeviceFailure, Cancelled
from protocol.messages import Command, INFO, State, READ
from mvp4.models import Config, Capture, COUNTERS, decode_meta, require, unpack_events


class Client(DeviceClient):
    @property
    def can_capture(self):
        return self.info is not None and self.info.get("profile") == "mvp4"

    def _connect(self):
        self._body(self.request(Command.SYNC),0,"SYNC")
        require(self.request(Command.PING,b"four-rules") == b"four-rules","PING不一致")
        info = INFO.unpack(self._body(self.request(Command.INFO),34,"INFO"))
        require(info[:8] == (4,50000000,65536,8,1,4,15,1024),"需要真正的四规则profile4固件，不能用单规则固件")
        firmware=info[8].rstrip(b"\0").decode("ascii")
        require(firmware == "mvp4-a7-0.1.0","四规则固件标识不匹配")
        self.info=dict(profile="mvp4",firmware=firmware,origin=self.origin,clock_hz=50000000,channels=8,rules=4,
                       divisor_mask=15,capture_supported=True,config_writable=True,transport=self.transport.kind,link=self.transport.description)
        config=self.get_config()
        return dict(info=self.info,status=self.status(),config=config.as_dict())

    def get_config(self):
        raw=self._body(self.request(Command.GET_CONFIG),104,"GET_CONFIG")
        self.config_id=int.from_bytes(raw[:4],"little")
        try:
            return Config.from_bytes(raw[4:])
        except ValueError:
            self.uncertain=True
            raise

    def configure(self,config):
        require(self.can_capture,"设备没有确认四规则能力")
        config.validate()
        self._body(self.request(Command.WRITE_CONFIG,config.to_bytes()),0,"WRITE_CONFIG")
        expected=int.from_bytes(self._body(self.request(Command.COMMIT),4,"COMMIT"),"little")
        actual=self.get_config()
        if actual.to_bytes()!=config.to_bytes() or self.config_id!=expected:
            self.uncertain=True
            raise DeviceFailure("四规则配置回读不一致")
        return dict(config_id=expected,config=actual.as_dict())

    def status(self):
        raw=self._body(self.request(Command.STATUS),126,"STATUS4")
        state,ready,session,cfg,mask,primary,trigger,reserved=struct.unpack("<BBIIBBBB",raw[:14])
        require(state in State._value2member_map_ and ready in (0,1) and bool(ready)==(state==State.ARMED)
                and 1<=mask<=15 and primary in (0,1,2,3,255) and trigger<=15 and reserved==0,"STATUS4非法")
        self.session_id=session
        counts=[dict(zip(COUNTERS,struct.unpack_from("<7I",raw,14+i*28))) for i in range(4)]
        return dict(state=State(state).name,ready=bool(ready),session_id=session,config_id=cfg,enabled_mask=mask,
                    primary_rule=None if primary==255 else primary,trigger_rule_mask=trigger,counts=counts)

    def read_capture(self,progress=None):
        progress=progress or (lambda a,b:None)
        try:
            meta=decode_meta(self.request(Command.META,struct.pack("<I",self.session_id)))
            meta.update(origin=self.origin,firmware=self.info["firmware"],transport=self.info["transport"],link=self.info["link"],
                        validation_level="protocol_only_not_independently_verified")
            require(meta["session_id"]==self.session_id and meta["config_id"]==self.config_id,"META4身份不一致")
            Capture(meta,b"",b"").validate()
        except (ValueError,TypeError,KeyError) as exc:
            self.uncertain=True
            raise DeviceFailure("META4错误："+str(exc)) from exc
        samples,events=bytearray(),bytearray()
        total=meta["valid_count"]+meta["event_count"]*16
        try:
            for cmd,target,size,chunk in ((Command.SAMPLES,samples,meta["valid_count"],1004),(Command.EVENTS,events,meta["event_count"]*16,992)):
                while len(target)<size:
                    offset=len(target)
                    body=self.request(cmd,READ.pack(self.session_id,offset,min(chunk,size-offset)))
                    require(len(body)>=10,"分片头过短")
                    session,start,count=READ.unpack(body[:10])
                    require(session==self.session_id and start==offset and 0<count<=min(chunk,size-offset)
                            and count==len(body)-10,"分片身份/长度错误")
                    if cmd==Command.EVENTS:
                        unpack_events(body[10:])
                    target.extend(body[10:])
                    progress(len(samples)+len(events),total)
        except (Cancelled,DeviceFailure,ValueError,ConnectionError) as exc:
            meta["transfer_error"]=str(exc)
            self.uncertain=True
        meta["transfer_complete"]=len(samples)==meta["valid_count"] and len(events)==meta["event_count"]*16
        meta["crc_errors"]=self.decoder.crc_errors
        meta["transport_retries"]=self.retries
        return Capture(meta,bytes(samples),bytes(events)).validate()

    def wait_frozen(self,timeout=2):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            status=self.status()
            if status["state"]=="FROZEN":
                return status
            time.sleep(.005)
        raise TimeoutError("尚未冻结；未擅自释放/停止")

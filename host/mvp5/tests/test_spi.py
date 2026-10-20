import unittest,struct
from dataclasses import replace
from mvp5.models import Config,Rule,unpack_events
from mvp5.examples import spi_example
from mvp5.spi_decode import decode_spi
from mvp5.client import Client
from protocol.messages import Command

def waveform(mode,values=(0xc3,0x3c),partial=0,selected_start=False):
    out=[];v=1|(int(not selected_start)<<4)|((mode>>1)<<2)
    def hold(n=5):out.extend([v]*n)
    hold();v &= ~(1<<4);hold()
    for value,count in [(x,8) for x in values]+([(0xc3,partial)] if partial else []):
        for bit in range(7,7-count,-1):
            v=(v&~8)|(((value>>bit)&1)<<3);hold()
            v^=4;hold();v^=4;hold()
    v|=16;hold();return bytes(out)

class SpiTests(unittest.TestCase):
    def test_modes(self):
        for mode in range(4):
            cfg=replace(spi_example(),spi_mode=mode)
            rows=decode_spi(waveform(mode),1000,5,cfg)
            self.assertEqual([x['value'] for x in rows],[0xc3,0x3c]);self.assertFalse(any(x['error'] for x in rows))
    def test_exact_eighth_edge(self):
        rows=decode_spi(waveform(0,values=(0xc3,)),1000,5,spi_example())
        # 5 idle + 5 CS setup + 7*15 bit intervals + 5 data setup = index120.
        self.assertEqual(rows,[dict(tick=1600,value=0xc3,error=False)])
    def test_truncated(self):
        rows=decode_spi(waveform(0,values=(),partial=3),1000,5,spi_example())
        self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['value']);self.assertTrue(rows[0]['error'])
    def test_complete_then_truncated(self):
        rows=decode_spi(waveform(0,values=(0xc3,),partial=3),1000,5,spi_example())
        self.assertEqual([x['value'] for x in rows],[0xc3,None])
    def test_mid_cs_start(self):
        rows=decode_spi(waveform(0,values=(0xc3,),selected_start=True),1000,5,spi_example())
        self.assertEqual(rows,[])
    def test_idle_clock(self):
        self.assertEqual(decode_spi(bytes([0x11,0x15]*20),0,5,spi_example()),[])
    def test_config_wire_exact(self):
        cfg=spi_example();raw=cfg.to_bytes();self.assertEqual(raw[:4],bytes((5,0,15,1)))
        self.assertEqual(raw[10:12],b'\x1a\x01');self.assertEqual(raw[65:68],bytes((1,0xc3,255)))
        self.assertEqual(Config.from_bytes(raw),cfg)
    def test_invalid_colliding_pins(self):
        with self.assertRaises(ValueError):replace(spi_example(),spi_cs=2).to_bytes()
    def test_disabled_spi_rule(self):
        with self.assertRaises(ValueError):replace(spi_example(),spi_enabled=0,spi_clk=0,spi_mosi=0,spi_cs=0).to_bytes()
    def test_invalid_spi_mask(self):
        cfg=spi_example();rules=list(cfg.rules);rules[2]=replace(rules[2],spi_mask=0)
        with self.assertRaises(ValueError):replace(cfg,rules=tuple(rules)).to_bytes()
    def test_spi_gpio_noncanonical(self):
        with self.assertRaises(ValueError):replace(spi_example().rules[2],target_channel=1).validate(0)
    def test_old_profile_rejected(self):
        raw=bytearray(Config().to_bytes());raw[0]=4
        with self.assertRaises(ValueError):Config.from_bytes(raw)
    def test_spi_event_bits(self):
        word=(1<<112)|(0xc3<<113)|(2<<106)
        event=unpack_events(word.to_bytes(16,'little'))[0]
        self.assertEqual(event['spi'],0xc3);self.assertFalse(event['spi_error'])
    def test_error_without_byte(self):
        event=unpack_events(((1<<121)|(2<<106)).to_bytes(16,'little'))[0]
        self.assertIsNone(event['spi']);self.assertTrue(event['spi_error'])
    def test_invalid_byte_without_valid(self):
        with self.assertRaises(ValueError):unpack_events(((0xc3<<113)|(2<<106)).to_bytes(16,'little'))
    def test_bad_status_sets_uncertain(self):
        class Transport:
            kind='mock';origin='mock';description='no-hardware'
        client=Client(Transport());client.request=lambda *args:bytes(126)
        with self.assertRaises(ValueError):client.status()
        self.assertTrue(client.uncertain)
    def test_waveform_axis_stays_in_milliseconds(self):
        from PySide6.QtWidgets import QApplication
        from mvp5.waveform import FourWaveform
        app=QApplication.instance() or QApplication([])
        view=FourWaveform()
        # Exercise the actual set_capture path with a valid model fixture.
        from mvp5.tests.test_mvp4 import fixture
        view.set_capture(fixture())
        axis=view.plot.getAxis('bottom')
        self.assertFalse(axis.autoSIPrefix)
        view.plot.setXRange(-0.02,0.075,padding=0)
        app.processEvents()
        self.assertEqual(axis.autoSIPrefixScale,1.0)
        view.close()
    def test_visible_first_capture_and_repeated_ranges_keep_ms_scale(self):
        from PySide6.QtWidgets import QApplication
        from mvp5.waveform import FourWaveform
        from mvp5.tests.test_mvp4 import fixture
        app=QApplication.instance() or QApplication([])
        view=FourWaveform();view.resize(1400,600);view.show();app.processEvents()
        try:
            axis=view.plot.getAxis('bottom')
            self.assertEqual(axis.autoSIPrefixScale,1.0)
            for repeat in range(2):
                view.set_capture(fixture());app.processEvents()
                self.assertEqual(axis.autoSIPrefixScale,1.0)
                self.assertEqual(axis.tickStrings([-3.,0.,3.],axis.autoSIPrefixScale*axis.scale,1.),['-3','0','3'])
                for low,high in ((-.02,.075),(-3.4,3.4),(-30.,30.)):
                    view.plot.setXRange(low,high,padding=0);app.processEvents()
                    self.assertFalse(axis.autoSIPrefix)
                    self.assertEqual(axis.autoSIPrefixScale,1.0)
        finally:view.close()
if __name__=='__main__':unittest.main()

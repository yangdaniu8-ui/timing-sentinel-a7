"""Independent raw-waveform reference; never derives bytes from FPGA events.

Sampling aliasing cannot be repaired. Connector SCK/MOSI minimum intervals
are a physical precondition, not something this decoder proves for hidden edges.
"""
from app.models import TICK_MASK
def decode_spi(samples,first,divisor,config):
    if not samples:return []
    clk,mosi,cs=config.spi_clk,config.spi_mosi,config.spi_cs
    previous=samples[0];qualified=bool(previous>>cs&1);count=0;shift=0;out=[]
    sampling_level=((config.spi_mode>>1)&1) ^ (not(config.spi_mode&1))
    for i,current in enumerate(samples[1:],1):
        tick=(first+i*divisor)&TICK_MASK
        if current>>cs&1:
            if not previous>>cs&1 and qualified and count:
                out.append(dict(tick=tick,value=None,error=True))
            qualified=True;count=shift=0
        elif previous>>cs&1:
            count=shift=0
        elif qualified and ((current^previous)>>clk&1) and (current>>clk&1)==sampling_level:
            shift=((shift<<1)|(current>>mosi&1))&255;count+=1
            if count==8:out.append(dict(tick=tick,value=shift,error=False));count=0
        previous=current
    return out

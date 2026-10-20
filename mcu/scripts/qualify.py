"""Native compiled C against independent Python integer reference; no hardware."""
import argparse, hashlib, json, re, struct, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MASK=0xffffffff
def golden():
    x=0x20261011; data=[]
    for _ in range(64):
        x=(1664525*x+1013904223)&MASK; data.append((x>>16)&1023)
    h=2166136261
    for p in range(3):
        for i in range(64):
            total=sum((data[(i+k)%64]-512)*c for k,c in enumerate([-3,5,11,19,19,11,5,-3]))
            h=((h^(total&MASK))*16777619)&MASK
            h=((h^(i+64*p))*16777619)&MASK
    return h
def irq_step(x):
    for _ in range(32):
        x^=(x<<13)&MASK; x^=x>>17; x^=(x<<5)&MASK
    return x
def expected_packet(i):
    profile=[0,1,2,2,1,0][(i-1)%6]; phase=(i*37)&255
    check=(i^(i>>8)^profile^phase^0x96)&255
    return bytes([0xe2,0x40|(i>>8),0x40|((i>>4)&15),0x40|(i&15),0x60|profile,
        0x50|(phase>>4),0x50|(phase&15),0x70|(check>>4),0x70|(check&15),0xa5])
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(); p.add_argument('--build',type=Path,required=True)
    p.add_argument('--gcc',default='gcc'); p.add_argument('--fromelf',default='fromelf'); a=p.parse_args()
    out=a.build/'qualification'; out.mkdir(exist_ok=False)
    report=dict(passed=False,programmed=False,hardware_transactions=0)
    try:
        exe=out/'native.exe'
        r=subprocess.run([a.gcc,'-std=c99','-O2','-Wall','-Wextra','-Werror',
            '-I'+str(ROOT/'src'),str(ROOT/'src/workload.c'),str(ROOT/'tests/native.c'),'-o',str(exe)],capture_output=True,text=True)
        (out/'compile.log').write_text(r.stdout+r.stderr); r.check_returncode()
        r=subprocess.run([str(exe)],capture_output=True,text=True); r.check_returncode()
        (out/'native.log').write_text(r.stdout)
        lines=r.stdout.splitlines()
        assert int(lines[0].split()[1],16)==golden()==0x08065d77
        assert int(lines[1].split()[1],16)==irq_step(0x12345678)
        assert len(lines)==4097
        for i,line in enumerate(lines[2:],1):
            _,job,profile,period,packet=line.split()
            expected_profile=[0,1,2,2,1,0][(i-1)%6]
            assert (int(job),int(profile),int(period))==(i,expected_profile,[0,1000,125][expected_profile])
            assert bytes.fromhex(packet)==expected_packet(i)
            assert bytes.fromhex(packet).count(0xa5)==1
        symbols=subprocess.check_output([a.fromelf,'--text','-s',str(a.build/'stimulus.axf')],text=True)
        (out/'symbols.txt').write_text(symbols)
        def symbol(name): return int(re.search(r'\b'+name+r'\s+0x([0-9a-f]+)',symbols,re.I)[1],16)
        image=(a.build/'stimulus.bin').read_bytes(); vectors=struct.unpack('<59I',image[:236])
        assert vectors[45]==symbol('TIM3_IRQHandler') and vectors[45]&1
        assert all(v==symbol('Default_Handler') for i,v in enumerate(vectors[16:]) if i!=29)
        assert 0x20000000<vectors[0]<=0x20005000
        assert symbol('workload_debug')==0x20000010
        assert re.search(r'workload_debug\s+0x20000010.*0x50',symbols)
        assert len(image)<=65536
        manifest=json.loads((a.build/'build_manifest.json').read_text(encoding='utf-8-sig'))
        assert manifest['binary_sha256'].lower()==sha(a.build/'stimulus.bin')
        for key in ('sources','external_headers'):
            for path,value in manifest[key].items(): assert sha(path)==value.lower(),path
        report.update(passed=True,native_schedule_cases=4095,golden_checksum=f'{golden():08x}',
            irq_vector_hex=hex(vectors[45]),debug_address_hex=hex(symbol('workload_debug')),
            debug_bytes=80,binary_bytes=len(image),binary_sha256=sha(a.build/'stimulus.bin'),
            compiler_warning='ARMASM A1581W: 2-byte alignment padding; inspected, vector is verified',
            sources={str(f):sha(f) for f in [*list((ROOT/'src').glob('*')),*list((ROOT/'tests').glob('*')),Path(__file__)]})
    except Exception as exc:
        report['error']=str(exc); raise
    finally:
        (out/'qualification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report),flush=True)
if __name__=='__main__': main()

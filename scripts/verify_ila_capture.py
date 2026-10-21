"""Preserved event-qualified ILA comparison; read-only module, no board access."""
import csv,re
from app.models import delta_tick
from mvp5.models import unpack_events
from mvp5.analysis import audit_capture
from mvp5.workload import identify_workload
from protocol.messages import Result,State
WIDTHS=[48,32,18,29,6]
def decode_words(words):
    if len(words)!=5 or any(type(v)!=int or not 0<=v<(1<<w) for v,w in zip(words,WIDTHS)):
        raise ValueError('Probe width/type out of range')
    tick,signals,protocol,rules,control=words
    return dict(tick=tick,value=signals>>8&255,rise=signals>>16&255,fall=signals>>24&255,
        uart=protocol&255 if protocol>>8&1 else None,
        spi=protocol>>9&255 if protocol>>17&1 else None,
        uart_error=bool(control>>4&1),spi_error=bool(control>>5&1),
        accepted_mask=rules>>16&15,results=[Result(rules>>(i*4)&15).name for i in range(4)],
        state=State(rules>>25&7).name,
        event_enable=bool(control&1),result_valid=rules>>20&15,first_trigger=bool(rules>>24&1))

def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        fields=reader.fieldnames or []
        keys=[]
        for i,w in enumerate(WIDTHS):
            match=[name for name in fields if re.fullmatch(r'sentinel_p'+str(i)+r'\['+str(w-1)+r':0\]',name)]
            if len(match)!=1:raise ValueError('Missing/duplicate physical bus alias')
            keys.append(match[0])
        rows=[];radix=False
        for row in reader:
            if row['Sample in Buffer'].startswith('Radix'):
                if any(row[k]!='HEX' for k in keys):raise ValueError('Probe radix must be explicit HEX')
                radix=True;continue
            if not radix:raise ValueError('No radix declaration')
            index=int(row['Sample in Buffer'])
            if index!=len(rows):raise ValueError('Skipped/repeated sample index')
            words=[int(row[k],16) for k in keys]
            sample=decode_words(words);sample['trigger']=int(row['TRIGGER'])
            rows.append(sample)
    if len(rows)!=1024:raise ValueError('Incomplete ILA upload')
    return rows

def compare(rows,cap):
    assert cap.complete and len(cap.samples)==65536
    assert len(rows)==1024
    meta=cap.metadata;a=meta['rules'][0]['a_tick'];assert a is not None
    assert sum(r['trigger'] for r in rows)==1 and rows[0]['trigger']==1
    assert rows[0]['tick']==a and rows[0]['uart']==0xa5 and rows[0]['accepted_mask']==meta['enabled_mask']
    assert rows[-1]['state']=='FROZEN' and not rows[-1]['event_enable']
    gaps=[delta_tick(b['tick'],a['tick']) for a,b in zip(rows,rows[1:])]
    assert min(gaps)>0 and max(gaps)>1024,'Expected event-qualified sparse timestamps'
    keys=('tick','value','rise','fall','uart','spi','uart_error','spi_error','accepted_mask','results','state')
    actual=[{k:r[k] for k in keys} for r in rows if r['event_enable']]
    board=[{k:r[k] for k in keys} for r in unpack_events(cap.events) if delta_tick(r['tick'],a)>=0]
    assert actual==board,'ILA events and frozen event records differ after the trigger A'
    rules=[]
    for i in range(4):
        if not meta['enabled_mask']&(1<<i):continue
        entry=meta['rules'][i];result=entry['result']
        matches=[r for r in rows if r['event_enable'] and r['results'][i]==result and r['result_valid']&(1<<i)]
        assert len(matches)==1 and matches[0]['tick']==entry['result_tick'],f'R{i} terminal tick mismatch'
        cfg=meta['config']['rules'][i]
        found=[r['tick'] for r in rows if r['event_enable'] and
            ((r['spi'] is not None and r['spi']&cfg['spi_mask']==cfg['spi_value']) if cfg['target_kind'] else
             bool((r['fall'] if cfg['edge'] else r['rise'])&(1<<cfg['target_channel'])))]
        observed=found[0] if found else None
        assert observed==entry['observed_b_tick'],f'R{i} observed B mismatch'
        rules.append(dict(rule=i,result=result,a_tick=a,result_tick=matches[0]['tick'],observed_b_tick=observed,
            observed_ms=delta_tick(observed,a)/50000 if observed is not None else None))
    audit=audit_capture(cap);assert audit['status']=='verified'
    identity=identify_workload(cap);assert identity and identity['verified']
    deadline=meta['config']['rules'][0]['max_ticks']/50000
    assert deadline in (3.5,8)
    expected='TIMEOUT' if deadline==3.5 and identity['workload']['profile']==2 else 'PASS'
    assert all(r['result']==expected for r in rules),'Unexpected real workload verdict'
    return dict(passed=True,scope='Same debug-image transaction: ILA/metadata/event log/raw sample reconstruction',
        session=meta['session_id'],config=meta['config_id'],workload=identity['workload'],rules=rules,
        ila_samples=1024,matched_events_after_a=len(actual),pre_a_events_not_checked_by_ila=True,
        compared_event_fields=list(keys),overlap_not_probed=True,capture_mode='event_enable OR FROZEN, trigger UART A5',
        sparse_timestamps=True,maximum_gap_ticks=max(gaps),raw_waveform_audit=audit,
        independent_instrument_calibration=False,production_usr43_with_ila_not_same_bit=True)

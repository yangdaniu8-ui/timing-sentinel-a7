"""Plain-language hints; never decide device verdicts or issue device commands."""
from datetime import datetime
from mvp5.workload_experiment import workload_config
from mvp5.paired_deadlines import paired_deadline_config

def next_step(*,connected,state,uncertain,protected,current,has_capture,busy,storage_ready):
    if busy:return None,'正在处理，请稍候。'
    if protected:return 'save','当前记录尚未保存。\n请先保存，避免丢失。'
    if uncertain:return 'connect','连接状态待确认。\n请断开后重新连接。'
    if not connected:return 'connect','请先连接设备，\n或打开已保存的记录。'
    if state in ('PREFILL','ARMED','POST_TRIGGER'):
        return None,'设备正在采集。\n完成后可读取记录。'
    if state=='FROZEN' and not current:
        return 'read','设备中已有一份记录。\n点击“读取板端记录”查看。'
    if storage_ready is False:return 'storage','保存位置不可用。\n请先更换保存位置。'
    if state in ('IDLE','FROZEN'):
        return 'workload',('记录已载入，可查看结果，\n或开始下一组实验。' if has_capture else '设备已就绪。\n点击“开始实验”即可。')
    return None,'请刷新设备状态后再操作。'

def demo_expectation(cap,identity):
    """Only the exact verified real-task preset permits a demo expectation label."""
    if not cap.complete or cap.metadata['origin']!='hardware' or not identity.get('verified'):
        return None
    if cap.metadata.get('crc_errors')!=0 or cap.metadata.get('transport_retries')!=0:
        return None
    task=identity.get('workload')
    if identity.get('kind')!='workload' or not task:return None
    if cap.metadata['config']==paired_deadline_config().as_dict():
        expected=['TIMEOUT','PASS','TIMEOUT','PASS'] if task['profile']==2 else ['PASS']*4
        actual=[r['result'] for r in cap.metadata['rules']]
        return dict(matches=actual==expected,expected=expected,deadline_ms=None,
            detail='同次任务演示预期：'+ ' / '.join(f'R{i} {r}' for i,r in enumerate(expected))+
                   '；比较同一响应的3.5 / 8 ms期限，不改变硬件判定。')
    if cap.metadata['enabled_mask']!=5:return None
    deadline=next((d for d in (3.5,8) if cap.metadata['config']==workload_config(d).as_dict()),None)
    if deadline is None:return None
    expected='TIMEOUT' if deadline==3.5 and task['profile']==2 else 'PASS'
    actual=[cap.metadata['rules'][i]['result'] for i in (0,2)]
    return dict(matches=actual==[expected,expected],expected=expected,deadline_ms=deadline,
        detail=f'演示预期：R0 / R2 {expected}；仅对应本记录的{deadline:g} ms任务预设。'
               '这是演示对照，不改变硬件判定。')

def suggested_record_name(cap,identity,token):
    task=(identity or {}).get('workload') if (identity or {}).get('verified') else None
    load=('noirq','light','heavy')[task['profile']] if task else 'capture'
    job=f'_task{task["id"]}' if task else ''
    limits=sorted({r['max_ticks']/50000 for i,r in enumerate(cap.metadata['config']['rules'])
                   if cap.metadata['enabled_mask']&(1<<i)})
    window=(f'{limits[0]:g}'.replace('.','p')+'ms') if len(limits)==1 else 'custom'
    return f'{datetime.now():%Y%m%d_%H%M%S}_{load}_{window}{job}_s{cap.metadata["session_id"]}_{token}.tscap'

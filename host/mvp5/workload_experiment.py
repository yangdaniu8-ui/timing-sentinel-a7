"""Explicit GUI workload collection: protect, capture, audit, save, then release."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import html
from pathlib import Path
import time

from app.services.record_store import atomic_output
from app.services.capture_storage import probe_directory
from mvp5.analysis import audit_capture, delay_statistics, IDENTITY_FIELDS
from mvp5.examples import spi_example
from mvp5.models import Config, require
from mvp5.paired_deadlines import paired_deadline_config, paired_capture_summary, RULE_LABELS
from mvp5.store import blob, digest, load_record, save_record
from mvp5.workload import identify_workload, LOAD_NAMES


def workload_config(deadline_ms=3.5):
    # None selects the explicit same-transaction, four-rule preset.
    if deadline_ms is None:
        return paired_deadline_config()
    require(type(deadline_ms) in (float, int) and deadline_ms in (3.5, 8),
            "真实任务预设仅支持3.5ms或8ms")
    base = spi_example()
    rules = list(base.rules)
    for i in (0, 2):
        rules[i] = replace(rules[i], action=2, min_ticks=0, max_ticks=round(deadline_ms * 50000))
    cfg = replace(base, enabled_mask=5, divisor=10, pre=45000, post=20535, rules=tuple(rules))
    return Config.from_bytes(cfg.to_bytes())


def same_raw(a, b):
    return (a.samples == b.samples and a.events == b.events and
            all(blob(a.metadata[k]) == blob(b.metadata[k]) for k in IDENTITY_FIELDS))


def publish(path, value):
    with atomic_output(path) as temporary:
        temporary.write_bytes(blob(value))


def load_statistics(cases, rule_ids=(0, 2)):
    groups = []
    for profile in range(3):
        rows = [c for c in cases if c['identity']['profile'] == profile]
        rules = {}
        for rule_id in rule_ids:
            selected = [next(r for r in c['audit']['rules'] if r['rule_id'] == rule_id) for c in rows]
            rules[str(rule_id)] = dict(
                observed=delay_statistics([r['observed_delay_ms'] for r in selected
                                            if r['observed_delay_ms'] is not None]),
                results=dict(Counter(r['result'] for r in selected)))
        groups.append(dict(profile=profile, name=LOAD_NAMES[profile], count=len(rows), rules=rules))
    return groups


def publish_summary(directory, report):
    paired = report.get('experiment_kind') == 'same_transaction_deadlines'
    rule_ids = ('0', '1', '2', '3') if paired else ('0', '2')
    rows = []
    for group in report['groups']:
        cells = [group['name'], str(group['count'])]
        for rule_id in rule_ids:
            row = group['rules'][rule_id]
            mean = row['observed']['mean_ms']
            cells += ['—' if mean is None else f'{mean:.6f}', str(row['results'])]
        rows.append('<tr>' + ''.join('<td>' + html.escape(s) + '</td>' for s in cells) + '</tr>')
    links = ''.join(f'<li>{html.escape(c["identity"]["name"])} · 任务#{c["identity"]["id"]}：'
                    f'<code>{html.escape(c["file"])}</code></li>' for c in report['cases'])
    comparison = ''
    if paired:
        comparison_rows = []
        for case in report['cases']:
            for pair in case['deadline_comparison']['pairs']:
                observed = pair['observed_delay_ms']
                cells = [str(case['identity']['id']), case['identity']['name'], pair['target'],
                         '观察窗内未见响应' if observed is None else f'{observed:.6f}',
                         *pair['results']]
                comparison_rows.append('<tr>' + ''.join('<td>' + html.escape(s) + '</td>'
                                                       for s in cells) + '</tr>')
        comparison = ('<h2>同一次任务的两种期限</h2><p>每行两种判定来自同一记录、同一个A和同一个实际B。'
                      '仅期限不同，不是两次任务，也不表示宽窗让计算变快。'
                      '本模式仅作波形对照，正式USR43不对该配置启用判定灯或蜂鸣器。</p>'
                      '<table><tr><th>任务</th><th>负载</th><th>响应</th><th>实际观测 ms</th>'
                      '<th>3.5 ms规则</th><th>8 ms规则</th></tr>' + ''.join(comparison_rows) + '</table>')
    description = ('同一次FIR任务同时执行3.5 ms与8 ms四规则对照，三档标称中断负载各两次。'
                   if paired else f'同一FIR任务，三档标称中断负载，各两次；判定期限{report["deadline_ms"]} ms。')
    labels = RULE_LABELS if paired else ('GPIO', 'SPI')
    headers = ''.join('<th>' + html.escape(label + suffix) + '</th>'
                      for label in labels for suffix in ('观测均值 ms', '结果'))
    content = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<title>真实负载实验</title><style>body{font:16px sans-serif;max-width:1100px;margin:30px auto;'
        'line-height:1.6}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd;padding:10px}'
        'th{background:#eef3f8}code{overflow-wrap:anywhere}</style><h1>真实负载实验</h1>'
        '<p>' + description + '</p>' + comparison +
        '<h2>按负载汇总</h2><table><tr><th>负载</th><th>任务数</th>' + headers + '</tr>' +
        ''.join(rows) + '</table><h2>波形记录</h2><ul>'
        + links + '</ul><p>身份、样点、事件和FPGA规则一致性已检查；GUI未读取MCU内部计算结果。'
        '每档两次仅用于演示；时间按标称时基换算。TIMEOUT后的实际响应保留，缺失响应不填零。'
        'READY控制的分段采集包含上传空档，不代表连续全流监测。</p></html>')
    with atomic_output(directory / 'summary.html') as temporary:
        temporary.write_text(content, encoding='utf-8')


def run_workload(client, directory, deadline_ms=3.5, progress=None, message=None, preserve=None):
    cfg = workload_config(deadline_ms)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    probe_directory(directory)  # Before STATUS/configuration/release/ARM, even for non-GUI callers.
    progress = progress or (lambda a, b: None)
    message = message or (lambda value: None)
    preserve = preserve or (lambda capture, path: None)
    initial_retries, initial_crc = client.retries, client.decoder.crc_errors
    report = dict(schema=1, passed=False, generated_utc=datetime.now(timezone.utc).isoformat(),
        source='gui_workload_live_collection', deadline_ms=deadline_ms, config=cfg.as_dict(), cases=[],
        groups=[], latest_record=None, protected_previous=None, last_capture_saved=False,
        last_capture_frozen=False, independent_instrument_verified=False,
        mcu_internal_result_checked=False, firmware_programmed=False,
        initial_link_counters=dict(retries=initial_retries, crc_errors=initial_crc))
    paired = deadline_ms is None
    if paired:
        report.update(source='gui_same_transaction_deadlines_collection',
                      experiment_kind='same_transaction_deadlines', deadlines_ms=[3.5, 8],
                      rule_labels=list(RULE_LABELS),board_sound_light_supported=False)
    capture = None
    active_error = None
    started = time.monotonic()

    def not_cancelled():
        require(not client.cancel(), '已停止实验；已收到的证据保留，未继续释放或ARM')

    def clean_link():
        require(client.retries == initial_retries and client.decoder.crc_errors == initial_crc,
                '实验发生通信重试或CRC错误；当前记录保留，请重连检查')

    try:
        not_cancelled()
        status = client.status()
        report['initial_status'] = status
        require(status['state'] in ('IDLE', 'FROZEN'), '板端仍在采集，先等待冻结或停止')
        if status['state'] == 'FROZEN':
            client.get_config()
            previous = client.read_capture()
            preserve(previous, None)
            path = directory / 'protected_previous.tscap'
            save_record(previous, path)
            require(same_raw(previous, load_record(path)), '旧记录保存后回读不一致，未释放')
            preserve(previous, path)
            report['protected_previous'] = dict(file=path.name, sha256=digest(path.read_bytes()))
            publish(directory / 'experiment.json', report)
            require(previous.complete and same_raw(previous, load_record(path)), '旧记录未完整保护，未释放')
            require(same_raw(previous, client.read_capture()), '旧冻结记录变化，未释放')
            require(client.status() == status, '保护期间板端身份变化，未释放')
            not_cancelled(); clean_link()
            require(client.release()['state'] == 'IDLE', '旧记录已保存，但释放状态未确认')
        not_cancelled(); clean_link()
        applied = client.configure(cfg)
        require(Config.from_dict(applied['config']).to_bytes() == cfg.to_bytes(), '实验配置回读不一致')
        report['config_id'] = applied['config_id']
        for index in range(6):
            not_cancelled(); clean_link()
            message(f'真实负载任务 {index + 1}/6：自动读取、核验并保存')
            client.arm(); client.wait_frozen(3)
            report['last_capture_saved'] = False
            capture = client.read_capture(lambda a, b: progress(index * 100 + int(a * 100 / max(b, 1)), 600))
            preserve(capture, None)
            path = directory / f'round_{index + 1:02d}_s{capture.metadata["session_id"]}.tscap'
            save_record(capture, path)
            require(same_raw(capture, load_record(path)), '本轮保存后回读不一致，未释放')
            preserve(capture, path)
            report['latest_record'] = path.name
            report['last_capture_saved'] = True
            publish(directory / 'experiment.json', report)
            require(capture.complete and same_raw(capture, load_record(path)), '本轮记录不完整，已保存前缀，未释放')
            require(Config.from_dict(capture.metadata['config']).to_bytes() == cfg.to_bytes()
                    and capture.metadata['config_id'] == applied['config_id'], '本轮配置身份不一致')
            identity = identify_workload(capture)
            require(identity and identity['verified'], '未得到有效E2负载证据；请确认STM32任务固件，记录已保存')
            task = identity['workload']
            require(task['id'] + 5 - index <= 4095, '任务编号接近固件上限，无法完成六任务；本轮已保存，未释放')
            require(task['profile'] == (0, 1, 2, 2, 1, 0)[(task['id'] - 1) % 6]
                    and task['phase'] == (task['id'] * 37) & 255, '任务标记与本实验固件调度约定不一致')
            if report['cases']:
                prior = report['cases'][-1]
                require(task['id'] == prior['identity']['id'] + 1
                        and capture.metadata['session_id'] == prior['session_id'] + 1,
                        '任务或采集会话不连续；当前记录已保存，未释放')
            audit = audit_capture(capture)
            require(audit['status'] == 'verified', '原始样点重判未通过，记录已保存')
            case = dict(file=path.name, sha256=digest(path.read_bytes()),
                        identity=task, session_id=capture.metadata['session_id'], audit=audit)
            if paired:
                case['deadline_comparison'] = paired_capture_summary(capture, audit)
            report['cases'].append(case)
            publish(directory / 'experiment.json', report)
            message(f'{task["name"]} · 任务#{task["id"]} · ' +
                    ' / '.join(f'R{r["rule_id"]} {r["result"]}' for r in audit['rules']))
            not_cancelled(); clean_link()
            if index < 5:
                require(client.release()['state'] == 'IDLE', '已保存本轮，但释放未确认')
        require(Counter(c['identity']['profile'] for c in report['cases']) == {0: 2, 1: 2, 2: 2},
                '三档负载覆盖不足')
        report['groups'] = load_statistics(report['cases'], (0, 1, 2, 3) if paired else (0, 2))
        report['final_status'] = client.status()
        require(report['final_status']['state'] == 'FROZEN', '末轮冻结状态未确认')
        clean_link(); not_cancelled()
        publish_summary(directory, report)
        report.update(passed=True, last_capture_frozen=True)
    except Exception as exc:
        active_error = exc
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        report.update(seconds=time.monotonic() - started,
            new_link_counters=dict(retries=client.retries - initial_retries,
                                   crc_errors=client.decoder.crc_errors - initial_crc))
        try:
            publish(directory / 'experiment.json', report)
        except (OSError, ValueError) as publish_error:
            if active_error is None:
                raise
            active_error.add_note('实验中止报告也未写入：' + str(publish_error))
            message('中止报告未写入；已收到的记录仍交给界面保留：' + str(publish_error))
    return dict(report=report, capture=capture, directory=str(directory), status=report['final_status'], config=cfg)

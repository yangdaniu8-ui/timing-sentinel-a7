"""Offline, marker-independent single-transaction audit and selected-record statistics.

No serial imports, experiment mutation, or inference of unrecorded transactions.
"""
from collections import Counter
from datetime import datetime, timezone
import html
import io
import json
from pathlib import Path
from statistics import fmean, pstdev

from app.models import delta_tick, TICK_MASK
from app.services.record_store import atomic_output
from mvp5 import __version__
from mvp5.experiment import decode_uart
from mvp5.models import Config, unpack_events, require
from mvp5.spi_decode import decode_spi
from mvp5.store import blob, digest, load_record

MAX_FILES = 2500
MAX_FILE_BYTES = 2_000_000
IDENTITY_FIELDS = (
    "origin", "firmware", "trigger_valid", "manual_stop", "primary_rule", "enabled_mask",
    "session_id", "config_id", "clock_hz", "valid_count", "sample_divisor", "event_count",
    "event_overwrites", "first_sample_timestamp", "capture_anchor_timestamp", "decision_timestamp",
    "trigger_sample_index", "trigger_rule_mask", "incomplete_mask", "config", "rules",
)
LIMITATIONS = [
    "统计对象是所选、去重后的记录，不证明已覆盖全部运行事务；异常捕获样本不能代表全流延迟分布。",
    "只将完整实物、无手停/截断/事件覆盖、各启用规则单事务且样点重判一致的记录纳入统计。",
    "按固件标识、来源与完整规则/采样配置分组；不混用不同时间窗、采样率或动作配置。",
    "PASS/EARLY/TIMEOUT按各条规则统计；R1/R3等故意对照的TIMEOUT不是系统故障。",
    "匹配延迟只含PASS/EARLY；观测响应延迟另含TIMEOUT后实际出现的匹配B，不将无响应填为0。",
    "均值、总体标准差、P50/P95/P99（线性插值）仅描述当前样本；小样本不构成可靠性或尾延迟保证。",
    "配置相同但场景/负载不同也可能混入同组；混合标准差不等于单场景抖动，研究抖动应只选择同一工作条件的重复记录。",
    "时间按记录内标称50MHz换算；逻辑计数间隔20ns，波形采样间隔由采样配置决定。",
    "文件SHA与内容去重仅用于追溯和防止重复统计，不证明物理设备唯一性、来源真实性或全程无漏采。",
    "通信CRC/重试是每份记录保存的累计快照，不能相加当作本批新增错误；保留原实验FAIL，不回填PASS。",
]


def audit_capture(cap):
    """Reconstruct A and first B from samples, not Dn markers or device result ticks."""
    cap.validate()
    m = cap.metadata
    reasons = []
    if not cap.complete: reasons.append("不完整传输")
    if m["origin"] != "hardware": reasons.append("非实物记录")
    if m["manual_stop"]: reasons.append("手动停止")
    if m["incomplete_mask"]: reasons.append("采集截断")
    if m["event_overwrites"]: reasons.append("事件日志曾覆盖")
    enabled = [i for i in range(4) if m["enabled_mask"] & (1 << i)]
    if any(m["rules"][i]["counts"]["accepted"] != 1 for i in enabled):
        reasons.append("非每规则单事务快照")
    if any(m["rules"][i]["counts"][k] for i in enabled for k in ("overlap", "cancelled", "incomplete")):
        reasons.append("重叠/取消/未结案事务")
    if reasons:
        return dict(status="excluded", reasons=reasons, rules=[])
    cfg = Config.from_dict(m["config"])
    div = cfg.divisor
    if (m["clock_hz"] // cfg.baud) // div < 8:
        return dict(status="unverifiable", reasons=["UART样点密度不足，不能可靠重判"], rules=[])
    frames = decode_uart(bytes((v >> cfg.uart_channel) & 1 for v in cap.samples),
                         m["first_sample_timestamp"], div, m["clock_hz"] // cfg.baud)
    spi = decode_spi(cap.samples, m["first_sample_timestamp"], div, cfg) if cfg.spi_enabled else []
    events = unpack_events(cap.events)
    results = []
    try:
        require(not any(e["uart_error"] or e["overlap_mask"] for e in events), "事件含UART错误/重叠")
        spi_events = [e for e in events if e["spi"] is not None or e["spi_error"]]
        require(len(spi) == len(spi_events), "样点与日志SPI数量不一致")
        for sampled, event in zip(spi, spi_events):
            require(sampled["value"] == event["spi"] and sampled["error"] == event["spi_error"]
                    and 0 <= delta_tick(sampled["tick"], event["tick"]) < div,
                    "样点与日志SPI字节/截断/时间不一致")
        for i in enabled:
            rule, entry = cfg.rules[i], m["rules"][i]
            aa = [f for f in frames if f["value"] & rule.mask == rule.value]
            if len(aa) != 1:
                return dict(status="unverifiable", reasons=[f"R{i}波形内无唯一匹配UART A"], rules=[])
            a = aa[0]["nominal_stop_center"]
            require(entry["a_tick"] is not None and 0 <= delta_tick(a, entry["a_tick"]) < div,
                    f"R{i}样点A与设备A不一致")
            if rule.target_kind:
                edges = [s["tick"] for s in spi if s["value"] is not None
                         and s["value"] & rule.spi_mask == rule.spi_value]
            else:
                edges = [(m["first_sample_timestamp"] + j * div) & TICK_MASK
                         for j in range(1, len(cap.samples))
                         if ((cap.samples[j-1] >> rule.target_channel) & 1,
                             (cap.samples[j] >> rule.target_channel) & 1)
                         == ((1, 0) if rule.edge else (0, 1))]
            candidates = [delta_tick(t, a) for t in edges]
            ds = sorted(d for d in candidates if d >= 0)
            if any(abs(d) < div for d in candidates) or (ds and any(
                    abs(ds[0] - bound) < div for bound in (rule.min_ticks, rule.max_ticks))):
                return dict(status="ambiguous", reasons=[f"R{i}响应接近样点量化边界，不强行判定"], rules=[])
            result = ("EARLY" if ds[0] < rule.min_ticks else "PASS") if ds and ds[0] <= rule.max_ticks else "TIMEOUT"
            if result == "TIMEOUT":
                end = (m["first_sample_timestamp"] + (len(cap.samples)-1)*div) & TICK_MASK
                if delta_tick(end, a) <= rule.max_ticks + div:
                    return dict(status="unverifiable", reasons=[f"R{i}波形观察窗不足以证明超时"], rules=[])
            require(result == entry["result"], f"R{i}样点重判{result}与设备{entry['result']}不一致")
            require(sum(e["tick"] == entry["a_tick"] and bool(e["accepted_mask"] & (1 << i)) for e in events) == 1,
                    f"R{i}A日志缺失或重复")
            require(sum(e["tick"] == entry["result_tick"] and e["results"][i] == result for e in events) == 1,
                    f"R{i}终端结果日志缺失或重复")
            require(entry["counts"][result.lower()] == 1
                    and sum(entry["counts"][k] for k in ("pass", "early", "timeout")) == 1,
                    f"R{i}单事务计数与终端结果不一致")
            observed = entry["observed_b_tick"]
            require(bool(ds) == (observed is not None), f"R{i}观测响应有无与样点不一致")
            if ds:
                sampled_b = (a + ds[0]) & TICK_MASK
                require(0 <= delta_tick(sampled_b, observed) < div, f"R{i}第一观测响应时间不一致")
            matched = entry["b_tick"]
            if result in ("PASS", "EARLY"):
                require(matched == observed, f"R{i}匹配B不是第一观测B")
            results.append(dict(rule_id=i, result=result, sampled_observed_ms=ds[0]/50000 if ds else None,
                matched_delay_ms=delta_tick(matched, entry["a_tick"])/50000 if matched is not None else None,
                observed_delay_ms=delta_tick(observed, entry["a_tick"])/50000 if observed is not None else None))
        return dict(status="verified", reasons=[], rules=results, method="sample_reconstruction_without_scene_markers")
    except ValueError as exc:
        return dict(status="mismatch", reasons=[str(exc)], rules=[])


def delay_statistics(values):
    data = sorted(values)
    if not data:
        return dict(n=0, min_ms=None, max_ms=None, mean_ms=None, stddev_ms=None,
                    span_ms=None, p50_ms=None, p95_ms=None, p99_ms=None, histogram=[])
    def percentile(p):
        position = (len(data)-1)*p
        lower = int(position)
        return data[lower] + (data[min(lower+1, len(data)-1)]-data[lower])*(position-lower)
    lo, hi = data[0], data[-1]
    if lo == hi:
        histogram = [dict(low_ms=lo, high_ms=hi, count=len(data))]
    else:
        histogram = [dict(low_ms=lo+(hi-lo)*j/8, high_ms=lo+(hi-lo)*(j+1)/8, count=0) for j in range(8)]
        for value in data:
            histogram[min(7, int((value-lo)/(hi-lo)*8))]["count"] += 1
    return dict(n=len(data), min_ms=lo, max_ms=hi, mean_ms=fmean(data), stddev_ms=pstdev(data),
                span_ms=hi-lo, p50_ms=percentile(.5), p95_ms=percentile(.95), p99_ms=percentile(.99),
                histogram=histogram)


def _bounded_read(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES+1)
    require(len(raw) <= MAX_FILE_BYTES, "输入文件超过2MB限制")
    return raw


def analyze_records(paths, cancel=None, progress=None):
    paths = [Path(p).resolve() for p in paths]
    require(0 < len(paths) <= MAX_FILES, f"请选择1–{MAX_FILES}份记录")
    cancel, progress = cancel or (lambda: False), progress or (lambda a, b: None)
    report = dict(schema=1, app_version=__version__, generated_utc=datetime.now(timezone.utc).isoformat(),
        analysis_completed=False, scope="selected_records_only", serial_opened=False,
        new_hardware_transactions=0, independently_calibrated=False, limitations=LIMITATIONS,
        sources=[], groups=[], experiment_contexts=[])
    seen, groups, parents = {}, {}, set()
    for number, path in enumerate(paths, 1):
        if cancel(): raise InterruptedError("已取消离线统计，未发布报告，未操作板端")
        source = dict(path=str(path), file_sha256=None, status="invalid")
        report["sources"].append(source)
        parents.add(path.parent)
        try:
            require(path.suffix.lower() == ".tscap", "仅接受tscap原始记录")
            raw = _bounded_read(path)
            source["file_sha256"] = digest(raw)
            cap = load_record(io.BytesIO(raw))
            m = cap.metadata
            source.update(record_id=m["record_id"], session_id=m["session_id"], config_id=m["config_id"],
                origin=m["origin"], samples_received=len(cap.samples), transfer_complete=cap.complete,
                saved_crc_errors=m.get("crc_errors"), saved_transport_retries=m.get("transport_retries"))
            identity = {k: m[k] for k in IDENTITY_FIELDS}
            identity.update(samples_sha256=digest(cap.samples), events_sha256=digest(cap.events))
            source["capture_content_sha256"] = key = digest(blob(identity))
            if key in seen:
                source.update(status="duplicate", duplicate_of=seen[key])
                progress(number, len(paths)); continue
            seen[key] = str(path)
            cfg = Config.from_dict(m["config"])
            group_key = digest(blob(dict(origin=m["origin"], firmware=m["firmware"], config_hex=cfg.to_bytes().hex())))
            source["group_id"] = group_key
            audit = audit_capture(cap)
            source.update(status=audit["status"], audit=audit)
            if group_key not in groups:
                groups[group_key] = dict(group_id=group_key, origin=m["origin"], firmware=m["firmware"],
                    config_sha256=digest(cfg.to_bytes()), config=cfg.as_dict(), unique_records=0,
                    verified_single_transaction_records=0, audit_status_counts=Counter(), rules={})
            group = groups[group_key]
            group["unique_records"] += 1
            group["audit_status_counts"][audit["status"]] += 1
            if audit["status"] == "verified":
                group["verified_single_transaction_records"] += 1
                for row in audit["rules"]:
                    rule = group["rules"].setdefault(row["rule_id"], dict(rule_id=row["rule_id"],
                        result_counts=Counter(), matched=[], observed=[]))
                    rule["result_counts"][row["result"]] += 1
                    if row["matched_delay_ms"] is not None: rule["matched"].append(row["matched_delay_ms"])
                    if row["observed_delay_ms"] is not None: rule["observed"].append(row["observed_delay_ms"])
        except (ValueError, KeyError, TypeError, OSError, OverflowError) as exc:
            source.update(status="invalid", error=type(exc).__name__+": "+str(exc))
        progress(number, len(paths))
    for group in groups.values():
        rows = []
        for rule in group.pop("rules").values():
            counts = dict(rule["result_counts"])
            n = sum(counts.values())
            rows.append(dict(rule_id=rule["rule_id"], n=n, result_counts=counts,
                selected_record_window_violation_fraction=(counts.get("EARLY",0)+counts.get("TIMEOUT",0))/n,
                matched_delay=delay_statistics(rule["matched"]), observed_response_delay=delay_statistics(rule["observed"])))
        group["rules"] = sorted(rows, key=lambda x: x["rule_id"])
        group["audit_status_counts"] = dict(group["audit_status_counts"])
        report["groups"].append(group)
    for parent in sorted(parents):
        path = parent / "experiment.json"
        if not path.is_file(): continue
        context = dict(path=str(path), association="same_directory_context_only_not_membership_proof")
        try:
            raw = _bounded_read(path)
            original = json.loads(raw)
            require(isinstance(original, dict), "实验报告不是对象")
            context.update(file_sha256=digest(raw), declared_passed=original.get("passed")
                if type(original.get("passed")) is bool else None, original_error=str(original.get("error", "")))
        except (ValueError, OSError) as exc:
            context.update(declared_passed=None, read_error=str(exc))
        report["experiment_contexts"].append(context)
    statuses = Counter(s["status"] for s in report["sources"])
    warnings = [dict(path=s["path"], crc_errors=s.get("saved_crc_errors"),
                     transport_retries=s.get("saved_transport_retries"))
                for s in report["sources"] if (s.get("saved_crc_errors") or s.get("saved_transport_retries"))]
    report.update(analysis_completed=True, selected_files=len(paths), source_status_counts=dict(statuses),
        unique_capture_contents=len(seen), verified_records=statuses["verified"],
        saved_transport_warning_snapshots=warnings, saved_transport_counter_scope="per_file_cumulative_snapshots_not_summed",
        original_failed_experiment_contexts=sum(c.get("declared_passed") is False for c in report["experiment_contexts"]))
    if cancel(): raise InterruptedError("已取消离线统计，未发布报告，未操作板端")
    return report


def summary_text(report):
    lines = [f"离线批量分析 · 上位机 {report['app_version']}",
        f"选择 {report['selected_files']} 文件 / {report['unique_capture_contents']} 个不同采集内容 / {report['verified_records']} 份样点重判一致",
        "状态："+json.dumps(report["source_status_counts"], ensure_ascii=False),
        f"保存的通信警告快照 {len(report['saved_transport_warning_snapshots'])} 份；原实验FAIL上下文 {report['original_failed_experiment_contexts']} 份（不改写）",
        "", "统计边界：", *["• "+note for note in report["limitations"]]]
    return "\n".join(lines)


def render_html(report):
    esc = lambda value: html.escape(str(value), quote=True)
    number = lambda value: "—" if value is None else f"{value:.6f}"
    sections = ["<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Timing Sentinel · 离线实验报告</title><style>",
        "body{font:15px/1.7 'Microsoft YaHei',sans-serif;max-width:1150px;margin:24px auto;padding:0 20px;color:#183047}",
        "table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #bdccd7;padding:7px;text-align:left;overflow-wrap:anywhere}",
        "th{background:#e6eff5}.note{padding:16px;background:#eef4f7}.warn{color:#a23b26}code{overflow-wrap:anywhere}",
        "@media print{body{margin:0;max-width:none}h2{break-after:avoid}tr{break-inside:avoid}}</style></head><body>",
        "<h1>Timing Sentinel A7 · 离线批量实验报告</h1>",
        f"<p>版本 {esc(report['app_version'])} · UTC {esc(report['generated_utc'])}</p>",
        f"<p>所选文件 <b>{report['selected_files']}</b>，不同采集内容 <b>{report['unique_capture_contents']}</b>，样点重判一致 <b>{report['verified_records']}</b>。</p>",
        "<div class='note'><b>这是所选记录分析，不是整机/正式对照验收证书。</b><ul>",
        *["<li>"+esc(note)+"</li>" for note in report["limitations"]], "</ul></div>"]
    if report["saved_transport_warning_snapshots"] or report["original_failed_experiment_contexts"]:
        sections.append("<p class='warn'><b>存在通信警告快照或原实验FAIL，原失败状态保留。不能据本报告宣布正式对照通过。</b></p>")
    for group in report["groups"]:
        sections += [f"<h2>配置组 {esc(group['group_id'][:12])} · {esc(group['origin'])}</h2>",
            f"<p>不同记录 {group['unique_records']} / 可统计 {group['verified_single_transaction_records']}；配置SHA256 <code>{esc(group['config_sha256'])}</code></p>",
            "<details><summary>查看完整配置与统计准入状态</summary><pre>"+
            esc(json.dumps(dict(config=group['config'], audit_status_counts=group['audit_status_counts']),ensure_ascii=False,indent=2))+"</pre></details>",
            "<table><tr><th>规则</th><th>N</th><th>PASS / EARLY / TIMEOUT</th><th>匹配均值 ms</th><th>观测B均值 / σ ms</th><th>观测B P50 / P95 / P99 ms</th><th>观测B最小 / 最大 ms</th></tr>"]
        for row in group["rules"]:
            counts, obs = row["result_counts"], row["observed_response_delay"]
            sections.append(f"<tr><td>R{row['rule_id']}</td><td>{row['n']}</td><td>"+
                "/".join(str(counts.get(k,0)) for k in ("PASS","EARLY","TIMEOUT"))+
                f"</td><td>{number(row['matched_delay']['mean_ms'])}（N={row['matched_delay']['n']}）</td>"
                f"<td>{number(obs['mean_ms'])} / {number(obs['stddev_ms'])}（N={obs['n']}）</td>"
                f"<td>{number(obs['p50_ms'])} / {number(obs['p95_ms'])} / {number(obs['p99_ms'])}</td>"
                f"<td>{number(obs['min_ms'])} / {number(obs['max_ms'])}</td></tr>")
        sections.append("</table>")
        for row in group["rules"]:
            bins = row["observed_response_delay"]["histogram"]
            if not bins: continue
            maximum = max(b["count"] for b in bins)
            bars = []
            for i, item in enumerate(bins):
                width = int(520*item["count"]/max(1,maximum))
                y = 20+i*30
                bars.append(f"<text x='0' y='{y+15}' font-size='12'>{item['low_ms']:.4f}–{item['high_ms']:.4f} ms</text><rect x='190' y='{y}' width='{width}' height='20' fill='#2687aa'/><text x='{200+width}' y='{y+15}' font-size='12'>{item['count']}</text>")
            sections.append(f"<h3>R{row['rule_id']} 所选记录观测响应分布（含迟到，不含无响应）</h3><svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 780 {40+len(bins)*30}' role='img' aria-label='所选记录延迟直方图'>"+"".join(bars)+"</svg>")
    sections += ["<h2>原实验上下文（只读）</h2><ul>"]
    for context in report["experiment_contexts"]:
        sections.append("<li>"+esc(context["path"])+" · 原passed="+esc(context.get("declared_passed"))+
                        " · "+esc(context.get("original_error",context.get("read_error","")))+
                        "<br>SHA256 <code>"+esc(context.get("file_sha256", "未读取"))+"</code></li>")
    sections += ["</ul><h2>逐文件追溯与排除原因</h2><table><tr><th>文件 / SHA256</th><th>状态</th><th>原因 / 重复来源</th></tr>"]
    for source in report["sources"]:
        detail = source.get("error") or source.get("duplicate_of") or "；".join(source.get("audit",{}).get("reasons",[]))
        sections.append("<tr><td>"+esc(source["path"])+"<br><code>"+esc(source["file_sha256"])+
                        "</code></td><td>"+esc(source["status"])+"</td><td>"+esc(detail)+"</td></tr>")
    sections += ["</table><p>JSON包含完整配置、逐规则统计、文件散列及审计原因；原始记录未修改。</p></body></html>"]
    return "".join(sections)


def export_analysis(report, path):
    path = Path(path).resolve()
    require(path.suffix.lower() in (".json", ".html"), "报告只允许.json或.html，不能覆盖tscap")
    protected = {Path(s["path"]).resolve() for s in report["sources"]}
    protected.update(Path(c["path"]).resolve() for c in report["experiment_contexts"])
    require(path not in protected, "不能覆盖原始记录或原实验报告")
    if path.name.casefold() == "experiment.json":
        raise ValueError("请使用analysis_report名称，不覆盖任何experiment.json")
    content = blob(report) if path.suffix.lower() == ".json" else render_html(report).encode("utf-8")
    with atomic_output(path) as temporary:
        temporary.write_bytes(content)
    return str(path)

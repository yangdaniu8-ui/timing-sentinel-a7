"""Sample-exact profile5 exports. No transport access or evidence mutation."""
import csv
import json
from pathlib import Path

from app.models import TICK_MASK, delta_tick
from app.services.record_store import atomic_output
from mvp5.models import require
from mvp5.store import digest


CSV_FIELDS = (
    "record_id", "origin", "session_id", "config_id", "transfer_complete",
    "waveform_points_received", "waveform_points_expected", "events_received",
    "events_expected", "manual_stop", "incomplete_mask", "event_overwrites",
    "nominal_tick_ns", "reference_kind", "sample_index", "tick_50mhz", "elapsed_ns",
    "relative_to_decision_ns", *(f"CH{i}" for i in range(8)),
)
VCD_IDS = tuple(chr(ord("!") + i) for i in range(8))


def export_info(capture):
    capture.validate()
    m = capture.metadata
    reference = m["decision_timestamp"]
    if reference is None:
        reference = m["first_sample_timestamp"]
    return dict(
        profile="mvp5", record_id=m["record_id"], origin=m["origin"],
        session_id=m["session_id"], config_id=m["config_id"],
        firmware=m["firmware"], transfer_complete=capture.complete,
        waveform_points_received=len(capture.samples), waveform_points_expected=m["valid_count"],
        events_received=len(capture.events) // 16, events_expected=m["event_count"],
        event_overwrites=m["event_overwrites"], manual_stop=m["manual_stop"],
        incomplete_mask=m["incomplete_mask"], nominal_clock_hz=m["clock_hz"],
        nominal_tick_ns=20, sample_divisor=m["sample_divisor"],
        first_sample_tick=m["first_sample_timestamp"], reference_tick=reference,
        reference_kind="decision" if m["decision_timestamp"] is not None else "first_sample",
        relative_start_ns=delta_tick(m["first_sample_timestamp"], reference) * 20,
        independently_calibrated=False, samples_sha256=digest(capture.samples),
        rules=m["rules"],
        note="Sampled digital levels only; no invented event-level transitions or missing samples.",
    )


def _prepare(capture, path, suffix):
    info = export_info(capture)
    require(Path(path).suffix.lower() == suffix, f"请使用{suffix}扩展名，不能覆盖tscap原始记录")
    require(bool(capture.samples), "尚无收到的波形样点；请先读取波形，元数据仍可单独导出")
    return info


def export_csv(capture, path):
    info = _prepare(capture, path, ".csv")
    prefix = tuple(info[k] for k in CSV_FIELDS[:CSV_FIELDS.index("sample_index")])
    first = info["first_sample_tick"]
    divisor = info["sample_divisor"]
    with atomic_output(path) as temporary:
        # UTF-8 BOM helps Windows spreadsheet applications identify the encoding.
        with temporary.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(CSV_FIELDS)
            for i, value in enumerate(capture.samples):
                elapsed = i * divisor * 20
                writer.writerow((*prefix, i, (first + i * divisor) & TICK_MASK,
                                 elapsed, info["relative_start_ns"] + elapsed,
                                 *((value >> ch) & 1 for ch in range(8))))
    return info


def export_vcd(capture, path):
    info = _prepare(capture, path, ".vcd")
    # Escape dollar signs so imported metadata cannot terminate a VCD $comment.
    comment = json.dumps(info, ensure_ascii=True, sort_keys=True).replace("$", "\\u0024")
    with atomic_output(path) as temporary:
        with temporary.open("w", encoding="ascii", newline="\n") as output:
            output.write("$version Timing Sentinel A7 profile5 sampled waveform $end\n")
            output.write("$timescale 1 ns $end\n")
            output.write("$comment " + comment + " $end\n")
            output.write("$scope module timing_sentinel $end\n")
            for ch, identifier in enumerate(VCD_IDS):
                output.write(f"$var wire 1 {identifier} CH{ch} $end\n")
            output.write("$upscope $end\n$enddefinitions $end\n#0\n$dumpvars\n")
            previous = capture.samples[0]
            for ch, identifier in enumerate(VCD_IDS):
                output.write(f"{(previous >> ch) & 1}{identifier}\n")
            output.write("$end\n")
            last_written = 0
            step = info["sample_divisor"] * 20
            for i, value in enumerate(capture.samples[1:], 1):
                changed = value ^ previous
                if changed:
                    last_written = i * step
                    output.write(f"#{last_written}\n")
                    for ch, identifier in enumerate(VCD_IDS):
                        if changed & (1 << ch):
                            output.write(f"{(value >> ch) & 1}{identifier}\n")
                previous = value
            # Retain the actual final sampled time even when every signal is static.
            end = (len(capture.samples) - 1) * step
            if end != last_written:
                output.write(f"#{end}\n")
    return info

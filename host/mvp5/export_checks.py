"""Read exports back independently and reconstruct every received sample."""
import csv
import json
from pathlib import Path

from app.models import TICK_MASK, delta_tick
from mvp5.models import require


def verify_csv(capture, path):
    capture.validate()
    m = capture.metadata
    reference = m["decision_timestamp"] if m["decision_timestamp"] is not None else m["first_sample_timestamp"]
    seen = 0
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        for i, row in enumerate(reader):
            require(i < len(capture.samples), "CSV fabricated trailing samples")
            require(int(row["sample_index"]) == i, "CSV sample order")
            expected_tick = (m["first_sample_timestamp"] + i * m["sample_divisor"]) & TICK_MASK
            require(int(row["tick_50mhz"]) == expected_tick, "CSV absolute tick")
            require(int(row["elapsed_ns"]) == i * m["sample_divisor"] * 20, "CSV elapsed time")
            relative = (delta_tick(m["first_sample_timestamp"], reference) + i * m["sample_divisor"]) * 20
            require(int(row["relative_to_decision_ns"]) == relative, "CSV reference alignment")
            bits = [int(row[f"CH{ch}"]) for ch in range(8)]
            require(all(v in (0, 1) for v in bits), "CSV digital levels")
            require(sum(v << ch for ch, v in enumerate(bits)) == capture.samples[i], "CSV raw levels")
            require(row["record_id"] == m["record_id"] and row["origin"] == m["origin"], "CSV provenance")
            require(int(row["session_id"]) == m["session_id"] and int(row["config_id"]) == m["config_id"], "CSV identity")
            require(row["transfer_complete"] == str(capture.complete), "CSV completeness")
            require(row["manual_stop"] == str(m["manual_stop"]) and int(row["incomplete_mask"]) == m["incomplete_mask"], "CSV cancellation/truncation")
            require(int(row["event_overwrites"]) == m["event_overwrites"] and int(row["nominal_tick_ns"]) == 20, "CSV logs/nominal clock")
            require(row["reference_kind"] == ("decision" if m["decision_timestamp"] is not None else "first_sample"), "CSV reference kind")
            for field, expected in (("waveform_points_received", len(capture.samples)),
                                    ("waveform_points_expected", m["valid_count"]),
                                    ("events_received", len(capture.events) // 16),
                                    ("events_expected", m["event_count"])):
                require(int(row[field]) == expected, "CSV received/expected lengths")
            seen += 1
    require(seen == len(capture.samples) and seen > 0, "CSV missing samples")
    return dict(passed=True, samples=seen, transfer_complete=capture.complete)


def verify_vcd(capture, path):
    capture.validate()
    text = Path(path).read_text(encoding="ascii")
    lines = text.splitlines()
    require("$timescale 1 ns $end" in lines, "VCD timescale")
    comments = [s for s in lines if s.startswith("$comment ")]
    require(len(comments) == 1 and comments[0].endswith(" $end"), "VCD metadata comment")
    info = json.loads(comments[0][9:-5])
    m = capture.metadata
    require(info["record_id"] == m["record_id"] and info["origin"] == m["origin"], "VCD provenance")
    require(info["transfer_complete"] == capture.complete and not info["independently_calibrated"], "VCD completeness/precision")
    require(info["waveform_points_received"] == len(capture.samples) and info["waveform_points_expected"] == m["valid_count"], "VCD sample counts")
    require(info["events_received"] == len(capture.events) // 16 and info["events_expected"] == m["event_count"], "VCD event counts")
    reference = m["decision_timestamp"] if m["decision_timestamp"] is not None else m["first_sample_timestamp"]
    require(info["reference_tick"] == reference and info["first_sample_tick"] == m["first_sample_timestamp"], "VCD references")
    require(info["relative_start_ns"] == delta_tick(m["first_sample_timestamp"], reference) * 20, "VCD relative alignment")
    identifiers = {}
    for line in lines:
        if line.startswith("$var "):
            parts = line.split()
            require(parts[:3] == ["$var", "wire", "1"] and parts[-1] == "$end", "VCD wire declaration")
            ch = int(parts[4][2:])
            require(parts[4] == f"CH{ch}" and ch in range(8) and parts[3] not in identifiers, "VCD channel mapping")
            identifiers[parts[3]] = ch
    require(sorted(identifiers.values()) == list(range(8)), "VCD needs eight unique channels")
    states = []
    initialized = set()
    value = 0
    now = None
    started = False
    step = m["sample_divisor"] * 20
    for line in lines:
        if line == "$enddefinitions $end":
            started = True
        elif started and line.startswith("#"):
            if now is not None:
                require(len(initialized) == 8, "VCD initial levels missing")
                states.append((now, value))
            timestamp = int(line[1:])
            require(timestamp >= 0 and timestamp % step == 0 and (now is None or timestamp > now), "VCD timestamp ordering")
            now = timestamp
        elif started and line and line[0] in "01":
            require(now is not None and line[1:] in identifiers, "VCD unknown signal")
            ch = identifiers[line[1:]]
            value = (value & ~(1 << ch)) | (int(line[0]) << ch)
            initialized.add(ch)
    require(now is not None and len(initialized) == 8, "VCD no initial samples")
    states.append((now, value))
    require(states[0][0] == 0 and states[-1][0] == (len(capture.samples) - 1) * step, "VCD actual capture duration")
    pos = 0
    for i, sample in enumerate(capture.samples):
        while pos + 1 < len(states) and states[pos + 1][0] <= i * step:
            pos += 1
        require(states[pos][1] == sample, "VCD reconstructed raw levels")
    return dict(passed=True, samples=len(capture.samples), transfer_complete=capture.complete)

import csv
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID
import zipfile
from app.models import Capture
from protocol.messages import unpack_events

MAX_RECORD_BYTES = 2_000_000


@contextmanager
def atomic_output(path):
    """Publish only a finished export; a failed write keeps the old destination."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ts_export_", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        yield temporary
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def save_record(capture, path):
    capture.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    files = {"metadata.json": json_bytes(capture.metadata),
             "observations.json": json_bytes({"algorithm": "mvp1", "note": capture.metadata["observation"]})}
    ranges = []
    if capture.complete:
        files.update({"samples.bin": capture.samples, "events.bin": capture.events})
    else:
        for kind, data in (("capture", capture.samples), ("events", capture.events)):
            name = f"segments/{kind}_0.bin"
            files[name] = data
            ranges.append({"kind": kind, "offset": 0, "length": len(data), "file": name})
    manifest = {"schema_version": 1, "profile": "mvp1", "origin": capture.metadata["origin"],
                "record_id": capture.metadata["record_id"], "transfer_complete": capture.complete,
                "ranges": ranges, "files": {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                                              for name, data in files.items()}}
    fd, temporary = tempfile.mkstemp(prefix=".tscap_", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json_bytes(manifest))
            for name, data in files.items():
                archive.writestr(name, data)
        restored = load_record(temporary)
        if restored.samples != capture.samples or restored.events != capture.events:
            raise ValueError("保存回读不一致")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)


def load_record(path):
    try:
        return _load_record(path)
    except (KeyError, TypeError, AttributeError, UnicodeError, zipfile.BadZipFile) as exc:
        raise ValueError(f"记录结构损坏或字段类型错误: {exc}") from exc


def _load_record(path):
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or sum(info.file_size for info in infos) > MAX_RECORD_BYTES:
            raise ValueError("记录大小超限或包含重复文件")
        allowed = {"manifest.json", "metadata.json", "observations.json", "samples.bin", "events.bin",
                   "segments/capture_0.bin", "segments/events_0.bin"}
        if not set(names) <= allowed or "manifest.json" not in names:
            raise ValueError("记录文件清单非法")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict):
            raise ValueError("记录清单必须为对象")
        if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1 or manifest.get("profile") != "mvp1":
            raise ValueError("不支持的记录版本")
        complete = manifest.get("transfer_complete")
        if type(complete) is not bool:
            raise ValueError("完整性标志必须为布尔值")
        files = {}
        declared = manifest["files"]
        expected_files = {"metadata.json", "observations.json"} | (
            {"samples.bin", "events.bin"} if complete else
            {"segments/capture_0.bin", "segments/events_0.bin"})
        if not isinstance(declared, dict) or set(declared) != expected_files:
            raise ValueError("完整/部分记录文件布局不一致")
        if set(names) != set(declared) | {"manifest.json"}:
            raise ValueError("清单与包内文件不一致")
        for name, description in declared.items():
            if name not in allowed:
                raise ValueError("非法文件名")
            data = archive.read(name)
            if not isinstance(description, dict) or type(description.get("size")) is not int or not isinstance(description.get("sha256"), str):
                raise ValueError(f"文件描述字段类型错误: {name}")
            if len(data) != description["size"] or hashlib.sha256(data).hexdigest() != description["sha256"]:
                raise ValueError(f"文件哈希/大小不匹配: {name}")
            files[name] = data
        metadata = json.loads(files["metadata.json"])
        if not isinstance(metadata, dict) or type(metadata.get("transfer_complete")) is not bool:
            raise ValueError("元数据或完整性字段类型错误")
        for field in ("record_id", "app_version", "firmware", "observation"):
            if not isinstance(metadata.get(field), str) or not metadata[field]:
                raise ValueError(f"记录身份/显示字段缺失或类型错误: {field}")
        try:
            UUID(metadata["record_id"])
        except ValueError as exc:
            raise ValueError("记录身份必须为UUID") from exc
        observations = json.loads(files["observations.json"])
        if not isinstance(observations, dict) or observations.get("algorithm") != "mvp1" or observations.get("note") != metadata.get("observation"):
            raise ValueError("观察说明与元数据不一致")
        if manifest["origin"] != metadata["origin"] or manifest["record_id"] != metadata["record_id"]:
            raise ValueError("记录来源或身份不一致")
        if complete:
            samples, events = files["samples.bin"], files["events.bin"]
            if manifest.get("ranges") != []:
                raise ValueError("完整记录不应包含部分片段")
        else:
            ranges = manifest.get("ranges")
            if not isinstance(ranges, list) or any(
                not isinstance(item, dict) or type(item.get("offset")) is not int or
                type(item.get("length")) is not int for item in ranges
            ):
                raise ValueError("片段范围字段类型错误")
            expected = [{"kind": k, "offset": 0, "length": len(files[f"segments/{k}_0.bin"]),
                         "file": f"segments/{k}_0.bin"} for k in ("capture", "events")]
            if manifest.get("ranges") != expected:
                raise ValueError("首版仅支持连续前缀片段")
            samples, events = files["segments/capture_0.bin"], files["segments/events_0.bin"]
        capture = Capture(metadata, samples, events).validate()
        if capture.complete != complete or metadata["transfer_complete"] != complete:
            raise ValueError("完整性标志与实际数据不一致")
        unpack_events(events)
        return capture


def export_json(capture, path):
    capture.validate()
    value = {"metadata": capture.metadata, "transfer_complete": capture.complete,
             "received_samples": len(capture.samples), "received_event_records": len(capture.events) // 16,
             "samples_sha256": hashlib.sha256(capture.samples).hexdigest(),
             "events_sha256": hashlib.sha256(capture.events).hexdigest(),
             "events": unpack_events(capture.events)}
    with atomic_output(path) as temporary:
        temporary.write_bytes(json_bytes(value))
    return str(path)


def export_csv(capture, path):
    capture.validate()
    fields = ["index", "origin", "session_id", "timestamp_tick", "gpio_value", "gpio_rise",
              "gpio_fall", "uart_byte", "uart_error", "rule0_result", "overlap_mask", "source_accept_mask", "state"]
    with atomic_output(path) as temporary, temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for index, event in enumerate(unpack_events(capture.events)):
            writer.writerow({"index": index, "origin": capture.metadata["origin"],
                             "session_id": capture.metadata["session_id"], "timestamp_tick": event["tick"],
                             "gpio_value": f"0x{event['value']:02X}", "gpio_rise": f"0x{event['rise']:02X}",
                             "gpio_fall": f"0x{event['fall']:02X}",
                             "uart_byte": "" if event["uart"] is None else f"0x{event['uart']:02X}",
                             "uart_error": event["uart_error"],
                             "rule0_result": event["result"], "overlap_mask": event["overlap_mask"],
                             "source_accept_mask": event["accepted_mask"], "state": event["state"]})
    return str(path)


def export_vcd(capture, path):
    """Lossless transition-only sampled waveform; missing suffix is omitted."""
    capture.validate()
    m = capture.metadata
    with atomic_output(path) as temporary, temporary.open("w", encoding="ascii", newline="\n") as stream:
        stream.write(f"$version Timing Sentinel {m.get('app_version', 'unknown')} $end\n")
        stream.write(f"$comment origin={m['origin']}; complete={capture.complete}; session={m['session_id']}; first_tick={m['first_sample_timestamp']}; nominal_clock_hz={m['clock_hz']} $end\n")
        stream.write("$timescale 1 ns $end\n$scope module gpio $end\n")
        for channel in range(8):
            stream.write(f"$var wire 1 {chr(33+channel)} CH{channel} $end\n")
        stream.write("$upscope $end\n$enddefinitions $end\n")
        previous = None
        period_ns = m["sample_divisor"] * 20
        for i, value in enumerate(capture.samples):
            changed = 0xFF if previous is None else previous ^ value
            if changed or i == len(capture.samples) - 1:
                stream.write(f"#{i * period_ns}\n")
            for channel in range(8):
                if changed & (1 << channel):
                    stream.write(f"{(value >> channel) & 1}{chr(33+channel)}\n")
            previous = value
    return str(path)

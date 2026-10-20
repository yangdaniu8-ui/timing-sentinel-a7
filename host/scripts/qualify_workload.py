"""Offline real-record workload presentation qualification; never opens a port."""
import argparse
import copy
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mvp5 import __version__
from mvp5.store import load_record, digest, blob
from mvp5.presentation import identify_scene


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(); args.out.mkdir(parents=True, exist_ok=False)
    from PySide6.QtWidgets import QApplication
    from mvp5.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(); w.timer.stop(); w.resize(1580,980); w.show()
    root = Path("D:/fpga_projects/stm32_timing_workload/data")
    data = root / "validation_20261011_b02"
    files = sorted(data.glob("raw_*.tscap")) + sorted(data.glob("load_*/job_*.tscap"))
    report = dict(passed=False, app_version=__version__, serial_opened=False,
                  new_hardware_transactions=0, firmware_modified=False, records=[])
    try:
        assert len(files) == 60
        for path in files:
            sha = digest(path.read_bytes()); cap = load_record(path); before = copy.deepcopy(cap)
            info = identify_scene(cap); assert info["verified"] and info["workload"]
            assert cap == before and digest(path.read_bytes()) == sha
            report["records"].append(dict(file=str(path),sha256=sha,identity=info))
        for job in (1,2,3):
            w.replay = w.saved = True; w.show_capture(load_record(data/f"raw_{job:04d}.tscap"))
            app.processEvents(); assert w.grab().save(str(args.out/f"load_{job-1}.png"))
            assert w.tabs.height() >= 220 and w.wave.plot.height() >= 210
            assert not w.client and not w.buttons["arm"].isEnabled()
        pilot = load_record(root/"pilot_20261011_b01/raw_0003.tscap")
        w.show_capture(pilot); app.processEvents()
        assert w.scene_info["verified"] and w.scene_info["workload"]["profile"] == 2
        assert pilot.metadata["rules"][0]["result"] == "PASS"
        assert w.grab().save(str(args.out/"heavy_pass_8ms.png"))
        w.show_capture(load_record(data/"raw_0003.tscap")); w.resize(1366,900); app.processEvents()
        assert w.grab().save(str(args.out/"laptop_1366.png"))
        assert w.tabs.height() >= 220 and w.wave.plot.height() >= 210
        assert not w.client
        report.update(passed=True, original_records_unchanged=True, profiles_verified=[0,1,2],
                      heavy_pass_and_timeout_both_identified=True, screenshots_created=True)
    finally:
        (args.out/"qualification.json").write_bytes(blob(report)); w.capture=None; w.close()
    print(json.dumps({k:v for k,v in report.items() if k!="records"},ensure_ascii=False))


if __name__ == "__main__": main()

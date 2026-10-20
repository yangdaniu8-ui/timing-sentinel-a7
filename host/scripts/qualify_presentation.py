"""Offline real-record source GUI audit, screenshots, and immutable raw hashes."""
import argparse
import copy
import json
from pathlib import Path
import sys
import unittest

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
from mvp5.store import load_record, digest, blob
from mvp5.presentation import identify_scene
from mvp5.models import unpack_events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    from PySide6.QtWidgets import QApplication
    from mvp5.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(); w.timer.stop(); w.resize(1580,980); w.show()
    records = Path("D:/fpga_projects/timing_sentinel_a7_compare/data/demo_recovery_20261011_b03/restored_hardware_six")
    report = dict(passed=False, serial_opened=False, new_hardware_transactions=0, records=[])
    try:
        for path in sorted(records.glob("*.tscap")):
            cap = load_record(path); before = copy.deepcopy(cap); sha = digest(path.read_bytes())
            w.replay = w.saved = True; w.show_capture(cap)
            w.wave.unit_combo.setCurrentIndex(0); w.wave.reference_combo.setCurrentIndex(0)
            app.processEvents(); w.data_splitter.setSizes([330,300]); app.processEvents()
            info = identify_scene(cap); assert info["verified"]
            assert w.grab().save(str(args.out/(info["scene"]+"_full.png")))
            if w.wave.spi_zoom.isEnabled():
                w.wave.zoom_spi(); app.processEvents()
                assert w.grab().save(str(args.out/(info["scene"]+"_spi.png")))
            assert before == cap and digest(path.read_bytes()) == sha
            report["records"].append(dict(file=str(path), sha256=sha, scene=info,
                rows=w.event_table.rowCount(), samples=len(cap.samples)))
        w.wave.unit_combo.setCurrentIndex(0); w.wave.fit()
        w.resize(1366,900); app.processEvents()
        assert w.grab().save(str(args.out/"laptop_1366.png"))
        for size in ((1580,980),(1366,900)):
            w.resize(*size); app.processEvents()
            assert w.tabs.height()>=180 and w.wave.plot.height()>=210
        assert w.client is None
        report.update(passed=True, screenshots_created=True, original_records_unchanged=True)
    finally:
        (args.out/"qualification.json").write_bytes(blob(report))
        w.capture=None; w.close()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__": main()

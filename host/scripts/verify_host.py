"""Run the suite and generate deterministic mock demo evidence and a UI image."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import time
import unittest
import json

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
from app.cli import demo
from app import __version__
from app.services.record_store import export_csv, export_json, export_vcd, save_record
from scripts.build_release import environment_versions, source_fingerprint, version_tag, write_json


def main():
    out = HOST.parent / "data" / "verification"
    out.mkdir(parents=True, exist_ok=True)
    tag = version_tag()
    report_path = out / f"host_{tag}_verification.json"
    input_hash = source_fingerprint(verification=True)
    # Invalidate any earlier success before imports/tests/demos can fail.
    report = {"app_version": __version__, "tests": 0, "failures": 0, "errors": 0,
              "passed": False, "source": "mock", "hardware_validation": False, "demos": [],
              "verification_sha256": input_hash, "environment": environment_versions()}
    write_json(report_path, report)
    suite = unittest.defaultTestLoader.discover(str(HOST / "tests"), top_level_dir=str(HOST))
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report.update(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  elapsed_seconds=round(time.monotonic() - started, 3))
    write_json(report_path, report)
    if not result.wasSuccessful():
        return 1
    for scenario in ("normal", "early", "late", "missing"):
        capture = demo(scenario)
        path = HOST.parent / "data" / "captures" / f"mock_{scenario}_{tag}.tscap"
        save_record(capture, path)
        export_json(capture, path.with_suffix(".json"))
        export_csv(capture, path.with_suffix(".csv"))
        export_vcd(capture, path.with_suffix(".vcd"))
        report["demos"].append({"scenario": scenario, "result": capture.metadata["result"],
                                "samples": len(capture.samples), "complete": capture.complete})
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow
    application = QApplication.instance() or QApplication([])
    window = MainWindow(auto_connect=False)
    window.timer.stop()
    window.show()
    window.scenario.setCurrentIndex(2)
    window.present(demo("late"))
    window.source.setText("模拟数据 · MOCK / 1规则")
    window.summary.setText("证据已冻结  |  TIMEOUT  |  2.2ms迟到响应 · 模拟演示")
    window.saved_path = "verification"
    application.processEvents()
    image_path = out / f"host_{tag}.png"
    image_ok = window.grab().save(str(image_path))
    window.close()
    report["screenshot"] = str(image_path)
    report["passed"] = bool(image_ok and result.testsRun > 0 and source_fingerprint(verification=True) == input_hash)
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

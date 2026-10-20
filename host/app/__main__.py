import argparse
import sys
import json
import logging
from pathlib import Path
from app import __version__
from app.runtime import data_root, setup_logging
from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-connect", action="store_true")
    parser.add_argument("--demo", choices=("normal", "early", "late", "missing"))
    parser.add_argument("--data-dir", type=Path, help="Override writable data/log root")
    parser.add_argument("--smoke-test-report", type=Path, help="Run bundled GUI/mock/replay check and exit")
    args = parser.parse_args()
    if args.no_connect and args.demo:
        parser.error("--demo requires auto-connect")
    application = QApplication(sys.argv[:1])
    application.setApplicationName("Timing Sentinel A7")
    root = data_root(args.data_dir)
    logger = setup_logging(root)
    def exception_hook(kind, value, traceback):
        logger.error("Unhandled exception", exc_info=(kind, value, traceback))
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Timing Sentinel运行错误", f"{value}\n日志：{root / 'logs' / 'host.log'}")
    sys.excepthook = exception_hook
    if args.smoke_test_report:
        from app.cli import demo
        from app.services.record_store import load_record, save_record, export_vcd
        from transport.serial_port import enumerate_ports
        capture = demo("normal")
        file = root / "captures" / "smoke_mock.tscap"
        save_record(capture, file)
        restored = load_record(file)
        export_vcd(restored, file.with_suffix(".vcd"))
        window = MainWindow(auto_connect=False, root=root)
        window.show()
        window.present(restored)
        window.source.setText("打包自检 · 模拟数据（不是硬件测试）")
        application.processEvents()
        image_path = root / "smoke_gui.png"
        image_ok = window.grab().save(str(image_path))
        report = {"app_version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
                  "passed": restored.complete and restored.samples == capture.samples and image_ok,
                  "samples": len(restored.samples), "result": restored.metadata["result"],
                  "origin": restored.metadata["origin"], "hardware_validation": False,
                  "ports": enumerate_ports(), "screenshot": str(image_path)}
        args.smoke_test_report.parent.mkdir(parents=True, exist_ok=True)
        args.smoke_test_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.saved_path = str(file)
        window.close()
        return 0 if report["passed"] else 1
    window = MainWindow(auto_connect=not args.no_connect, demo_scenario=args.demo, root=root)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())

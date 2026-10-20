"""Standalone profile5 entry; hardware experiments require explicit CLI flags."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

from mvp5 import __version__
from mvp5.client import Client
from mvp5.examples import spi_example
from mvp5.experiment import SCENES, verify_capture
from mvp5.models import require
from mvp5.store import blob, digest, load_record, save_record
from app.services.record_store import atomic_output
from app.services.capture_storage import initial_root, preference_path, probe_directory
from transport.serial_port import SerialTransport


def write_json(path, value):
    with atomic_output(path) as temp:
        temp.write_bytes(blob(value))


def default_root():
    # A portable binary may live on a read-only share or in Program Files.
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "TimingSentinelA7" / "profile5" / "captures"


def emit(message):
    if sys.stdout:
        print(message, flush=True)


def same_frozen(actual, expected):
    require(actual.complete and expected.complete, "Frozen evidence must be complete")
    for key in ("session_id", "config_id", "first_sample_timestamp", "decision_timestamp", "capture_anchor_timestamp", "config", "rules"):
        require(blob(actual.metadata[key]) == blob(expected.metadata[key]), "Saved frozen identity mismatch: " + key)
    require(actual.samples == expected.samples and actual.events == expected.events,
            "Live frozen raw evidence differs from the saved record; not released")


def soak(port, count, out, prior_record=None):
    require(port and out and 1 <= count <= 10000, "Explicit port, fresh output and 1..10000 rounds required")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    probe_directory(out)
    started = time.monotonic()
    report = dict(schema=1, app_version=__version__, frozen=bool(getattr(sys, "frozen", False)),
                  generated_utc=datetime.now(timezone.utc).isoformat(), passed=False,
                  requested_rounds=count, source="hardware", independently_calibrated=False,
                  cases=[], arm_used=True, samples_synthesized=False)
    transport = None
    try:
        transport = SerialTransport(port, 115200)
        client = Client(transport)
        identity = client.connect()
        report["identity"] = identity
        status = identity["status"]
        if status["state"] == "FROZEN":
            require(prior_record is not None, "FROZEN data protected: supply its previously saved record")
            prior = load_record(prior_record)
            actual = client.read_capture()
            save_record(actual, out / "protected_previous.tscap")
            same_frozen(actual, prior)
            verify_capture(actual)
            report["protected_previous"] = dict(original=str(Path(prior_record).resolve()),
                original_sha256=digest(Path(prior_record).read_bytes()), saved="protected_previous.tscap",
                saved_sha256=digest((out / "protected_previous.tscap").read_bytes()),
                session_id=status["session_id"], new_transaction=False)
            client.release()
        else:
            require(status["state"] == "IDLE", "Do not interrupt an active capture")
        client.configure(spi_example())
        identities = set()
        first_scene = None
        for i in range(count):
            report["active_round"] = i + 1
            write_json(out / "experiment.json", report)
            client.arm()
            client.wait_frozen(3)
            cap = client.read_capture()
            name = "round_%03d_s%d.tscap" % (i + 1, client.session_id)
            path = out / name
            save_record(cap, path)  # Save failures/partial prefixes BEFORE verification or release.
            report["last_saved_file"] = name
            require(load_record(path) == cap, "Saved roundtrip mismatch")
            check = verify_capture(cap)
            require(cap.metadata["crc_errors"] == 0 and cap.metadata["transport_retries"] == 0,
                    "Transport CRC/retry observed; evidence retained")
            epoch_id = (cap.metadata["config_id"], cap.metadata["session_id"])
            require(epoch_id not in identities, "Repeated session is not a new transaction")
            identities.add(epoch_id)
            if first_scene is None:
                first_scene = SCENES.index(check["scene"])
            require(check["scene"] == SCENES[(first_scene + i) % 6], "MCU six-scene sequence changed")
            report["cases"].append(dict(file=name, sha256=digest(path.read_bytes()),
                                       session_id=epoch_id[1], config_id=epoch_id[0], verification=check))
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            write_json(out / "experiment.json", report)
            emit("ROUND %d/%d PASS %s session=%d elapsed=%.1fs" %
                 (i + 1, count, check["scene"], epoch_id[1], report["elapsed_seconds"]))
            if i < count - 1:
                client.release()
        report.update(passed=True, distinct_transactions=len(identities),
                      scene_counts=dict(Counter(c["verification"]["scene"] for c in report["cases"])),
                      total_samples=sum(c["verification"]["samples"] for c in report["cases"]),
                      total_events=sum(c["verification"]["events"] for c in report["cases"]),
                      final_status=client.status(), final_record_saved_frozen=True,
                      assurance="bounded_repeated_transaction_test_not_long_term_or_absolute_timing_certification")
        return report
    except BaseException as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(out / "experiment.json", report)
        if transport:
            transport.close()


def gui(args):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from mvp5.ui import MainWindow
    app = QApplication(sys.argv[:1])
    fallback = (default_root() if getattr(sys, 'frozen', False) else
                Path(__file__).resolve().parents[1] / 'data' / 'user_captures')
    root, storage_warning = initial_root(args.root, fallback, preference_path())
    window = MainWindow(root=root, port=None if args.self_test_report else args.port)
    window.check_capture_root()
    if storage_warning:
        window.log.appendPlainText('保存位置设置未采用：' + storage_warning)
    if not getattr(sys, "frozen", False):
        window.setWindowTitle(window.windowTitle() + " · 源码调试")
    from app.diagnostics import record_event
    record_event("gui_created", app_version=__version__, source_debug=not bool(getattr(sys,"frozen",False)))
    window.show()
    if args.open:
        cap = load_record(args.open)
        window.replay = window.saved = True
        window.show_capture(cap,path=args.open)
    if args.self_test_report:
        output = Path(args.self_test_report)
        require(not output.exists(), "Self-test report already exists")
        report = dict(passed=False, frozen=bool(getattr(sys, "frozen", False)), app_version=__version__,
                      source="replay_of_saved_hardware", new_hardware_transactions=0,
                      independently_calibrated=False, checks=[], root=str(root))

        def test():
            try:
                from unittest.mock import patch
                from mvp5.export_checks import verify_csv, verify_vcd
                from mvp5.exports import export_csv, export_vcd
                from mvp5.models import Capture
                from mvp5.presentation import identify_scene
                resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
                records = sorted((resources / "demo_records").glob("*.tscap"))
                require(len(records) == 6, "Six hardware demo records required")
                seen = set()
                for path in records:
                    cap = load_record(path)
                    check = verify_capture(cap)
                    seen.add(check["scene"])
                    window.replay = window.saved = True
                    window.show_capture(cap)
                    app.processEvents()
                    info = identify_scene(cap)
                    require(window.scene_info == info and info["verified"], "Packaged scene detection failed")
                    require(window.wave.reference == cap.metadata["rules"][0]["a_tick"], "A-event reference mismatch")
                    require(window.tabs.height() >= 220, "Event panel collapsed below readable size")
                    if window.wave.spi_zoom.isEnabled():
                        window.wave.zoom_spi(); app.processEvents()
                        require(window.wave.axis.unit == "us", "SPI zoom did not select explicit microseconds")
                        window.wave.unit_combo.setCurrentIndex(0); window.wave.fit(); app.processEvents()
                    axis = window.wave.plot.getAxis("bottom")
                    require(not axis.autoSIPrefix and axis.autoSIPrefixScale == 1.0,
                            "First/repeated record ms axis changed")
                    require(axis.tickStrings([-3., 0., 3.], axis.autoSIPrefixScale * axis.scale, 1.)
                            == ["-3", "0", "3"], "Displayed ms ticks changed")
                    require(not window.client and not window.buttons["arm"].isEnabled()
                            and not window.buttons["release"].isEnabled(), "Replay must not control live buffers")
                    for r, entry in enumerate(cap.metadata["rules"]):
                        require(window.result_table.item(r, 1).text() == entry["result"], "GUI replay matrix mismatch")
                    copy = output.parent / ("replayed_" + path.name)
                    save_record(cap, copy)
                    require(load_record(copy) == cap, "Packaged save roundtrip failed")
                    exports = {}
                    for kind, checker in (("csv", verify_csv), ("vcd", verify_vcd)):
                        destination = output.parent / (path.stem + "." + kind)
                        saved, replay, state = window.saved, window.replay, window.device_state
                        with patch("mvp5.ui.QFileDialog.getSaveFileName", return_value=(str(destination), "")):
                            window.export_menu.actions()[1 if kind == "csv" else 2].trigger()
                        deadline = time.monotonic() + 30
                        while window.busy and time.monotonic() < deadline:
                            app.processEvents(); time.sleep(.005)
                        require(not window.busy and destination.is_file(), "Packaged GUI export failed")
                        require((window.saved, window.replay, window.device_state) == (saved, replay, state)
                                and window.capture == cap and not window.client, "Export changed evidence/device state")
                        exports[kind] = checker(cap, destination)
                    report["checks"].append(dict(file=path.name, sha256=digest(path.read_bytes()), verification=check,
                                               displayed_ms_axis_verified=True, si_scale=axis.autoSIPrefixScale,
                                               scene_detection=info, a_reference_verified=True,
                                               exports=exports))
                require(seen == set(SCENES), "Six scenes missing")
                if __version__ in ("0.4.6", "0.4.7", "0.4.8", "0.4.9"):
                    workload_records = sorted((resources / "workload_records").glob("*.tscap"))
                    require(len(workload_records) == 4, "Four workload records required")
                    report["workload_checks"] = []; profiles = set(); heavy_results = set()
                    for path in workload_records:
                        task_cap = load_record(path); sha = digest(path.read_bytes())
                        window.replay = window.saved = True; window.show_capture(task_cap); app.processEvents()
                        info = identify_scene(task_cap); identity = info.get("workload")
                        require(identity and info["verified"] and window.scene_info == info, "Workload identity failed")
                        require(f"任务 #{identity['id']}" in window.scene_banner.text()
                                and identity["name"] in window.scene_banner.text(), "Workload banner missing identity")
                        require(not window.client and not window.buttons["arm"].isEnabled(), "Workload replay touched board")
                        if __version__ in ("0.4.8", "0.4.9"):
                            require(window.compact_ready and window.empty_wave_hint.isHidden(), "Compact replay layout missing")
                            require(identity['name'] in window.compact_record_hint.text(), "Compact card lost workload identity")
                            require(window.compact_events.rowCount() <= window.event_table.rowCount(), "Compact filter created extra events")
                            require(not window.buttons['release'].isEnabled(), "Compact replay enabled device release")
                        profiles.add(identity["profile"])
                        if identity["profile"] == 2: heavy_results.add(task_cap.metadata["rules"][0]["result"])
                        copied = output.parent/("replayed_"+path.name); save_record(task_cap,copied)
                        require(load_record(copied) == task_cap and digest(path.read_bytes()) == sha, "Workload evidence changed")
                        require(window.grab().save(str(output.parent/(path.stem+".png"))), "Workload screenshot failed")
                        report["workload_checks"].append(dict(file=path.name, sha256=sha, identity=info))
                    require(profiles == {0,1,2} and heavy_results == {"PASS","TIMEOUT"}, "Workload guessed from result")
                    partial_task = Capture(task_cap.metadata,task_cap.samples[:41],task_cap.events)
                    window.show_capture(partial_task); app.processEvents()
                    require(not window.scene_info["verified"] and window.scene_info.get("workload"), "Partial falsely verified")
                    if __version__ in ("0.4.8", "0.4.9"):
                        require(window.metric_labels['result'][0].text() == '数据不完整', "Partial compact card falsely green")
                        report['compact_ui_safety_checks_passed'] = True
                    report["gui_workload_identity_passed"] = True
                    window.show_capture(cap); app.processEvents()
                    if __version__ in ("0.4.7", "0.4.8", "0.4.9"):
                        from mvp5.workload_experiment import workload_config
                        original_capture = window.capture
                        for index, deadline_ms in enumerate((3.5, 8)):
                            window.workload_deadline.setCurrentIndex(index)
                            window.workload_preset.click(); app.processEvents()
                            require(window.read_draft().to_bytes() == workload_config(deadline_ms).to_bytes(),
                                    "Packaged workload preset mismatch")
                            require(window.capture == original_capture and not window.client
                                    and not window.workload_run.isEnabled(), "Offline preset changed evidence or ran hardware")
                        report["gui_workload_runner_presets_passed"] = True
                    if __version__ == "0.4.9":
                        from PySide6.QtWidgets import QMessageBox
                        require(not window.legacy_debug_enabled and window.buttons['batch'].isHidden()
                                and not window.buttons['batch'].isEnabled(), "Default legacy entry exposed")
                        with patch.object(QMessageBox,'warning'):
                            window.batch()
                        require(not window.client and not window.busy, "Rejected legacy slot opened hardware")
                        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.No):
                            window.legacy_mode.setChecked(True)
                        require(not window.legacy_debug_enabled and not window.legacy_mode.isChecked(),
                                "Declined legacy consent did not stay closed")
                        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):
                            window.legacy_mode.setChecked(True)
                        require(window.legacy_debug_enabled and not window.buttons['batch'].isHidden()
                                and not window.workload_run.isEnabled() and not window.client,
                                "Explicit legacy consent/mutual exclusion failed")
                        window.legacy_mode.setChecked(False)
                        require(not window.legacy_debug_enabled and window.buttons['batch'].isHidden()
                                and not window.client and not window.busy, "Legacy exit changed hardware")
                        report['legacy_mode_default_closed_verified'] = True
                        report['legacy_mode_explicit_consent_verified'] = True
                        report['legacy_mode_direct_slot_guard_verified'] = True
                partial = Capture(cap.metadata, cap.samples[:41], cap.events[:16]).validate()
                report["partial_exports"] = {}
                for kind, writer, checker in (("csv", export_csv, verify_csv), ("vcd", export_vcd, verify_vcd)):
                    destination = output.parent / ("partial." + kind)
                    writer(partial, destination)
                    report["partial_exports"][kind] = checker(partial, destination)
                window.wave.plot.setXRange(-0.02, 0.075, padding=0)
                app.processEvents()
                axis = window.wave.plot.getAxis("bottom")
                require(not axis.autoSIPrefix and axis.autoSIPrefixScale == 1.0, "ms axis changed under zoom")
                require(window.grab().save(str(output.with_suffix(".png"))), "Screenshot save failed")
                from mvp5.analysis import analyze_records
                state_before = (window.capture, window.saved, window.replay, window.device_state)
                with patch("mvp5.ui.QFileDialog.getOpenFileNames", return_value=([str(p) for p in records]+[str(records[0])], "")):
                    window.analysis_button.click()
                deadline = time.monotonic()+30
                while window.busy and time.monotonic()<deadline:
                    app.processEvents(); time.sleep(.005)
                require(not window.busy and window.analysis_report is not None, "Packaged batch analysis failed")
                stats = window.analysis_report
                require(stats["selected_files"] == 7 and stats["verified_records"] == 6
                        and stats["source_status_counts"].get("duplicate") == 1
                        and window.analysis_table.rowCount() == 4, "Packaged deduplicated statistics differ")
                require(window.analysis_table.viewport().height()>=4*28, "Statistics table hides its four rule rows")
                expected_stats = analyze_records(records+[records[0]])
                require(stats["groups"] == expected_stats["groups"], "Packaged aggregate mismatch")
                for kind in ("html", "json"):
                    destination = output.parent/("analysis_report."+kind)
                    with patch("mvp5.ui.QFileDialog.getSaveFileName", return_value=(str(destination), "")):
                        (window.analysis_html if kind == "html" else window.analysis_json).click()
                    deadline = time.monotonic()+30
                    while window.busy and time.monotonic()<deadline:
                        app.processEvents(); time.sleep(.005)
                    require(not window.busy and destination.is_file(), "Packaged statistics export failed")
                require((window.capture, window.saved, window.replay, window.device_state) == state_before
                        and not window.client, "Analysis/export changed evidence or device state")
                require(window.grab().save(str(output.parent/"analysis_gui.png")), "Analysis screenshot failed")
                report["batch_analysis"] = dict(selected_files=stats["selected_files"], verified_records=stats["verified_records"],
                    source_status_counts=stats["source_status_counts"], report_sha256={kind:digest((output.parent/("analysis_report."+kind)).read_bytes()) for kind in ("html","json")})
                report.update(passed=True, gui_replay_save_zoom_passed=True,
                              gui_csv_vcd_exports_passed=True, gui_scene_reference_and_panels_passed=True,
                              gui_batch_analysis_and_reports_passed=True, serial_opened=False)
            except BaseException as exc:
                report["error"] = type(exc).__name__ + ": " + str(exc)
            finally:
                write_json(output, report)
                window.close()
                app.exit(0 if report["passed"] else 1)
        QTimer.singleShot(400, test)
    return app.exec()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port")
    parser.add_argument("--root")
    parser.add_argument("--open", type=Path)
    parser.add_argument("--self-test-report", type=Path)
    parser.add_argument("--soak-count", type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--prior-record", type=Path)
    analysis = parser.add_mutually_exclusive_group()
    analysis.add_argument("--analyze-records", type=Path, nargs="+")
    analysis.add_argument("--analyze-directory", type=Path)
    parser.add_argument("--analysis-report", type=Path)
    args = parser.parse_args(argv)
    if args.analyze_records is not None or args.analyze_directory is not None:
        require(args.analysis_report is not None, "Offline analysis requires --analysis-report .json/.html")
        require(not any((args.port, args.root, args.open, args.self_test_report, args.out, args.prior_record))
                and args.soak_count is None, "Offline analysis cannot be combined with hardware or GUI options")
        from mvp5.analysis import analyze_records, export_analysis
        paths = args.analyze_records if args.analyze_records is not None else sorted(args.analyze_directory.glob("*.tscap"))
        result = analyze_records(paths)
        export_analysis(result, args.analysis_report)
        emit("OFFLINE_ANALYSIS_COMPLETE selected=%d verified=%d report=%s (not experiment qualification)"%
             (result["selected_files"],result["verified_records"],args.analysis_report))
        return 0
    require(args.analysis_report is None, "--analysis-report requires a selection of records")
    if args.soak_count is not None:
        require(not args.open and not args.self_test_report and not args.root, "Experiment cannot be combined with GUI options")
        soak(args.port, args.soak_count, args.out, args.prior_record)
        return 0
    return gui(args)


if __name__ == "__main__":
    from app.diagnostics import SessionDiagnostics
    with SessionDiagnostics():
        raise SystemExit(main())

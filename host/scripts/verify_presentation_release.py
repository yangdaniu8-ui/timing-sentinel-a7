"""Qualify a new immutable UI portable; never opens serial or changes firmware."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
from mvp5.store import digest, blob
from mvp5 import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    release = args.release
    report = dict(passed=False, serial_opened=False, new_hardware_transactions=0,
                  firmware_modified=False, tests=[], previous_packages_unchanged=False)
    try:
        env = os.environ.copy(); env["QT_QPA_PLATFORM"] = "offscreen"
        env["PYTHONIOENCODING"] = "utf-8"
        for folder in ("mvp5/tests", "mvp4/tests", "tests"):
            run = subprocess.run([sys.executable,"-m","unittest","discover","-s",folder,"-v"],
                                 cwd=HOST,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            log = args.out/(folder.split("/")[0]+"_tests.log")
            log.write_bytes(run.stdout)
            output = run.stdout.decode("utf-8",errors="replace")
            assert run.returncode == 0 and "Traceback" not in output
            count = int(re.search(r"Ran (\d+) tests",output).group(1))
            report["tests"].append(dict(folder=folder, count=count, passed=True, log=log.name,
                                       sha256=digest(run.stdout)))
        report["total_source_tests"] = sum(t["count"] for t in report["tests"])
        manifest = json.loads((release/"binary_manifest.json").read_text(encoding="utf-8"))
        app = release/"binary/TimingSentinelA7-SPI"
        for name, sha in manifest["runtime_files"].items():
            assert digest((app/name).read_bytes()) == sha, name
        report["runtime_files_verified"] = len(manifest["runtime_files"])
        with zipfile.ZipFile(app/"host_source.zip") as source:
            assert source.testzip() is None
            for name, sha in manifest["source_fingerprint"].items():
                assert digest(source.read(name)) == sha and digest((HOST/name).read_bytes()) == sha
        archive = next(release.glob("*-portable.zip"))
        release_manifest = json.loads((release/"release_manifest.json").read_text(encoding="utf-8"))
        assert digest(archive.read_bytes()) == release_manifest["portable_zip_sha256"]
        with zipfile.ZipFile(archive) as z:
            assert z.testzip() is None
            for name, sha in manifest["runtime_files"].items():
                assert digest(z.read("TimingSentinelA7-SPI/"+name)) == sha
        qa = manifest["frozen_self_test"]
        assert qa["passed"] and qa["frozen"] and qa["gui_scene_reference_and_panels_passed"] and qa["serial_opened"] is False
        assert qa["app_version"] == __version__
        if __version__ in ("0.4.5", "0.4.6", "0.4.7"):
            assert qa["gui_batch_analysis_and_reports_passed"]
            assert qa["batch_analysis"]["verified_records"] == 6
            assert qa["batch_analysis"]["source_status_counts"]["duplicate"] == 1
        if __version__ in ("0.4.6", "0.4.7"):
            assert qa["gui_workload_identity_passed"] and len(qa["workload_checks"]) == 4
        if __version__ == "0.4.7":
            assert qa["gui_workload_runner_presets_passed"]
        assert len(qa["checks"]) == 6
        old_packages = {
            "D:/fpga_projects/timing_sentinel_a7_buttons/releases/tabletop_fullflash_20261011_b03/TimingSentinelA7-Bplus-Tabletop-Candidate-20261011.zip": "320cb5dfca6b7428b08cae73f04ceb9a37361eac1e2f7f53dc022887a1698bb8",
            "D:/fpga_projects/timing_sentinel_a7_buttons/releases/tabletop_fullflash_20261011_b04/TimingSentinelA7-Bplus-Tabletop-Candidate-20261011.zip": "7b99baa0695e0edd3436986f5461c5838244e4b81bbb82f0ae3b2cf6e4dd5e7a",
            "D:/fpga_projects/timing_sentinel_a7_ui043/releases/waveform_export_20261011_b01/TimingSentinelA7-SPI-0.4.3-win64-portable.zip": "bccc770dfc267f9870e88e495d616a0ad922452d2f43ca6548ce50f4aba959a9",
            "D:/fpga_projects/timing_sentinel_a7_ui044/releases/scenario_waveform_20261011_b01/TimingSentinelA7-SPI-0.4.4-win64-portable.zip": "e036ccf54fb45de1e0b60a9c5494f11fc38ab1d064bf4393abe82751595631dc",
            "D:/fpga_projects/timing_sentinel_a7_ui044/releases/batch_analysis_20261011_b02/TimingSentinelA7-SPI-0.4.5-win64-portable.zip": "0de4634b72572f5ed1a2ae72d08ee78307f2cb06ea04ba01a3590ab9f60f1b99",
            "D:/fpga_projects/timing_sentinel_a7_ui044/releases/workload_identity_20261011_b01/TimingSentinelA7-SPI-0.4.6-win64-portable.zip": "31661d70e742d10c856b9529d8f776d436544ba70a534cea475ce7af0a3c5c4a",
        }
        for name, sha in old_packages.items():
            assert digest(Path(name).read_bytes()) == sha, name
        old_source = Path("D:/fpga_projects/timing_sentinel_a7_ui043/host")
        delta = []
        for path in old_source.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                relative = path.relative_to(old_source)
                if digest(path.read_bytes()) != digest((HOST/relative).read_bytes()):
                    delta.append(relative.as_posix())
        report.update(passed=True, app_version=__version__,previous_packages_unchanged=True, source_delta_from_043=sorted(delta),
                      portable_zip=str(archive), sha256=digest(archive.read_bytes()),
                      exe_sha256=digest((app/"TimingSentinelA7-SPI.exe").read_bytes()),
                      packaged_scene_and_export_checks_passed=True,
                      actual_ui_hardware_capture_verified=False, second_pc_verified=False,
                      independently_calibrated=False)
    finally:
        (args.out/"verification.json").write_bytes(blob(report))
    print(json.dumps(report,ensure_ascii=False))


if __name__ == "__main__": main()

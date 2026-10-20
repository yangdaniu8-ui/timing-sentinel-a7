"""New immutable profile5 portable build; never relabel the legacy EXE."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from importlib.metadata import version

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
from mvp5.store import digest, blob, load_record
from mvp5.experiment import SCENES, verify_capture
from mvp5 import __version__


def fingerprint():
    files = []
    for folder in ("app", "protocol", "transport", "simulator", "mvp4", "mvp5"):
        files += [p for p in (HOST / folder).rglob("*.py") if "__pycache__" not in p.parts]
    files += [HOST / "release_spi.py", HOST / "packaging/spi.spec", Path(__file__).resolve()]
    files += [HOST / "scripts/build_release.py", HOST / "requirements-lock.txt", HOST / "requirements-build.txt"]
    files += sorted((HOST / "packaging/license_cache").glob("*.txt"))
    if __version__ in ("0.4.8", "0.4.9"):
        files += [HOST / "packaging/COMPACT_QUICK_START.md", HOST / "packaging/NOTICES.md"]
    return {p.relative_to(HOST).as_posix(): digest(p.read_bytes()) for p in sorted(files)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--experiment", type=Path, required=True)
    ap.add_argument("--workload-records", type=Path, nargs="+", default=[])
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    before = fingerprint()
    experiment = json.loads(args.experiment.read_text(encoding="utf-8"))
    assert experiment["passed"] and len(experiment["cases"]) == 6
    demo = out / "demo_source"
    demo.mkdir()
    seen = set()
    for case in experiment["cases"]:
        source = args.experiment.parent / case["file"]
        assert digest(source.read_bytes()) == case["sha256"]
        checked = verify_capture(load_record(source))
        seen.add(checked["scene"])
        shutil.copy2(source, demo / (checked["scene"] + ".tscap"))
    assert seen == set(SCENES)
    workloads = out / "workload_source"
    workload_sources = []
    if __version__ in ("0.4.6", "0.4.7", "0.4.8", "0.4.9"):
        from mvp5.presentation import identify_scene
        workloads.mkdir()
        profiles = set()
        for source in args.workload_records:
            cap = load_record(source); identity = identify_scene(cap)
            assert identity.get("workload") and identity["verified"], str(source)
            profiles.add(identity["workload"]["profile"])
            name = source.parent.name + "_" + source.name
            shutil.copy2(source, workloads/name)
            workload_sources.append(dict(file=name, source=str(source.resolve()), sha256=digest(source.read_bytes())))
        assert profiles == {0,1,2} and len(workload_sources) == 4, "Three profiles plus heavy PASS example required"
    env = os.environ.copy()
    temp = out / "temp"
    temp.mkdir()
    env.update(TS_SPI_DEMO_DIR=str(demo), TEMP=str(temp), TMP=str(temp))
    if workloads.exists(): env["TS_WORKLOAD_DEMO_DIR"] = str(workloads)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(out / "binary"),
               "--workpath", str(out / "build"), str(HOST / "packaging/spi.spec")]
    with (out / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=temp, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    assert fingerprint() == before, "Sources changed while packaging"
    app = out / "binary/TimingSentinelA7-SPI"
    legacy = HOST / "scripts/build_release.py"
    spec = importlib.util.spec_from_file_location("legacy_license_helper", legacy)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.copy_licenses(app / "licenses")
    for name in ("requirements-lock.txt", "requirements-build.txt"):
        shutil.copy2(legacy.parents[1] / name, app / name)
    with zipfile.ZipFile(app / "host_source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in before:
            archive.write(HOST / name, name)
    shutil.copy2(HOST / "packaging/COMPACT_QUICK_START.md" if __version__ in ("0.4.8", "0.4.9") else HOST / "packaging/QUICK_START.md", app / "QUICK_START.md")
    shutil.copy2(HOST / "packaging/NOTICES.md", app / "NOTICES.md")
    shutil.copytree(demo, app / "demo_records")
    if workloads.exists(): shutil.copytree(workloads, app / "workload_records")
    qa = out / "packaged_qa"
    qa.mkdir()
    exe = app / "TimingSentinelA7-SPI.exe"
    clean = env.copy()
    clean["QT_QPA_PLATFORM"] = "windows"
    subprocess.run([str(exe), "--self-test-report", str(qa / "self_test.json"), "--root", str(qa / "captures")],
                   cwd=str(qa), env=clean, check=True, timeout=120)
    smoke = json.loads((qa / "self_test.json").read_text(encoding="utf-8"))
    assert smoke["passed"] and smoke["frozen"] and smoke["app_version"] == __version__
    manifest = dict(schema=1, version=__version__, profile="mvp5", source_fingerprint=before,
                    python=sys.version, packages={p: version(p) for p in ("PySide6", "numpy", "pyqtgraph", "pyserial", "pyinstaller")},
                    frozen_self_test=smoke, hardware_tests_inside_package=False,
                    independent_instrument_verified=False, installer_built=False, signed=False,
                    source_hardware_example_report=dict(path=str(args.experiment.resolve()), sha256=digest(args.experiment.read_bytes())),
                    workload_example_sources=workload_sources,
                    runtime_files={p.relative_to(app).as_posix(): digest(p.read_bytes()) for p in app.rglob("*") if p.is_file()})
    (out / "binary_manifest.json").write_bytes(blob(manifest))
    archive = out / ("TimingSentinelA7-SPI-" + __version__ + "-win64-portable.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in app.rglob("*"):
            if p.is_file():
                z.write(p, str(p.relative_to(app.parent)))
    (out / "release_manifest.json").write_bytes(blob(dict(passed=True, portable_zip=archive.name,
        portable_zip_sha256=digest(archive.read_bytes()), portable_zip_bytes=archive.stat().st_size,
        binary_manifest_sha256=digest((out / "binary_manifest.json").read_bytes()), installer_built=False)))
    print("SPI_PORTABLE_RELEASE_PASS " + str(archive), flush=True)


if __name__ == "__main__":
    main()

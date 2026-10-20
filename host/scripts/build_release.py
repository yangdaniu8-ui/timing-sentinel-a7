"""Native Windows portable build; only publish ZIP after packaged smoke passes."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import urllib.request
import zipfile

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
from app import __version__


def version_tag(version=__version__):
    return "v" + version.replace(".", "_")


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(host=HOST, *, verification=False):
    """Hash inputs, not mtimes; docs/license-only repackaging stays possible."""
    host = Path(host)
    folders = ["app", "protocol", "transport", "simulator"]
    names = ["desktop_entry.py", "requirements-lock.txt", "requirements-build.txt",
             "packaging/timing_sentinel.spec"]
    if verification:
        folders += ["tests", "scripts"]
        names += ["packaging/installer.iss"]
    files = {path for folder in folders for path in (host / folder).rglob("*")
             if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
    files.update(host / name for name in names)
    hashes = {path.relative_to(host).as_posix(): file_hash(path)
              for path in sorted(files) if path.is_file()}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("utf-8")).hexdigest()


def environment_versions(*, build=False):
    packages = ("PySide6", "PySide6_Essentials", "shiboken6", "numpy", "pyqtgraph",
                "pyserial", "colorama")
    if build:
        packages += ("pyinstaller", "pyinstaller-hooks-contrib")
    return {"python": sys.version, **{name: importlib.metadata.version(name) for name in packages}}


def read_verification(host=HOST, version=__version__):
    path = Path(host).parent / "data/verification" / f"host_{version_tag(version)}_verification.json"
    report = json.loads(path.read_text("utf-8"))
    if (report.get("passed") is not True or report.get("app_version") != version
            or report.get("tests", 0) < 1 or report.get("failures") != 0 or report.get("errors") != 0
            or report.get("verification_sha256") != source_fingerprint(host, verification=True)
            or report.get("environment") != environment_versions()):
        raise RuntimeError("A successful test report matching the current sources and environment is required")
    return report, path


def validate_existing_bundle(app, manifest, host=HOST, version=__version__):
    if (manifest.get("app_version") != version
            or manifest.get("source_sha256") != source_fingerprint(host)
            or manifest.get("environment") != environment_versions(build=True)):
        raise RuntimeError("Existing executable does not match current sources/environment; rebuild without --package-existing")
    files = manifest.get("runtime_files")
    if not isinstance(files, dict) or "TimingSentinelA7.exe" not in files:
        raise RuntimeError("Existing executable has no trustworthy build manifest; rebuild it")
    extras = {"team_quick_start.md", "third_party_notices.md", "protocol_v1.md",
              "host_source.zip", "sha256_manifest.json"}
    for path in Path(app).rglob("*"):
        if not path.is_file():
            continue
        name = path.relative_to(app).as_posix()
        if name not in files and name not in extras and name.split("/")[0] not in {"licenses", "demo_records"}:
            raise RuntimeError(f"Untracked runtime file in existing bundle: {name}")
    for name, expected in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Invalid path in binary build manifest")
        path = Path(app) / relative
        if not path.is_file() or file_hash(path) != expected:
            raise RuntimeError(f"Existing bundle changed since build: {name}")


def run_packaged_smoke(app, report, data_dir, version=__version__):
    report = Path(report)
    # A successful exit alone must never bless a report from a previous run.
    report.unlink(missing_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="windows")
    env.pop("PYTHONPATH", None)
    result = subprocess.run([str(Path(app) / "TimingSentinelA7.exe"), "--smoke-test-report", str(report),
                             "--data-dir", str(data_dir)], cwd=report.parent, env=env, timeout=90)
    if result.returncode or not report.is_file():
        raise RuntimeError("Packaged executable did not produce a successful fresh smoke report")
    evidence = json.loads(report.read_text("utf-8"))
    expected = {"app_version": version, "frozen": True, "passed": True, "origin": "mock",
                "hardware_validation": False, "samples": 65536, "result": "PASS"}
    if any(type(evidence.get(key)) is not type(value) or evidence.get(key) != value
           for key, value in expected.items()):
        raise RuntimeError("Packaged smoke report has an unexpected version, runtime or result")
    return evidence


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def record_installer(installer, host=HOST, version=__version__):
    """Called only after ISCC has emitted a new setup executable."""
    out = Path(host) / "dist" / version
    installer = Path(installer).resolve()
    expected = (out / f"TimingSentinelA7-{version}-setup.exe").resolve()
    if installer != expected or not installer.is_file() or installer.stat().st_size == 0:
        raise RuntimeError("Expected nonempty installer was not produced")
    path = out / "release_manifest.json"
    release = json.loads(path.read_text("utf-8"))
    portable = out / f"TimingSentinelA7-{version}-win64-portable.zip"
    if release.get("app_version") != version or release.get("sha256") != file_hash(portable):
        raise RuntimeError("Portable release manifest does not match the current release")
    release.update(installer_built=True, installer=str(installer),
                   installer_sha256=file_hash(installer), installer_bytes=installer.stat().st_size)
    temporary = path.with_suffix(".json.tmp")
    write_json(temporary, release)
    os.replace(temporary, path)
    return release


def checked_run(command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def copy_licenses(destination):
    destination.mkdir(parents=True, exist_ok=True)
    packages = ("PySide6", "PySide6_Essentials", "shiboken6", "numpy", "pyqtgraph", "pyserial", "colorama", "pyinstaller")
    versions = {}
    for package in packages:
        distribution = importlib.metadata.distribution(package)
        versions[package] = distribution.version
        metadata_path = destination / package / "distribution_metadata.txt"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(distribution.read_text("METADATA") or "", encoding="utf-8")
        for file in distribution.files or []:
            if any("license" in part.lower() or "copying" in part.lower() for part in file.parts) and file.suffix != ".pyc":
                source = Path(distribution.locate_file(file))
                if source.is_file() and source.stat().st_size < 2_000_000 and file.suffix not in (".py", ".pyd"):
                    target = destination / package / str(file).replace("../", "")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", destination / "python-LICENSE.txt")
    # Official Qt LGPL/GPL texts are missing from the PyPI wheel license folder.
    cache = HOST / "packaging" / "license_cache"
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt", "Apache-2.0.txt", "BSD-3-Clause.txt"):
        file = cache / name
        if not file.exists():
            url = "https://raw.githubusercontent.com/qt/qtbase/v6.11.2/LICENSES/" + name
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(200_000)
            if len(data) < 100 or b"<html" in data.lower():
                raise ValueError("License download did not return plain license text")
            file.write_bytes(data)
        shutil.copy2(file, destination / name)
    serial_license = cache / "pyserial-3.5-LICENSE.txt"
    if not serial_license.exists():
        with urllib.request.urlopen("https://raw.githubusercontent.com/pyserial/pyserial/v3.5/LICENSE.txt", timeout=30) as response:
            data = response.read(200_000)
        if b"Chris Liechti" not in data or b"Redistribution" not in data:
            raise ValueError("pyserial license download is not the expected text")
        serial_license.write_bytes(data)
    shutil.copy2(serial_license, destination / "pyserial" / "LICENSE.txt")
    (destination / "versions.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")


def populate_distribution(app, host=HOST, version=__version__):
    for name in ("team_quick_start.md", "third_party_notices.md"):
        shutil.copy2(host / "docs" / name, app / name)
    shutil.copy2(host / "docs/protocol_v1.md", app / "protocol_v1.md")
    copy_licenses(app / "licenses")
    # Teammates receive editable source for debugging; no global open-source
    # license for user-owned application code is invented by this build.
    with zipfile.ZipFile(app / "host_source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for folder in ("app", "protocol", "transport", "simulator", "tests", "scripts", "docs", "packaging"):
            for file in (host / folder).rglob("*"):
                if file.is_file() and "__pycache__" not in file.parts and "license_cache" not in file.parts:
                    archive.write(file, str(file.relative_to(host)))
        for name in ("desktop_entry.py", "README.md", "requirements-lock.txt", "requirements-build.txt", "run_host.cmd"):
            archive.write(host / name, name)
    samples = app / "demo_records"
    samples.mkdir(exist_ok=True)
    for scenario in ("normal", "early", "late", "missing"):
        file = host.parent / "data/captures" / f"mock_{scenario}_{version_tag(version)}.tscap"
        shutil.copy2(file, samples / file.name)


def publish_staged(stage, out):
    """Keep the preceding release recoverable if replacing a running exe fails."""
    names = ("TimingSentinelA7", "smoke_data", "packaged_smoke.json", "binary_build_manifest.json",
             f"TimingSentinelA7-{__version__}-win64-portable.zip", "release_manifest.json")
    backup = Path(tempfile.mkdtemp(prefix=".previous-", dir=out)).resolve()
    if backup.parent != out.resolve():
        raise RuntimeError("Unexpected backup directory outside release folder")
    replaced = []
    try:
        for name in names:
            target = out / name
            had_previous = target.exists()
            if had_previous:
                target.rename(backup / name)
            try:
                (stage / name).rename(target)
            except BaseException:
                if had_previous:
                    (backup / name).rename(target)
                raise
            replaced.append((name, had_previous))
    except BaseException:
        # If restoration itself fails, leave the backup directory for recovery.
        for name, had_previous in reversed(replaced):
            (out / name).rename(stage / name)
            if had_previous:
                (backup / name).rename(out / name)
        raise
    else:
        shutil.rmtree(backup)


def main():
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("Build on Windows with 64-bit Python")
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-existing", action="store_true", help="Repackage docs/licenses only; verify binary/source identity and re-run smoke")
    parser.add_argument("--record-installer", type=Path, help="Record freshly compiled setup.exe in the release manifest")
    args = parser.parse_args()
    if args.record_installer:
        if args.package_existing:
            parser.error("--record-installer and --package-existing cannot be combined")
        print(json.dumps(record_installer(args.record_installer), indent=2))
        return
    out = HOST / "dist" / __version__
    out.mkdir(parents=True, exist_ok=True)
    initial_source = source_fingerprint()
    if not args.package_existing:
        checked_run([sys.executable, str(HOST / "scripts/verify_host.py")], cwd=HOST)
    _, verification_path = read_verification()
    verification_sha = source_fingerprint(verification=True)
    if source_fingerprint() != initial_source:
        raise RuntimeError("Sources changed during verification; rerun after editing is complete")
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=out) as temporary:
        stage = Path(temporary)
        app = stage / "TimingSentinelA7"
        if args.package_existing:
            build_manifest = json.loads((out / "binary_build_manifest.json").read_text("utf-8"))
            validate_existing_bundle(out / app.name, build_manifest)
            shutil.copytree(out / app.name, app)
        else:
            # Never overwrite the previous working release before smoke succeeds.
            checked_run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(stage),
                         "--workpath", str(HOST / "build" / __version__),
                         str(HOST / "packaging/timing_sentinel.spec")], cwd=HOST)
            if not (app / "TimingSentinelA7.exe").is_file():
                raise RuntimeError("Build did not emit the expected executable")
            build_manifest = {"app_version": __version__, "source_sha256": initial_source,
                              "built_at": datetime.now(timezone.utc).isoformat(), "environment": environment_versions(build=True),
                              "runtime_files": {file.relative_to(app).as_posix(): file_hash(file)
                                                for file in app.rglob("*") if file.is_file()}}
        populate_distribution(app)
        report_path = stage / "packaged_smoke.json"
        evidence = run_packaged_smoke(app, report_path, stage / "smoke_data")
        if (source_fingerprint() != initial_source
                or source_fingerprint(verification=True) != verification_sha):
            raise RuntimeError("Build/test inputs changed during packaging; no new release published")
        # Relocate the evidence pointer together with its screenshot directory.
        evidence["screenshot"] = str(out / "smoke_data/smoke_gui.png")
        evidence["source_sha256"] = initial_source
        evidence["executable_sha256"] = file_hash(app / "TimingSentinelA7.exe")
        write_json(report_path, evidence)
        write_json(stage / "binary_build_manifest.json", build_manifest)
        files = {file.relative_to(app).as_posix(): file_hash(file)
                 for file in app.rglob("*") if file.is_file() and file.name != "sha256_manifest.json"}
        write_json(app / "sha256_manifest.json", files)
        archive_path = stage / f"TimingSentinelA7-{__version__}-win64-portable.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for file in app.rglob("*"):
                if file.is_file():
                    archive.write(file, file.relative_to(stage).as_posix())
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("Portable ZIP integrity verification failed")
        release = {"app_version": __version__, "portable": str(out / archive_path.name),
                   "bytes": archive_path.stat().st_size, "sha256": file_hash(archive_path),
                   "source_sha256": initial_source, "verification_report": str(verification_path),
                   "verification_report_sha256": file_hash(verification_path),
                   "smoke_report": str(out / report_path.name), "smoke_report_sha256": file_hash(report_path),
                   "installer_built": False, "hardware_validation": False}
        write_json(stage / "release_manifest.json", release)
        publish_staged(stage, out)
    print(json.dumps(release, indent=2))


if __name__ == "__main__":
    main()

import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import zipfile
from app import __version__
from app.services.record_store import atomic_output
from transport.serial_port import enumerate_ports


def export_diagnostics(path, root, snapshot):
    report = {"app_version": __version__, "platform": platform.platform(),
              "python": platform.python_version(), "packaged": bool(getattr(sys, "frozen", False)),
              "ports": enumerate_ports(), "snapshot": snapshot, "hardware_validation": False,
              "dependencies": {}}
    for package in ("PySide6", "numpy", "pyqtgraph", "pyserial"):
        try:
            report["dependencies"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["dependencies"][package] = "not available in bundle"
    with atomic_output(path) as temporary, zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(report, ensure_ascii=False, indent=2))
        for log in (Path(root) / "logs").glob("host.log*"):
            if log.is_file() and log.stat().st_size <= 1_100_000:
                archive.write(log, "logs/" + log.name)
    return str(path)

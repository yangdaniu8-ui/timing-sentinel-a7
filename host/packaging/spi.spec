from pathlib import Path
import os
import PySide6
from PyInstaller.utils.hooks import copy_metadata

host = Path(SPECPATH).parent
keep = {"QtCore", "QtGui", "QtWidgets", "QtSvg", "QtTest", "QtOpenGL", "QtOpenGLWidgets"}
excluded_qt = ["PySide6." + p.stem for p in Path(PySide6.__file__).parent.glob("Qt*.pyd") if p.stem not in keep]
metadata = []
for package in ("PySide6", "PySide6_Essentials", "shiboken6", "numpy", "pyqtgraph", "pyserial"):
    metadata.extend(copy_metadata(package))
demo = Path(os.environ["TS_SPI_DEMO_DIR"])
workload_data = [(os.environ["TS_WORKLOAD_DEMO_DIR"], "workload_records")] if os.environ.get("TS_WORKLOAD_DEMO_DIR") else []
a = Analysis([str(host / "release_spi.py")], pathex=[str(host)], binaries=[],
             datas=metadata + [(str(demo), "demo_records")] + workload_data,
             hiddenimports=["serial.tools.list_ports_windows", "serial.serialwin32", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"],
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=excluded_qt + ["PyQt5", "PyQt6", "PySide2", "matplotlib", "scipy", "pandas", "IPython", "jupyter", "tkinter"],
             noarchive=False, optimize=0)
# Preserve the proven Qt6.11 fix: do not bundle foreign PATH ICU DLLs.
a.binaries = [entry for entry in a.binaries if not Path(entry[0]).name.lower().startswith("icu")]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TimingSentinelA7-SPI",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TimingSentinelA7-SPI")

"""Also supply system fonts to Qt's offscreen plugin on Windows."""
import os
from pathlib import Path
from PySide6.QtGui import QFont, QFontDatabase

_configured = False


def configure_fonts(application):
    global _configured
    if _configured:
        return
    if not QFontDatabase.families():
        directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            path = directory / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    application.setFont(QFont("Microsoft YaHei UI", 9))
    _configured = True

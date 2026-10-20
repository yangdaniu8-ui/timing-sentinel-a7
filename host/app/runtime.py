"""Writable paths in source checkouts and per-user packaged installations."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from app import __version__


def data_root(override=None):
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TimingSentinelA7"
    return Path(__file__).resolve().parents[2] / "data"


def setup_logging(root):
    root = Path(root)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(root / "logs" / "host.log", maxBytes=1_000_000,
                                 backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("timing_sentinel")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.info("Start host %s; frozen=%s", __version__, bool(getattr(sys, "frozen", False)))
    return logger

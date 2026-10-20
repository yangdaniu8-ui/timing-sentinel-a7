"""Local capture-directory checks; never touch existing records or OS settings."""
import json
import os
from pathlib import Path
import tempfile

from app.services.record_store import atomic_output


def preference_path():
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    return base / 'TimingSentinelA7' / 'profile5' / 'capture_preferences.json'


def read_preference(path):
    path = Path(path)
    if not path.exists():
        return None
    if path.stat().st_size > 16384:
        raise ValueError('保存位置设置文件过大，未采用')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or type(value.get('schema')) is not int
            or value['schema'] != 1):
        raise ValueError('保存位置设置格式错误，未采用')
    root = value.get('capture_root')
    if not isinstance(root, str) or not root or not Path(root).is_absolute():
        raise ValueError('保存位置必须为绝对路径，未采用')
    return Path(root)


def write_preference(root, path):
    content = json.dumps(dict(schema=1, capture_root=str(Path(root).resolve())),
                         ensure_ascii=False, indent=2)
    with atomic_output(path) as temporary:
        temporary.write_text(content, encoding='utf-8')


def initial_root(explicit, fallback, settings):
    # An explicit command-line root always wins over a remembered GUI selection.
    if explicit:
        return Path(explicit), None
    try:
        return read_preference(settings) or Path(fallback), None
    except (OSError, ValueError) as exc:
        return Path(fallback), str(exc)


def probe_directory(directory):
    """Check creation AND atomic replacement, not just a writable permission bit.

    Both destinations are unique, disposable probe files. Existing user files,
    security/encryption settings and record publication semantics stay unchanged.
    This small preflight cannot guarantee a later large write will succeed.
    """
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Probe the actual selected parent, not a nested folder whose inherited
    # encryption/ACL/reparse behavior could differ from the real destination.
    fd, name = tempfile.mkstemp(prefix='.ts_storage_check_', suffix='.json', dir=root)
    os.close(fd)
    path = Path(name)
    try:
        path.unlink()
        for content in (b'{"probe":1}', b'{"probe":2}'):
            with atomic_output(path) as temporary:
                temporary.write_bytes(content)
            if path.read_bytes() != content:
                raise OSError('保存目录原子写入回读不一致')
    finally:
        path.unlink(missing_ok=True)
    return root

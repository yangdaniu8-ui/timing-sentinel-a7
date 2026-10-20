"""Best-effort local diagnostics; no Qt dependency, payload logging, or uploads."""
from datetime import datetime, timezone
import faulthandler
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import RLock, get_ident
import traceback
from uuid import uuid4

_active = None


def record_event(event, **fields):
    """Never let diagnostic I/O failures change acquisition/task behavior."""
    session = _active
    if session is not None:
        session.write(event, **fields)


class SessionDiagnostics:
    """Keep per-launch logs, restoring exception/fault handlers on normal exit.

    An existing faulthandler owner is preserved. Native failures may bypass
    __exit__; a missing session_end is only an unclean exit, not proof of a crash.
    Explicit log_root never silently redirects to a different directory.
    """
    def __init__(self, log_root=None):
        self.log_root = Path(log_root) if log_root is not None else None
        self.directory = None
        self.error = None
        self._events = self._fault = None
        self._owns_fault_handler = False
        self._lock = RLock()
        self._hook = self._previous_hook = None

    def write(self, event, **fields):
        try:
            with self._lock:
                if self._events is not None:
                    entry = dict(utc=datetime.now(timezone.utc).isoformat(), event=event,
                                 pid=os.getpid(), thread=get_ident(), **fields)
                    self._events.write(json.dumps(entry, ensure_ascii=False)+'\n')
                    self._events.flush()
        except (OSError, ValueError, TypeError):
            pass

    def _exception(self, kind, exception_type, exception, tb):
        # No locals or raw evidence bytes: stack only, retained locally.
        self.write(kind, exception_type=exception_type.__name__,
                   traceback=''.join(traceback.format_exception(exception_type, exception, tb)))

    def __enter__(self):
        global _active
        if _active is not None:
            raise RuntimeError('A diagnostics session is already active')
        roots = [self.log_root] if self.log_root is not None else [
            Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')/'TimingSentinelA7'/'profile5'/'diagnostics',
            Path(tempfile.gettempdir())/'TimingSentinelA7'/'diagnostics']
        for root in roots:
            try:
                directory = root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')+'_'+str(os.getpid())+'_'+uuid4().hex[:8])
                directory.mkdir(parents=True, exist_ok=False)
                self._events = (directory/'events.jsonl').open('x', encoding='utf-8', buffering=1)
                self.directory = directory
                self.error = None
                break
            except OSError as exc:
                self.error = f'{type(exc).__name__}: {exc}'
        if self._events is None:
            # Application can still run on a read-only/full disk.
            if sys.stderr:
                try: print('Local diagnostics unavailable: '+str(self.error), file=sys.stderr)
                except OSError: pass
            return self
        _active = self
        self.write('session_start', python=sys.version.split()[0],
                   frozen=bool(getattr(sys,'frozen',False)), entry=Path(sys.argv[0]).name)
        try:
            if not faulthandler.is_enabled():
                self._fault = (self.directory/'fault.log').open('x',encoding='utf-8')
                faulthandler.enable(file=self._fault, all_threads=True)
                self._owns_fault_handler = True
            self.write('fault_handler', owned=self._owns_fault_handler,
                       enabled=faulthandler.is_enabled())
        except (OSError, RuntimeError) as exc:
            self.write('fault_handler_unavailable', exception_type=type(exc).__name__)
        self._previous_hook = sys.excepthook
        def hook(exception_type, exception, tb):
            self._exception('uncaught_callback_exception',exception_type,exception,tb)
            self._previous_hook(exception_type,exception,tb)
        self._hook = hook
        sys.excepthook = hook
        return self

    def __exit__(self, exception_type, exception, tb):
        global _active
        if self._events is None:
            return False
        if exception_type is not None and not issubclass(exception_type,SystemExit):
            self._exception('uncaught_main_exception',exception_type,exception,tb)
        code = exception.code if isinstance(exception,SystemExit) else (0 if exception is None else 1)
        self.write('session_end', exit_code=code)
        if sys.excepthook is self._hook:
            sys.excepthook = self._previous_hook
        if self._owns_fault_handler:
            faulthandler.disable()
        with self._lock:
            if _active is self:
                _active = None
            for stream in (self._fault,self._events):
                if stream is not None:
                    try: stream.close()
                    except OSError: pass
            self._fault = self._events = None
        return False

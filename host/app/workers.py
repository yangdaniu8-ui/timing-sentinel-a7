from threading import Event
from PySide6.QtCore import QThread, Signal
from app.diagnostics import record_event


class Task(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)
    message = Signal(str)

    def __init__(self, operation, parent=None):
        # Python-only identity before entering Qt: a prior native failure occurred
        # inside this super() call, before MainWindow could log task_created.
        cls = type(self)
        record_event('task_qobject_init_begin', task_id=id(self), type_id=id(cls),
                     python_type=cls.__module__ + '.' + cls.__qualname__,
                     python_mro=[base.__module__ + '.' + base.__qualname__ for base in cls.__mro__],
                     parent_id=id(parent) if parent is not None else None,
                     parent_type=type(parent).__qualname__ if parent is not None else None)
        super().__init__(parent)
        record_event('task_qobject_init_complete', task_id=id(self))
        self.operation = operation
        self.cancelled = Event()

    def run(self):
        record_event("task_worker_enter", task_id=id(self))
        try:
            value = self.operation(self)
        except Exception as exc:
            record_event("task_worker_failure", task_id=id(self), exception_type=type(exc).__name__)
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            record_event("task_worker_success", task_id=id(self))
            self.succeeded.emit(value)
        finally:
            record_event("task_worker_exit", task_id=id(self))

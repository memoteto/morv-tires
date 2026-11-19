"""Runs read-only fetches off the UI thread and keeps every thread alive until it finishes."""
import atexit

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from shiboken6 import isValid

# Tests switch this on so reload() stays synchronous and deterministic.
SYNC = False

# Strong references: a QThread destroyed while running aborts the whole process.
_LIVE: set = set()


class _Worker(QObject):
    done = Signal(object, str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    @Slot()
    def run(self) -> None:
        try:
            self.done.emit(self._fn(), "")
        except Exception as e:
            self.done.emit(None, str(e) or type(e).__name__)


class _Receiver(QObject):
    """Lives in the UI thread so the callback never runs in the worker thread."""

    def __init__(self, owner, on_done):
        super().__init__()
        self._owner = owner
        self._on_done = on_done
        self._entry = None

    @Slot(object, str)
    def deliver(self, result, err) -> None:
        # The owner may have been destroyed while the fetch was running.
        if isValid(self._owner):
            self._on_done(result, err)

    @Slot()
    def finish(self) -> None:
        _LIVE.discard(self._entry)


def run_async(fn, on_done, owner: QObject) -> None:
    """Call fn off the UI thread, then on_done(result, error) on it unless owner was destroyed."""
    if SYNC:
        try:
            result, err = fn(), ""
        except Exception as e:
            result, err = None, str(e) or type(e).__name__
        on_done(result, err)
        return
    worker = _Worker(fn)
    thread = QThread()
    receiver = _Receiver(owner, on_done)
    entry = (thread, worker, receiver)
    receiver._entry = entry
    _LIVE.add(entry)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.done.connect(receiver.deliver, Qt.ConnectionType.QueuedConnection)
    worker.done.connect(thread.quit)
    thread.finished.connect(receiver.finish, Qt.ConnectionType.QueuedConnection)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()


def wait_all() -> None:
    """Block until every running fetch has finished; call before destroying windows."""
    for thread, _worker, _receiver in list(_LIVE):
        if isValid(thread) and thread.isRunning():
            thread.quit()
            thread.wait()


atexit.register(wait_all)

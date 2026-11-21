"""Single running copy per data folder, via an OS file lock released when the process ends."""
import sys
import time
from pathlib import Path

_held = []          # the open handle must live as long as the process


def _try_lock(handle) -> bool:
    try:
        if sys.platform.startswith("win"):
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire(folder, wait: float = 0.0, name: str = "morv.lock") -> bool:
    """True when this process now owns the folder; waits up to `wait` seconds for a closing copy."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    handle = open(Path(folder) / name, "a+")
    deadline = time.monotonic() + wait
    while not _try_lock(handle):
        if time.monotonic() >= deadline:
            handle.close()
            return False
        time.sleep(0.2)
    _held.append(handle)
    return True


def release_all() -> None:
    """Used by tests; the app keeps the lock until the process exits."""
    while _held:
        _held.pop().close()

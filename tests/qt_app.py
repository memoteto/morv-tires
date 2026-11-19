"""Offscreen Qt setup for the UI tests."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication  # noqa: F401 - the import itself is the check
    HAS_QT = True
except ImportError:
    HAS_QT = False


def require_qt():
    if HAS_QT or os.environ.get("MORV_REQUIRE_QT") == "1":
        return lambda cls: cls
    return unittest.skip("PySide6 غير مثبتة على هذا الجهاز")


_app = None


def _shutdown():
    if _app is not None:
        _app.closeAllWindows()
        _app.processEvents()
        _app.sendPostedEvents(None, 0)
        _app.processEvents()


def get_app():
    global _app
    from PySide6.QtWidgets import QApplication as App
    if _app is None:
        _app = App.instance() or App([])
        from morv.ui import workers
        workers.SYNC = True
        import atexit
        atexit.register(_shutdown)
    return _app

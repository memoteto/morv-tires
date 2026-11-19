"""Real threaded path of run_async: delivery, errors, and owners destroyed mid-fetch."""
import threading
import time
import unittest

from tests.qt_app import get_app, require_qt


@require_qt()
class RunAsyncTest(unittest.TestCase):
    def setUp(self):
        from morv.ui import workers
        self.app = get_app()
        self.workers = workers
        workers.SYNC = False
        self.addCleanup(setattr, workers, "SYNC", True)

    def pump(self, until, timeout=5.0):
        end = time.time() + timeout
        while not until() and time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def test_result_is_delivered_on_the_ui_thread(self):
        from PySide6.QtWidgets import QWidget
        owner = QWidget()
        got = []
        self.workers.run_async(lambda: threading.current_thread().name, lambda r, e: got.append((r, e, threading.current_thread().name)), owner)
        self.pump(lambda: got)
        self.assertEqual(len(got), 1)
        worker_thread, err, delivered_on = got[0]
        self.assertEqual(err, "")
        self.assertEqual(delivered_on, threading.main_thread().name)
        self.assertNotEqual(worker_thread, threading.main_thread().name)

    def test_error_is_reported_not_swallowed(self):
        from PySide6.QtWidgets import QWidget
        owner = QWidget()
        got = []

        def boom():
            raise ValueError("bad query")

        self.workers.run_async(boom, lambda r, e: got.append((r, e)), owner)
        self.pump(lambda: got)
        self.assertEqual(got, [(None, "bad query")])

    def test_owner_destroyed_mid_fetch_neither_crashes_nor_calls_back(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QWidget
        owner = QWidget()
        gate = threading.Event()
        got = []
        self.workers.run_async(lambda: gate.wait(5), lambda r, e: got.append(r), owner)
        owner.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gate.set()
        self.workers.wait_all()
        self.pump(lambda: not self.workers._LIVE)
        self.assertEqual(got, [])
        self.assertEqual(len(self.workers._LIVE), 0)


if __name__ == "__main__":
    unittest.main()

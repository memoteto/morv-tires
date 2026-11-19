"""Real threaded path of run_async: delivery, errors, and owners destroyed mid-fetch."""
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

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

    def file_db(self):
        from morv.infra.db.bootstrap import open_main_db
        tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(tmp)
        con = open_main_db(tmp / "morv_tires.db")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(con.close)
        return con

    def test_result_is_delivered_on_the_ui_thread(self):
        from PySide6.QtWidgets import QWidget
        owner = QWidget()
        con = self.file_db()
        got = []
        self.workers.run_async(lambda c: threading.current_thread().name,
                               lambda r, e: got.append((r, e, threading.current_thread().name)), owner, con)
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

        def boom(_c):
            raise ValueError("bad query")

        self.workers.run_async(boom, lambda r, e: got.append((r, e)), owner, self.file_db())
        self.pump(lambda: got)
        self.assertEqual(got, [(None, "bad query")])

    def test_owner_destroyed_mid_fetch_neither_crashes_nor_calls_back(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QWidget
        owner = QWidget()
        gate = threading.Event()
        got = []
        self.workers.run_async(lambda c: gate.wait(5), lambda r, e: got.append(r), owner, self.file_db())
        owner.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gate.set()
        self.workers.wait_all()
        self.pump(lambda: not self.workers._LIVE)
        self.assertEqual(got, [])
        self.assertEqual(len(self.workers._LIVE), 0)

    def test_reader_is_closed_even_when_the_fetch_fails(self):
        import sqlite3
        from PySide6.QtWidgets import QWidget
        seen = []
        real = self.workers.open_reader

        def spy(path):
            seen.append(real(path))
            return seen[-1]

        self.workers.open_reader = spy
        self.addCleanup(setattr, self.workers, "open_reader", real)
        got = []

        def boom(_c):
            raise ValueError("x")

        self.workers.run_async(boom, lambda r, e: got.append(e), QWidget(), self.file_db())
        self.pump(lambda: got)
        self.assertEqual(len(seen), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            seen[0].execute("SELECT 1")

    def test_every_list_page_fetches_through_a_read_only_reader(self):
        from morv.infra import dashboard_view, users
        from morv.infra.db.connection import db_path, open_reader
        from morv.ui.customers_page import CustomersPage
        from morv.ui.inventory_page import ItemsPage
        from morv.ui.installments_page import ContractsPage
        from morv.ui.purchases_page import PurchasesListPage
        from morv.ui.sales_page import SalesPage
        from morv.ui.session import Session
        from morv.ui.stock_pages import LotsPage, StockPage
        from morv.ui.suppliers_page import SuppliersPage
        con = self.file_db()
        admin = users.create_user(con, None, "admin", "المدير", "Admin12345", "manager")
        session = Session(con, admin)
        reader = open_reader(db_path(con))
        self.addCleanup(reader.close)
        for cls in (CustomersPage, ItemsPage, ContractsPage, PurchasesListPage, SalesPage, LotsPage,
                    StockPage, SuppliersPage):
            with self.subTest(page=cls.__name__):
                page = cls(session)
                self.assertIsInstance(page.fetch(reader, "", page.filter_key, 0), list)
        self.assertIsNotNone(dashboard_view.get(reader, period="today"))


@require_qt()
class NavigationRefreshTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, admin)
        self.app = get_app()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(self.con.close)

    def test_returning_to_a_screen_shows_rows_added_meanwhile(self):
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        w = MainWindow(self.session, self.app)
        w.health_timer.stop()
        self.addCleanup(dispose, w, self.app)
        w.go_key("suppliers")
        page = w.pages["suppliers"]
        self.assertEqual(len(page.model.rows), 0)
        w.go_key("home")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        w.go_key("suppliers")
        self.assertEqual(len(page.model.rows), 1)


if __name__ == "__main__":
    unittest.main()

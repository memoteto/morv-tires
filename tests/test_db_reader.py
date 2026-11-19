"""Background read connections: snapshot isolation, read-only, guard, and main-connection thread safety."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import UnsafeDatabase, db_path, open_reader, transaction


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")

    def count(self, con):
        return con.execute("SELECT count(*) FROM suppliers").fetchone()[0]

    def test_reader_sees_only_committed_rows(self):
        with transaction(self.con):
            self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('ثان','ثان','01000000002')")
            inside = open_reader(db_path(self.con))
            self.addCleanup(inside.close)
            self.assertEqual(self.count(self.con), 2)
            self.assertEqual(self.count(inside), 1)
        after = open_reader(db_path(self.con))
        self.addCleanup(after.close)
        self.assertEqual(self.count(after), 2)

    def test_reader_cannot_write(self):
        reader = open_reader(db_path(self.con))
        self.addCleanup(reader.close)
        with self.assertRaises(sqlite3.DatabaseError):
            reader.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('x','x','1')")

    def test_open_reader_does_not_block_a_writer(self):
        reader = open_reader(db_path(self.con))
        self.addCleanup(reader.close)
        reader.execute("SELECT * FROM suppliers").fetchall()
        with transaction(self.con):
            self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('ثالث','ثالث','01000000003')")
        self.assertEqual(self.count(self.con), 2)

    def test_old_database_name_is_refused(self):
        with self.assertRaises(UnsafeDatabase):
            open_reader(Path(self.tmp.name) / "tire_shop_live.db")

    def test_main_connection_refuses_other_threads(self):
        errors = []

        def use():
            try:
                self.con.execute("SELECT 1")
            except sqlite3.ProgrammingError as e:
                errors.append(e)

        t = threading.Thread(target=use)
        t.start()
        t.join()
        self.assertEqual(len(errors), 1)

    def test_in_memory_database_has_no_path(self):
        mem = sqlite3.connect(":memory:")
        self.addCleanup(mem.close)
        self.assertEqual(db_path(mem), "")


if __name__ == "__main__":
    unittest.main()

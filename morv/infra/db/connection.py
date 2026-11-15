"""Safe database opening and the one transaction helper."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from morv import APP_ID

FORBIDDEN_NAMES = {"tire_shop_live.db"}


# sqlite3 raises this on a closed connection; renamed here so the UI need not import sqlite3
ClosedDatabase = sqlite3.ProgrammingError


class UnsafeDatabase(RuntimeError):
    """A database file that is not this program's: always refused."""


class NestedTransaction(RuntimeError):
    """A transaction inside an open one: a bug, caught before it corrupts data."""


@contextmanager
def transaction(con: sqlite3.Connection):
    if con.in_transaction:
        raise NestedTransaction("معاملة داخل معاملة: استدعِ الدالة من خارج transaction()")
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise


def _apply_pragmas(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-16000")   # about 16 MB of cache
    con.execute("PRAGMA busy_timeout=3000")


def read_app_id(path: Path):
    ro = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        row = ro.execute("SELECT v FROM app_meta WHERE k='app_id'").fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        ro.close()


def _is_empty(path: Path) -> bool:
    ro = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        return ro.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    except sqlite3.DatabaseError:
        return False      # Corrupt, or not SQLite: never treated as empty, never touched
    finally:
        ro.close()


def open_db(path, create: bool = False) -> sqlite3.Connection:
    p = Path(path)
    if p.name.lower() in FORBIDDEN_NAMES:
        raise UnsafeDatabase(f"رفض فتح قاعدة البرنامج القديم: {p}")
    exists = p.exists() and p.stat().st_size > 0
    if not exists and not create:
        raise FileNotFoundError(p)
    # A file with no tables is a creation that failed before; only create may finish it
    if exists and read_app_id(p) != APP_ID and not (create and _is_empty(p)):
        raise UnsafeDatabase(f"هذه ليست قاعدة Morv Tires، تم الرفض: {p}")
    con = sqlite3.connect(p, isolation_level=None)
    _apply_pragmas(con)
    return con

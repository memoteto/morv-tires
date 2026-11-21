"""Database migrations."""
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_FK_OFF = {(13, "installment_returns"), (29, "stock_adjustments"),
           (31, "opening_inventory")}   # rebuild parent tables with live children

_BOOTSTRAP = """CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  checksum   TEXT NOT NULL,
  applied_at TEXT NOT NULL
);"""


class MigrationError(RuntimeError):
    """Migration failed: bad order, a changed file, or an SQL error."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def load_migrations(folder: Path = MIGRATIONS_DIR) -> list:
    found = []
    for f in sorted(folder.glob("*.sql")):
        m = _NAME.match(f.name)
        if not m:
            raise MigrationError(f"اسم ملف ترقية غير صالح: {f.name}")
        sql = f.read_text(encoding="utf-8")
        found.append(Migration(int(m.group(1)), m.group(2), sql, _checksum(sql)))
    for i, mig in enumerate(found, start=1):
        if mig.version != i:
            raise MigrationError(f"ترقيم الترقيات غير متتالٍ عند {mig.version:04d}")
    return found


def applied(con: sqlite3.Connection) -> dict:
    has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if not has:
        return {}
    return dict(con.execute("SELECT version, checksum FROM schema_migrations"))


def current_version(con: sqlite3.Connection) -> int:
    done = applied(con)
    return max(done) if done else 0


def pending(con: sqlite3.Connection, migrations=None) -> list:
    migrations = migrations if migrations is not None else load_migrations()
    done = applied(con)
    by_version = {m.version: m for m in migrations}
    for v, checksum in done.items():
        if v not in by_version:
            raise MigrationError(f"القاعدة فيها ترقية {v:04d} غير موجودة في هذا الإصدار (إصدار أقدم من القاعدة؟)")
        if by_version[v].checksum != checksum:
            raise MigrationError(f"ملف الترقية {v:04d} تغيّر بعد تطبيقه — ممنوع تعديل ترقية قديمة")
    return [m for m in migrations if m.version not in done]


def migrate(con: sqlite3.Connection, migrations=None, before_apply=None) -> list:
    todo = pending(con, migrations)
    if not todo:
        return []
    if before_apply is not None and current_version(con) > 0:
        before_apply()
    done = []
    for mig in todo:
        # SQLite cannot rebuild a parent table with live child FKs while FK enforcement is on.
        # Only the migrations in _FK_OFF need this atomic, pre-backup rebuild. Foreign-key integrity is
        # checked before commit and normal enforcement is restored even if migration fails.
        rebuild_returns = (mig.version, mig.name) in _FK_OFF
        if rebuild_returns:
            if con.in_transaction or con.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise MigrationError("إعادة بناء المرتجعات تتطلب اتصالًا بلا معاملة مع تفعيل العلاقات")
            con.execute("PRAGMA foreign_keys=OFF")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # A migration is atomic: the whole script in one transaction, then the check, then COMMIT.
        # The inlined values are safe: a number, a lower-case name, a hex checksum.
        script = (
            "BEGIN IMMEDIATE;\n" + _BOOTSTRAP + "\n" + mig.sql + "\n"
            f"INSERT INTO schema_migrations (version, name, checksum, applied_at) "
            f"VALUES ({mig.version}, '{mig.name}', '{mig.checksum}', '{now}');"
        )
        try:
            con.executescript(script)
            # A table rebuild can leave rows pointing at what is gone without SQLite objecting;
            # the check before COMMIT turns that into a rollback instead of corrupt data.
            broken = con.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                tables = "، ".join(sorted({r[0] for r in broken}))
                raise MigrationError(f"الترقية {mig.version:04d}_{mig.name} تترك {len(broken)} ارتباطًا مكسورًا في: {tables}")
            con.execute("COMMIT")
        except (sqlite3.Error, MigrationError) as e:
            if con.in_transaction:
                con.execute("ROLLBACK")
            if isinstance(e, MigrationError):
                raise
            raise MigrationError(f"فشلت الترقية {mig.version:04d}_{mig.name}: {e}") from e
        finally:
            if rebuild_returns:
                con.execute("PRAGMA foreign_keys=ON")
                if con.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                    raise MigrationError("تعذرت إعادة تفعيل فحص العلاقات بعد ترقية المرتجعات")
        done.append(mig.version)
    return done

"""ترقيات قاعدة البيانات.

كل تغيير في الجداول يُكتب في ملف SQL مرقّم داخل مجلد migrations، مثل 0001_foundation.sql.
عند فتح البرنامج:
1) يُقرأ رقم آخر ترقية مطبّقة من جدول schema_migrations.
2) إن وُجدت ترقيات جديدة وكانت القاعدة فيها بيانات، تُؤخذ نسخة احتياطية أولًا.
3) تُطبّق كل ترقية داخل معاملة واحدة: إما تنجح كاملة أو لا يتغير شيء.
4) تُحفظ بصمة (sha256) كل ملف مطبّق. لو تغيّر ملف بعد تطبيقه يرفض البرنامج العمل،
   لأن ذلك يعني أن نسختين من البرنامج ترى جداول مختلفة.
"""
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_BOOTSTRAP = """CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  checksum   TEXT NOT NULL,
  applied_at TEXT NOT NULL
);"""


class MigrationError(RuntimeError):
    """فشل في الترقيات: ملف بلا ترتيب صحيح، أو ملف تغيّر بعد تطبيقه، أو خطأ SQL."""


@dataclass(frozen=True)
class Migration:
    """ملف ترقية واحد: رقمه واسمه ونصه وبصمته."""
    version: int
    name: str
    sql: str
    checksum: str


def _checksum(sql: str) -> str:
    """بصمة الملف بعد توحيد نهايات الأسطر، حتى لا تختلف بين Windows وLinux."""
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def load_migrations(folder: Path = MIGRATIONS_DIR) -> list:
    """قراءة ملفات الترقية بالترتيب والتأكد أن أرقامها متتالية تبدأ من 1 بلا فجوات."""
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
    """الترقيات المطبّقة على هذه القاعدة: {الرقم: البصمة}."""
    has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if not has:
        return {}
    return dict(con.execute("SELECT version, checksum FROM schema_migrations"))


def current_version(con: sqlite3.Connection) -> int:
    """رقم آخر ترقية مطبّقة، أو 0 لقاعدة جديدة."""
    done = applied(con)
    return max(done) if done else 0


def pending(con: sqlite3.Connection, migrations=None) -> list:
    """الترقيات التي لم تُطبّق بعد، مع رفض أي ملف تغيّرت بصمته بعد تطبيقه."""
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
    """تطبيق الترقيات الجديدة بالترتيب، ويعيد قائمة أرقام ما طُبّق.

    before_apply: دالة تُستدعى مرة واحدة قبل التطبيق إذا كانت القاعدة غير جديدة
    (تُستخدم لأخذ نسخة احتياطية قبل الترقية).
    """
    todo = pending(con, migrations)
    if not todo:
        return []
    if before_apply is not None and current_version(con) > 0:
        before_apply()
    done = []
    for mig in todo:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # كل شيء في نص واحد بين BEGIN وCOMMIT حتى تكون الترقية ذرية.
        # القيم المدمجة آمنة: الرقم عدد، والاسم مطابق لنمط حروف صغيرة، والبصمة ست عشرية.
        script = (
            "BEGIN IMMEDIATE;\n" + _BOOTSTRAP + "\n" + mig.sql + "\n"
            f"INSERT INTO schema_migrations (version, name, checksum, applied_at) "
            f"VALUES ({mig.version}, '{mig.name}', '{mig.checksum}', '{now}');\nCOMMIT;"
        )
        try:
            con.executescript(script)
        except sqlite3.Error as e:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise MigrationError(f"فشلت الترقية {mig.version:04d}_{mig.name}: {e}") from e
        done.append(mig.version)
    return done

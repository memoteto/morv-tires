"""فتح قاعدة التشغيل وتجهيزها عند بدء البرنامج: فتح آمن ثم ترقيات ثم نسخة قبل الترقية."""
from pathlib import Path

from morv.infra import backup, paths
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import migrate


def open_main_db(path: Path | None = None):
    """فتح قاعدة البرنامج (وإنشاؤها أول مرة) وتطبيق أي ترقيات جديدة.

    إذا كانت القاعدة قديمة وفيها ترقيات جديدة، تُؤخذ نسخة احتياطية من نوع pre_migration
    قبل تطبيقها، حتى يمكن الرجوع لو فشلت الترقية.
    """
    p = Path(path) if path else paths.db_path()
    con = open_db(p, create=True)
    migrate(con, before_apply=lambda: backup.create_backup(con, "pre_migration", p.parent / "backups"))
    return con

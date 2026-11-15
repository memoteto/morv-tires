"""فتح قاعدة التشغيل وتجهيزها عند بدء البرنامج: فتح آمن ثم ترقيات ثم نسخة قبل الترقية."""
from pathlib import Path

from morv.infra import backup, paths
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import migrate
from morv.infra.logs import log


def open_main_db(path: Path | None = None):
    """فتح قاعدة البرنامج (وإنشاؤها أول مرة) وتطبيق أي ترقيات جديدة.

    إذا كانت القاعدة قديمة وفيها ترقيات جديدة، تُؤخذ نسخة احتياطية من نوع pre_migration
    قبل تطبيقها، حتى يمكن الرجوع لو فشلت الترقية.
    """
    p = Path(path) if path else paths.db_path()
    con = open_db(p, create=True)
    migrate(con, before_apply=lambda: _pre_migration_backup(con, p.parent / "backups"))
    return con


def _pre_migration_backup(con, folder: Path) -> None:
    """نسخة قبل الترقية: مشفّرة إن أمكن، وإلا عادية محلية. بلا نسخة لا تُطبَّق الترقية.

    التشفير قد يتعذر (مفتاح الجهاز مفقود بعد إعادة تثبيت Windows مثلًا). لو أوقفنا التشغيل
    لذلك لما استطاع المالك فتح البرنامج ليعيد ربط الجهاز أصلًا. النسخة العادية تبقى في مجلد
    البيانات بجوار القاعدة غير المشفّرة أصلًا، ولا تُنقل إلى الفلاشة.
    """
    try:
        backup.create_backup(con, "pre_migration", folder)
    except backup.BackupError as e:
        log.warning("تعذّرت النسخة المشفّرة قبل الترقية (%s)؛ تُؤخذ نسخة محلية عادية", e)
        backup.create_backup(con, "pre_migration", folder, plain=True)

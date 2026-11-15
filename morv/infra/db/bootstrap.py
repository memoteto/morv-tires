"""Open the live database at startup: safe open, migrations, pre-upgrade backup."""
from pathlib import Path

from morv.infra import backup, paths
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import migrate
from morv.infra.logs import log


def open_main_db(path: Path | None = None):
    p = Path(path) if path else paths.db_path()
    con = open_db(p, create=True)
    migrate(con, before_apply=lambda: _pre_migration_backup(con, p.parent / "backups"))
    return con


def _pre_migration_backup(con, folder: Path) -> None:
    try:
        backup.create_backup(con, "pre_migration", folder)
    except backup.BackupError as e:
        log.warning("تعذّرت النسخة المشفّرة قبل الترقية (%s)؛ تُؤخذ نسخة محلية عادية", e)
        backup.create_backup(con, "pre_migration", folder, plain=True)

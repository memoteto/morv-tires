"""Open the live database at startup: safe open, migrations, pre-upgrade backup."""
from pathlib import Path

from morv.infra import backup, paths, vault
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import migrate
from morv.infra.logs import log


def open_main_db(path: Path | None = None):
    p = Path(path) if path else paths.db_path()
    con = open_db(p, create=True)
    plain_copies: list[Path] = []
    migrate(con, before_apply=lambda: _pre_migration_backup(con, p.parent / "backups", plain_copies))
    for copy in plain_copies:      # kept only while the upgrade could still fail; it holds unencrypted data
        copy.unlink(missing_ok=True)
        for side in copy.parent.glob(copy.name + ".snapshot*"):   # sidecar files the snapshot leaves behind
            side.unlink(missing_ok=True)
    return con


def _pre_migration_backup(con, folder: Path, plain_copies: list) -> None:
    try:
        result = backup.create_backup(con, "pre_migration", folder)
    except backup.BackupError as e:
        log.warning("تعذّرت النسخة المشفّرة قبل الترقية (%s)؛ تُؤخذ نسخة محلية عادية", e)
        result = backup.create_backup(con, "pre_migration", folder, plain=True)
    if Path(result.path).suffix != vault.EXT:
        plain_copies.append(Path(result.path))

"""Runs the scheduled backup on a connection of its own, so no connection ever crosses a thread."""
from morv.infra import backup, paths
from morv.infra.db.connection import open_db


def run_scheduled_alone(username: str | None = None) -> list[str]:
    con = open_db(paths.db_path())
    try:
        return backup.run_scheduled(con, username)
    finally:
        con.close()

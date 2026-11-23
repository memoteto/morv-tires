"""Takes the scheduled backup in a worker thread so the window keeps painting while it runs."""
from PySide6.QtCore import QThread, Signal

from morv.infra import backup_job


class BackupRunner(QThread):
    done = Signal(list)

    def __init__(self, username: str | None, parent=None):
        super().__init__(parent)
        self._username = username        # Only a name crosses the thread, never a database connection
        self.lines: list[str] = []

    def run(self) -> None:
        try:
            self.lines = backup_job.run_scheduled_alone(self._username)
        except Exception as e:           # noqa: BLE001 - a worker must never take the program down
            self.lines = [f"تعذّر النسخ المجدول: {e}"]
        self.done.emit(self.lines)

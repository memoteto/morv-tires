"""Run and error log."""
import logging
import sys
from logging.handlers import RotatingFileHandler

from morv.infra import paths

log = logging.getLogger("morv")


def setup_logging() -> None:
    if log.handlers:
        return
    handler = RotatingFileHandler(paths.logs_dir() / "morv_tires.log", maxBytes=1_000_000,
                                  backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    previous = sys.excepthook

    def _hook(exc_type, exc, tb):
        log.critical("خطأ غير متوقع", exc_info=(exc_type, exc, tb))
        previous(exc_type, exc, tb)

    sys.excepthook = _hook

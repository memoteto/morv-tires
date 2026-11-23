"""App entry: logging, database, first-run setup, login, main window."""
import subprocess
import sys
import time

T_START = time.perf_counter()


SELFCHECK = "--selfcheck"
# --smoke was removed: a shipped build must never create a known administrator, whatever it is passed


def _selfcheck(argv, con, paths) -> int:
    """Proves a built copy opens its database and raises Qt, creating neither a shop nor a user."""
    from PySide6.QtWidgets import QApplication

    from morv import APP_NAME
    from morv.infra import setup
    from morv.ui import theme
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    theme.apply(app, False)
    if setup.is_complete(con):
        from morv.ui.login import LoginDialog
        dialog = LoginDialog(con)
    else:
        from morv.ui.setup_wizard import SetupWizard
        dialog = SetupWizard(con)
    ok = dialog.sizeHint().isValid() and bool(theme.pick_font().family())
    ms = (time.perf_counter() - T_START) * 1000
    (paths.data_dir() / "startup.txt").write_text(f"{'ok' if ok else 'fail'} {ms:.0f}ms\n", encoding="utf-8")
    from morv.ui.widgets import dispose
    dispose(dialog, app)
    return 0 if ok else 1


def _relaunch() -> None:
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable])
    else:
        subprocess.Popen([sys.executable, sys.argv[0]])


def _mark(log, label: str) -> None:
    log.info("startup %s %.0fms", label, (time.perf_counter() - T_START) * 1000)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    unknown = [a for a in argv[1:] if a.startswith("-") and a != SELFCHECK]
    if unknown:
        sys.stderr.write("وسيطة غير معروفة: " + " ".join(unknown) + "\n")
        return 2
    selfcheck = SELFCHECK in argv
    from morv.infra import paths
    from morv.infra.db.bootstrap import open_main_db
    from morv.infra.logs import log, setup_logging

    setup_logging()
    log.info("بدء التشغيل %s", argv[1:])
    from morv.infra import instance_lock
    if not instance_lock.acquire(paths.data_dir(), wait=4.0):      # a relaunch may overlap a closing copy
        from PySide6.QtWidgets import QApplication
        from morv.ui.widgets import info
        app = QApplication(argv)
        info(None, "البرنامج يعمل بالفعل",
             "نسخة أخرى من البرنامج تعمل على بيانات المحل نفسها. أغلقها أولًا، فتشغيل نسختين قد يُتلف البيانات.", error=True)
        del app
        return 1
    _mark(log, "logging_ready")
    con = open_main_db()
    _mark(log, "db_opened")
    if selfcheck:
        code = _selfcheck(argv, con, paths)
        con.close()
        return code

    from PySide6.QtCore import QLocale, QTimer
    from PySide6.QtWidgets import QApplication, QDialog

    from morv import APP_NAME
    from morv.infra import backup, keystore, setup, users, vault
    from morv.ui import theme
    from morv.ui.login import LoginDialog
    from morv.ui.session import Session
    from morv.ui.setup_wizard import SetupWizard
    from morv.ui.shell import MainWindow
    _mark(log, "imports_done")

    QLocale.setDefault(QLocale("en_US"))   # Latin digits whatever the system locale
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    theme.apply(app, False)

    if paths.looks_synced_or_network(paths.data_dir()):
        # Owner rule: cloud sync can corrupt a live SQLite file. Warn, never block.
        from morv.ui.widgets import info
        log.warning("مجلد البيانات داخل مجلد مزامنة أو شبكة: %s", paths.data_dir())
        info(None, "تحذير: مكان البيانات",
             "مجلد بيانات البرنامج يقع داخل مجلد مزامنة (مثل OneDrive أو Google Drive) أو مجلد شبكة.\n"
             "المزامنة أثناء العمل قد تُتلف قاعدة البيانات. انقل البيانات إلى مجلد محلي عادي.", error=True)

    # First run, or restore on a new device; resumes an interrupted one
    if not setup.is_complete(con):
        wiz = SetupWizard(con)
        if wiz.exec() != QDialog.DialogCode.Accepted:
            con.close()
            return 0
        if wiz.result_kind == "restore":
            con.close()
            try:
                safety = backup.restore_file(paths.db_path(), wiz.restore_path, secret=wiz.restore_secret)
            except (backup.BackupError, vault.VaultError) as e:
                # Restore is atomic: nothing changed. Close; the wizard returns on next start.
                log.exception("فشلت الاستعادة على جهاز جديد")
                from morv.ui.widgets import info
                info(None, "الاستعادة", f"لم تتم الاستعادة ولم يتغير شيء: {e}\nافتح البرنامج وأعد المحاولة.", error=True)
                return 1
            con = open_main_db()
            try:
                backup.relink_device(con, wiz.restore_secret)   # Writes the key fingerprint into an old keyring
            except (backup.BackupError, vault.VaultError) as e:
                log.warning("استُعيدت البيانات لكن تعذّر ربط الجهاز: %s", e)
            users.audit(con, None, "backup.restore", None, None,
                        f"استعادة على جهاز جديد من {wiz.restore_path}" + (f" (نسخة أمان: {safety.name})" if safety else ""))
            log.info("استعادة على جهاز جديد من %s", wiz.restore_path)

    state = {"con": con, "window": None, "code": 0}

    def do_restore(path: str, secret: str | None) -> None:
        dek = keystore.load_dek() if secret is None else None
        if dek is None and secret is None:
            from morv.ui.widgets import info
            info(state["window"], "الاستعادة", "مفتاح الجهاز مفقود؛ أعد ربط الجهاز أولًا من تبويب النسخ الاحتياطي", error=True)
            return
        win = state["window"]
        win.health_timer.stop()
        win.hide()
        state["con"].close()
        try:
            backup.restore_file(paths.db_path(), path, secret=secret, dek=dek)
            log.info("تمت الاستعادة من %s", path)
        except Exception as e:   # noqa: BLE001 - any failure here must reach the user
            # restore_file is atomic: a failure lands before the swap, so the database stands
            log.exception("فشلت الاستعادة")
            state["con"] = open_main_db()
            win.session.con = state["con"]
            from morv.ui.widgets import info
            info(None, "الاستعادة", f"لم تتم الاستعادة، والبيانات الحالية كما هي: {e}", error=True)
            win.show()
            win.health_timer.start()
            return
        _relaunch()
        app.quit()

    def do_logout() -> None:
        win = state["window"]
        win.health_timer.stop()
        win.close()
        state["window"] = None
        QTimer.singleShot(0, show_login)

    def show_login() -> None:
        dlg = LoginDialog(state["con"])
        _mark(log, "login_shown")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            app.quit()
            return
        user = dlg.user
        _mark(log, "login_accepted")
        session = Session(state["con"], user, on_logout=do_logout, on_restore=do_restore)
        window = MainWindow(session, app)
        _mark(log, "window_built")
        state["window"] = window
        window.showMaximized()
        _mark(log, "window_shown")
        QTimer.singleShot(1500, lambda: _scheduled(window, session))

    def _scheduled(window, session) -> None:
        from morv.ui.backup_runner import BackupRunner
        runner = BackupRunner(session.user.username, window)
        state["runner"] = runner
        runner.done.connect(lambda lines: _scheduled_done(window, lines))
        runner.start()

    def _scheduled_done(window, lines) -> None:
        for line in lines:
            log.info("نسخ مجدول: %s", line)
        if state["window"] is window:
            window.refresh_health()

    show_login()
    code = app.exec()
    from morv.ui.widgets import dispose
    dispose(state.get("window"), app)
    state["window"] = None
    if state["con"]:
        try:
            state["con"].close()
        except Exception:   # noqa: BLE001 - the restore may have closed it
            pass
    log.info("إغلاق البرنامج")
    return code


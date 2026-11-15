"""نقطة تشغيل البرنامج: السجل ← قاعدة البيانات ← (الإعداد الأول) ← الدخول ← النافذة.

الترتيب مقصود: قاعدة البيانات تُفتح وتُرقّى قبل بناء أي نافذة، فإن فشلت الترقية لا تظهر
واجهة على بيانات ناقصة؛ يُكتب الخطأ في السجل ويخرج البرنامج.

حلقة الدخول: تسجيل الخروج يغلق النافذة ويعيد شاشة الدخول دون إغلاق البرنامج.
الاستعادة: تُغلق القاعدة، ويُستبدل ملفها (بعد نسخة أمان)، ثم يعيد البرنامج تشغيل نفسه.
الاستبدال ذري (backup.restore_file): إن فشلت الاستعادة فالقاعدة كما كانت، فيُعاد فتحها
ويستمر العمل. المعالج يظهر ما دام الإعداد الأول لم يكتمل (علامة setup.completed_at).

الخيار --smoke (لاختبار البناء آليًا): يعدّ محلًا وهميًا إن لزم، يفتح النافذة كاملة، يكتب
startup.txt في مجلد البيانات ثم يغلق. وجود الملف وفيه ok يعني أن النسخة المبنية تعمل.
"""
import subprocess
import sys
import time

T_START = time.perf_counter()


def _smoke_prepare(con) -> None:
    """بيانات وهمية للاختبار الآلي فقط (لا تُستخدم في التشغيل العادي)."""
    from morv.core import security
    from morv.infra import setup
    if not setup.is_complete(con):
        setup.run_first_setup(
            con, shop_name="محل تجريبي", shop_phone="01000000000", shop_address="",
            admin_username="admin", admin_name="اختبار", admin_password="Smoke12345",
            backup_password="Smoke-Backup-2026", recovery_key=security.new_recovery_key())


def _relaunch() -> None:
    """إعادة تشغيل البرنامج كعملية جديدة (بعد الاستعادة)."""
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable])
    else:
        subprocess.Popen([sys.executable, sys.argv[0]])


def main(argv: list[str] | None = None) -> int:
    """تشغيل البرنامج وإرجاع رمز الخروج (0 = نجاح)."""
    argv = list(sys.argv if argv is None else argv)
    smoke = "--smoke" in argv
    from morv.infra import paths
    from morv.infra.db.bootstrap import open_main_db
    from morv.infra.logs import log, setup_logging

    setup_logging()
    log.info("بدء التشغيل %s", argv[1:])
    con = open_main_db()

    from PySide6.QtCore import QLocale, QTimer
    from PySide6.QtWidgets import QApplication, QDialog

    from morv import APP_NAME
    from morv.infra import backup, keystore, setup, users, vault
    from morv.ui import theme
    from morv.ui.login import LoginDialog
    from morv.ui.session import Session
    from morv.ui.setup_wizard import SetupWizard
    from morv.ui.shell import MainWindow

    QLocale.setDefault(QLocale("en_US"))   # الأرقام لاتينية مهما كانت لغة النظام
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    theme.apply(app, False)

    if smoke:
        _smoke_prepare(con)
    elif paths.looks_synced_or_network(paths.data_dir()):
        # قاعدة المالك: المزامنة قد تُفسد ملف SQLite أثناء الكتابة. تحذير لا منع.
        from morv.ui.widgets import info
        log.warning("مجلد البيانات داخل مجلد مزامنة أو شبكة: %s", paths.data_dir())
        info(None, "تحذير: مكان البيانات",
             "مجلد بيانات البرنامج يقع داخل مجلد مزامنة (مثل OneDrive أو Google Drive) أو مجلد شبكة.\n"
             "المزامنة أثناء العمل قد تُتلف قاعدة البيانات. انقل البيانات إلى مجلد محلي عادي.", error=True)

    # الإعداد الأول (أو الاستعادة على جهاز جديد)، ويُستكمل إن كان انقطع سابقًا
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
                # الاستعادة ذرية: لم يتغير شيء. يُغلق البرنامج، وعند فتحه يظهر المعالج من جديد.
                log.exception("فشلت الاستعادة على جهاز جديد")
                from morv.ui.widgets import info
                info(None, "الاستعادة", f"لم تتم الاستعادة ولم يتغير شيء: {e}\nافتح البرنامج وأعد المحاولة.", error=True)
                return 1
            con = open_main_db()
            try:
                backup.relink_device(con, wiz.restore_secret)   # يثبّت بصمة المفتاح في حلقة قديمة
            except (backup.BackupError, vault.VaultError) as e:
                log.warning("استُعيدت البيانات لكن تعذّر ربط الجهاز: %s", e)
            users.audit(con, None, "backup.restore", None, None,
                        f"استعادة على جهاز جديد من {wiz.restore_path}" + (f" (نسخة أمان: {safety.name})" if safety else ""))
            log.info("استعادة على جهاز جديد من %s", wiz.restore_path)

    state = {"con": con, "window": None, "code": 0}

    def do_restore(path: str, secret: str | None) -> None:
        """تنفيذ الاستعادة من داخل الإعدادات: إغلاق ← استبدال ← إعادة تشغيل."""
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
        except Exception as e:   # noqa: BLE001 — يجب إبلاغ المستخدم بأي فشل هنا
            # restore_file ذرية: أي فشل يقع قبل الاستبدال، فالقاعدة كما كانت ويُعاد فتحها
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
        """إغلاق النافذة والعودة إلى شاشة الدخول."""
        win = state["window"]
        win.health_timer.stop()
        win.close()
        state["window"] = None
        QTimer.singleShot(0, show_login)

    def show_login() -> None:
        """شاشة الدخول ثم النافذة الرئيسية."""
        if smoke:
            user = users.login(state["con"], "admin", "Smoke12345")
        else:
            dlg = LoginDialog(state["con"])
            if dlg.exec() != QDialog.DialogCode.Accepted:
                app.quit()
                return
            user = dlg.user
        session = Session(state["con"], user, on_logout=do_logout, on_restore=do_restore)
        window = MainWindow(session, app)
        state["window"] = window
        window.showMaximized()
        QTimer.singleShot(1500, lambda: _scheduled(window, session))
        if smoke:
            QTimer.singleShot(300, lambda: _smoke_done(app, paths, window))

    def _scheduled(window, session) -> None:
        """النسخ اليومي واختبار الاستعادة الأسبوعي بعد ظهور النافذة."""
        for line in backup.run_scheduled(session.con, session.user.username):
            log.info("نسخ مجدول: %s", line)
        if state["window"] is window:
            window.refresh_health()

    show_login()
    code = app.exec()
    if state["con"]:
        try:
            state["con"].close()
        except Exception:   # noqa: BLE001 — القاعدة قد تكون أُغلقت في الاستعادة
            pass
    log.info("إغلاق البرنامج")
    return code


def _smoke_done(app, paths, window) -> None:
    """كتابة نتيجة الاختبار السريع ثم الإغلاق."""
    ms = (time.perf_counter() - T_START) * 1000
    ok = window.isVisible() and window.stack.count() > 0
    (paths.data_dir() / "startup.txt").write_text(f"{'ok' if ok else 'fail'} {ms:.0f}ms\n", encoding="utf-8")
    app.quit()

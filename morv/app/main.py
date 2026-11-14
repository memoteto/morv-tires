"""نقطة تشغيل البرنامج: السجل ← قاعدة البيانات ← (الإعداد الأول) ← الدخول ← النافذة.

الترتيب مقصود: قاعدة البيانات تُفتح وتُرقّى قبل بناء أي نافذة، فإن فشلت الترقية لا تظهر
واجهة على بيانات ناقصة؛ يُكتب الخطأ في السجل ويخرج البرنامج.

حلقة الدخول: تسجيل الخروج يغلق النافذة ويعيد شاشة الدخول دون إغلاق البرنامج.
الاستعادة: تُغلق القاعدة، ويُستبدل ملفها (بعد نسخة أمان)، ثم يعيد البرنامج تشغيل نفسه.

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
    from morv.infra import backup, shop, users
    if users.count_users(con) == 0:
        shop.save(con, "محل تجريبي", "01000000000", "")
        users.create_user(con, None, "admin", "اختبار", "Smoke12345", "manager")
        backup.setup_encryption(con, "Smoke-Backup-2026", security.new_recovery_key())


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
    from morv.infra import backup, keystore, users
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

    # الإعداد الأول (أو الاستعادة على جهاز جديد)
    if users.count_users(con) == 0:
        wiz = SetupWizard(con)
        if wiz.exec() != QDialog.DialogCode.Accepted:
            con.close()
            return 0
        if wiz.result_kind == "restore":
            con.close()
            safety = backup.restore_file(paths.db_path(), wiz.restore_path, secret=wiz.restore_secret)
            con = open_main_db()
            backup.relink_device(con, wiz.restore_secret)
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
            log.exception("فشلت الاستعادة")
            state["con"] = open_main_db()
            from morv.ui.widgets import info
            info(None, "الاستعادة", f"فشلت الاستعادة ولم تتغير البيانات: {e}", error=True)
            win.show()
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

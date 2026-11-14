"""نقطة تشغيل البرنامج: السجل، ثم قاعدة البيانات، ثم الواجهة.

الترتيب مقصود: قاعدة البيانات تُفتح وتُرقّى قبل بناء أي نافذة، فإن فشلت الترقية لا تظهر
واجهة على بيانات ناقصة؛ يُكتب الخطأ في السجل ويخرج البرنامج.

الخيار --smoke (لاختبار البناء آليًا): يفتح البرنامج كاملًا ثم يكتب ملف startup.txt في
مجلد البيانات ويغلق. وجود الملف وفيه ok يعني أن النسخة المبنية تعمل.
"""
import sys
import time

T_START = time.perf_counter()


def main(argv: list[str] | None = None) -> int:
    """تشغيل البرنامج وإرجاع رمز الخروج (0 = نجاح)."""
    argv = list(sys.argv if argv is None else argv)
    from morv.infra import paths
    from morv.infra.db.bootstrap import open_main_db
    from morv.infra.logs import log, setup_logging

    setup_logging()
    log.info("بدء التشغيل %s", argv[1:])
    con = open_main_db()

    from PySide6.QtCore import QLocale, QTimer
    from PySide6.QtWidgets import QApplication

    from morv import APP_NAME
    from morv.ui.shell import MainWindow

    # الأرقام لاتينية في كل البرنامج مهما كانت لغة نظام التشغيل
    QLocale.setDefault(QLocale("en_US"))
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow(con, app)
    window.showMaximized()

    if "--smoke" in argv:
        def smoke():
            """كتابة نتيجة الاختبار السريع ثم الإغلاق."""
            ms = (time.perf_counter() - T_START) * 1000
            (paths.data_dir() / "startup.txt").write_text(f"ok {ms:.0f}ms\n", encoding="utf-8")
            app.quit()
        QTimer.singleShot(300, smoke)

    code = app.exec()
    con.close()
    log.info("إغلاق البرنامج")
    return code

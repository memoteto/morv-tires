"""تجهيز Qt للاختبارات بلا شاشة (offscreen).

إذا لم تكن PySide6 مثبتة تُتخطى اختبارات الواجهة محليًا، إلا إذا كان المتغير
MORV_REQUIRE_QT=1 (كما في GitHub Actions)؛ عندها يفشل الاختبار بدل التخطي، حتى لا
يمر البناء دون أن تُختبر الواجهة فعلًا.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


def require_qt():
    """مزخرف لكلاس اختبار: يتخطاه إن غابت Qt محليًا، ويفشل إن كانت مطلوبة."""
    if HAS_QT or os.environ.get("MORV_REQUIRE_QT") == "1":
        return lambda cls: cls
    return unittest.skip("PySide6 غير مثبتة على هذا الجهاز")


_app = None


def _shutdown():
    """إغلاق كل النوافذ وتصريف الأحداث قبل خروج المفسّر، حتى لا تُدمَّر كائنات Qt بترتيب خاطئ
    (انهيار 139 ظهر على Linux في CI بعد نجاح كل الاختبارات)."""
    if _app is not None:
        _app.closeAllWindows()
        _app.processEvents()
        _app.sendPostedEvents(None, 0)
        _app.processEvents()


def get_app():
    """تطبيق Qt واحد مشترك لكل الاختبارات (Qt لا يسمح بأكثر من واحد)."""
    global _app
    from PySide6.QtWidgets import QApplication as App
    if _app is None:
        _app = App.instance() or App([])
        import atexit
        atexit.register(_shutdown)
    return _app

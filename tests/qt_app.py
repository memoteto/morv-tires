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


def get_app():
    """تطبيق Qt واحد مشترك لكل الاختبارات (Qt لا يسمح بأكثر من واحد)."""
    from PySide6.QtWidgets import QApplication as App
    return App.instance() or App([])

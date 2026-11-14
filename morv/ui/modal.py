"""النافذة المنبثقة الموحّدة للبرنامج (بلا شريط عنوان من نظام التشغيل، كما في التصميم المعتمد).

لماذا ملف واحد: هذا هو المكان الوحيد المسموح فيه بنافذة بلا إطار، وكل ما يتعلق بها هنا:
- رأس داخلي فيه العنوان وزر إغلاق، ويُسحب به لتحريك النافذة.
- الجسم داخل منطقة تمرير، والارتفاع الأقصى مقيد بارتفاع الشاشة، حتى لا يخرج زر «حفظ» خارج
  شاشة 1366×768 (خطأ ظهر في تجربة المالك).
- أزرار التذييل ثابتة أسفل النافذة لا تتحرك مع التمرير. Esc يغلق. Ctrl+S يضغط الزر الرئيسي.
"""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractScrollArea, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from morv.ui import icons
from morv.ui.widgets import Notice, primary_button


class Modal(QDialog):
    """نافذة منبثقة موحّدة. الوارثون يبنون المحتوى في self.body ويستدعون add_footer()."""

    def __init__(self, title: str, parent=None, width: int = 560):
        """بناء الرأس والجسم القابل للتمرير والتذييل."""
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)   # لتظهر الزوايا المستديرة
        self.setObjectName("modal")
        self._drag: QPoint | None = None
        self.setFixedWidth(width)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        frame = QFrame()
        frame.setObjectName("modalFrame")
        outer.addWidget(frame)
        col = QVBoxLayout(frame)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("modalHeader")
        self.header.setFixedHeight(52)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(18, 0, 10, 0)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("modalTitle")
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("topButton")
        self.close_btn.setIcon(icons.icon("close", "#8792A3", 16))
        self.close_btn.setToolTip("إغلاق (Esc)")
        self.close_btn.clicked.connect(self.reject)
        h.addWidget(self.title_label, 1)
        h.addWidget(self.close_btn)
        col.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # يفضّل حجم المحتوى، ثم يقيده الارتفاع الأقصى فيظهر شريط التمرير
        self.scroll.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        body_widget = QWidget()
        body_widget.setObjectName("modalBody")
        self.body = QVBoxLayout(body_widget)
        self.body.setContentsMargins(22, 16, 22, 16)
        self.body.setSpacing(12)
        self.scroll.setWidget(body_widget)
        col.addWidget(self.scroll, 1)

        self.notice = Notice()
        notice_wrap = QWidget()
        nl = QVBoxLayout(notice_wrap)
        nl.setContentsMargins(22, 0, 22, 8)
        nl.addWidget(self.notice)
        col.addWidget(notice_wrap)

        self.footer = QWidget()
        self.footer.setObjectName("modalFooter")
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(22, 12, 22, 12)
        self.footer_layout.addStretch(1)
        self.footer.hide()
        col.addWidget(self.footer)

        # الارتفاع الأقصى: شاشة المستخدم ناقص هامش، فتظهر الأزرار دائمًا ويتمرر الجسم
        screen = QGuiApplication.primaryScreen()
        if screen:
            self.setMaximumHeight(screen.availableGeometry().height() - 60)
        QShortcut(QKeySequence("Esc"), self, activated=self.reject)

    def add_footer(self, primary_text: str, on_primary, secondary_text: str = "إلغاء") -> QPushButton:
        """زر رئيسي (Ctrl+S) وزر إلغاء في التذييل الثابت. يعيد الزر الرئيسي."""
        btn = primary_button(primary_text)
        btn.clicked.connect(on_primary)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=on_primary)
        cancel = QPushButton(secondary_text)
        cancel.clicked.connect(self.reject)
        self.footer_layout.insertWidget(0, btn)
        self.footer_layout.insertWidget(1, cancel)
        self.footer.show()
        return btn

    def add_footer_widget(self, w) -> None:
        """عنصر إضافي في التذييل (زر ثالث مثلًا)."""
        self.footer_layout.insertWidget(self.footer_layout.count() - 1, w)
        self.footer.show()

    # ---------- السحب من الرأس ----------
    def mousePressEvent(self, e) -> None:
        """بدء السحب إن كان الضغط على الرأس."""
        if e.button() == Qt.MouseButton.LeftButton and self.header.geometry().contains(e.position().toPoint()):
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        """تحريك النافذة أثناء السحب."""
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        """إنهاء السحب."""
        self._drag = None
        super().mouseReleaseEvent(e)

    def showEvent(self, e) -> None:
        """توسيط النافذة فوق النافذة الأم (أو الشاشة) عند الظهور."""
        super().showEvent(e)
        self.adjustSize()
        anchor = self.parentWidget().window() if self.parentWidget() else None
        geo = anchor.frameGeometry() if anchor else QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center() - self.rect().center())


class Confirm(Modal):
    """سؤال نعم/لا."""

    def __init__(self, title: str, text: str, parent=None, danger: bool = False, yes_text: str = "نعم"):
        """بناء السؤال."""
        super().__init__(title, parent, width=460)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        self.body.addWidget(lbl)
        btn = self.add_footer(yes_text, self.accept, "لا")
        if danger:
            btn.setObjectName("danger")


class Message(Modal):
    """رسالة معلومات أو خطأ بزر «حسنًا»."""

    def __init__(self, title: str, text: str, parent=None, error: bool = False):
        """بناء الرسالة."""
        super().__init__(title, parent, width=460)
        n = Notice()
        n.show_message(text, "error" if error else "success")
        self.body.addWidget(n)
        ok = primary_button("حسنًا")
        ok.clicked.connect(self.accept)
        self.footer_layout.insertWidget(0, ok)
        self.footer.show()


def ask(parent, title: str, text: str, danger: bool = False) -> bool:
    """سؤال نعم/لا بالنافذة الموحّدة. يعيد True عند الموافقة."""
    return Confirm(title, text, parent, danger).exec() == QDialog.DialogCode.Accepted


def info(parent, title: str, text: str, error: bool = False) -> None:
    """رسالة بالنافذة الموحّدة."""
    Message(title, text, parent, error).exec()

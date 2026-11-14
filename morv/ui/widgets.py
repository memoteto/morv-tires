"""مكونات واجهة مشتركة صغيرة، حتى لا تتكرر في كل شاشة ويبقى الشكل موحّدًا.

لا SQL هنا؛ كل ما تحتاجه الشاشات من قاعدة البيانات يمر عبر morv.infra.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)

from morv.ui import icons


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    """بطاقة بإطار وعنوان اختياري، تعيد (البطاقة، تخطيطها الداخلي) لإضافة المحتوى."""
    box = QFrame()
    box.setObjectName("card")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(12)
    if title:
        head = QLabel(title)
        head.setObjectName("cardTitle")
        lay.addWidget(head)
    return box, lay


def muted(text: str) -> QLabel:
    """نص توضيحي باهت يلتف على أكثر من سطر."""
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl


def form() -> QFormLayout:
    """نموذج حقول موحّد: العناوين فوق الحقول عند الضيق، ومحاذاة تتبع اتجاه التطبيق."""
    f = QFormLayout()
    f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    f.setLabelAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter)
    f.setFormAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignTop)
    f.setSpacing(10)
    return f


def ltr_edit(placeholder: str = "") -> QLineEdit:
    """حقل للقيم اللاتينية (هاتف، اسم دخول، كلمة مرور): اتجاهه يسار-يمين دائمًا."""
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    # القيمة لاتينية فالحقل وحده يُعرض LTR؛ هذا ليس قلبًا لتخطيط، بل لمحتوى الحقل
    e.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute)
    return e


class PasswordEdit(QWidget):
    """حقل كلمة مرور مع زر إظهار/إخفاء."""

    def __init__(self, placeholder: str = ""):
        """بناء الحقل والزر."""
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = ltr_edit(placeholder)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.toggle = QToolButton()
        self.toggle.setObjectName("topButton")
        self.toggle.setCheckable(True)
        self.toggle.setToolTip("إظهار كلمة المرور")
        self.toggle.toggled.connect(self._flip)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.toggle)
        self.set_icon_color("#566173")

    def set_icon_color(self, color: str) -> None:
        """تلوين أيقونة العين حسب الثيم."""
        self._color = color
        self._flip(self.toggle.isChecked())

    def _flip(self, show: bool) -> None:
        """تبديل الإظهار والإخفاء."""
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password)
        self.toggle.setIcon(icons.icon("eye_off" if show else "eye", self._color, 18))
        self.toggle.setToolTip("إخفاء كلمة المرور" if show else "إظهار كلمة المرور")

    def text(self) -> str:
        """النص المكتوب."""
        return self.edit.text()

    def setText(self, value: str) -> None:
        """تعيين النص (للاختبارات والمسح)."""
        self.edit.setText(value)

    def clear(self) -> None:
        """مسح الحقل."""
        self.edit.clear()


class Notice(QLabel):
    """شريط رسالة داخل الشاشة: خطأ أو نجاح أو تحذير، يختفي حين لا توجد رسالة."""

    def __init__(self):
        """يبدأ مخفيًا."""
        super().__init__()
        self.setWordWrap(True)
        self.setObjectName("noticeError")
        self.hide()

    def show_message(self, text: str, level: str = "error") -> None:
        """عرض رسالة بمستوى: error أو success أو warn. التنسيق من theme.py حسب objectName."""
        self.setObjectName({"error": "noticeError", "success": "noticeSuccess", "warn": "noticeWarn"}[level])
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText(text)
        self.setVisible(bool(text))


def primary_button(text: str) -> QPushButton:
    """زر الإجراء الرئيسي (أزرق)."""
    b = QPushButton(text)
    b.setObjectName("primary")
    return b


def danger_button(text: str) -> QPushButton:
    """زر إجراء خطر (أحمر) مثل الاستعادة أو الإيقاف."""
    b = QPushButton(text)
    b.setObjectName("danger")
    return b


def ask(parent, title: str, text: str) -> bool:
    """سؤال نعم/لا بالعربية. يعيد True عند الموافقة."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Question)
    yes = box.addButton("نعم", QMessageBox.ButtonRole.YesRole)
    box.addButton("لا", QMessageBox.ButtonRole.NoRole)
    box.exec()
    return box.clickedButton() is yes


def info(parent, title: str, text: str, error: bool = False) -> None:
    """رسالة معلومات أو خطأ بزر «حسنًا»."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Critical if error else QMessageBox.Icon.Information)
    box.addButton("حسنًا", QMessageBox.ButtonRole.AcceptRole)
    box.exec()

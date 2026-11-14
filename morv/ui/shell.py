"""هيكل النافذة الرئيسية: شريط جانبي قابل للطي، وشريط علوي، ومساحة الصفحات.

في المرحلة 0 كل صفحة مجرد لافتة تقول أي مرحلة ستبنيها. الصفحات الحقيقية تُضاف في
مراحلها وتحل محل اللافتة عبر NAV دون تغيير الهيكل.

الثيم وحالة طي الشريط تُحفظ في جدول settings عبر morv.infra.settings، فتعود كما تركها
المستخدم عند فتح البرنامج مرة أخرى.
"""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from morv import APP_NAME, VERSION
from morv.infra import settings
from morv.ui import icons, theme

SIDE_WIDE = 244     # عرض الشريط الجانبي مفتوحًا
SIDE_NARROW = 72    # عرضه مطويًا (أيقونات فقط)

# (المفتاح، الاسم الظاهر، الأيقونة، المرحلة التي تبني الصفحة) بترتيب ظهورها في الشريط
NAV = [
    ("dashboard", "لوحة التحكم", "dashboard", 7),
    ("sale", "البيع", "sale", 3),
    ("invoices", "فواتير البيع والمرتجع", "invoices", 3),
    ("purchase", "المشتريات", "purchase", 2),
    ("items", "الأصناف والمخزون", "items", 2),
    ("customers", "العملاء", "customers", 1),
    ("suppliers", "الموردون", "suppliers", 1),
    ("installments", "التقسيط", "installments", 5),
    ("treasury", "الخزينة", "treasury", 4),
    ("reports", "التقارير", "reports", 6),
    ("settings", "الإعدادات", "settings", 1),
    ("backup", "النسخ الاحتياطي", "backup", 1),
]


def placeholder(title: str, phase: int) -> QWidget:
    """صفحة مؤقتة تخبر المستخدم أن الشاشة لم تُبنَ بعد وفي أي مرحلة ستُبنى."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(24, 24, 24, 24)
    card = QFrame()
    card.setObjectName("card")
    inner = QVBoxLayout(card)
    inner.setContentsMargins(24, 24, 24, 24)
    head = QLabel(title)
    head.setObjectName("pageTitle")
    note = QLabel(f"هذه الشاشة تُبنى في المرحلة {phase} من خطة V1.")
    note.setObjectName("muted")
    inner.addWidget(head)
    inner.addWidget(note)
    inner.addStretch(1)
    lay.addWidget(card)
    return page


class MainWindow(QMainWindow):
    """النافذة الرئيسية. تستخدم إطار نظام التشغيل العادي (بلا نافذة بلا إطار)."""

    def __init__(self, con, app: QApplication):
        """بناء النافذة واسترجاع الثيم وحالة الشريط المحفوظة."""
        super().__init__()
        self.con = con
        self.app = app
        self.dark = settings.get(con, "ui.theme", "light") == "dark"
        self.collapsed = settings.get(con, "ui.sidebar_collapsed", "0") == "1"
        self.colors = theme.apply(app, self.dark)
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1024, 640)

        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_side())

        main = QWidget()
        col = QVBoxLayout(main)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        for _key, title, _icon, phase in NAV:
            self.stack.addWidget(placeholder(title, phase))
        col.addWidget(self.stack, 1)
        row.addWidget(main, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage(f"{APP_NAME} {VERSION}")

        self.nav.currentRowChanged.connect(self.go)
        self.nav.setCurrentRow(0)
        self._apply_collapse()
        self._refresh_icons()

    # ---------- البناء ----------
    def _build_side(self) -> QWidget:
        """الشريط الجانبي: زر الطي واسم البرنامج في الأعلى، ثم قائمة الشاشات."""
        self.side = QWidget()
        self.side.setObjectName("side")
        lay = QVBoxLayout(self.side)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setContentsMargins(12, 0, 12, 0)
        self.side_toggle = QToolButton()
        self.side_toggle.setObjectName("sideToggle")
        self.side_toggle.setIconSize(QSize(20, 20))
        self.side_toggle.clicked.connect(self.toggle_sidebar)
        self.brand = QLabel(APP_NAME)
        self.brand.setObjectName("brand")
        top.addWidget(self.side_toggle)
        top.addWidget(self.brand, 1)
        lay.addLayout(top)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for key, title, _icon, _phase in NAV:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(title)
            self.nav.addItem(item)
        lay.addWidget(self.nav, 1)
        return self.side

    def _build_topbar(self) -> QWidget:
        """الشريط العلوي: عنوان الصفحة الحالية وزر تبديل الثيم."""
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setFixedHeight(60)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("topButton")
        self.theme_button.setIconSize(QSize(18, 18))
        self.theme_button.clicked.connect(self.toggle_theme)
        lay.addWidget(self.title, 1)
        lay.addWidget(self.theme_button)
        return bar

    # ---------- السلوك ----------
    def go(self, row: int) -> None:
        """عرض الصفحة المختارة من الشريط وتحديث العنوان."""
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        self.title.setText(NAV[row][1])

    def go_key(self, key: str) -> None:
        """الانتقال إلى صفحة بمفتاحها (مثل 'backup')، للاستخدام من أي مكان في البرنامج."""
        for i, entry in enumerate(NAV):
            if entry[0] == key:
                self.nav.setCurrentRow(i)
                return
        raise KeyError(key)

    def toggle_theme(self) -> None:
        """التبديل بين الفاتح والداكن وحفظ الاختيار."""
        self.dark = not self.dark
        self.colors = theme.apply(self.app, self.dark)
        settings.put(self.con, "ui.theme", "dark" if self.dark else "light")
        self._refresh_icons()

    def toggle_sidebar(self) -> None:
        """طي الشريط الجانبي أو فتحه وحفظ الحالة."""
        self.collapsed = not self.collapsed
        settings.put(self.con, "ui.sidebar_collapsed", "1" if self.collapsed else "0")
        self._apply_collapse()
        self._refresh_icons()

    def _apply_collapse(self) -> None:
        """تطبيق حالة الطي: مطويًا تختفي النصوص وتبقى الأيقونات، والاسم يظهر كتلميح."""
        self.side.setFixedWidth(SIDE_NARROW if self.collapsed else SIDE_WIDE)
        self.brand.setVisible(not self.collapsed)
        for i, entry in enumerate(NAV):
            self.nav.item(i).setText("" if self.collapsed else entry[1])

    def _refresh_icons(self) -> None:
        """إعادة تلوين الأيقونات بعد تغيير الثيم أو الطي."""
        c = self.colors
        for i, entry in enumerate(NAV):
            self.nav.item(i).setIcon(icons.icon(entry[2], c["side_text"]))
        # السهم يشير لاتجاه الحركة: مطويًا يُفتح نحو اليسار، مفتوحًا يُطوى نحو اليمين
        self.side_toggle.setIcon(icons.icon("expand" if self.collapsed else "collapse", c["side_text"]))
        self.side_toggle.setToolTip("فتح الشريط" if self.collapsed else "طي الشريط")
        self.theme_button.setIcon(icons.icon("sun" if self.dark else "moon", c["text"], 18))
        self.theme_button.setToolTip("الوضع الفاتح" if self.dark else "الوضع الداكن")

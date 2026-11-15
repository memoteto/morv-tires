"""هيكل «قائمة + معاينة» المشترك لشاشات العملاء والموردين والأصناف.

- شريط أدوات: بحث، فلاتر مقطعية، زر إضافة (Ctrl+N).
- جدول Model/View بلا عناصر داخل الصفوف؛ النتائج صفحة صفحة (300) وزر «تحميل المزيد».
- لوحة المعاينة لا تظهر افتراضيًا؛ تفتح عند الضغط على صف وتُغلق بزر أو Esc (قرار المالك).

كل شاشة ترث ListPage وتحدد: الأعمدة، ودالة البحث، وبناء المعاينة، ونافذة الإضافة/التعديل.
"""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QTableView, QVBoxLayout, QWidget)

from morv.core.text import RLM
from morv.infra.db.connection import ClosedDatabase
from morv.ui import icons
from morv.ui.widgets import muted, primary_button

PAGE = 300


class TupleModel(QAbstractTableModel):
    """نموذج جدول فوق قائمة صفوف (tuples) مع دوال عرض لكل عمود.

    columns: قائمة (العنوان، دالة تأخذ الصف وتعيد النص). الصفوف تُضاف بالدفعات دون إعادة البناء.
    """

    def __init__(self, columns: list, icon_for=None):
        """تجهيز الأعمدة. icon_for: دالة تأخذ الصف وتعيد اسم أيقونة للعمود الأول (اختياري)."""
        super().__init__()
        self.columns = columns
        self.icon_for = icon_for
        self.icon_color = "#8792A3"
        self._icon_cache: dict = {}
        self.rows: list = []

    def reset_rows(self, rows: list) -> None:
        """استبدال كل الصفوف (بحث جديد)."""
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def append_rows(self, rows: list) -> None:
        """إلحاق صفحة جديدة."""
        if not rows:
            return
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(rows) - 1)
        self.rows.extend(rows)
        self.endInsertRows()

    def rowCount(self, parent=QModelIndex()) -> int:
        """عدد الصفوف."""
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        """عدد الأعمدة."""
        return len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """نص الخلية."""
        if role == Qt.ItemDataRole.DisplayRole:
            return self.columns[index.column()][1](self.rows[index.row()])
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0 and self.icon_for:
            name = self.icon_for(self.rows[index.row()])
            if name not in self._icon_cache:
                from morv.ui import icons
                self._icon_cache[name] = icons.icon(name, self.icon_color, 18)
            return self._icon_cache[name]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """عناوين الأعمدة."""
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][0]
        return None


class ListPage(QWidget):
    """صفحة قائمة ومعاينة. الوارثون يعرّفون: columns, title, add_label, fetch(), build_preview(), open_dialog()."""

    columns: list = []
    title = ""
    add_label = "جديد"
    add_icon = "plus"
    row_icon = None            # دالة (صف → اسم أيقونة) لعمود الاسم
    filters: list = []          # [(المفتاح، العنوان)]؛ فارغة = بلا فلاتر
    search_placeholder = "بحث"

    def __init__(self, session):
        """بناء الصفحة."""
        super().__init__()
        self.session = session
        self.filter_key = self.filters[0][0] if self.filters else None
        self.offset = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        self.count_label = muted("")
        head.addWidget(self.count_label, 1)
        self.add_btn = primary_button(self.add_label, self.add_icon)
        self.add_btn.clicked.connect(lambda: self.open_dialog(None))
        self.add_btn.setEnabled(self.can_edit())
        head.addWidget(self.add_btn)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(12)
        left = QVBoxLayout()
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.search_placeholder)
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", "#8792A3", 16), QLineEdit.ActionPosition.LeadingPosition)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._debounced_reload)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        tools.addWidget(self.search, 1)
        self.filter_buttons = {}
        for key, label in self.filters:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("segment")
            b.setChecked(key == self.filter_key)
            b.clicked.connect(lambda _c=False, k=key: self.set_filter(k))
            self.filter_buttons[key] = b
            tools.addWidget(b)
        left.addLayout(tools)

        self.model = TupleModel(self.columns, self.row_icon)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)   # عمود الاسم يتمدد
        self.view.horizontalHeader().setMinimumSectionSize(90)
        self.view.setIconSize(QSize(18, 18))
        self.view.clicked.connect(lambda idx: self.show_preview(self.model.rows[idx.row()]))
        self.view.doubleClicked.connect(lambda idx: self.open_dialog(self.model.rows[idx.row()][0]) if self.can_edit() else None)
        left.addWidget(self.view, 1)
        self.more_btn = QPushButton("تحميل المزيد")
        self.more_btn.clicked.connect(self.load_more)
        self.more_btn.hide()
        self.empty_label = muted("لا توجد نتائج مطابقة.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        left.addWidget(self.empty_label)
        left.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignCenter)
        body.addLayout(left, 3)

        # لوحة المعاينة (مخفية حتى يُضغط صف)
        self.preview = QFrame()
        self.preview.setObjectName("card")
        # عرض المعاينة نسبي للشاشة: 360 على الشاشات الواسعة، وأقل على الضيقة
        screen = QGuiApplication.primaryScreen()
        avail_w = screen.availableGeometry().width() if screen else 1366
        self.preview.setFixedWidth(max(280, min(360, avail_w // 4)))
        pv = QVBoxLayout(self.preview)
        pv.setContentsMargins(16, 12, 16, 16)
        top = QHBoxLayout()
        top.addWidget(muted("المعاينة"), 1)
        close = QPushButton("إغلاق")
        close.clicked.connect(self.hide_preview)
        top.addWidget(close)
        pv.addLayout(top)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_body = QWidget()
        self.preview_layout = QVBoxLayout(self.preview_body)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_scroll.setWidget(self.preview_body)
        pv.addWidget(self.preview_scroll, 1)
        self.preview.hide()
        body.addWidget(self.preview)
        root.addLayout(body, 1)

        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.open_dialog(None) if self.can_edit() else None)
        QShortcut(QKeySequence("Esc"), self, activated=self.hide_preview)
        QShortcut(QKeySequence("F2"), self, activated=self.search.setFocus)
        self.current_id = None
        self.reload()

    # ---------- ما يعرّفه الوارث ----------
    def can_edit(self) -> bool:
        """هل يملك المستخدم صلاحية الإضافة والتعديل؟"""
        return True

    def fetch(self, query: str, filter_key, offset: int) -> list:
        """جلب صفحة من الصفوف؛ العمود الأول دائمًا هو المعرّف."""
        raise NotImplementedError

    def total(self) -> str:
        """نص العدّاد أعلى الصفحة."""
        return ""

    def build_preview(self, row_id: int) -> None:
        """ملء preview_layout بتفاصيل السجل."""
        raise NotImplementedError

    def open_dialog(self, row_id: int | None) -> None:
        """فتح نافذة إضافة (None) أو تعديل."""
        raise NotImplementedError

    # ---------- السلوك ----------
    def set_filter(self, key) -> None:
        """تبديل الفلتر."""
        self.filter_key = key
        for k, b in self.filter_buttons.items():
            b.setChecked(k == key)
        self.reload()

    def reload(self, keep_id: int | None = None) -> None:
        """إعادة البحث من البداية، مع إبقاء المعاينة على نفس السجل إن طُلب."""
        self.offset = 0
        rows = self.fetch(self.search.text(), self.filter_key, 0)
        self.model.reset_rows(rows)
        self.view.resizeColumnsToContents()
        self.more_btn.setVisible(len(rows) == PAGE)
        self.empty_label.setVisible(not rows)
        self.count_label.setText(self.total())
        if keep_id is not None:
            self.show_preview_id(keep_id)
        elif self.current_id is not None and not any(r[0] == self.current_id for r in rows):
            self.hide_preview()

    def _debounced_reload(self) -> None:
        """إعادة البحث بعد توقف الكتابة. إن أُغلقت القاعدة في الأثناء (تسجيل خروج) يُتجاهل."""
        try:
            self.reload()
        except ClosedDatabase:
            pass

    def load_more(self) -> None:
        """الصفحة التالية."""
        self.offset += PAGE
        rows = self.fetch(self.search.text(), self.filter_key, self.offset)
        self.model.append_rows(rows)
        self.more_btn.setVisible(len(rows) == PAGE)

    def show_preview(self, row: tuple) -> None:
        """فتح المعاينة لصف."""
        self.show_preview_id(row[0])

    def show_preview_id(self, row_id: int) -> None:
        """فتح المعاينة لسجل بمعرّفه."""
        self.current_id = row_id
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.build_preview(row_id)
        self.preview_layout.addStretch(1)
        self.preview.show()

    def hide_preview(self) -> None:
        """إغلاق المعاينة."""
        self.current_id = None
        self.preview.hide()
        self.view.clearSelection()

    # ---------- مساعدات للمعاينة ----------
    def kv(self, key: str, value: str) -> None:
        """سطر «عنوان: قيمة» في المعاينة: العنوان بعرض ثابت، والقيمة تبدأ من نفس الحافة دائمًا.

        QLabel يحاذي النص حسب اتجاه النص نفسه لا اتجاه الواجهة، فقيمة تبدأ بأرقام لاتينية كانت تقفز
        إلى أقصى اليسار (ملاحظة المالك). علامة RLM في أول النص تجعل اتجاه الفقرة عربيًا دون أن تغيّر
        عزل الأرقام بـ ltr().
        """
        row = QHBoxLayout()
        row.setSpacing(10)
        k = muted(key)
        k.setFixedWidth(118)
        k.setAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignTop)
        v = QLabel(RLM + (value or "—"))
        v.setWordWrap(True)
        v.setAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignTop)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(k)
        row.addWidget(v, 1)
        w = QWidget()
        w.setLayout(row)
        self.preview_layout.addWidget(w)

    def heading(self, text: str, sub: str = "") -> None:
        """عنوان المعاينة."""
        h = QLabel(text)
        h.setObjectName("cardTitle")
        h.setWordWrap(True)
        self.preview_layout.addWidget(h)
        if sub:
            self.preview_layout.addWidget(muted(sub))

    def actions(self, *buttons) -> None:
        """صف أزرار في المعاينة."""
        row = QHBoxLayout()
        for b in buttons:
            row.addWidget(b)
        row.addStretch(1)
        w = QWidget()
        w.setLayout(row)
        self.preview_layout.addWidget(w)

"""شاشة المخزون بتبويباتها: الأصناف، والدفعات والجرد (المرحلة 3).
إعداد حقول بطاقة الصنف في «الإعدادات» (للمدير؛ يُستخدم نادرًا — قرار المالك).

نافذة الصنف تُبنى من إعداد الحقول: كل حقل ظاهر يصير عنصر إدخال بنوعه (نص/رقم/قائمة/نعم-لا/مقاس)،
والإلزامي يُعلَّم بنجمة ويُفحص في طبقة البيانات لا في الواجهة فقط.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from morv.core import validate
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import items
from morv.infra.items import Item
from morv.ui.listpage import ListPage
from morv.ui.modal import Modal
from morv.ui.shell_placeholder import placeholder
from morv.ui.widgets import Notice, ask, danger_button, form, ltr_edit, muted, primary_button


class InventoryPage(QWidget):
    """حاوية تبويبات المخزون."""

    def __init__(self, session):
        """بناء التبويبات حسب الصلاحيات."""
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        self.tabs = QTabWidget()
        self.items_tab = ItemsPage(session)
        self.tabs.addTab(self.items_tab, "الأصناف")
        self.tabs.addTab(placeholder(3), "الدفعات (DOT)")
        self.tabs.addTab(placeholder(3), "الجرد والتسويات")
        lay.addWidget(self.tabs)


class ItemsPage(ListPage):
    """قائمة الأصناف. التكلفة والرصيد يأتيان في المرحلة 3 مع الدفعات."""
    title = "الأصناف"
    add_label = "صنف جديد"
    search_placeholder = "الاسم، الكود، المقاس (205/55R16) أو الباركود (F2)"
    filters = [("all", "الكل"), ("tire", "إطارات"), ("battery", "بطاريات"), ("service", "خدمات"), ("inactive", "الموقوفة")]
    columns = [
        ("الصنف", lambda r: r[3]),
        ("النوع", lambda r: items.KINDS[r[1]]),
        ("الكود", lambda r: ltr(r[2])),
        ("المواصفة", lambda r: ltr(r[4]) if r[4] else "—"),
        ("قطاعي", lambda r: ltr(fmt(r[5]))),
        ("جملة", lambda r: ltr(fmt(r[6]))),
        ("جملة الجملة", lambda r: ltr(fmt(r[7]))),
        ("حد الطلب", lambda r: "—" if r[1] == "service" else ltr(str(r[8]))),
    ]

    def __init__(self, session):
        """بناء الصفحة مع تهيئة الصفوف قبل أول بحث."""
        super().__init__(session)
        self.search.returnPressed.connect(self.scan_enter)

    def can_edit(self) -> bool:
        """صلاحية الأصناف."""
        return self.session.can("items.manage")

    def fetch(self, query, filter_key, offset):
        """صفحة من البحث حسب النوع."""
        kind = filter_key if filter_key in items.KINDS else None
        return items.search(self.session.con, query, kind=kind, include_inactive=filter_key == "inactive", offset=offset)

    def total(self) -> str:
        """العدّاد."""
        return f"{items.count(self.session.con)} صنف نشط"

    def scan_enter(self) -> None:
        """Enter في البحث: إن طابق النص باركودًا أو كودًا فتح معاينته مباشرة (مسار الماسح الضوئي)."""
        it = items.by_barcode(self.session.con, self.search.text())
        if it:
            self.show_preview_id(it.id)

    def build_preview(self, row_id: int) -> None:
        """تفاصيل الصنف وخصائصه الظاهرة."""
        it = items.get(self.session.con, row_id)
        self.heading(it.name, f"{items.KINDS[it.kind]} · {ltr(it.code)}" + ("" if it.active else " · موقوف"))
        if it.barcode:
            self.kv("الباركود", ltr(it.barcode))
        if it.brand:
            self.kv("الماركة", it.brand + (f" · {it.model}" if it.model else ""))
        self.kv("قطاعي", ltr(fmt(it.price_retail)))
        self.kv("جملة", ltr(fmt(it.price_wholesale)))
        self.kv("جملة الجملة", ltr(fmt(it.price_super)))
        if it.kind != "service":
            self.kv("حد الطلب", ltr(str(it.min_qty)))
        labels = {f.key: f for f in items.fields(self.session.con, it.kind)}
        for key, val in it.attrs.items():
            f = labels.get(key)
            if not f:
                continue
            shown = "نعم" if f.ftype == "bool" else (ltr(val) if any(ch.isascii() and ch.isalnum() for ch in val) else val)
            self.kv(f.label, shown)
        if it.notes:
            self.kv("ملاحظات", it.notes)
        self.preview_layout.addWidget(muted("الرصيد والدفعات والتكلفة تظهر هنا بعد المرحلة 3."))
        if self.can_edit():
            edit = primary_button("تعديل")
            edit.clicked.connect(lambda: self.open_dialog(it.id))
            toggle = danger_button("إيقاف" if it.active else "تفعيل")
            toggle.clicked.connect(lambda: self.toggle_active(it))
            self.actions(edit, toggle)

    def toggle_active(self, it: Item) -> None:
        """إيقاف أو تفعيل مع تأكيد."""
        verb = "إيقاف" if it.active else "تفعيل"
        if ask(self, verb, f"{verb} الصنف «{it.name}»؟" + (" لن يظهر في البيع." if it.active else ""), danger=it.active):
            items.set_active(self.session.con, self.session.user, it.id, not it.active)
            self.reload(keep_id=it.id)

    def open_dialog(self, row_id):
        """نافذة إضافة أو تعديل."""
        if not self.can_edit():
            return
        dlg = ItemDialog(self.session, items.get(self.session.con, row_id) if row_id else None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=dlg.saved.id)


class ItemDialog(Modal):
    """نافذة الصنف المبنية من إعداد الحقول.

    الترتيب كما في التصميم: حقول النوع (المقاس...) أولًا، ثم الماركة والموديل والباركود، ثم الاسم
    والكود (يُقترحان تلقائيًا من البيانات ويمكن تعديلهما)، ثم الأسعار وحد الطلب، ثم الباقي.
    """
    FIRST = ("brand", "model", "barcode", "name", "code", "price_retail", "price_wholesale", "price_super", "min_qty")

    def __init__(self, session, it: Item | None, parent=None):
        """بناء النافذة؛ تغيير النوع يعيد بناء الحقول."""
        super().__init__("تعديل صنف" if it else "صنف جديد", parent, width=700)
        self.session, self.it, self.saved = session, it, None
        kinds = QHBoxLayout()
        kinds.addWidget(muted("نوع الصنف"))
        self.kind_buttons = {}
        for k, label in items.KINDS.items():
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("segment")
            b.clicked.connect(lambda _c=False, kk=k: self.set_kind(kk))
            self.kind_buttons[k] = b
            kinds.addWidget(b)
        kinds.addStretch(1)
        self.body.addLayout(kinds)
        self.fields_box = QWidget()
        self.body.addWidget(self.fields_box)
        self.body.addWidget(muted("الحقول الظاهرة والإلزامية تتبع «الإعدادات ← حقول بطاقة الصنف»."))
        self.widgets: dict = {}
        self.add_footer("حفظ", self.save)
        self.set_kind(it.kind if it else "tire")
        if it:
            for b in self.kind_buttons.values():
                b.setEnabled(False)      # النوع لا يتغير بعد الإنشاء

    def _ordered_fields(self, kind: str) -> list:
        """حقول النوع أولًا، ثم المشتركة بالترتيب المعتمد، ثم بقية المشتركة."""
        all_f = [f for f in items.fields(self.session.con, kind) if f.show]
        if kind == "service":
            all_f = [f for f in all_f if f.key not in ("brand", "model", "min_qty", "origin", "loc", "war", "cond")]
        specific = [f for f in all_f if f.kind != "common"]
        common = {f.key: f for f in all_f if f.kind == "common"}
        head = [common.pop(k) for k in self.FIRST if k in common]
        return specific + head + list(common.values())

    def set_kind(self, kind: str) -> None:
        """إعادة بناء الحقول للنوع المختار."""
        self.kind = kind
        for k, b in self.kind_buttons.items():
            b.setChecked(k == kind)
        old = self.fields_box
        self.fields_box = QWidget()
        f = form()
        self.widgets = {}
        it = self.it
        price_ok = self.session.user.is_manager or self.session.can("items.prices")
        for fld in self._ordered_fields(kind):
            label = fld.label + (" *" if fld.required else "")
            current = ""
            if it:
                current = getattr(it, fld.key, None) if fld.key in items.FIXED_KEYS else it.attrs.get(fld.key, "")
                if fld.key.startswith("price_"):
                    current = fmt(current) if current else ""
                current = "" if current is None else str(current)
            if fld.ftype == "sel":
                w = QComboBox()
                w.addItem("")
                w.addItems(fld.options)
                if current in fld.options:
                    w.setCurrentText(current)
                w.currentIndexChanged.connect(self._suggest)
            elif fld.ftype == "bool":
                w = QCheckBox("نعم")
                w.setChecked(current == "1")
            elif fld.key == "min_qty":
                w = QSpinBox()
                w.setRange(0, 100000)
                w.setValue(int(current or 0))
            elif fld.ftype in ("num", "size") or fld.key in ("code", "barcode"):
                w = ltr_edit(fld.example)
                w.setText(current)
                w.textChanged.connect(self._suggest)
            else:
                w = QLineEdit(current)
                w.setPlaceholderText(fld.example)
                w.textChanged.connect(self._suggest)
            if fld.key.startswith("price_") and not price_ok:
                w.setEnabled(False)
            self.widgets[fld.key] = w
            f.addRow(label, w)
            if fld.key == "code":
                f.addRow("", muted("الاسم والكود يُقترحان تلقائيًا من الماركة والمقاس؛ اتركهما فارغين لقبول المقترح أو اكتب ما تريد"))
        self.fields_box.setLayout(f)
        self.body.replaceWidget(old, self.fields_box)
        old.hide()
        old.deleteLater()
        self._suggest()

    def _value(self, key: str) -> str:
        """قيمة حقل كنص."""
        w = self.widgets.get(key)
        if w is None:
            return ""
        if isinstance(w, QComboBox):
            return w.currentText()
        if isinstance(w, QCheckBox):
            return "1" if w.isChecked() else ""
        if isinstance(w, QSpinBox):
            return str(w.value())
        return w.text()

    def suggestions(self) -> tuple[str, str]:
        """(الاسم، الكود) المقترحان من البيانات الحالية."""
        attrs = {k: self._value(k) for k in ("size", "li", "si", "ah")}
        if attrs.get("size"):
            try:
                attrs["size"] = validate.tire_size(attrs["size"])["size"]
            except validate.ValidationError:
                pass
        name = validate.suggest_item_name(self.kind, self._value("brand"), self._value("model"), attrs)
        code = validate.suggest_item_code(self.kind, self._value("brand"), attrs, items.next_seq(self.session.con, self.kind))
        return name, code

    def _suggest(self, *_args) -> None:
        """عرض المقترح كنص باهت داخل حقلي الاسم والكود وهما فارغان."""
        if "name" not in self.widgets:
            return
        name, code = self.suggestions()
        self.widgets["name"].setPlaceholderText(name or "اسم الصنف كما يظهر في الفاتورة")
        if "code" in self.widgets:
            self.widgets["code"].setPlaceholderText(code)

    def save(self) -> None:
        """جمع القيم والحفظ؛ الاسم والكود الفارغان يأخذان المقترح."""
        try:
            prices = [parse_money(self._value(k)) if self._value(k).strip() else 0
                      for k in ("price_retail", "price_wholesale", "price_super")]
        except MoneyError as e:
            self.notice.show_message(f"السعر: {e}")
            return
        name, code = self.suggestions()
        attrs = {k: self._value(k) for k in self.widgets if k not in items.FIXED_KEYS}
        it = Item(self.it.id if self.it else None, self.kind, self._value("code").strip() or code,
                  self._value("name").strip() or name, self._value("barcode"), self._value("brand"),
                  self._value("model"), *prices, int(self._value("min_qty") or 0), self._value("notes"), True, attrs)
        try:
            self.saved = (items.update if self.it else items.create)(self.session.con, self.session.user, it)
        except items.ItemError as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class ItemFieldsTab(QWidget):
    """إعداد حقول بطاقة الصنف: إظهار/إلزامي/طباعة/فلتر لكل حقل، وإضافة حقل مخصص."""
    COLS = ["الحقل", "مثال", "إظهار", "إلزامي", "في الطباعة", "فلتر بحث"]

    def __init__(self, session):
        """بناء الجدول لكل نوع."""
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.kind = QComboBox()
        for k, label in (("common", "مشترك"), *items.KINDS.items()):
            self.kind.addItem(label, k)
        self.kind.currentIndexChanged.connect(self.reload)
        top.addWidget(muted("مجموعة الحقول"))
        top.addWidget(self.kind)
        top.addStretch(1)
        add = QPushButton("إضافة حقل مخصص")
        add.clicked.connect(self.add_custom)
        top.addWidget(add)
        lay.addLayout(top)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.on_change)
        lay.addWidget(self.table, 1)
        lay.addWidget(muted("الحقول المقفلة يعتمد عليها البرنامج في البحث والفواتير فتبقى ظاهرة وإلزامية. "
                            "عند جعل حقل إلزاميًا بعد وجود أصناف، الأصناف القديمة لا تُمنع من البيع."))
        self.notice = Notice()
        lay.addWidget(self.notice)
        self._loading = False
        self.reload()

    def reload(self) -> None:
        """إعادة ملء الجدول. الخلايا القابلة للتبديل مربعات اختيار في الخلية نفسها (لا عناصر مضمّنة)."""
        self._loading = True
        kind = self.kind.currentData()
        rows = [f for f in items.fields(self.session.con, kind) if f.kind == kind]
        self.table.setRowCount(len(rows))
        for i, f in enumerate(rows):
            name = QTableWidgetItem(f.label + ("  🔒" if f.builtin else ""))
            name.setData(Qt.ItemDataRole.UserRole, f.key)
            self.table.setItem(i, 0, name)
            self.table.setItem(i, 1, QTableWidgetItem(ltr(f.example) if f.example else ""))
            for col, val, locked in ((2, f.show, f.builtin), (3, f.required, f.builtin), (4, f.print, False), (5, f.filter, False)):
                cell = QTableWidgetItem()
                flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                if locked:
                    flags = Qt.ItemFlag.ItemIsUserCheckable
                cell.setFlags(flags)
                cell.setCheckState(Qt.CheckState.Checked if val else Qt.CheckState.Unchecked)
                self.table.setItem(i, col, cell)
        self._loading = False

    def on_change(self, cell: QTableWidgetItem) -> None:
        """حفظ التبديل فورًا."""
        if self._loading or cell.column() < 2:
            return
        key = self.table.item(cell.row(), 0).data(Qt.ItemDataRole.UserRole)
        on = cell.checkState() == Qt.CheckState.Checked
        kw = {2: "show", 3: "required", 4: "print_", 5: "filter_"}[cell.column()]
        try:
            items.set_field(self.session.con, self.session.user, self.kind.currentData(), key, **{kw: on})
        except items.ItemError as e:
            self.notice.show_message(str(e))
        self.reload()

    def add_custom(self) -> None:
        """حوار حقل مخصص."""
        dlg = CustomFieldDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                items.add_custom_field(self.session.con, self.session.user, self.kind.currentData(), dlg.label, dlg.ftype, dlg.options)
            except items.ItemError as e:
                self.notice.show_message(str(e))
            self.reload()


class CustomFieldDialog(Modal):
    """حوار حقل مخصص: الاسم والنوع والاختيارات."""

    def __init__(self, parent=None):
        """الحقول."""
        super().__init__("حقل مخصص", parent, width=440)
        self.label = self.ftype = ""
        self.options = []
        lay = self.body
        f = form()
        self.name = QLineEdit()
        self.type = QComboBox()
        for k, v in (("text", "نص"), ("num", "رقم"), ("sel", "قائمة اختيارات"), ("bool", "نعم / لا")):
            self.type.addItem(v, k)
        self.opts = QLineEdit()
        self.opts.setPlaceholderText("للقائمة: الاختيارات مفصولة بـ |")
        f.addRow("اسم الحقل", self.name)
        f.addRow("النوع", self.type)
        f.addRow("الاختيارات", self.opts)
        lay.addLayout(f)
        self.add_footer("إضافة", self.go)

    def go(self) -> None:
        """جمع القيم."""
        self.label = self.name.text()
        self.ftype = self.type.currentData()
        self.options = [o for o in self.opts.text().split("|") if o.strip()]
        self.accept()

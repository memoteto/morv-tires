"""Stock screen tabs: items, lots and stocktake."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from morv.core import tire_catalog, validate
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr, norm_ar
from morv.infra import inventory, items
from morv.infra.items import Item
from morv.ui import tokens
from morv.ui.adaptive_layout import AdaptiveTableColumns
from morv.ui.listpage import ListPage
from morv.ui.modal import Modal
from morv.ui.stock_pages import LotsPage, StockPage
from morv.ui.widgets import (Notice, ask, danger_button, field, form, ltr_edit, muted, muted_icon, primary_button,
                             search_completer, searchable_edit)


class InventoryPage(QWidget):
    def __init__(self, session):
        super().__init__()
        self._session = session
        self.lots_tab: "LotsPage | None" = None
        self.stock_tab: "StockPage | None" = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        self.tabs = QTabWidget()
        self.items_tab = ItemsPage(session)
        self.tabs.addTab(self.items_tab, muted_icon("inventory"), "الأصناف")
        # Lots and stock tabs are built on first selection to avoid blocking startup.
        self.tabs.addTab(QWidget(), muted_icon("calendar"), "الدفعات (DOT)")
        self.tabs.addTab(QWidget(), muted_icon("check"), "الجرد والتسويات")
        self.tabs.currentChanged.connect(self._refresh_tab)
        lay.addWidget(self.tabs)

    def refresh(self) -> None:
        page = self.tabs.currentWidget()
        page.refresh() if hasattr(page, "refresh") else page.reload()

    def _refresh_tab(self, index: int) -> None:
        # Build lazy tabs on first access; guard against re-entrant signal during insertTab.
        if index == 1 and self.lots_tab is None:
            self.tabs.currentChanged.disconnect(self._refresh_tab)
            self.lots_tab = LotsPage(self._session)
            self.tabs.removeTab(1)
            self.tabs.insertTab(1, self.lots_tab, muted_icon("calendar"), "الدفعات (DOT)")
            self.tabs.setCurrentIndex(1)
            self.tabs.currentChanged.connect(self._refresh_tab)
            return
        if index == 2 and self.stock_tab is None:
            self.tabs.currentChanged.disconnect(self._refresh_tab)
            self.stock_tab = StockPage(self._session)
            self.tabs.removeTab(2)
            self.tabs.insertTab(2, self.stock_tab, muted_icon("check"), "الجرد والتسويات")
            self.tabs.setCurrentIndex(2)
            self.tabs.currentChanged.connect(self._refresh_tab)
            return
        page = self.tabs.currentWidget()
        if page is not self.items_tab:
            page.reload()


class ItemsPage(ListPage):
    title = "الأصناف والمخزون"
    add_label = "صنف جديد"
    search_placeholder = "الاسم، الكود، المقاس (205/55R16) أو الباركود (F2)"
    filters_as_dropdown = True
    filters = [("all", "الكل"), ("tire", "إطارات"), ("battery", "بطاريات"), ("service", "خدمات"), ("inactive", "الموقوفة")]
    row_icon = staticmethod(lambda r: r[1])      # tire / battery / service
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
        super().__init__(session)
        self.search.returnPressed.connect(self.scan_enter)

    def can_edit(self) -> bool:
        return self.session.can("items.manage")

    def fetch(self, con, query, filter_key, offset):
        kind = filter_key if filter_key in items.KINDS else None
        return items.search(con, query, kind=kind, include_inactive=filter_key == "inactive", offset=offset)

    def total(self) -> str:
        return f"{items.count(self.session.con)} صنف نشط"

    def scan_enter(self) -> None:
        it = items.by_barcode(self.session.con, self.search.text())
        if it:
            self.show_preview_id(it.id)

    def build_preview(self, row_id: int) -> None:
        it = items.get(self.session.con, row_id)
        self.heading(it.name, f"{items.KINDS[it.kind]} · {ltr(it.code)}" + ("" if it.active else " · موقوف"))
        if it.kind != "service":
            metrics = [("الرصيد", ltr(str(inventory.on_hand(self.session.con, it.id)))),
                       ("حد الطلب", ltr(str(it.min_qty)))]
            if self.session.can("purchases.post") or self.session.can("items.cost_view"):
                metrics.append(("القيمة الدفترية", ltr(fmt(inventory.book_value(self.session.con, it.id)))))
            self.summary_metrics(metrics)
        self.summary_section("الأسعار", "cash")
        self.summary_metrics([
            ("قطاعي", ltr(fmt(it.price_retail))),
            ("جملة", ltr(fmt(it.price_wholesale))),
            ("جملة الجملة", ltr(fmt(it.price_super))),
        ])
        details = []
        if it.barcode:
            details.append(("الباركود", ltr(it.barcode)))
        if it.brand:
            details.append(("الماركة", it.brand + (f" · {it.model}" if it.model else "")))
        labels = {f.key: f for f in items.fields(self.session.con, it.kind)}
        for key, val in it.attrs.items():
            f = labels.get(key)
            if not f:
                continue
            shown = "نعم" if f.ftype == "bool" else (ltr(val) if any(ch.isascii() and ch.isalnum() for ch in val) else val)
            details.append((f.label, shown))
        if it.notes:
            details.append(("ملاحظات", it.notes))
        if details:
            self.summary_section("بيانات الصنف", it.kind)
            self.summary_details(details)
        if it.kind != "service":
            self.summary_note("تفاصيل الدفعات وDOT في تبويب «الدفعات (DOT)».")
        if self.can_edit():
            edit = primary_button("تعديل", "edit")
            edit.clicked.connect(lambda: self.open_dialog(it.id))
            toggle = danger_button("إيقاف" if it.active else "تفعيل", "pause" if it.active else "play")
            toggle.clicked.connect(lambda: self.toggle_active(it))
            self.actions(edit, toggle)

    def toggle_active(self, it: Item) -> None:
        verb = "إيقاف" if it.active else "تفعيل"
        if ask(self, verb, f"{verb} الصنف «{it.name}»؟" + (" لن يظهر في البيع." if it.active else ""), danger=it.active):
            items.set_active(self.session.con, self.session.user, it.id, not it.active)
            self.reload(keep_id=it.id)

    def open_dialog(self, row_id):
        if not self.can_edit():
            return
        dlg = ItemDialog(self.session, items.get(self.session.con, row_id) if row_id else None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=dlg.saved.id)


class ItemDialog(Modal):
    FIRST = ("brand", "model", "barcode", "name", "code", "price_retail", "price_wholesale", "price_super", "min_qty")

    def __init__(self, session, it: Item | None, parent=None):
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
                b.setEnabled(False)      # Kind is fixed once created

    def _ordered_fields(self, kind: str) -> list:
        all_f = items.visible_fields(self.session.con, kind)
        specific = [f for f in all_f if f.kind != "common"]
        common = {f.key: f for f in all_f if f.kind == "common"}
        head = [common.pop(k) for k in self.FIRST if k in common]
        return specific + head + list(common.values())

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        for k, b in self.kind_buttons.items():
            b.setChecked(k == kind)
        old = self.fields_box
        self.fields_box = QWidget()
        f = form()
        self.widgets = {}
        it = self.it
        self._category_manual = it is not None
        self._tyre_catalog = items.tyre_catalog(self.session.con) if kind == "tire" else {}
        price_ok = self.session.can("items.prices")
        for fld in self._ordered_fields(kind):
            label = fld.label + (" *" if fld.required else "")
            current = ""
            if it:
                current = getattr(it, fld.key, None) if fld.key in items.FIXED_KEYS else it.attrs.get(fld.key, "")
                if fld.key.startswith("price_"):
                    current = fmt(current) if current else ""
                current = "" if current is None else str(current)
            if kind == "tire" and fld.key == "size":
                w = searchable_edit(sorted(self._tyre_catalog), tire_catalog.size_search_key, fld.example)
                w.setText(current)
                w.textChanged.connect(self._suggest)
                w.textEdited.connect(self._suggest_category)
                w.completer().activated[str].connect(self._suggest_category)
            elif fld.ftype == "sel":
                w = QComboBox()
                options = list(fld.options)
                if kind == "tire" and fld.key == "cat" and current in tire_catalog.LEGACY_CATEGORIES:
                    options.append(current)
                w.addItem("")
                w.addItems(options)
                if kind == "tire" and fld.key == "cat":
                    w.setEditable(True)
                    w.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                    completer = search_completer(w, options, norm_ar)
                    w.setCompleter(completer)
                    w.lineEdit().textEdited.connect(self._category_edited)
                    w.activated.connect(self._category_edited)
                    completer.activated[str].connect(self._category_edited)
                if current in options:
                    w.setCurrentText(current)
                w.currentTextChanged.connect(self._suggest)
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

    def _category_edited(self, *_args) -> None:
        self._category_manual = True

    def _suggest_category(self, value: str) -> None:
        category = self.widgets.get("cat")
        if self._category_manual or category is None:
            return
        try:
            size = validate.tire_size(value)["size"]
        except validate.ValidationError:
            category.setCurrentText("")
            return
        suggestion = tire_catalog.suggested_category(size, self._tyre_catalog.get(size, {}))
        category.setCurrentText(suggestion)

    def _value(self, key: str) -> str:
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
        if "name" not in self.widgets:
            return
        name, code = self.suggestions()
        self.widgets["name"].setPlaceholderText(name or "اسم الصنف كما يظهر في الفاتورة")
        if "code" in self.widgets:
            self.widgets["code"].setPlaceholderText(code)

    def save(self) -> None:
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
    COLS = ["الحقل", "مثال", "إظهار", "إلزامي", "في الطباعة", "فلتر بحث"]
    CONTROLS = {2: ("إظهار", "eye"), 3: ("إلزامي", "alert"),
                4: ("طباعة", "printer"), 5: ("بحث", "search")}

    def __init__(self, session):
        super().__init__()
        self.session = session
        self._loading = False
        self._loaded_kind = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.LAYOUT["pane_gap"])
        self.hint = muted("عرض فقط — تعديل الحقول يتطلب صلاحية إدارة الأصناف.")
        self.hint.hide()
        lay.addWidget(self.hint)
        top = QHBoxLayout()
        top.setSpacing(tokens.LAYOUT["page_back_gap"])
        self.kind = QComboBox()
        for k, label in (("common", "مشترك"), *items.KINDS.items()):
            self.kind.addItem(label, k)
        top.addWidget(muted("مجموعة الحقول"))
        top.addWidget(self.kind)
        top.addStretch(1)
        self.add_btn = primary_button("إضافة حقل مخصص", "plus")
        self.add_btn.clicked.connect(self.add_custom)
        top.addWidget(self.add_btn)
        lay.addLayout(top)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        controls = {}
        for col, (label, name) in self.CONTROLS.items():
            head = self.table.horizontalHeaderItem(col)
            head.setText(label)
            head.setToolTip(self.COLS[col])
            head.setData(Qt.ItemDataRole.AccessibleTextRole, self.COLS[col])
            controls[col] = (label, muted_icon(name))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.columns = AdaptiveTableColumns(
            self.table, {0: tokens.LAYOUT["item_field_name_weight"],
                         1: tokens.LAYOUT["item_field_example_weight"]}, controls)
        self.table.itemChanged.connect(self.on_change)
        lay.addWidget(self.table, 1)
        self.notice = Notice()
        lay.addWidget(self.notice)
        self.kind.currentIndexChanged.connect(self.reload)
        self.reload()

    def showEvent(self, event):
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        kind = self.kind.currentData()
        current = self.table.currentRow()
        selected = self.table.item(current, 0) if current >= 0 else None
        key = selected.data(Qt.ItemDataRole.UserRole) if selected and self._loaded_kind == kind else None
        column = max(0, self.table.currentColumn())
        editable = self.session.can("items.manage")
        self.add_btn.setEnabled(editable)
        self.hint.setVisible(not editable)
        rows = [f for f in items.fields(self.session.con, kind) if f.kind == kind]
        self._loading = True
        try:
            self.table.setRowCount(len(rows))
            for i, f in enumerate(rows):
                name = QTableWidgetItem(f.label)
                name.setData(Qt.ItemDataRole.UserRole, f.key)
                if f.builtin:
                    name.setIcon(muted_icon("lock"))
                name.setToolTip(f.label + (" — حقل أساسي" if f.builtin else ""))
                self.table.setItem(i, 0, name)
                example = QTableWidgetItem(ltr(f.example) if f.example else "")
                example.setToolTip(example.text())
                self.table.setItem(i, 1, example)
                for col, val, locked in ((2, f.show, f.builtin), (3, f.required, f.builtin),
                                         (4, f.print, False), (5, f.filter, False)):
                    cell = QTableWidgetItem()
                    flags = Qt.ItemFlag.ItemIsUserCheckable
                    if editable and not locked:
                        flags |= Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                    cell.setFlags(flags)
                    cell.setCheckState(Qt.CheckState.Checked if val else Qt.CheckState.Unchecked)
                    cell.setToolTip(self.COLS[col] + (" — حقل أساسي" if locked else ""))
                    self.table.setItem(i, col, cell)
                if f.key == key:
                    self.table.setCurrentCell(i, column)
            self._loaded_kind = kind
        finally:
            self._loading = False
        self.columns.schedule()

    def on_change(self, cell: QTableWidgetItem) -> None:
        if self._loading or cell.row() < 0 or cell.column() < 2:
            return
        if not cell.flags() & Qt.ItemFlag.ItemIsEnabled:
            self.reload()
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
        if not self.session.can("items.manage"):
            self.notice.show_message("لا تملك صلاحية إدارة الأصناف")
            self.reload()
            return
        dlg = CustomFieldDialog(self)
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted:
                try:
                    items.add_custom_field(self.session.con, self.session.user, self.kind.currentData(),
                                           dlg.label, dlg.ftype, dlg.options)
                except items.ItemError as e:
                    self.notice.show_message(str(e))
                self.reload()
        finally:
            dlg.deleteLater()


class CustomFieldDialog(Modal):
    def __init__(self, parent=None):
        super().__init__("حقل مخصص", parent, width=440)
        self.label = self.ftype = ""
        self.options = []
        self.name = QLineEdit()
        self.type = QComboBox()
        for k, v in (("text", "نص"), ("num", "رقم"), ("sel", "قائمة اختيارات"), ("bool", "نعم / لا")):
            self.type.addItem(v, k)
        self.opts = QLineEdit()
        self.opts.setPlaceholderText("الاختيارات مفصولة بـ |")
        self.body.addWidget(field("اسم الحقل *", self.name))
        self.body.addWidget(field("النوع", self.type))
        self.options_field = field("الاختيارات *", self.opts)
        self.body.addWidget(self.options_field)
        self.type.currentIndexChanged.connect(self._type_changed)
        self._type_changed()
        self.add_footer("إضافة", self.go)

    def _type_changed(self):
        self.options_field.setVisible(self.type.currentData() == "sel")
        if self.isVisible():
            self._fit_height()

    def go(self) -> None:
        label = self.name.text().strip()
        ftype = self.type.currentData()
        options = [o.strip() for o in self.opts.text().split("|") if o.strip()] if ftype == "sel" else []
        if not label:
            self.notice.show_message("اسم الحقل مطلوب")
            self.name.setFocus()
            return
        if ftype == "sel" and not options:
            self.notice.show_message("قائمة الاختيارات مطلوبة")
            self.opts.setFocus()
            return
        self.label, self.ftype, self.options = label, ftype, options
        self.accept()

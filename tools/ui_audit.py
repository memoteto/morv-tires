"""Renders every page and key dialog at fixed sizes with fake data; saves PNGs and a layout report."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 1093x614 is 1366x768 at Windows 125% scale, in logical pixels
SIZES = [(1366, 768), (1920, 1080), (1093, 614)]
# A window on a screen of that size is shorter: the title bar and the task bar take this much
CHROME_PX = 40


def _dialogs(session, item_id):
    from morv.ui.customers_page import CustomerDialog
    from morv.ui.inventory_page import ItemDialog
    from morv.ui.opening_dialogs import OpeningBalancesDialog
    from morv.ui.period_dialogs import PeriodLockDialog
    from morv.ui.profit_dialogs import DistributionDialog
    from morv.ui.reconciliation_dialogs import ReconciliationDialog
    from morv.ui.settings_page import NewUserDialog
    from morv.ui.stock_adjustment_dialogs import StockAdjustmentDialog
    from morv.ui.suppliers_page import SupplierDialog
    from morv.ui.treasury_dialogs import CloseDialog, MovementDialog, SafeCountDialog
    return [("opening_balances", lambda: OpeningBalancesDialog(session)),
            ("stock_adjustment", lambda: StockAdjustmentDialog(session, item_id)),
            ("item", lambda: ItemDialog(session, None)), ("customer", lambda: CustomerDialog(session, None)),
            ("supplier", lambda: SupplierDialog(session, None)), ("new_user", lambda: NewUserDialog(session)),
            ("treasury_movement", lambda: MovementDialog(session)), ("treasury_close", lambda: CloseDialog(session)),
            ("safe_count", lambda: SafeCountDialog(session)),
            ("reconciliation", lambda: ReconciliationDialog(session)),
            ("profit_distribution", lambda: DistributionDialog(session)),
            ("period_lock", lambda: PeriodLockDialog(session))]


# Tabs holding a table or form of their own, which a shot of the page's first tab never shows
EXTRA_TABS = {"inventory": (("lots", "الدفعات (DOT)"), ("stock", "الجرد والتسويات"))}


def _open_first_preview(page, app) -> bool:
    """Preview panels only exist after a row is clicked, so no plain page shot ever shows one."""
    rows = getattr(getattr(page, "model", None), "rows", None)
    if not rows or not hasattr(page, "show_preview"):
        return False
    try:
        page.show_preview(rows[0])
    except Exception as e:                      # a preview that cannot open is worth seeing in the log
        print(f"audit: preview failed: {type(e).__name__}: {e}", flush=True)
        return False
    _settle(app)
    return True


def _tabs_worth_capturing(tabs, key) -> list:
    if tabs is None:
        return []
    wanted = {title: slug for slug, title in EXTRA_TABS.get(key, ())}
    return [(wanted[tabs.tabText(i)], i) for i in range(tabs.count()) if tabs.tabText(i) in wanted]


def _ancestors(w):
    p = w.parentWidget()
    while p is not None:
        yield p
        p = p.parentWidget()


def _clipped_buttons(root) -> list:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QAbstractButton, QScrollArea
    out = []
    bounds = root.rect()
    for b in root.findChildren(QAbstractButton):
        if not b.isVisibleTo(root) or not b.text().strip():
            continue
        top_left = b.mapTo(root, QPoint(0, 0))
        right, bottom = top_left.x() + b.width(), top_left.y() + b.height()
        if top_left.x() < 0 or top_left.y() < 0 or right > bounds.width() or bottom > bounds.height():
            in_scroll = any(isinstance(a, QScrollArea) for a in _ancestors(b))
            out.append({"text": b.text(), "x": top_left.x(), "y": top_left.y(), "w": b.width(), "h": b.height(),
                        "in_scroll": in_scroll})
    return out


def _table_report(root) -> list:
    from PySide6.QtWidgets import QTableView
    rows = []
    for t in root.findChildren(QTableView):
        if not t.isVisibleTo(root):
            continue
        h = t.horizontalHeader()
        needed = sum(h.sectionSize(i) for i in range(h.count()))
        rows.append({"name": t.objectName() or type(t).__name__, "viewport_w": t.viewport().width(),
                     "columns_w": needed, "h_scroll": t.horizontalScrollBar().isVisible(), "rows": t.model().rowCount() if t.model() else 0})
    return rows


def _measure(root) -> dict:
    return {"clipped_buttons": _clipped_buttons(root), "tables": _table_report(root),
            "window": [root.width(), root.height()]}


def _settle(app) -> None:
    """Lists load on worker threads; wait for them so screenshots show rows, not empty tables."""
    from morv.ui.workers import wait_all
    for _ in range(3):
        app.processEvents()
        wait_all()
        app.processEvents()


def _seed_search_examples(con, admin):
    from morv.core.text import norm_ar
    from morv.infra import purchasing

    examples = (
        ("tire", "إطار تجريبي Michelin Primacy 205/55R16", "size", "205/55R16", 1250050, 4, 12),
        ("tire", "إطار تجريبي Continental PremiumContact 6 215/60R16 95V", "size", "215/60R16", 875000, 4, 2),
        ("tire", "إطار تجريبي Bridgestone Turanza", "size", "225/45R17", 990000, 3, 0),
        ("battery", "بطارية تجريبية Varta 70 أمبير", "ah", "70", 465000, 3, 8),
        ("service", "خدمة تجريبية ترصيص العجلات", None, None, 15000, 0, 0),
    )
    lines = []
    for index, (kind, name, key, value, price, minimum, qty) in enumerate(examples):
        item_id = con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super, min_qty) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (kind, f"AUDIT-SEARCH-{index}", name, norm_ar(name), price, price, price, minimum)).lastrowid
        if key:
            con.execute("INSERT INTO item_attrs (item_id, field_key, value) VALUES (?,?,?)",
                        (item_id, key, value))
        if qty:
            lines.append(purchasing.PurchaseLine(item_id, qty, price // 2, "2324" if kind == "tire" else None))
    supplier_id = con.execute("SELECT id FROM suppliers ORDER BY id LIMIT 1").fetchone()[0]
    purchasing.post_invoice(con, admin, request_key="audit-search-stock", supplier_id=supplier_id,
                            supplier_invoice_no="AUDIT-SEARCH", invoice_date="2026-11-01", lines=lines)


def _capture_item_search(form, app, out, name, expected, report):
    from PySide6.QtWidgets import QLabel

    search = form.item_search
    search.setFocus()
    search.setText("AUDIT-SEARCH")
    search.search()
    _settle(app)
    popup = search.popup
    try:
        if not popup.isVisible() or popup._layout.count() != expected + 1:
            raise RuntimeError(f"audit: missing search results in {name}")
        if not popup.grab().save(str(out / f"{name}.png")):
            raise RuntimeError(f"audit: cannot save {name}")
        info = _measure(popup)
        info["search_width"] = search.width()
        info["minimum_popup_width"] = min(search.width(), search.screen().availableGeometry().width())
        info["fits_screen"] = popup.screen().availableGeometry().contains(popup.frameGeometry())
        info["search_labels"] = []
        for index in range(expected):
            button = popup._layout.itemAt(index).widget()
            for label in button.findChildren(QLabel):
                if not label.text():
                    continue
                width = label.width()
                height = label.height()
                needed_w = label.sizeHint().width()
                needed_h = (label.heightForWidth(width) if label.wordWrap()
                            else label.sizeHint().height())
                info["search_labels"].append({
                    "row": index, "role": label.objectName(), "text": label.text(),
                    "width": width, "height": height, "needed_width": needed_w,
                    "needed_height": needed_h,
                    "clipped": not button.rect().contains(label.geometry())
                    or needed_h > height or (not label.wordWrap() and needed_w > width),
                })
        report["pages"][name] = info
        print(f"audit: captured {name} ({expected} results)", flush=True)
    finally:
        search.clear()
        search.close_results()
        _settle(app)


def _capture_checked(root, app, out, name, report, *, dialog=False, screen_h=None):
    """Capture an actual settled widget; a missing required screenshot is an audit failure."""
    _settle(app)
    if not root.isVisible() or not root.grab().save(str(out / f"{name}.png")):
        raise RuntimeError(f"audit: cannot capture {name}")
    info = _measure(root)
    if dialog:
        info["size"] = [root.width(), root.height()]
        info["screen_h"] = screen_h
    report["dialogs" if dialog else "pages"][name] = info
    print(f"audit: captured {name}", flush=True)


def _capture_pos_vat(win, page, session, app, out, tag, report):
    from morv.infra import settings

    con = session.con
    previous = settings.get(con, "tax.enabled", "0")
    if page._rows:
        raise RuntimeError("audit: POS VAT requires an empty audit draft")
    try:
        settings.put(con, "tax.enabled", "1")
        page.refresh()
        for code in ("AUDIT-SEARCH-0", "AUDIT-SEARCH-4"):
            row = con.execute("SELECT id,kind,name FROM items WHERE code=?", (code,)).fetchone()
            page._add_row(*row)
        page.invoice_disc.setText("100.00")
        for inclusive in (True, False):
            page.tax_inclusive.setChecked(inclusive)
            _settle(app)
            area = (page.invoice_layout.summary_area if page.invoice_layout.columns == 2
                    else page.invoice_layout.primary_area)
            area.ensureWidgetVisible(page.tax_inclusive)
            _settle(app)
            if (not page.tax_inclusive.isVisible() or not page.tax_row.isVisible()
                    or not page.before_tax_row.isVisible() or not page._line_table.sale_tax_visible
                    or any(row["tax_lbl"].isHidden() for row in page._rows)
                    or page._tax_quote is None or not page.summary_footer.isVisible()):
                raise RuntimeError("audit: missing POS VAT preview or fixed footer")
            mode = "inclusive" if inclusive else "exclusive"
            _capture_checked(win, app, out, f"page_pos_vat_{mode}_{tag}", report)
    finally:
        settings.put(con, "tax.enabled", previous)
        page._reset_after_post()
        _settle(app)


def _capture_sale_returns(win, session, app, out, tag, report, screen_h):
    from morv.core.sales_rules import Payment
    from morv.infra import sale_view, sales, settings
    from morv.infra.sales import SaleLineIn
    from morv.ui.sale_return_dialog import SaleReturnDialog

    con = session.con
    previous = settings.get(con, "tax.enabled", "0")
    try:
        items = con.execute("SELECT id FROM items WHERE kind='service' ORDER BY id LIMIT 2").fetchall()
        if len(items) != 2:
            raise RuntimeError("audit: two service items are required")
        for enabled in (False, True):
            settings.put(con, "tax.enabled", str(int(enabled)))
            lines = [SaleLineIn(items[0][0], 2, 57000), SaleLineIn(items[1][0], 1, 57000)]
            sale = sales.post_sale(con, session.user, request_key=f"audit-return-{tag}-{enabled}",
                                   customer_id=None, lines=lines, payments=[Payment("cash", 171000, received=171000)],
                                   prices_tax_inclusive=True if enabled else None)
            dlg = SaleReturnDialog(session, sale_view.get_sale(con, sale.id),
                                   sale_view.returnable_lines(con, sale.id),
                                   sale_view.refund_methods(con, sale.id), win)
            try:
                dlg.reason.setCurrentText("مقاس خاطئ")
                dlg.model.setData(dlg.model.index(0, dlg.model.QTY), 1)
                dlg.show()
                _settle(app)
                if (dlg.scroll is not None or not dlg.footer.isVisible()
                        or not dlg.screen().availableGeometry().contains(dlg.frameGeometry())
                        or dlg.view.horizontalScrollBar().isVisible() or _clipped_buttons(dlg)):
                    raise RuntimeError("audit: sale return controls or dialog bounds are clipped")
                mode = "vat" if enabled else "legacy"
                _capture_checked(dlg, app, out, f"dlg_sale_return_{mode}_{tag}", report,
                                 dialog=True, screen_h=screen_h)
            finally:
                dlg.close()
                dlg.deleteLater()
                _settle(app)
    finally:
        settings.put(con, "tax.enabled", previous)


def _capture_purchase_returns(win, session, app, out, tag, report, screen_h):
    from morv.infra import purchasing, settings
    from morv.infra.purchasing import PurchaseLine
    from morv.ui.purchase_return_dialogs import PurchaseReturnDialog

    con = session.con
    previous = settings.get(con, "tax.enabled", "0")
    try:
        item_ids = [r[0] for r in con.execute("SELECT id FROM items WHERE kind='tire' ORDER BY id LIMIT 2")]
        if len(item_ids) != 2:
            raise RuntimeError("audit: two tire items are required")
        for enabled in (False, True):
            settings.put(con, "tax.enabled", str(int(enabled)))
            name = f"مورد مراجعة المرتجع {tag} {int(enabled)}"
            next_id = con.execute("SELECT coalesce(max(id),0)+1 FROM suppliers").fetchone()[0]
            sid = con.execute("INSERT INTO suppliers (name,name_norm,mobile) VALUES (?,?,?)",
                              (name, name, f"019{next_id:08d}")).lastrowid
            tax = dict(input_tax_rate=1400, input_tax_deductible=True) if enabled else {}
            purchasing.post_invoice(
                con, session.user, request_key=f"audit-preturn-{tag}-{enabled}", supplier_id=sid,
                supplier_invoice_no=f"AUDIT-{tag}-{enabled}", invoice_date="2026-11-01",
                lines=[PurchaseLine(i, 3, 100000, "2324", **tax) for i in item_ids])
            dlg = PurchaseReturnDialog(session, sid, name, win)
            try:
                dlg.date.setText("2026-11-28")
                dlg.reason.setText("عيب تصنيع")
                dlg.model.setData(dlg.model.index(0, dlg.model.QTY), 2)
                dlg.model.setData(dlg.model.index(1, dlg.model.QTY), 1)
                dlg.settlement_mode.setCurrentIndex(dlg.settlement_mode.findData("book"))
                dlg.show()
                _settle(app)
                mode = "vat" if enabled else "legacy"
                if (dlg.scroll is not None or not dlg.post_btn.isEnabled()
                        or not dlg.screen().availableGeometry().contains(dlg.frameGeometry())
                        or dlg.view.horizontalScrollBar().isVisible() or _clipped_buttons(dlg)):
                    raise RuntimeError("audit: purchase return controls or dialog bounds are clipped")
                _capture_checked(dlg, app, out, f"dlg_purchase_return_{mode}_{tag}", report,
                                 dialog=True, screen_h=screen_h)
            finally:
                dlg.close()
                dlg.deleteLater()
                _settle(app)
    finally:
        settings.put(con, "tax.enabled", previous)


def _capture_vat_print_preview(win, app, out, tag, report, screen_h):
    from morv.ui.printing import PrintPreview
    from tools.print_samples import _shop, _vat_sale

    dlg = PrintPreview(win, _shop(), _vat_sale(False))
    try:
        dlg.show()
        _settle(app)
        if (dlg.preview_area.verticalScrollBar().maximum()
                or dlg.preview_area.horizontalScrollBar().maximum()):
            raise RuntimeError("audit: fitted VAT sheet unexpectedly requires scrolling")
        _capture_checked(dlg, app, out, f"dlg_vat_print_preview_{tag}", report,
                         dialog=True, screen_h=screen_h)
    finally:
        dlg.close()
        dlg.deleteLater()
        _settle(app)


def _capture_item_vat(win, session, app, out, tag, report, screen_h):
    from morv.infra import items, settings
    from morv.ui.inventory_page import ItemDialog

    con = session.con
    old_enabled = settings.get(con, "tax.enabled", "0")
    try:
        settings.put(con, "tax.enabled", "1")
        for kind, code in (("tire", "AUDIT-SEARCH-0"), ("battery", "AUDIT-SEARCH-3"),
                           ("service", "AUDIT-SEARCH-4")):
            item_id = con.execute("SELECT id FROM items WHERE code=?", (code,)).fetchone()[0]
            dlg = ItemDialog(session, items.get(con, item_id), win)
            try:
                dlg.show()
                _settle(app)
                if (dlg.kind != kind or not dlg.tax_treatment.isVisible()
                        or not dlg.tax_rate.isVisible()):
                    raise RuntimeError(f"audit: missing item VAT controls for {kind}")
                dlg.scroll.ensureWidgetVisible(dlg.tax_rate)
                _capture_checked(dlg, app, out, f"dlg_item_vat_{kind}_standard_{tag}", report,
                                 dialog=True, screen_h=screen_h)
                dlg.tax_treatment.setCurrentIndex(dlg.tax_treatment.findData("exempt"))
                _settle(app)
                if not dlg.tax_rate.isHidden():
                    raise RuntimeError(f"audit: item VAT rate not hidden for {kind}")
                dlg.scroll.ensureWidgetVisible(dlg.tax_treatment)
                _capture_checked(dlg, app, out, f"dlg_item_vat_{kind}_exempt_{tag}", report,
                                 dialog=True, screen_h=screen_h)
            finally:
                dlg.close()
                dlg.deleteLater()
                _settle(app)
    finally:
        settings.put(con, "tax.enabled", old_enabled)


def _capture_purchase_vat(win, page, session, supplier_records, app, out, tag, report, screen_h):
    """Populate an isolated draft in the real purchase workspace, without posting."""
    from morv.infra import settings
    from morv.ui.purchase_vat import PurchaseVatDialog
    from morv.ui.purchases_page import PurchaseForm
    from morv.ui.suppliers_page import SupplierDialog

    con = session.con
    old_enabled = settings.get(con, "tax.enabled", "0")
    old_rate = settings.get(con, "tax.rate", "1400")
    form = None
    try:
        settings.put(con, "tax.enabled", "1")
        settings.put(con, "tax.rate", "1400")
        form = PurchaseForm(session, page)
        page.addWidget(form)
        page.setCurrentWidget(form)
        items = con.execute(
            "SELECT id, kind, name FROM items WHERE code IN ('AUDIT-SEARCH-0','AUDIT-SEARCH-3') "
            "ORDER BY code").fetchall()
        if len(items) != 2:
            raise RuntimeError("audit: missing VAT purchase examples")
        for item_id, kind, name in items:
            form._add_row(item_id, kind, name)
            row = form._rows[-1]
            row["cost_edit"].setText("1000")
            if kind == "tire":
                row["dot_edit"].setText("2324")
        for mode, supplier in zip(("exclusive", "inclusive"), supplier_records, strict=True):
            form._pick_supplier(supplier.id, supplier.name, supplier.mobile)
            for amount, freight in (("0", "no_freight"), ("100", "freight")):
                form.ship.setText(amount)
                _settle(app)
                expected_tax = "280.00" if mode == "exclusive" else "245.62"
                # Freight is untaxed in this fixture; two lines of 1000 each.
                if (form._vat_mode != mode or form.tax_row.isHidden()
                        or expected_tax not in form.tax.text()
                        or form.freight_basis_field.isHidden() != (amount == "0")):
                    raise RuntimeError(f"audit: wrong purchase VAT state {mode}/{freight}")
                workspace = form.invoice_layout
                area = workspace.summary_area if workspace.columns == 2 else workspace.primary_area
                area.verticalScrollBar().setValue(0)
                _capture_checked(win, app, out, f"form_purchase_vat_{mode}_{freight}_{tag}", report)
                # A stacked or short layout may put the summary below the initial viewport.
                area.ensureWidgetVisible(form.freight_basis_field if amount != "0" else form.ship)
                _capture_checked(win, app, out, f"form_purchase_vat_{mode}_{freight}_summary_{tag}", report)
        # Instantiate exactly the production detail dialog with the populated draft.
        dlg = PurchaseVatDialog(form._vat_mode, form._vat_freight, form._rows,
                                form._default_vat_rate(), form)
        try:
            dlg.show()
            _settle(app)
            if len(dlg.editors) != 2 or not dlg.line_box.isVisible():
                raise RuntimeError("audit: missing VAT line editors")
            _capture_checked(dlg, app, out, f"dlg_purchase_vat_{tag}", report,
                             dialog=True, screen_h=screen_h)
            dlg.scroll.ensureWidgetVisible(dlg.editors[-1][1])
            _capture_checked(dlg, app, out, f"dlg_purchase_vat_lines_{tag}", report,
                             dialog=True, screen_h=screen_h)
        finally:
            dlg.close()
            dlg.deleteLater()
        # The registered supplier opens expanded; scroll to the real VAT controls.
        dlg = SupplierDialog(session, supplier_records[0], win)
        try:
            dlg.show()
            _settle(app)
            if not dlg.more.isChecked() or not dlg.vat_fields.prices.isVisible():
                raise RuntimeError("audit: missing expanded supplier VAT fields")
            _capture_checked(dlg, app, out, f"dlg_supplier_expanded_{tag}", report,
                             dialog=True, screen_h=screen_h)
            dlg.scroll.ensureWidgetVisible(dlg.vat_fields.prices)
            _capture_checked(dlg, app, out, f"dlg_supplier_vat_{tag}", report,
                             dialog=True, screen_h=screen_h)
        finally:
            dlg.close()
            dlg.deleteLater()
    finally:
        # Only the audit-owned temporary draft is discarded. No invoice is posted,
        # no production discard confirmation is bypassed, and later shots are untaxed.
        if form is not None:
            page.setCurrentWidget(page.list_page)
            page.removeWidget(form)
            form.deleteLater()
        settings.put(con, "tax.enabled", old_enabled)
        settings.put(con, "tax.rate", old_rate)
        _settle(app)


def run(out: Path, size: tuple) -> dict:
    os.environ["MORV_DATA_DIR"] = tempfile.mkdtemp()
    from PySide6.QtWidgets import QApplication
    from morv.infra import suppliers, users
    from morv.infra.db.bootstrap import open_main_db
    from morv.ui import theme
    from morv.ui.session import Session
    from morv.ui.shell import NAV, MainWindow
    from tools.seed_fake import seed
    app = QApplication.instance() or QApplication(sys.argv)
    theme.load_bundled_fonts()
    con = open_main_db(Path(os.environ["MORV_DATA_DIR"]) / "audit.db")
    admin = users.create_user(con, None, "admin", "المدير", "Admin12345", "manager")
    seed(con, admin, n_customers=60, n_items=80, n_suppliers=10)
    _seed_search_examples(con, admin)
    supplier_records = [
        suppliers.create(con, admin, suppliers.Supplier(
            None, name, mobile, vat_status="registered", vat_prices_inclusive=inclusive))
        for name, mobile, inclusive in (
            ("مورد تجريبي غير شامل الضريبة", "01099990001", False),
            ("مورد تجريبي شامل الضريبة", "01099990002", True),
        )
    ]
    item_id = con.execute("SELECT id FROM items ORDER BY id LIMIT 1").fetchone()[0]
    session = Session(con, admin)
    geo = app.primaryScreen().availableGeometry()
    print(f"audit: app ready on {app.platformName()} screen {geo.width()}x{geo.height()}", flush=True)
    report: dict = {"platform": app.platformName(), "screen": [geo.width(), geo.height()], "pages": {}, "dialogs": {}}
    out.mkdir(parents=True, exist_ok=True)
    for dark in (False, True):
        mode = "dark" if dark else "light"
        win = MainWindow(session, app)
        win.health_timer.stop()
        if win.dark != dark:
            win.toggle_theme()
        win.show()
        for w, h in [size]:
            win.resize(w, h - CHROME_PX)
            _settle(app)
            if win.collapsed:       # narrow screens start collapsed; the open sidebar is the tighter case
                win.toggle_sidebar()
                _settle(app)
                win.grab().save(str(out / f"sidebar_open_{w}x{h}_{mode}.png"))
                report["pages"][f"sidebar_open_{w}x{h}_{mode}"] = _measure(win)
                win.toggle_sidebar()
                _settle(app)
            for key, _title, _icon, _phase in NAV:
                win.go_key(key)
                _settle(app)
                print(f"audit: page {key} {mode}", flush=True)
                name = f"page_{key}_{w}x{h}_{mode}"
                win.grab().save(str(out / f"{name}.png"))
                report["pages"][name] = _measure(win)
                if key == "pos":
                    _capture_item_search(win.stack.currentWidget(), app, out,
                                         f"popup_pos_search_{w}x{h}_{mode}", 5, report)
                    _capture_pos_vat(win, win.stack.currentWidget(), session, app, out,
                                     f"{w}x{h}_{mode}", report)
                if key == "purchases":
                    page = win.stack.currentWidget()
                    page.open_form()
                    _settle(app)
                    _capture_item_search(page.form_page, app, out,
                                         f"popup_purchase_search_{w}x{h}_{mode}", 4, report)
                    page.back_to_list()
                    _settle(app)
                    _capture_purchase_vat(win, page, session, supplier_records, app, out,
                                          f"{w}x{h}_{mode}", report, h)
                if _open_first_preview(win.stack.currentWidget(), app):
                    shot = f"page_{key}_preview_{w}x{h}_{mode}"
                    print(f"audit: preview {key} {mode}", flush=True)
                    win.grab().save(str(out / f"{shot}.png"))
                    report["pages"][shot] = _measure(win)
                    win.go_key(key)          # rebuild the page so the next shot starts without a preview
                    _settle(app)
                if key == "settings":
                    page = win.stack.currentWidget()
                    win.toggle_sidebar()
                    _settle(app)
                    side = "collapsed" if win.collapsed else "open"
                    shot = f"page_settings_sidebar_{side}_{w}x{h}_{mode}"
                    win.grab().save(str(out / f"{shot}.png"))
                    report["pages"][shot] = _measure(win)
                    win.toggle_sidebar()
                    _settle(app)
                    for route in page.destinations:
                        if not page.open_page(route):
                            raise RuntimeError(f"audit: cannot open settings/{route}")
                        _settle(app)
                        shot = f"page_settings_tab_{route}_{w}x{h}_{mode}"
                        win.grab().save(str(out / f"{shot}.png"))
                        report["pages"][shot] = _measure(win)
                    page.show_hub()
                    _settle(app)
                tabs = getattr(win.stack.currentWidget(), "tabs", None)
                for tab_name, tab_index in _tabs_worth_capturing(tabs, key):
                    tabs.setCurrentIndex(tab_index)
                    _settle(app)
                    tab_shot = f"page_{key}_tab_{tab_name}_{w}x{h}_{mode}"
                    print(f"audit: tab {key}/{tab_name} {mode}", flush=True)
                    win.grab().save(str(out / f"{tab_shot}.png"))
                    report["pages"][tab_shot] = _measure(win)
                    tabs.setCurrentIndex(0)
                    _settle(app)
            _capture_item_vat(win, session, app, out, f"{w}x{h}_{mode}", report, h)
            _capture_vat_print_preview(win, app, out, f"{w}x{h}_{mode}", report, h)
            _capture_sale_returns(win, session, app, out, f"{w}x{h}_{mode}", report, h)
            _capture_purchase_returns(win, session, app, out, f"{w}x{h}_{mode}", report, h)
            for dname, make in _dialogs(session, item_id):
                name = f"dlg_{dname}_{w}x{h}_{mode}"
                try:
                    dlg = make()
                    dlg.show()
                    _settle(app)
                    dlg.grab().save(str(out / f"{name}.png"))
                    if dname == "period_lock":      # the calendar popup is a separate window; capture it too
                        from morv.ui.widgets import DateEdit
                        popup = dlg.findChild(DateEdit).calendarWidget().window()
                        popup.show()
                        _settle(app)
                        popup.grab().save(str(out / f"popup_calendar_{w}x{h}_{mode}.png"))
                        popup.hide()
                    info = _measure(dlg)
                    info["size"] = [dlg.width(), dlg.height()]
                    info["screen_h"] = h
                    report["dialogs"][name] = info
                    dlg.close()
                    dlg.deleteLater()
                except Exception as e:   # one broken dialog must not hide the others
                    report["dialogs"][name] = {"error": f"{type(e).__name__}: {e}"}
        win.close()
        win.deleteLater()
        app.processEvents()
    con.close()
    shutil.rmtree(os.environ["MORV_DATA_DIR"], ignore_errors=True)
    return report


def summary(report: dict) -> str:
    lines = []
    for kind in ("pages", "dialogs"):
        for name, r in report[kind].items():
            if "error" in r:
                lines.append(f"ERROR {name}: {r['error']}")
                continue
            for b in r["clipped_buttons"]:
                where = "inside a scroll area (reachable by scrolling)" if b["in_scroll"] else "NOT in a scroll area (unreachable)"
                lines.append(f"CLIPPED {name}: window {r['window']}: button '{b['text']}' at ({b['x']},{b['y']}) "
                             f"{b['w']}x{b['h']} - {where}")
            if "search_labels" in r:
                if r["window"][0] < r["minimum_popup_width"] or not r["fits_screen"]:
                    lines.append(f"SEARCH-BOUNDS {name}: popup {r['window']}, input {r['search_width']}px, "
                                 f"fits screen: {r['fits_screen']}")
                for label in r["search_labels"]:
                    if label["clipped"]:
                        lines.append(f"SEARCH-CLIPPED {name}: row {label['row']} {label['role']} "
                                     f"{label['width']}x{label['height']} needs "
                                     f"{label['needed_width']}x{label['needed_height']}: {label['text']}")
            for t in r["tables"]:
                if t["h_scroll"] or t["columns_w"] > t["viewport_w"]:
                    lines.append(f"TABLE-SCROLL {name}: {t['name']} needs {t['columns_w']}px in {t['viewport_w']}px")
            if kind == "dialogs" and r["size"][1] >= r["screen_h"] - 60:
                lines.append(f"TALL {name}: dialog {r['size'][0]}x{r['size'][1]} on screen height {r['screen_h']}")
    return "\n".join(lines) or "no layout findings"


def _screen_config(w: int, h: int) -> str:
    """Offscreen Qt defaults to an 800x600 screen; a config file gives it the size being audited."""
    cfg = {"screens": [{"name": "audit", "x": 0, "y": 0, "width": w, "height": h,
                        "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}
    # Qt splits platform arguments on ':', so a Windows drive path (C:\...) would be cut; keep it relative
    name = f"ui_audit_screen_{w}x{h}.json"
    Path(name).write_text(json.dumps(cfg), encoding="utf-8")
    return name


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0] if args else "ui_audit_out")
    native = "--native" in sys.argv
    one = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--size=")), None)
    if one:
        import faulthandler
        faulthandler.enable()
    if one:      # child process: one screen size per process, since the screen is fixed at start-up
        w, h = (int(v) for v in one.split("x"))
        part = run(out, (w, h))
        print(f"audit: {one} captured {len(part['pages'])} pages, {len(part['dialogs'])} dialogs", flush=True)
        (out / f"report_{one}.json").write_text(json.dumps(part, ensure_ascii=False, indent=1), encoding="utf-8")
        print("audit: report written", flush=True)
        return 0
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"pages": {}, "dialogs": {}, "screens": {}}
    for w, h in SIZES:
        env = dict(os.environ)
        if not native:
            env["QT_QPA_PLATFORM"] = f"offscreen:configfile={_screen_config(w, h)}"
        tag = f"{w}x{h}"
        subprocess.run([sys.executable, __file__, str(out), f"--size={tag}"] + (["--native"] if native else []),
                       env=env, check=True)
        part = json.loads((out / f"report_{tag}.json").read_text(encoding="utf-8"))
        report["screens"][tag] = part["screen"]
        report["pages"].update(part["pages"])
        report["dialogs"].update(part["dialogs"])
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    text = "\n".join(f"SCREEN {k}: available {v[0]}x{v[1]}" for k, v in report["screens"].items())
    text += "\n" + summary(report)
    (out / "summary.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

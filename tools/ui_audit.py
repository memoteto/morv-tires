"""Renders every page and key dialog at fixed sizes with fake data; saves PNGs and a layout report."""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SIZES = [(1366, 768), (1920, 1080)]


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


def run(out: Path) -> dict:
    os.environ["MORV_DATA_DIR"] = tempfile.mkdtemp()
    if "--native" not in sys.argv:       # --native uses the real display, as on the owner's machine
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from morv.infra import users
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
    item_id = con.execute("SELECT id FROM items ORDER BY id LIMIT 1").fetchone()[0]
    session = Session(con, admin)
    report: dict = {"platform": app.platformName(), "pages": {}, "dialogs": {}}
    out.mkdir(parents=True, exist_ok=True)
    for dark in (False, True):
        mode = "dark" if dark else "light"
        win = MainWindow(session, app)
        win.health_timer.stop()
        if win.dark != dark:
            win.toggle_theme()
        win.show()
        for w, h in SIZES:
            win.resize(w, h)
            app.processEvents()
            for key, _title, _icon, _phase in NAV:
                win.go_key(key)
                app.processEvents()
                name = f"page_{key}_{w}x{h}_{mode}"
                win.grab().save(str(out / f"{name}.png"))
                report["pages"][name] = _measure(win)
            for dname, make in _dialogs(session, item_id):
                name = f"dlg_{dname}_{w}x{h}_{mode}"
                try:
                    dlg = make()
                    dlg.show()
                    app.processEvents()
                    dlg.grab().save(str(out / f"{name}.png"))
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
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
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
            for t in r["tables"]:
                if t["h_scroll"] or t["columns_w"] > t["viewport_w"]:
                    lines.append(f"TABLE-SCROLL {name}: {t['name']} needs {t['columns_w']}px in {t['viewport_w']}px")
            if kind == "dialogs" and r["size"][1] >= r["screen_h"] - 60:
                lines.append(f"TALL {name}: dialog {r['size'][0]}x{r['size'][1]} on screen height {r['screen_h']}")
    return "\n".join(lines) or "no layout findings"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--native"]
    out = Path(args[0] if args else "ui_audit_out")
    report = run(out)
    text = summary(report)
    (out / "summary.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

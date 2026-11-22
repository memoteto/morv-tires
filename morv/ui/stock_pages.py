"""Stock tabs: lots with their movements, and stocktake. Read-only."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPushButton

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import inventory, stock_view
from morv.ui.listpage import ListPage
from morv.ui.stock_adjustment_dialogs import AdjustmentsHistoryDialog, StockAdjustmentDialog
from morv.ui.widgets import info

MOVE_LABELS = {"receipt": "استلام", "issue": "صرف", "return": "مرتجع", "restock": "إرجاع من مرتجع مورد",
               "adjust_in": "زيادة جرد", "adjust_out": "عجز جرد", "opening_in": "رصيد افتتاحي",
               "opening_out": "عكس رصيد افتتاحي"}


def _cost_ok(session, con=None) -> bool:
    return session.can("purchases.post", con) or session.can("items.cost_view", con)


class LotsPage(ListPage):
    title = "الدفعات (DOT)"
    search_placeholder = "الصنف، الكود، رمز DOT، أو مرجع الدفعة (F2)"
    filters = [("stock", "بها رصيد"), ("all", "كل الدفعات")]
    columns = [
        ("الصنف", lambda r: r[2]),
        ("الدفعة", lambda r: ltr(r[1])),
        ("DOT", lambda r: ltr(r[4]) if r[4] else "—"),
        ("الاستلام", lambda r: ltr(r[5])),
        ("المستلم", lambda r: ltr(str(r[6]))),
        ("المتبقي", lambda r: ltr(str(r[7]))),
        ("تكلفة الوحدة", lambda r: ltr(fmt(r[8])) if r[8] is not None else "—"),
    ]

    def __init__(self, session):
        super().__init__(session)
        self.add_btn.hide()

    def can_edit(self) -> bool:
        return False

    def fetch(self, con, query, filter_key, offset):
        rows = stock_view.lots(con, query, in_stock_only=filter_key == "stock", offset=offset)
        if _cost_ok(self.session, con):
            return rows
        return [r[:8] + (None,) for r in rows]

    def total(self) -> str:
        qty, _value = stock_view.totals(self.session.con)
        return f"إجمالي الرصيد على الرف: {qty}"

    def build_preview(self, row_id: int) -> None:
        row = next(r for r in self.model.rows if r[0] == row_id)
        self.heading(row[2], f"الدفعة {ltr(row[1])}")
        self.summary_metrics([
            ("DOT", ltr(row[4]) if row[4] else "—"),
            ("المستلم", ltr(str(row[6]))),
            ("المتبقي", ltr(str(row[7]))),
        ])
        self.summary_section("بيانات الدفعة", "tire")
        details = [("الدفعة", ltr(row[1])), ("تاريخ الاستلام", ltr(row[5]))]
        if row[8] is not None:
            details.append(("تكلفة الوحدة", ltr(fmt(row[8]))))
        self.summary_details(details)
        self.summary_section("حركة الدفعة", "truck")
        moves = stock_view.lot_movements(self.session.con, row_id)
        if moves:
            has_override = any(kind == "issue" and non_oldest
                               for kind, _qty, _at, non_oldest, _reason in moves)
            self.summary_table(
                ["الحركة", "الكمية", "التاريخ"],
                [(f"{MOVE_LABELS.get(kind, kind)}\n{reason}"
                  if kind == "issue" and non_oldest else MOVE_LABELS.get(kind, kind),
                  ltr(str(qty)), ltr(at))
                 for kind, qty, at, non_oldest, reason in moves],
                column_alignments=(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter,
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                ),
                one_line_dates=True, multiline_rows=has_override)
        else:
            self.summary_note("لا توجد حركات مسجلة لهذه الدفعة.")



class StockPage(ListPage):
    title = "الجرد والتسويات"
    # Nothing is created here: an adjustment belongs to one item and opens from its row,
    # and matching is a check, not a new document, so neither sits in the create slot
    add_label = None
    search_placeholder = "الصنف أو الكود (F2)"
    filters = [("all", "الكل"), ("stock", "بها رصيد"), ("low", "تحت حد الطلب")]
    columns = [
        ("الصنف", lambda r: r[1]),
        ("الكود", lambda r: ltr(r[2])),
        ("الرصيد", lambda r: ltr(str(r[4]))),
        ("الدفعات", lambda r: ltr(str(r[5]))),
        ("حد الطلب", lambda r: ltr(str(r[7])) if r[7] else "—"),
        ("القيمة الدفترية", lambda r: ltr(fmt(r[6])) if r[6] is not None else "—"),
    ]

    def can_edit(self) -> bool:
        return _cost_ok(self.session)

    def extra_actions(self) -> list:
        self.match_btn = self.history_btn = None
        actions = []
        if _cost_ok(self.session):
            self.match_btn = QPushButton("تشغيل المطابقة")
            self.match_btn.clicked.connect(self.run_match)
            actions.append(self.match_btn)
        if self.session.can("stock.adjust"):
            self.history_btn = QPushButton("سجل التسويات")
            self.history_btn.clicked.connect(self.open_history)
            actions.append(self.history_btn)
        return actions

    def fetch(self, con, query, filter_key, offset):
        rows = stock_view.summary(con, query, filter_key, offset=offset)
        if _cost_ok(self.session, con):
            return rows
        return [r[:6] + (None,) + r[7:] for r in rows]

    def total(self) -> str:
        qty, value = stock_view.totals(self.session.con)
        text = f"إجمالي الرصيد: {qty}"
        return text + f" · القيمة الدفترية: {fmt(value)} جنيه" if _cost_ok(self.session) else text

    def build_preview(self, row_id: int) -> None:
        row = next(r for r in self.model.rows if r[0] == row_id)
        self.heading(row[1], f"{ltr(row[2])} · الرصيد {ltr(str(row[4]))}")
        metrics = [("الرصيد", ltr(str(row[4]))), ("الدفعات", ltr(str(row[5])))]
        if row[6] is not None:
            metrics.append(("القيمة الدفترية", ltr(fmt(row[6]))))
        self.summary_metrics(metrics)
        self.summary_section("تفاصيل الدفعات (LOT)", "tire")
        lots = [lot for lot in inventory.list_lots(self.session.con, row_id) if lot[5] > 0]
        if lots:
            if _cost_ok(self.session):
                self.summary_table(
                    ["DOT / مرجع الدفعة", "المتبقي", "تكلفة الشراء"],
                    [(f"{ltr(dot) if dot else '—'}\n{ltr(ref)}",
                      ltr(str(on_hand)), ltr(fmt(unit_cost)))
                     for _id, ref, dot, _received, _qty_in, on_hand, unit_cost in lots],
                    multiline_rows=True)
                self.summary_note("تكلفة الشراء التاريخية للدفعات مستقلة عن تقييم FIFO والقيمة الدفترية.")
            else:
                self.summary_table(
                    ["DOT / مرجع الدفعة", "المتبقي"],
                    [(f"{ltr(dot) if dot else '—'}\n{ltr(ref)}", ltr(str(on_hand)))
                     for _id, ref, dot, _received, _qty_in, on_hand, _unit_cost in lots],
                    multiline_rows=True)
        else:
            self.summary_note("لا رصيد لهذا الصنف.")
        if self.session.can("stock.adjust"):
            adjust = QPushButton("تسوية جرد")
            adjust.clicked.connect(lambda: self.open_adjustment(row_id))
            history = QPushButton("سجل التسويات")
            history.clicked.connect(self.open_history)
            self.actions(adjust, history)

    def open_adjustment(self, item_id: int) -> None:
        if StockAdjustmentDialog(self.session, item_id, self).exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=item_id)

    def open_history(self) -> None:
        AdjustmentsHistoryDialog(self.session, self).exec()
        self.reload()

    def run_match(self) -> None:
        problems = inventory.reconcile(self.session.con)
        if problems:
            info(self, "المطابقة", f"وُجدت {len(problems)} فروق:\n" + "\n".join(problems[:15]), error=True)
        else:
            info(self, "المطابقة", "متطابق: الدفعات والحركات وطبقات التكلفة والقيود بلا أي فرق.")

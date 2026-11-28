"""Sales return: one atomic reversing document; the invoice is never touched."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core import costing
from morv.core import return_rules as rules
from morv.core.sales_rules import PAYMENT_ACCOUNT
from morv.infra import customer_receipts, customer_statement, installments, ledger
from morv.infra.db.connection import transaction
from morv.infra.inventory import COGS_ACCOUNTS
from morv.infra.users import audit, has_permission


class ReturnError(ValueError):
    """Sales return refused, with a message fit to show."""


@dataclass(frozen=True)
class ReturnLineIn:
    sale_line_id: int
    qty: int
    condition: str = "sound"


@dataclass(frozen=True)
class PostedReturn:
    id: int
    number: str
    total: int
    replayed: bool


def _payload_hash(sale_id, day, lines, refund_method, reason) -> str:
    body = [sale_id, day, refund_method, reason,
            [[x.sale_line_id, x.qty, x.condition] for x in lines]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _refund_method(con, sale_id: int, asked) -> str:
    methods = [r[0] for r in con.execute(
        "SELECT method FROM sale_payments WHERE sale_id=? ORDER BY id", (sale_id,))]
    if asked:
        if asked not in methods and not (set(methods) == {"credit"} and asked in customer_receipts.METHODS):
            raise ReturnError("طريقة الرد يجب أن تكون من وسائل دفع الفاتورة الأصلية")
        return asked
    if len(set(methods)) != 1:
        raise ReturnError("الفاتورة دُفعت بوسيلتين؛ حدّد طريقة الرد")
    return methods[0]



def _require_complete_sale(con, sale_id: int, lines: list[ReturnLineIn]) -> None:
    """Only every original invoice line and quantity may cancel the installment contract."""
    expected = dict(con.execute(
        "SELECT id, qty FROM sale_lines WHERE sale_id=?", (sale_id,),
    ).fetchall())
    submitted = {line.sale_line_id: line.qty for line in lines}
    if len(submitted) != len(lines) or submitted != expected:
        raise ReturnError("مرتجع التقسيط الجزئي موقوف حتى اعتماد سياسة إعادة قياس EIR")
    if con.execute(
        "SELECT 1 FROM sale_returns WHERE sale_id=? LIMIT 1", (sale_id,),
    ).fetchone():
        raise ReturnError("لا يمكن إلغاء عقد سبق تسجيل مرتجع منه")


@dataclass(frozen=True)
class InstallmentSettlement:
    contract_id: int
    principal: int
    markup: int
    refunded: int
    receivable_reduced: int
    refunds: tuple
    allocations: tuple
    recognized_reversal: int

    @property
    def deferred_reversal(self) -> int:
        return self.markup - self.recognized_reversal


def _installment_settlement(con, sale_id: int, amount: int, day: str) -> InstallmentSettlement:
    """Full rescission: cancel the entire contract and refund only actual receipts."""
    contract = con.execute(
        "SELECT id, cash_price, down, down_method, markup, contract_date "
        "FROM installment_contracts WHERE sale_id=?", (sale_id,),
    ).fetchone()
    if contract is None:
        raise ReturnError("لا يمكن إلغاء فاتورة التقسيط قبل اعتماد عقدها")
    cid, cash_price, down, down_method, markup, contract_date = contract
    if amount != cash_price:
        raise ReturnError("مرتجع التقسيط الجزئي موقوف حتى اعتماد سياسة إعادة قياس EIR")
    if con.execute(
        "SELECT 1 FROM installment_return_adjustments WHERE contract_id=? LIMIT 1", (cid,),
    ).fetchone():
        raise ReturnError("سُجلت تسوية أخرى لهذا العقد؛ لا يجوز تكرار الإلغاء")
    last_payment = con.execute(
        "SELECT max(payment_date) FROM installment_payments WHERE contract_id=?", (cid,),
    ).fetchone()[0]
    last_interest = con.execute(
        "SELECT max(entry_date) FROM active_interest_recognitions WHERE contract_id=?", (cid,),
    ).fetchone()[0]
    if day < max(d for d in (contract_date, last_payment, last_interest) if d):
        raise ReturnError("تاريخ إلغاء التقسيط يسبق عقدًا أو تحصيلًا أو قيد فائدة مرحّلًا")

    recognized = con.execute(
        "SELECT coalesce(sum(amount),0) FROM active_interest_recognitions WHERE contract_id=?",
        (cid,),
    ).fetchone()[0]
    if not 0 <= recognized <= markup:
        raise ReturnError("إيراد فوائد EIR المعترف به لا يطابق إجمالي فوائد العقد")

    receipts = {"cash": 0, "card": 0, "transfer": 0, "wallet": 0}
    if down:
        receipts[down_method] += down
    for method, paid in con.execute(
        "SELECT method, sum(amount) FROM installment_payments "
        "WHERE contract_id=? GROUP BY method", (cid,),
    ):
        receipts[method] += paid
    received = sum(receipts.values())
    due = installments.remaining_rows(con, cid)
    receivable = sum(row[4] for row in due)
    if cash_price + markup != received + receivable:
        raise ReturnError("تسوية التقسيط موقوفة: التحصيل والمديونية لا يساويان قيمة العقد")
    return InstallmentSettlement(
        cid, cash_price, markup, received, receivable,
        tuple((method, value) for method, value in receipts.items() if value),
        tuple((row_id, outstanding) for row_id, _no, _due, _amount, outstanding in due),
        recognized,
    )


def preview_installment_refund(con, sale_id: int, lines: list,
                               return_date: str) -> InstallmentSettlement | None:
    """Read-only full-return preview; partial installment returns fail closed."""
    if not con.execute(
        "SELECT 1 FROM sale_payments WHERE sale_id=? AND method='installment'",
        (sale_id,),
    ).fetchone():
        return None
    try:
        day = ledger.valid_date(return_date, "تاريخ المرتجع")
    except ledger.LedgerError as exc:
        raise ReturnError(str(exc)) from exc
    if not lines:
        raise ReturnError("حدّد كل أصناف الفاتورة لإلغاء عقد التقسيط بالكامل")
    _require_complete_sale(con, sale_id, lines)
    total = 0
    for line in lines:
        _, _, _item, sold, net, _kind, already = _sale_line(con, sale_id, line.sale_line_id)
        try:
            rules.check_condition(line.condition)
            qty = rules.returnable(sold, already, line.qty)
        except rules.ReturnRuleError as exc:
            raise ReturnError(str(exc)) from exc
        total += rules.portion(net, already, qty, sold)
    return _installment_settlement(con, sale_id, total, day)


def _sale_line(con, sale_id: int, line_id: int) -> tuple:
    row = con.execute(
        "SELECT l.id, l.line_no, l.item_id, l.qty, l.net_total, i.kind FROM sale_lines l "
        "JOIN items i ON i.id = l.item_id WHERE l.id=? AND l.sale_id=?", (line_id, sale_id)).fetchone()
    if not row:
        raise ReturnError("سطر غير موجود في هذه الفاتورة")
    already = con.execute("SELECT coalesce(sum(qty), 0) FROM sale_return_lines WHERE sale_line_id=?",
                          (line_id,)).fetchone()[0]
    return (*row, already)


def _issue_parts(con, sale_id: int, line_id: int) -> tuple:
    issue = con.execute("SELECT id FROM stock_issues WHERE source_type='sale' AND source_id=? AND source_line_id=?",
                        (sale_id, line_id)).fetchone()
    if not issue:
        raise ReturnError("لا يوجد صرف مخزون لهذا السطر")
    layers = [(r[0], r[1], r[2]) for r in con.execute(
        "SELECT layer_id, qty, cost FROM cost_allocations WHERE issue_id=? ORDER BY id", (issue[0],))]
    lots = [(r[0], -r[1], 0) for r in con.execute(
        "SELECT lot_id, qty FROM stock_movements WHERE issue_id=? ORDER BY id", (issue[0],))]
    return layers, lots


def post_return(con: sqlite3.Connection, by, *, request_key: str, sale_id: int, lines: list, reason: str,
                refund_method: str | None = None, return_date: str | None = None,
                notes: str | None = None) -> PostedReturn:
    if by is None or not has_permission(con, by, "sales.return"):
        raise ReturnError("لا تملك صلاحية ترحيل مرتجعات البيع")
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ReturnError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    if not lines:
        raise ReturnError("المرتجع بلا أسطر")
    try:
        why = rules.check_reason(reason)
        for ln in lines:
            rules.check_condition(ln.condition)
        day = ledger.valid_date(return_date or date.today().isoformat(), "تاريخ المرتجع")
    except (rules.ReturnRuleError, ledger.LedgerError) as e:
        raise ReturnError(str(e)) from e

    with transaction(con):
        sale = con.execute("SELECT id, number, sale_date, journal_entry_id FROM sales WHERE id=?",
                           (sale_id,)).fetchone()
        if not sale:
            raise ReturnError("الفاتورة غير موجودة")
        if day < sale[2]:
            raise ReturnError("تاريخ المرتجع قبل تاريخ الفاتورة")
        method = _refund_method(con, sale_id, refund_method)
        if con.execute(
            "SELECT 1 FROM sale_payments WHERE sale_id=? AND method='installment'",
            (sale_id,),
        ).fetchone() and method != "installment":
            raise ReturnError("مرتجع التقسيط يجب تسويته على عقده لا برد نقدي منفرد")
        h = _payload_hash(sale_id, day, lines, method, why)
        old = con.execute("SELECT id, number, total, payload_hash FROM sale_returns WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ReturnError("مفتاح الطلب سبق استخدامه لمرتجع بمحتوى مختلف")
            return PostedReturn(old[0], old[1], old[2], True)
        # Historical migration fixtures still post on pre-VAT schemas.
        vat_schema = any(row[1] == "tax_total" for row in con.execute("PRAGMA table_info(sale_returns)"))
        posted_tax = con.execute(
            "SELECT coalesce(sum(credit-debit),0) FROM journal_lines WHERE entry_id=? AND account_code='2350'",
            (sale[3],)).fetchone()[0]
        tax_enabled = False
        if vat_schema:
            tax_enabled, original_tax = con.execute(
                "SELECT tax_enabled, tax_total FROM sales WHERE id=?", (sale_id,)).fetchone()
            line_tax = con.execute("SELECT coalesce(sum(tax_amount),0) FROM sale_lines WHERE sale_id=?",
                                   (sale_id,)).fetchone()[0]
            if original_tax != posted_tax or original_tax != line_tax:
                raise ReturnError("ضريبة الفاتورة الأصلية لا تطابق قيدها وسطورها")
        elif posted_tax:
            raise ReturnError("مرتجع الفاتورة الضريبية غير متاح حتى اكتمال معالجة الضريبة")
        if len({ln.sale_line_id for ln in lines}) != len(lines):
            raise ReturnError("سطر الفاتورة مكرر في المرتجع؛ اجمعه في سطر واحد")

        shaped = []
        for i, ln in enumerate(lines, start=1):
            _, _, item_id, sold, net_total, kind, already = _sale_line(con, sale_id, ln.sale_line_id)
            try:
                qty = rules.returnable(sold, already, ln.qty)
            except rules.ReturnRuleError as e:
                raise ReturnError(f"السطر {i}: {e}") from e
            value = rules.portion(net_total, already, qty, sold)
            tax = 0
            if vat_schema and tax_enabled:
                original = con.execute("SELECT tax_amount FROM sale_lines WHERE id=?",
                                       (ln.sale_line_id,)).fetchone()[0]
                previous = con.execute(
                    "SELECT coalesce(sum(value),0), coalesce(sum(tax_amount),0) "
                    "FROM sale_return_lines WHERE sale_line_id=?", (ln.sale_line_id,)).fetchone()
                if previous != (rules.portion(net_total, 0, already, sold),
                                rules.tax_portion(net_total, original, 0, already, sold)):
                    raise ReturnError(f"السطر {i}: مبالغ المرتجعات السابقة لا تطابق الفاتورة الأصلية")
                tax = rules.tax_portion(net_total, original, already, qty, sold)
            layer_take, lot_take = [], []
            if kind in COGS_ACCOUNTS:
                layers, lots = _issue_parts(con, sale_id, ln.sale_line_id)
                try:
                    layer_take = costing.take_units(layers, already, qty)
                    lot_take = costing.take_units(lots, already, qty)
                except costing.InsufficientStock as e:
                    raise ReturnError(f"السطر {i}: {e}") from e
            cost = sum(c for _, _, c in layer_take)
            shaped.append((ln, item_id, kind, qty, value, cost, layer_take, lot_take, tax))

        if method == "installment":
            _require_complete_sale(con, sale_id, lines)
        total = sum(x[4] for x in shaped)
        cost_total = sum(x[5] for x in shaped)
        tax_total = sum(x[8] for x in shaped)
        if total <= 0 and cost_total <= 0:
            raise ReturnError("المرتجع بلا قيمة ولا تكلفة")
        owner = con.execute("SELECT customer_id FROM sales WHERE id=?", (sale_id,)).fetchone()[0]
        kinds = {r[0] for r in con.execute("SELECT method FROM sale_payments WHERE sale_id=?", (sale_id,))}
        credit_part = 0
        if method == "credit":
            credit_part = total
        elif "credit" in kinds and "installment" not in kinds:
            # Money goes back only up to what was paid in money or collected; the rest settles on the account
            room = customer_receipts.sale_money_room(con, sale_id) + max(
                0, customer_receipts.collected_unrefunded(con, owner, day))
            if min(total, room) <= 0:
                raise ReturnError("لا يوجد مبلغ مدفوع أو محصَّل يُرَدّ نقدًا؛ اختر الرد على حساب العميل")
            credit_part = total - min(total, room)
        if credit_part and credit_part > customer_receipts.customer_balance(con, owner):
            raise ReturnError("الرد على الحساب أكبر من دين العميل الآجل المتبقي؛ لا يُترك رصيد دائن للعميل")
        return_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM sale_returns").fetchone()[0]
        number = f"SR-{return_id:06d}"

        settlement = _installment_settlement(con, sale_id, total, day) if method == "installment" else None
        if settlement and tax_enabled and settlement.markup:
            raise ReturnError("مرتجع التقسيط الضريبي بفوائد موقوف حتى اعتماد معاملة ضريبة الفوائد")
        entry = []
        if total - tax_total:
            entry.append((rules.RETURNS_ACCOUNT, total - tax_total, 0))
        if tax_total:
            entry.append(("2350", tax_total, 0))
        if settlement:
            if settlement.deferred_reversal:
                entry.append((installments.DEFERRED_INTEREST_ACCOUNT, settlement.deferred_reversal, 0))
            if settlement.recognized_reversal:
                entry.append(("4140", settlement.recognized_reversal, 0))
            if settlement.receivable_reduced:
                entry.append((installments.RECEIVABLE_ACCOUNT, 0, settlement.receivable_reduced))
            entry.extend((PAYMENT_ACCOUNT[m], 0, amount) for m, amount in settlement.refunds)
        elif total:
            if total - credit_part:
                entry.append((PAYMENT_ACCOUNT[method], 0, total - credit_part))
            if credit_part:
                entry.append((customer_statement.CUSTOMER_ACCOUNT, 0, credit_part))
        stock_side: dict[str, int] = {}
        cogs_side: dict[str, int] = {}
        for ln, _item, kind, _qty, _value, cost, _layers, _lots, _tax in shaped:
            if not cost:
                continue
            acc = rules.stock_account(ln.condition)
            stock_side[acc] = stock_side.get(acc, 0) + cost
            cogs_side[COGS_ACCOUNTS[kind]] = cogs_side.get(COGS_ACCOUNTS[kind], 0) + cost
        entry += [(a, v, 0) for a, v in sorted(stock_side.items())]
        entry += [(a, 0, v) for a, v in sorted(cogs_side.items())]
        try:
            entry_id = ledger.post_entry(con, by, day, "sale_return", return_id,
                                         f"مرتجع بيع {number} على {sale[1]}", entry)
        except ledger.LedgerError as e:
            raise ReturnError(str(e)) from e

        columns = ["id", "number", "request_key", "payload_hash", "sale_id", "return_date", "total", "cost_total",
                   "reason", "refund_method", "notes", "journal_entry_id", "user_id"]
        values = [return_id, number, key, h, sale_id, day, total, cost_total, why, method,
                  (notes or "").strip() or None, entry_id, by.id]
        if credit_part:                                  # the column defaults to 0; schemas before 0025 lack it
            columns.append("credit_part")
            values.append(credit_part)
        if vat_schema:
            columns.append("tax_total")
            values.append(tax_total)
        con.execute(f"INSERT INTO sale_returns ({', '.join(columns)}) VALUES ({', '.join('?' * len(values))})", values)
        if settlement:
            con.execute(
                "INSERT INTO installment_return_adjustments "
                "(return_id,contract_id,principal,markup,refunded,receivable_reduced) "
                "VALUES (?,?,?,?,?,?)",
                (return_id, settlement.contract_id, total, settlement.markup, settlement.refunded,
                 settlement.receivable_reduced))
            for method_name, amount in settlement.refunds:
                con.execute(
                    "INSERT INTO installment_return_refunds (return_id,method,amount) VALUES (?,?,?)",
                    (return_id, method_name, amount))
            for row_id, amount in settlement.allocations:
                con.execute(
                    "INSERT INTO installment_return_allocations (return_id,row_id,amount) VALUES (?,?,?)",
                    (return_id, row_id, amount))
        for no, (ln, item_id, _kind, qty, value, cost, layer_take, lot_take, tax) in enumerate(shaped, start=1):
            line_columns = "return_id, line_no, sale_line_id, qty, condition, value, cost"
            line_values = (return_id, no, ln.sale_line_id, qty, ln.condition, value, cost)
            if vat_schema:
                line_columns += ", tax_amount"
                line_values += (tax,)
            cur = con.execute(
                f"INSERT INTO sale_return_lines ({line_columns}) VALUES ({','.join('?' for _ in line_values)})",
                line_values)
            if ln.condition != "sound":
                continue                                  # defective returns neither to sellable stock nor to its layer
            for lot_id, q, _ in lot_take:
                con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand + ? WHERE id=?", (q, lot_id))
                con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, return_line_id, user_id) "
                            "VALUES (?,?,'return',?,?,?)", (item_id, lot_id, q, cur.lastrowid, by.id))
            for layer_id, q, c in layer_take:
                con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining + ?, "
                            "cost_remaining = cost_remaining + ? WHERE id=?", (q, c, layer_id))
                con.execute("INSERT INTO cost_allocations (return_line_id, layer_id, qty, cost) VALUES (?,?,?,?)",
                            (cur.lastrowid, layer_id, -q, -c))
        audit(con, by, "sale.return", "sale_return", return_id, f"{number} · {total} · {why}")
    return PostedReturn(return_id, number, total, False)

"""Post immutable ACT/365F EIR accruals only for unmodified on-schedule contracts."""
import hashlib
import json
import re
import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from morv.core import eir
from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

DEFERRED_ACCOUNT = "2410"
INCOME_ACCOUNT = "4140"
POLICY = "ACT365F_V1"


class RecognitionError(ValueError):
    """EIR posting refused for invalid, inconsistent, or modified contracts."""


@dataclass(frozen=True)
class PostedRecognition:
    id: int
    contract_id: int
    period: str
    amount: int
    replayed: bool


def _closed_month_end(period: str) -> date:
    if not isinstance(period, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", period):
        raise RecognitionError("الشهر المطلوب بصيغة YYYY-MM")
    try:
        first = date.fromisoformat(period + "-01")
    except ValueError as exc:
        raise RecognitionError("الشهر المطلوب غير صالح") from exc
    last = date(first.year, first.month, monthrange(first.year, first.month)[1])
    if last >= date.today():
        raise RecognitionError("لا يمكن ترحيل فوائد شهر لم ينتهِ بعد")
    return last


def _schedule_hash(contract, rows, schedule) -> str:
    payload = [POLICY, contract, rows,
               [(month.period, month.interest) for month in schedule.months],
               str(schedule.annual_rate)]
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _verify_receipts(con: sqlite3.Connection, contract_id: int, closed_on: str) -> None:
    """Prevent posting a theoretical EIR when collections deviate from contractual dates."""
    rows = con.execute(
        "SELECT r.id, r.due_date, r.amount, "
        "coalesce(sum(CASE WHEN p.payment_date <= ? THEN l.amount ELSE 0 END), 0) "
        "FROM installment_rows r "
        "LEFT JOIN installment_payment_lines l ON l.row_id=r.id "
        "LEFT JOIN installment_payments p ON p.id=l.payment_id "
        "WHERE r.contract_id=? GROUP BY r.id ORDER BY r.no",
        (closed_on, contract_id),
    ).fetchall()
    for _row_id, due_date, amount, paid in rows:
        expected_paid = amount if due_date <= closed_on else 0
        if paid != expected_paid:
            raise RecognitionError("توقفت الفوائد: التحصيلات لا تطابق جدول العقد في نهاية الشهر")
    mismatch = con.execute(
        "SELECT 1 FROM installment_payment_lines l "
        "JOIN installment_payments p ON p.id=l.payment_id "
        "JOIN installment_rows r ON r.id=l.row_id "
        "WHERE r.contract_id=? AND p.payment_date <= ? AND p.payment_date != r.due_date LIMIT 1",
        (contract_id, closed_on),
    ).fetchone()
    if mismatch:
        raise RecognitionError("توقفت الفوائد: تاريخ تحصيل يختلف عن تاريخ استحقاق القسط")


def recognise_month(con: sqlite3.Connection, by, *, contract_id: int,
                    period: str, entry_date: str | None = None) -> PostedRecognition | None:
    """Post one closed month for one contract; zero-interest months create no journal."""
    if by is None or not has_permission(con, by, "installments.manage"):
        raise RecognitionError("لا تملك صلاحية الاعتراف بفوائد التقسيط")
    if type(contract_id) is not int or contract_id <= 0:
        raise RecognitionError("رقم عقد التقسيط غير صالح")
    closed_on = _closed_month_end(period)
    try:
        day = ledger.valid_date(entry_date or date.today().isoformat(), "تاريخ القيد")
    except ledger.LedgerError as exc:
        raise RecognitionError(str(exc)) from exc
    if day < closed_on.isoformat():
        raise RecognitionError("تاريخ القيد لا يمكن أن يسبق نهاية شهر الفوائد")

    with transaction(con):
        contract = con.execute(
            "SELECT id, sale_id, contract_date, financed, markup, months, number "
            "FROM installment_contracts WHERE id=?", (contract_id,),
        ).fetchone()
        if contract is None:
            raise RecognitionError("عقد التقسيط غير موجود")
        _id, sale_id, start, financed, markup, count, number = contract
        if con.execute("SELECT 1 FROM sale_returns WHERE sale_id=? LIMIT 1", (sale_id,)).fetchone():
            raise RecognitionError("الاعتراف موقوف لعقد له مرتجع سابق يحتاج تسوية مستقلة")
        if con.execute(
            "SELECT 1 FROM installment_return_adjustments WHERE contract_id=? LIMIT 1", (contract_id,),
        ).fetchone():
            raise RecognitionError("الاعتراف موقوف لعقد عُدلت مديونيته بسبب مرتجع")
        rows = con.execute(
            "SELECT due_date, amount FROM installment_rows WHERE contract_id=? ORDER BY no",
            (contract_id,),
        ).fetchall()
        if len(rows) != count or sum(row[1] for row in rows) != financed + markup:
            raise RecognitionError("جدول الأقساط لا يطابق أصل العقد وفوائده")
        try:
            schedule = eir.calculate(financed, start, rows)
        except eir.EirError as exc:
            raise RecognitionError(str(exc)) from exc
        if schedule.markup != markup:
            raise RecognitionError("إجمالي فوائد EIR لا يطابق فوائد العقد")
        monthly = {m.period: m.interest for m in schedule.months}
        if period not in monthly:
            raise RecognitionError("الشهر خارج مدة الفوائد التعاقدية")
        if any(amount < 0 for amount in monthly.values()):
            raise RecognitionError("توزيع الفوائد يتضمن مبلغًا سالبًا غير مدعوم")
        fingerprint = _schedule_hash(contract, rows, schedule)
        historic = con.execute(
            "SELECT id, period, amount, schedule_hash, policy FROM interest_recognitions "
            "WHERE contract_id=? ORDER BY period", (contract_id,),
        ).fetchall()
        posted = {}
        for rec_id, older_period, older_amount, older_hash, older_policy in historic:
            if older_period not in monthly or older_amount != monthly[older_period] \
                    or older_hash != fingerprint or older_policy != POLICY:
                raise RecognitionError("توقف الاعتراف: جدول الفوائد تغيّر بعد الترحيل")
            posted[older_period] = (rec_id, older_amount)
        amount = monthly[period]
        if period in posted:
            rec_id, prior_amount = posted[period]
            return PostedRecognition(rec_id, contract_id, period, prior_amount, True)
        if amount == 0:
            return None
        if any(older_period < period and prior > 0 and older_period not in posted
               for older_period, prior in monthly.items()):
            raise RecognitionError("يجب ترحيل أشهر الفوائد السابقة لهذا العقد أولًا")
        if any(older_period > period for older_period in posted):
            raise RecognitionError("لا يجوز إدراج قيد شهر أقدم بعد قيد لاحق")
        _verify_receipts(con, contract_id, closed_on.isoformat())
        if sum(item[1] for item in posted.values()) + amount > markup:
            raise RecognitionError("المبلغ المعترف به يتجاوز فوائد العقد")
        rec_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM interest_recognitions").fetchone()[0]
        try:
            journal_id = ledger.post_entry(
                con, by, day, "interest_recognition", rec_id,
                f"اعتراف فوائد {period} للعقد {number}",
                [(DEFERRED_ACCOUNT, amount, 0), (INCOME_ACCOUNT, 0, amount)],
            )
        except ledger.LedgerError as exc:
            raise RecognitionError(str(exc)) from exc
        con.execute(
            "INSERT INTO interest_recognitions "
            "(id, contract_id, period, entry_date, amount, annual_rate, schedule_hash, policy, "
            "journal_entry_id, user_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rec_id, contract_id, period, day, amount, str(schedule.annual_rate),
             fingerprint, POLICY, journal_id, by.id),
        )
        audit(con, by, "installment.interest_recognise", "interest_recognition", rec_id,
              f"{number} · {period} · {amount}")
    return PostedRecognition(rec_id, contract_id, period, amount, False)

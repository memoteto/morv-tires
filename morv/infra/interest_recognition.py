"""Monthly interest recognition: moves a month's instalment markup from deferred interest to income."""
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.installments import DEFERRED_INTEREST_ACCOUNT
from morv.infra.users import audit, has_permission

INCOME_ACCOUNT = "4140"


class RecognitionError(ValueError):
    """Recognition refused, with a message fit to show."""


@dataclass(frozen=True)
class PostedRecognition:
    id: int
    number: str
    period: str
    amount: int
    rows: int
    replayed: bool


def _check_period(period: str) -> str:
    text = (period or "").strip()
    try:
        date.fromisoformat(text + "-01")
    except ValueError as e:
        raise RecognitionError("الشهر بصيغة YYYY-MM") from e
    if text > date.today().isoformat()[:7]:
        raise RecognitionError("لا يُعترف بفوائد شهر لم يأتِ بعد")
    return text


def pending(con: sqlite3.Connection, period: str) -> list[tuple]:
    """Instalments due that month whose markup was never recognised; the schedule split it equally."""
    return con.execute(
        "SELECT r.id, r.markup, c.number, r.no, r.due_date FROM installment_rows r "
        "JOIN installment_contracts c ON c.id = r.contract_id "
        "WHERE substr(r.due_date, 1, 7) = ? AND r.markup > 0 "
        "AND NOT EXISTS (SELECT 1 FROM interest_recognition_rows x WHERE x.row_id = r.id) "
        "ORDER BY r.due_date, r.id", (period,)).fetchall()


def period_total(con: sqlite3.Connection, period: str) -> int:
    return sum(r[1] for r in pending(con, period))


def recognise(con: sqlite3.Connection, by, *, period: str, entry_date: str | None = None) -> PostedRecognition:
    """Posts one entry for the whole month, or writes nothing at all."""
    if by is None or not has_permission(con, by, "installments.manage"):
        raise RecognitionError("لا تملك صلاحية ترحيل قيد الفوائد")
    month = _check_period(period)
    last_day = _month_end(month)
    try:
        day = ledger.valid_date(entry_date or min(last_day, date.today().isoformat()), "تاريخ القيد")
    except ledger.LedgerError as e:
        raise RecognitionError(str(e)) from e
    if day[:7] < month:
        raise RecognitionError("تاريخ القيد قبل الشهر المُعترف به")

    with transaction(con):
        old = con.execute("SELECT id, number, amount, "
                          "(SELECT count(*) FROM interest_recognition_rows x WHERE x.recognition_id = i.id) "
                          "FROM interest_recognitions i WHERE period=?", (month,)).fetchone()
        if old:
            return PostedRecognition(old[0], old[1], month, old[2], old[3], True)
        rows = pending(con, month)
        if not rows:
            raise RecognitionError("لا توجد فوائد غير معترف بها في هذا الشهر")
        amount = sum(r[1] for r in rows)
        recognition_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM interest_recognitions").fetchone()[0]
        number = f"IR-{month}"
        try:
            entry_id = ledger.post_entry(con, by, day, "interest_recognition", recognition_id,
                                         f"اعتراف فوائد تقسيط {number}",
                                         [(DEFERRED_INTEREST_ACCOUNT, amount, 0), (INCOME_ACCOUNT, 0, amount)])
        except ledger.LedgerError as e:
            raise RecognitionError(str(e)) from e
        con.execute("INSERT INTO interest_recognitions (id, number, period, entry_date, amount, journal_entry_id, "
                    "user_id) VALUES (?,?,?,?,?,?,?)",
                    (recognition_id, number, month, day, amount, entry_id, by.id))
        for row_id, share, *_rest in rows:
            con.execute("INSERT INTO interest_recognition_rows (recognition_id, row_id, amount) VALUES (?,?,?)",
                        (recognition_id, row_id, share))
        audit(con, by, "installment.recognise", "interest_recognition", recognition_id, f"{number} · {amount}")
    return PostedRecognition(recognition_id, number, month, amount, len(rows), False)


def _month_end(month: str) -> str:
    year, m = (int(x) for x in month.split("-"))
    nxt = date(year + 1, 1, 1) if m == 12 else date(year, m + 1, 1)
    return str(date.fromordinal(nxt.toordinal() - 1))

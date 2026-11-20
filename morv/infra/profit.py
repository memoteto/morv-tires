"""Partner profit shares and distribution of the profit of locked periods; equity moves only, no cash."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from morv.core import profit_rules
from morv.infra import income_statement, ledger, period_lock, settings
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

EARNINGS = "3200"
RESERVE_ACCOUNT = "3250"
RESERVE_KEY = "profit.reserve_bp"
BEGINNING = "0001-01-01"


class ProfitError(ValueError):
    pass


@dataclass(frozen=True)
class Preview:
    from_date: str
    through_date: str
    period_net: int
    cumulative_net: int
    distributed_before: int        # earlier distributions plus their reserve, not reversed
    reserve_bp: int
    reserve_amount: int
    max_amount: int                # what may still go to partners after the reserve
    share_set_id: int
    shares: tuple[tuple[int, str, int], ...]     # (partner id, name, share bp)


@dataclass(frozen=True)
class Posted:
    id: int
    number: str
    amount: int
    replayed: bool


def _require(con, by) -> None:
    if by is None or not has_permission(con, by, "profit.manage"):
        raise ProfitError("لا تملك صلاحية إدارة حصص الأرباح وتوزيعها")


def _key(request_key) -> str:
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ProfitError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    return key


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------- Settings and shares ----------

def reserve_bp(con: sqlite3.Connection) -> int:
    """Legal reserve as basis points of a positive period result; per-shop setting, 0 by default."""
    try:
        return int(settings.get(con, RESERVE_KEY, "0"))
    except ValueError:
        return 0


def set_reserve_bp(con: sqlite3.Connection, by, bp: int) -> None:
    _require(con, by)
    if type(bp) is not int or not 0 <= bp <= profit_rules.TOTAL_BP:
        raise ProfitError("نسبة الاحتياطي بين 0 و100%")
    with transaction(con):
        settings.put(con, RESERVE_KEY, str(bp))
        audit(con, by, "profit.reserve", "settings", None, f"{bp}")


def shares_effective(con: sqlite3.Connection, day: str) -> tuple[int, str, tuple[tuple[int, str, int], ...]] | None:
    """(set id, effective from, shares) of the latest set effective on or before day."""
    row = con.execute("SELECT id, effective_from FROM profit_share_sets WHERE effective_from <= ? "
                      "ORDER BY effective_from DESC, id DESC LIMIT 1", (day,)).fetchone()
    if row is None:
        return None
    lines = con.execute("SELECT p.id, p.name, l.share_bp FROM profit_share_lines l JOIN partners p ON p.id = l.partner_id "
                        "WHERE l.set_id = ? ORDER BY p.id", (row[0],)).fetchall()
    return row[0], row[1], tuple(lines)


def latest_distribution(con: sqlite3.Connection) -> tuple | None:
    """(id, number, through date, amount, reserve) of the newest distribution not reversed."""
    return con.execute(
        "SELECT id, number, through_date, amount, reserve_amount FROM profit_distributions d "
        "WHERE kind = 'distribution' AND NOT EXISTS (SELECT 1 FROM profit_distributions r WHERE r.reverses_id = d.id) "
        "ORDER BY through_date DESC, id DESC LIMIT 1").fetchone()


def set_shares(con: sqlite3.Connection, by, *, request_key: str, effective_from: str,
               shares: list[tuple[int, int]], memo: str) -> Posted:
    """Approves a new set of shares (partner id, bp); it cannot reach back into a distributed period."""
    _require(con, by)
    key = _key(request_key)
    memo = (memo or "").strip()
    if not memo:
        raise ProfitError("مرجع الاعتماد أو البيان مطلوب")
    try:
        day = ledger.valid_date(effective_from, "تاريخ سريان الحصص")
    except ledger.LedgerError as e:
        raise ProfitError(str(e)) from e
    shares = sorted((int(p), b) for p, b in shares)
    if not shares or len({p for p, _ in shares}) != len(shares):
        raise ProfitError("حصة واحدة لكل شريك")
    if any(type(b) is not int or b <= 0 for _, b in shares) or sum(b for _, b in shares) != profit_rules.TOTAL_BP:
        raise ProfitError("مجموع الحصص يجب أن يساوي 100% وكل حصة موجبة")
    with transaction(con):
        h = _digest("shares", day, shares, memo)
        old = con.execute("SELECT id, number, payload_hash FROM profit_share_sets WHERE request_key=?", (key,)).fetchone()
        if old:
            if old[2] != h:
                raise ProfitError("مفتاح الطلب سبق استخدامه لحصص مختلفة")
            return Posted(old[0], old[1], 0, True)
        for pid, _ in shares:
            row = con.execute("SELECT active FROM partners WHERE id=?", (pid,)).fetchone()
            if row is None or not row[0]:
                raise ProfitError("الشريك غير موجود أو معطّل")
        last = latest_distribution(con)
        if last and day <= last[2]:
            raise ProfitError(f"لا تسري الحصص على فترة وُزّعت أرباحها (آخر توزيع حتى {last[2]})")
        newest = con.execute("SELECT max(effective_from) FROM profit_share_sets").fetchone()[0]
        if newest and day <= newest:
            raise ProfitError(f"يجب أن يلي تاريخ السريان آخر مجموعة حصص ({newest})")
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM profit_share_sets").fetchone()[0]
        number = f"PS-{day[:4]}-{new_id:06d}"
        con.execute("INSERT INTO profit_share_sets (id, number, request_key, payload_hash, effective_from, memo, user_id) "
                    "VALUES (?,?,?,?,?,?,?)", (new_id, number, key, h, day, memo, by.id))
        con.executemany("INSERT INTO profit_share_lines (set_id, partner_id, share_bp) VALUES (?,?,?)",
                        [(new_id, p, b) for p, b in shares])
        audit(con, by, "profit.shares", "profit_share_set", new_id, f"{number} · من {day}")
    return Posted(new_id, number, 0, False)


# ---------- Distribution ----------

def preview(con: sqlite3.Connection, through_date: str) -> Preview:
    """What a distribution through that date would be; raises ProfitError when it cannot be made."""
    try:
        through = ledger.valid_date(through_date, "تاريخ نهاية الفترة")
    except ledger.LedgerError as e:
        raise ProfitError(str(e)) from e
    locked = ledger.locked_through(con)
    if not locked or through > locked:
        raise ProfitError("لا يُوزَّع ربح فترة غير مقفلة؛ اقفل الفترة أولًا")
    # a lock made before a rule existed must not let an unreviewed period be distributed
    problems = period_lock.blockers(con, through)
    if problems:
        raise ProfitError("سلامة الفترة غير مكتملة: " + "؛ ".join(problems))
    last = latest_distribution(con)
    if last:
        from_date = (date.fromisoformat(last[2]) + timedelta(days=1)).isoformat()
    else:
        first = con.execute("SELECT min(entry_date) FROM journal_entries WHERE status = 'posted'").fetchone()[0]
        from_date = first or through
    if through < from_date:
        raise ProfitError(f"الفترة تبدأ من {from_date}؛ اختر نهاية فترة لا تسبقه")
    effective = shares_effective(con, from_date)
    if effective is None:
        raise ProfitError("لا توجد حصص شركاء معتمدة تسري على هذه الفترة")
    stopped = [name for _, name, _ in effective[2] if not con.execute(
        "SELECT active FROM partners WHERE name = ?", (name,)).fetchone()[0]]
    if stopped:
        raise ProfitError("في الحصص شريك موقوف (" + "، ".join(stopped) + ")؛ اعتمد حصصًا جديدة")
    period_net = income_statement.build(con, from_date, through).net_result
    cumulative = income_statement.build(con, BEGINNING, through).net_result
    before = con.execute("SELECT coalesce(sum(amount + reserve_amount), 0) FROM profit_distributions d "
                         "WHERE kind = 'distribution' AND NOT EXISTS "
                         "(SELECT 1 FROM profit_distributions r WHERE r.reverses_id = d.id)").fetchone()[0]
    bp = reserve_bp(con)
    available = max(cumulative - before, 0)
    reserve = min(profit_rules.reserve_for(period_net, bp), available)
    return Preview(from_date, through, period_net, cumulative, before, bp, reserve,
                   max(available - reserve, 0), effective[0], effective[2])


def distribute(con: sqlite3.Connection, by, *, request_key: str, through_date: str, amount: int,
               memo: str) -> Posted:
    """Credits each partner's current account by share; earlier losses and distributions cap the amount."""
    _require(con, by)
    key = _key(request_key)
    memo = (memo or "").strip()
    if not memo:
        raise ProfitError("البيان مطلوب")
    if type(amount) is not int or amount <= 0:
        raise ProfitError("المبلغ عدد صحيح موجب بالقرش")
    with transaction(con):
        h = _digest("distribution", through_date, amount, memo)
        old = con.execute("SELECT id, number, amount, payload_hash FROM profit_distributions WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ProfitError("مفتاح الطلب سبق استخدامه لتوزيع مختلف")
            return Posted(old[0], old[1], old[2], True)
        p = preview(con, through_date)
        if amount > p.max_amount:
            raise ProfitError("المبلغ يتجاوز الربح القابل للتوزيع بعد الخسائر السابقة والتوزيعات والاحتياطي")
        accounts = dict(con.execute("SELECT id, current_account FROM partners").fetchall())
        parts = profit_rules.split(amount, [(pid, bp) for pid, _, bp in p.shares])
        today = date.today().isoformat()
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM profit_distributions").fetchone()[0]
        number = f"PD-{today[:4]}-{new_id:06d}"
        lines = [(EARNINGS, amount + p.reserve_amount, 0)]
        if p.reserve_amount:
            lines.append((RESERVE_ACCOUNT, 0, p.reserve_amount))
        lines += [(accounts[pid], 0, part) for pid, part in parts if part]
        try:
            entry_id = ledger.post_entry(con, by, today, "profit_distribution", new_id,
                                         f"توزيع أرباح {number} عن {p.from_date} إلى {p.through_date}: {memo}", lines)
        except ledger.LedgerError as e:
            raise ProfitError(str(e)) from e
        con.execute(
            "INSERT INTO profit_distributions (id, number, request_key, payload_hash, kind, from_date, through_date, "
            "period_net, reserve_bp, reserve_amount, amount, share_set_id, memo, journal_entry_id, user_id) "
            "VALUES (?,?,?,?,'distribution',?,?,?,?,?,?,?,?,?,?)",
            (new_id, number, key, h, p.from_date, p.through_date, p.period_net, p.reserve_bp, p.reserve_amount,
             amount, p.share_set_id, memo, entry_id, by.id))
        bps = {pid: bp for pid, _, bp in p.shares}
        con.executemany("INSERT INTO profit_distribution_lines (distribution_id, partner_id, share_bp, amount) "
                        "VALUES (?,?,?,?)", [(new_id, pid, bps[pid], part) for pid, part in parts])
        audit(con, by, "profit.distribute", "profit_distribution", new_id, f"{number} · {amount}")
    return Posted(new_id, number, amount, False)


def reverse_distribution(con: sqlite3.Connection, by, *, request_key: str, distribution_id: int,
                         memo: str) -> Posted:
    """Reverses the newest live distribution with a mirrored entry dated today."""
    _require(con, by)
    key = _key(request_key)
    memo = (memo or "").strip()
    if not memo:
        raise ProfitError("سبب العكس مطلوب")
    with transaction(con):
        h = _digest("reversal", distribution_id, memo)
        old = con.execute("SELECT id, number, amount, payload_hash FROM profit_distributions WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ProfitError("مفتاح الطلب سبق استخدامه لعكس مختلف")
            return Posted(old[0], old[1], old[2], True)
        latest = latest_distribution(con)
        if latest is None or latest[0] != distribution_id:
            raise ProfitError("يُعكس آخر توزيع ساري فقط")
        src = con.execute("SELECT number, from_date, through_date, period_net, reserve_bp, reserve_amount, amount "
                          "FROM profit_distributions WHERE id=?", (distribution_id,)).fetchone()
        parts = con.execute("SELECT p.current_account, l.amount FROM profit_distribution_lines l "
                            "JOIN partners p ON p.id = l.partner_id WHERE l.distribution_id = ? AND l.amount > 0 "
                            "ORDER BY p.id", (distribution_id,)).fetchall()
        today = date.today().isoformat()
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM profit_distributions").fetchone()[0]
        number = f"PD-{today[:4]}-{new_id:06d}"
        lines = [(account, part, 0) for account, part in parts]
        if src[5]:
            lines.append((RESERVE_ACCOUNT, src[5], 0))
        lines.append((EARNINGS, 0, src[6] + src[5]))
        entry_id = ledger.post_entry(con, by, today, "profit_distribution", new_id,
                                     f"عكس توزيع الأرباح {src[0]} {number}: {memo}", lines)
        con.execute(
            "INSERT INTO profit_distributions (id, number, request_key, payload_hash, kind, from_date, through_date, "
            "period_net, reserve_bp, reserve_amount, amount, reverses_id, memo, journal_entry_id, user_id) "
            "VALUES (?,?,?,?,'reversal',?,?,?,?,?,?,?,?,?,?)",
            (new_id, number, key, h, src[1], src[2], src[3], src[4], src[5], src[6], distribution_id, memo,
             entry_id, by.id))
        audit(con, by, "profit.reverse", "profit_distribution", new_id, f"{number} عكس {src[0]}")
    return Posted(new_id, number, src[6], False)

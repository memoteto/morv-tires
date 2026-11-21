"""Users, login, permissions and the audit trail (database layer)."""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from morv.core import security
from morv.infra.db.connection import transaction

_TS = "%Y-%m-%d %H:%M:%S"


class AuthError(ValueError):
    """Login or user action refused, with a message fit to show."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    active: bool
    must_change_pw: bool

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"


def _now() -> datetime:
    return datetime.now()


def _row_user(r) -> User:
    return User(r["id"], r["username"], r["display_name"], r["role"], bool(r["active"]), bool(r["must_change_pw"]))


def audit(con: sqlite3.Connection, user: User | None, action: str, entity: str | None = None,
          entity_id=None, detail: str | None = None) -> None:
    con.execute("INSERT INTO audit_log (user_id, username, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
                (user.id if user else None, user.username if user else None, action, entity,
                 str(entity_id) if entity_id is not None else None, detail))


def count_users(con) -> int:
    return con.execute("SELECT count(*) FROM users").fetchone()[0]


def list_users(con) -> list[User]:
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM users ORDER BY id").fetchall()
    con.row_factory = None
    return [_row_user(r) for r in rows]


def get_user(con, user_id: int) -> User:
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    con.row_factory = None
    if not r:
        raise AuthError("المستخدم غير موجود")
    return _row_user(r)


def _check_username(username: str) -> str:
    u = (username or "").strip()
    if len(u) < 3 or not all(c.isascii() and (c.isalnum() or c in "_.") for c in u):
        raise AuthError("اسم الدخول أحرف إنجليزية أو أرقام، 3 على الأقل")
    return u


def check_new_user(username: str, display_name: str, password: str, role: str) -> tuple[str, str]:
    u = _check_username(username)
    if role not in security.ROLES:
        raise AuthError("دور غير معروف")
    if not (display_name or "").strip():
        raise AuthError("الاسم مطلوب")
    try:
        return u, security.hash_password(password)
    except ValueError as e:
        raise AuthError(str(e)) from e


def create_user(con, by: User | None, username: str, display_name: str, password: str, role: str,
                must_change_pw: bool = False) -> User:
    u, pw_hash = check_new_user(username, display_name, password, role)
    if not (by is None and con.execute("SELECT 1 FROM users").fetchone() is None):   # only the very first manager is unauthenticated
        require(con, by, "users.manage")
    with transaction(con):
        uid = insert_user(con, by, u, display_name.strip(), role, pw_hash, must_change_pw)
    return get_user(con, uid)


def insert_user(con, by: User | None, username: str, display_name: str, role: str, pw_hash: str,
                must_change_pw: bool = False) -> int:
    try:
        cur = con.execute(
            "INSERT INTO users (username, display_name, role, pw_hash, must_change_pw) VALUES (?,?,?,?,?)",
            (username, display_name, role, pw_hash, int(must_change_pw)))
    except sqlite3.IntegrityError as e:
        raise AuthError("اسم الدخول مستخدم من قبل") from e
    uid = cur.lastrowid
    _reset_permissions(con, uid, role)
    audit(con, by, "user.create", "user", uid, f"{username} ({security.ROLES[role]})")
    return uid


def put_first_manager(con, username: str, display_name: str, pw_hash: str) -> int:
    row = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if not row:
        return insert_user(con, None, username, display_name, "manager", pw_hash)
    try:
        con.execute("UPDATE users SET username=?, display_name=?, role='manager', pw_hash=?, must_change_pw=0, "
                    "active=1, failed_count=0, locked_until=NULL WHERE id=?", (username, display_name, pw_hash, row[0]))
    except sqlite3.IntegrityError as e:
        raise AuthError("اسم الدخول مستخدم من قبل") from e
    _reset_permissions(con, row[0], "manager")
    audit(con, None, "user.setup_resume", "user", row[0], f"{username}: استكمال إعداد انقطع سابقًا")
    return row[0]


def _reset_permissions(con, user_id: int, role: str) -> None:
    con.execute("DELETE FROM user_permissions WHERE user_id=?", (user_id,))
    for perm in security.role_permissions(role):
        con.execute("INSERT INTO user_permissions (user_id, perm, allowed) VALUES (?,?,1)", (user_id, perm))


def _active_managers(con, excluding: int | None = None) -> int:
    return con.execute("SELECT count(*) FROM users WHERE role='manager' AND active=1 AND id IS NOT ?",
                       (excluding,)).fetchone()[0]


def set_active(con, by: User, user_id: int, active: bool) -> None:
    require(con, by, "users.manage", AuthError)
    target = get_user(con, user_id)
    if not active:
        if by.id == user_id:
            raise AuthError("لا يمكنك إيقاف حسابك الحالي")
        if target.is_manager and _active_managers(con, excluding=user_id) == 0:
            raise AuthError("لا بد من مدير واحد نشط على الأقل")
    with transaction(con):
        con.execute("UPDATE users SET active=? WHERE id=?", (int(active), user_id))
        audit(con, by, "user.activate" if active else "user.deactivate", "user", user_id, target.username)


def set_role(con, by: User, user_id: int, role: str) -> None:
    require(con, by, "users.manage", AuthError)
    if role not in security.ROLES:
        raise AuthError("دور غير معروف")
    target = get_user(con, user_id)
    if target.is_manager and role != "manager" and _active_managers(con, excluding=user_id) == 0:
        raise AuthError("لا بد من مدير واحد نشط على الأقل")
    with transaction(con):
        con.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
        _reset_permissions(con, user_id, role)
        audit(con, by, "user.role", "user", user_id, f"{target.username} → {security.ROLES[role]}")


def set_permission(con, by: User, user_id: int, perm: str, allowed: bool, limit_value: int | None = None) -> None:
    require(con, by, "users.manage", AuthError)
    if perm not in security.PERMISSIONS:
        raise AuthError("صلاحية غير معروفة")
    with transaction(con):
        con.execute("INSERT INTO user_permissions (user_id, perm, allowed, limit_value) VALUES (?,?,?,?) "
                    "ON CONFLICT(user_id, perm) DO UPDATE SET allowed=excluded.allowed, limit_value=excluded.limit_value",
                    (user_id, perm, int(allowed), limit_value))
        audit(con, by, "user.permission", "user", user_id, f"{perm}={'1' if allowed else '0'}")


def permissions(con, user: User) -> set:
    if user.is_manager:
        return set(security.PERMISSIONS)
    rows = con.execute("SELECT perm FROM user_permissions WHERE user_id=? AND allowed=1", (user.id,)).fetchall()
    return {r[0] for r in rows} & set(security.PERMISSIONS)


def has_permission(con, user: User, perm: str) -> bool:
    return perm in permissions(con, user)


def require(con, by, perm: str, error=AuthError) -> None:
    """Refuse the write unless the acting user holds the permission (the UI is not the only gate)."""
    if by is None or not has_permission(con, by, perm):
        raise error(f"لا تملك صلاحية: {security.PERMISSIONS[perm]}")


def permission_limit(con, user: User, perm: str) -> int | None:
    """Numeric cap for a permission; None means unlimited (managers), a missing cap means 0."""
    if user.is_manager:
        return None
    row = con.execute("SELECT limit_value FROM user_permissions WHERE user_id=? AND perm=? AND allowed=1",
                      (user.id, perm)).fetchone()
    return row[0] or 0 if row else 0


def set_password(con, by: User, user_id: int, new_password: str, must_change: bool = False) -> None:
    try:
        pw_hash = security.hash_password(new_password)
    except ValueError as e:
        raise AuthError(str(e)) from e
    if by.id != user_id:
        require(con, by, "users.manage")
    with transaction(con):
        con.execute("UPDATE users SET pw_hash=?, must_change_pw=?, failed_count=0, locked_until=NULL WHERE id=?",
                    (pw_hash, int(must_change), user_id))
        audit(con, by, "user.password", "user", user_id, "تعيين كلمة مرور" if by.id != user_id else "تغيير كلمة المرور")


def change_own_password(con, user: User, old_password: str, new_password: str) -> None:
    stored = con.execute("SELECT pw_hash FROM users WHERE id=?", (user.id,)).fetchone()[0]
    if not security.verify_password(old_password, stored):
        raise AuthError("كلمة المرور الحالية غير صحيحة")
    # Found in testing: the temporary password was accepted again, so the manager still knew it
    if new_password == old_password:
        raise AuthError("كلمة المرور الجديدة يجب أن تختلف عن الحالية")
    set_password(con, user, user.id, new_password, must_change=False)


def login(con, username: str, password: str) -> User:
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM users WHERE username=?", ((username or "").strip(),)).fetchone()
    con.row_factory = None
    if not r or not r["active"]:
        audit(con, None, "login.failed", "user", None, f"{username}: مستخدم غير موجود أو موقوف")
        raise AuthError("اسم المستخدم أو كلمة المرور غير صحيحة")
    now = _now()
    if r["locked_until"] and datetime.strptime(r["locked_until"], _TS) > now:
        left = int((datetime.strptime(r["locked_until"], _TS) - now).total_seconds() // 60) + 1
        raise AuthError(f"الحساب موقوف مؤقتًا. حاول بعد {left} دقيقة")
    if not security.verify_password(password, r["pw_hash"]):
        failed = r["failed_count"] + 1
        locked = None
        if failed >= security.MAX_LOGIN_FAILURES:
            locked = (now + timedelta(minutes=security.LOCK_MINUTES)).strftime(_TS)
            failed = 0
        with transaction(con):
            con.execute("UPDATE users SET failed_count=?, locked_until=? WHERE id=?", (failed, locked, r["id"]))
            audit(con, None, "login.failed", "user", r["id"], f"{r['username']}: كلمة مرور خطأ" + (" — قُفل الحساب" if locked else ""))
        if locked:
            raise AuthError(f"تم إيقاف الدخول {security.LOCK_MINUTES} دقائق بعد {security.MAX_LOGIN_FAILURES} محاولات خاطئة")
        raise AuthError(f"كلمة المرور غير صحيحة. المحاولات المتبقية: {security.MAX_LOGIN_FAILURES - failed}")
    user = _row_user(r)
    with transaction(con):
        con.execute("UPDATE users SET failed_count=0, locked_until=NULL, last_login_at=? WHERE id=?",
                    (now.strftime(_TS), r["id"]))
        audit(con, user, "login.ok", "user", user.id, None)
    return user


def audit_rows(con, limit: int = 200, before_id: int | None = None) -> list[tuple]:
    if before_id is None:
        return con.execute("SELECT id, at, username, action, entity, entity_id, detail FROM audit_log "
                           "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return con.execute("SELECT id, at, username, action, entity, entity_id, detail FROM audit_log "
                       "WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, limit)).fetchall()

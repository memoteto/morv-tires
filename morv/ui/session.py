"""جلسة التشغيل: الاتصال بالقاعدة، والمستخدم الداخل، وطلبات تخص التطبيق كله.

الشاشات تأخذ الجلسة بدل أن تأخذ الاتصال والمستخدم كلًا على حدة، وتطلب منها ما يتجاوز
الشاشة الواحدة (إعادة التشغيل بعد الاستعادة، وتسجيل الخروج) عبر دوال يضبطها التطبيق.
"""
from dataclasses import dataclass, field
from typing import Callable

from morv.infra import users


@dataclass
class Session:
    """حالة الجلسة الحالية."""
    con: object
    user: users.User
    on_logout: Callable[[], None] = field(default=lambda: None)
    on_restore: Callable[[str, str], None] = field(default=lambda path, secret: None)

    def can(self, perm: str) -> bool:
        """هل يملك المستخدم الحالي الصلاحية؟"""
        return users.has_permission(self.con, self.user, perm)

    def refresh_user(self) -> None:
        """إعادة قراءة بيانات المستخدم بعد تعديلها."""
        self.user = users.get_user(self.con, self.user.id)

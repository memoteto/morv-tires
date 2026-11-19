"""Run session: database connection, signed-in user, app-wide requests."""
from dataclasses import dataclass, field
from typing import Callable

from morv.infra import users


@dataclass
class Session:
    con: object
    user: users.User
    on_logout: Callable[[], None] = field(default=lambda: None)
    on_restore: Callable[[str, str], None] = field(default=lambda path, secret: None)

    def can(self, perm: str, con=None) -> bool:
        return users.has_permission(con or self.con, self.user, perm)

    def refresh_user(self) -> None:
        self.user = users.get_user(self.con, self.user.id)

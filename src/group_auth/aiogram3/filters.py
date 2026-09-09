"""Filters, so an admin-only command reads like one.

    @router.message(Command("stats"), AdminFilter(checker))
    async def stats(message: Message) -> None: ...

rather than a permission check hidden in the body of the handler, where the
next command someone adds will not have one.
"""

from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from ..membership import MembershipChecker


class AdminFilter(BaseFilter):
    """Passes only administrators of the bot, from ``AUTH_ADMIN_IDS``.

    Not group administrators: those are a Telegram fact about a chat, and a
    person who administers the group is not thereby an operator of the bot.
    """

    def __init__(self, checker: MembershipChecker, *, negate: bool = False) -> None:
        self._checker = checker
        self._negate = negate

    async def __call__(self, event: TelegramObject, **_: Any) -> bool:
        user = getattr(event, "from_user", None)
        if user is None:
            return self._negate
        result = self._checker.is_admin(user.id)
        return not result if self._negate else result


class NotAdminFilter(AdminFilter):
    """Everyone who got through the gate but is not an administrator."""

    def __init__(self, checker: MembershipChecker) -> None:
        super().__init__(checker, negate=True)

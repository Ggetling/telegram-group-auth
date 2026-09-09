"""The shapes this package expects, as structural types.

Nothing here is a base class to inherit from. They are ``typing.Protocol``s, so
an object satisfies them by having the right methods — which is what lets the
core work against aiogram, python-telegram-bot, or a hand-rolled HTTP client
without importing any of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from .models import UserRecord, UserRef


@runtime_checkable
class MembershipApi(Protocol):
    """Anything that can ask Telegram about one person in one chat.

    The return value is read field by field and may be an object or a mapping,
    so a raw ``getChatMember`` JSON payload is a valid answer.
    """

    async def get_chat_member(self, chat_id: int, user_id: int) -> Any: ...


@runtime_checkable
class AdminListApi(Protocol):
    """Optional: the one enumeration Telegram does allow.

    ``getChatAdministrators`` returns administrators only — never the full
    membership. See docs/DESIGN.md.
    """

    async def get_chat_administrators(self, chat_id: int) -> Any: ...


class AuthStore(Protocol):
    """Where the roster and the bound group live.

    Every method is allowed to fail; the gate treats the store as bookkeeping
    and never lets a store error turn into a refusal. Two implementations ship
    with the package: :class:`~group_auth.store_memory.MemoryStore` and
    :class:`~group_auth.store_sqlite.SqliteStore`.
    """

    async def remember_user(self, user: UserRef, *, source: str) -> None: ...

    async def set_membership(self, user_id: int, member: bool) -> None: ...

    async def set_group_admin(self, user_id: int, is_group_admin: bool) -> None: ...

    async def get_user(self, user_id: int) -> UserRecord | None: ...

    async def list_users(self, *, members_only: bool = False) -> list[UserRecord]: ...

    async def get_bound_groups(self) -> tuple[int, ...]: ...

    async def set_bound_groups(self, group_ids: Sequence[int]) -> None: ...

    async def close(self) -> None: ...

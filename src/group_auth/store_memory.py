"""The roster, in memory. The default: no files, no schema, nothing to migrate.

Loses everything on restart, which for the gate itself costs nothing — the
authority is always ``getChatMember``, never the roster. What is lost is the
list of people seen so far and the bound group; if you need those to survive a
restart, use :class:`~group_auth.store_sqlite.SqliteStore` instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ._time import utcnow_iso
from .models import UserRecord, UserRef


class MemoryStore:
    def __init__(self) -> None:
        self._users: dict[int, UserRecord] = {}
        self._bound: tuple[int, ...] = ()

    async def remember_user(self, user: UserRef, *, source: str) -> None:
        now = utcnow_iso()
        current = self._users.get(user.user_id)
        if current is None:
            self._users[user.user_id] = UserRecord(
                user_id=user.user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                first_seen=now,
                last_seen=now,
                source=source,
            )
            return
        # Names are only overwritten when the new event actually carries one:
        # a join notification without a username must not erase the one we had.
        self._users[user.user_id] = replace(
            current,
            username=user.username or current.username,
            first_name=user.first_name or current.first_name,
            last_name=user.last_name or current.last_name,
            last_seen=now,
        )

    async def set_membership(self, user_id: int, member: bool) -> None:
        current = self._users.get(user_id)
        if current is None:
            self._users[user_id] = UserRecord(
                user_id=user_id,
                is_member=member,
                first_seen=utcnow_iso(),
                last_seen=utcnow_iso(),
                source="membership",
            )
            return
        self._users[user_id] = replace(current, is_member=member)

    async def set_group_admin(self, user_id: int, is_group_admin: bool) -> None:
        current = self._users.get(user_id)
        if current is None:
            self._users[user_id] = UserRecord(
                user_id=user_id,
                is_group_admin=is_group_admin,
                first_seen=utcnow_iso(),
                last_seen=utcnow_iso(),
                source="admins_sync",
            )
            return
        self._users[user_id] = replace(current, is_group_admin=is_group_admin)

    async def get_user(self, user_id: int) -> UserRecord | None:
        return self._users.get(user_id)

    async def list_users(self, *, members_only: bool = False) -> list[UserRecord]:
        rows = sorted(self._users.values(), key=lambda r: r.user_id)
        if members_only:
            rows = [r for r in rows if r.is_member]
        return rows

    async def get_bound_groups(self) -> tuple[int, ...]:
        return self._bound

    async def set_bound_groups(self, group_ids: Sequence[int]) -> None:
        self._bound = tuple(dict.fromkeys(group_ids))

    async def close(self) -> None:
        return None

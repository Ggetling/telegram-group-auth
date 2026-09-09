"""Plain data passed between the core, the stores and the adapters.

These types exist so that nothing in the core has to import a bot framework.
An adapter converts its framework's user object into a ``UserRef`` and the
core never learns which framework it was.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserRef:
    """Who is asking. Only the fields a roster needs to show a name."""

    user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

    @property
    def label(self) -> str:
        """A short human label, never empty — falls back to the numeric id."""
        if self.username:
            return f"@{self.username}"
        name = " ".join(p for p in (self.first_name, self.last_name) if p)
        return name or str(self.user_id)


@dataclass(frozen=True)
class UserRecord:
    """One row of the roster.

    ``is_group_admin`` is a Telegram fact — this person administers the bound
    group. It is NOT the same as the bot's own admin list from ``AUTH_ADMIN_IDS``;
    conflating the two would silently hand bot administration to whoever runs
    the group.
    """

    user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_member: bool | None = None
    is_group_admin: bool = False
    first_seen: str = ""
    last_seen: str = ""
    source: str = ""

    @property
    def label(self) -> str:
        return UserRef(
            self.user_id, self.username, self.first_name, self.last_name
        ).label

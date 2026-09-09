"""Telegram bot authorization by group membership.

The rule: whoever is in the group is a user of the bot; administrators are
listed separately and always get in.

Everything worth importing is exported here::

    from group_auth import AuthConfig, MembershipChecker, MemoryStore

For aiogram 3 there is a ready-made adapter, imported separately so that this
package keeps working with no bot framework installed at all::

    from group_auth.aiogram3 import install, lifecycle_router

Note that the roster can never be complete: the Bot API has no method that
lists a group's membership. See docs/DESIGN.md.
"""

from __future__ import annotations

from .config import AuthConfig, BindPolicy
from .errors import ConfigError, GroupAuthError
from .membership import (
    ALLOWED_STATUSES,
    NOT_MEMBER_MARKERS,
    MembershipChecker,
    Reason,
    Verdict,
    explain,
    looks_like_absence,
    member_is_allowed,
    user_ref,
    user_refs,
)
from .models import UserRecord, UserRef
from .protocols import AdminListApi, AuthStore, MembershipApi
from .store_memory import MemoryStore
from .store_sqlite import SqliteStore

__all__ = [
    "ALLOWED_STATUSES",
    "NOT_MEMBER_MARKERS",
    "AdminListApi",
    "AuthConfig",
    "AuthStore",
    "BindPolicy",
    "ConfigError",
    "GroupAuthError",
    "MembershipApi",
    "MembershipChecker",
    "MemoryStore",
    "Reason",
    "SqliteStore",
    "UserRecord",
    "UserRef",
    "Verdict",
    "explain",
    "looks_like_absence",
    "member_is_allowed",
    "user_ref",
    "user_refs",
]

__version__ = "0.1.0"

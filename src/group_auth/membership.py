"""The gate: may this person use the bot?

Telegram gives a bot no way to list a group's members. ``getChatAdministrators``
returns administrators only, ``getChatMemberCount`` returns only a number, and
there is no "give me everyone" method in the Bot API. So membership is checked
one person at a time, at the moment they write, and the answer is cached.

For the person the effect is the one asked for — join the group and the bot
works, with nobody granting anything — but the roster can never be complete.
Anything that needs a list of users reads :mod:`group_auth.protocols.AuthStore`,
which is filled from what Telegram volunteers, and is documented as partial.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .config import AuthConfig
from .models import UserRef
from .protocols import AuthStore, MembershipApi

log = logging.getLogger("group_auth")

#: Statuses that make somebody a member. ``restricted`` is a member too — just
#: one with fewer rights — and is checked separately through ``is_member``,
#: because a restricted user who has actually left carries the same status.
ALLOWED_STATUSES = frozenset({"creator", "administrator", "member"})


class Reason(str, Enum):
    """Why the gate decided what it decided.

    A stable code, not a sentence: the wording, the language and the tone of
    the refusal belong to the bot that embeds this, not to the library.
    Use :func:`explain` for a default English text.
    """

    ADMIN = "admin"
    MEMBER = "member"
    CACHED = "cached"
    GRACE = "grace"
    NOT_MEMBER = "not_member"
    NO_GROUP = "no_group"
    API_ERROR = "api_error"


_TEXTS: dict[Reason, str] = {
    Reason.ADMIN: "You are an administrator of this bot.",
    Reason.MEMBER: "You are a member of the group that grants access.",
    Reason.CACHED: "Reusing the result of a recent membership check.",
    Reason.GRACE: "Telegram is not answering; trusting your last successful check.",
    Reason.NOT_MEMBER: "You are not a member of the group that grants access.",
    Reason.NO_GROUP: (
        "No access group is connected yet, so only administrators can use this bot."
    ),
    Reason.API_ERROR: "Your membership could not be checked right now.",
}


#: Substrings that mark an API error meaning "this person is not in the chat"
#: rather than "the question could not be asked". Telegram does not always
#: answer ``getChatMember`` with a status for somebody who was never in the
#: chat; sometimes it is a 400.
#:
#: **This list is a guess.** It was assembled from the error texts the Bot API
#: is reported to return, and has not been confirmed against a live API by
#: this project. Getting it wrong is not dangerous — an unmatched error becomes
#: :attr:`Reason.API_ERROR`, which refuses access too — it just costs a call to
#: Telegram for every message from an outsider, because an unknown answer is
#: never cached. Pass ``absence_detector=`` to override it, and please correct
#: it if you have measured otherwise.
NOT_MEMBER_MARKERS: tuple[str, ...] = (
    "user not found",
    "participant_id_invalid",
    "chat member not found",
    "user_not_participant",
    "member list is inaccessible",
)


def looks_like_absence(exc: BaseException) -> bool:
    """True if this error says "not in the chat" rather than "cannot tell"."""
    text = str(exc).lower()
    return any(marker in text for marker in NOT_MEMBER_MARKERS)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: Reason
    is_admin: bool = False
    #: Raw Telegram status, when one was actually obtained.
    status: str | None = None
    #: Which group answered, when one did.
    group_id: int | None = None

    def __bool__(self) -> bool:
        return self.allowed


def explain(verdict: Verdict) -> str:
    """A default English explanation. Replace it with your own wording."""
    return _TEXTS[verdict.reason]


def _field(member: Any, name: str) -> Any:
    """Read one field from a chat-member payload.

    Objects and mappings both work, so an aiogram model, a
    python-telegram-bot model and a bare ``getChatMember`` JSON dict are all
    acceptable answers. This is the whole of what makes the core
    framework-agnostic, and it is covered by a test that passes a plain dict.
    """
    if isinstance(member, Mapping):
        return member.get(name)
    return getattr(member, name, None)


def member_is_allowed(member: Any) -> tuple[bool, str]:
    """Decide from a chat-member payload. Returns ``(allowed, status)``."""
    status = str(_field(member, "status") or "")
    if status == "restricted":
        return bool(_field(member, "is_member")), status
    return status in ALLOWED_STATUSES, status


class MembershipChecker:
    """Holds the verdict cache and the list of groups that grant access.

    One instance per bot. It is not thread-safe on purpose: an asyncio bot has
    one loop, and a lock here would buy nothing but a way to deadlock.
    """

    def __init__(
        self,
        config: AuthConfig | None = None,
        *,
        store: AuthStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        absence_detector: Callable[[BaseException], bool] = looks_like_absence,
    ) -> None:
        self.config = config or AuthConfig()
        self.store = store
        self._absent = absence_detector
        # Injected so the cache and grace windows can be tested without sleeping.
        # Tests that need `sleep` do not get run.
        self._clock = clock
        # user_id -> (verdict, when it was taken)
        self._cache: dict[int, tuple[bool, float]] = {}
        # user_id -> when Telegram last confirmed membership for real
        self._last_ok: dict[int, float] = {}
        self._bound: tuple[int, ...] = ()
        self._api_broken = False

    # ---------------------------------------------------------------- groups

    @property
    def groups(self) -> tuple[int, ...]:
        """Configured groups first, then any bound at runtime."""
        seen = dict.fromkeys(self.config.group_ids)
        seen.update(dict.fromkeys(self._bound))
        return tuple(seen)

    @property
    def api_broken(self) -> bool:
        """True if the last attempt to reach Telegram failed."""
        return self._api_broken

    async def load(self) -> None:
        """Pull the bound group out of the store. Call once at startup."""
        if self.store is None:
            return
        try:
            self._bound = tuple(await self.store.get_bound_groups())
        except Exception as exc:
            log.warning("could not load bound groups: %s", exc)

    async def bind(self, group_id: int, *, replace: bool = False) -> None:
        """Start honouring ``group_id``, and remember it across restarts."""
        groups = (
            (group_id,) if replace else tuple(dict.fromkeys((*self._bound, group_id)))
        )
        self._bound = groups
        # A newly bound group can only widen access, but a rebind (replace)
        # narrows it, so the cache has to go either way.
        self._cache.clear()
        if self.store is not None:
            try:
                await self.store.set_bound_groups(groups)
            except Exception as exc:
                log.warning("could not persist bound groups %s: %s", groups, exc)

    async def unbind(self, group_id: int) -> None:
        self._bound = tuple(g for g in self._bound if g != group_id)
        self._cache.clear()
        if self.store is not None:
            try:
                await self.store.set_bound_groups(self._bound)
            except Exception as exc:
                log.warning("could not persist bound groups: %s", exc)

    # ----------------------------------------------------------------- admin

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.config.admin_ids

    # ----------------------------------------------------------------- cache

    def forget(self, user_id: int | None = None) -> None:
        """Drop cached verdicts.

        Called when Telegram reports somebody joining or leaving, so access
        appears and disappears at once instead of living out the cache. With no
        argument, forgets everyone.
        """
        if user_id is None:
            self._cache.clear()
            self._last_ok.clear()
            return
        self._cache.pop(user_id, None)

    def _cached(self, user_id: int, now: float) -> bool | None:
        hit = self._cache.get(user_id)
        if hit is None:
            return None
        allowed, taken = hit
        ttl = self.config.cache_ttl if allowed else self.config.deny_cache_ttl
        if now - taken >= ttl:
            return None
        return allowed

    # ----------------------------------------------------------------- check

    async def check(
        self,
        api: MembershipApi,
        user_id: int,
        *,
        user: UserRef | None = None,
    ) -> Verdict:
        """The only question this package answers."""
        admin = self.is_admin(user_id)
        if user is not None:
            await self._remember(user, source="contact")

        # Administrators pass unconditionally: no group needed, and no working
        # Telegram API needed either. Whoever has to repair the bot must be
        # able to reach it precisely when everything else has fallen over.
        if admin:
            return Verdict(True, Reason.ADMIN, is_admin=True)

        groups = self.groups
        if not groups:
            return Verdict(False, Reason.NO_GROUP)

        now = self._clock()
        cached = self._cached(user_id, now)
        if cached is not None:
            return Verdict(cached, Reason.CACHED)

        failures = 0
        last_status: str | None = None
        for group_id in groups:
            try:
                member = await api.get_chat_member(group_id, user_id)
            except Exception as exc:
                if self._absent(exc):
                    # A definite answer, just delivered as an error: this
                    # person is not in that chat. Treating it as a failure
                    # would mean asking Telegram again on every message they
                    # ever send.
                    last_status = "absent"
                    continue
                failures += 1
                self._api_broken = True
                log.warning(
                    "could not check membership of %s in %s: %s", user_id, group_id, exc
                )
                continue

            self._api_broken = False
            allowed, status = member_is_allowed(member)
            last_status = status
            if allowed:
                self._cache[user_id] = (True, now)
                self._last_ok[user_id] = now
                await self._set_membership(user_id, True)
                return Verdict(True, Reason.MEMBER, status=status, group_id=group_id)

        if failures:
            # At least one group never answered, so "not a member" is not a
            # fact — it is an unknown. An unknown must not be cached as a
            # refusal, and it must not switch the bot off for people who were
            # verified moments ago.
            last = self._last_ok.get(user_id)
            if last is not None and now - last < self.config.grace:
                return Verdict(True, Reason.GRACE)
            return Verdict(False, Reason.API_ERROR)

        self._cache[user_id] = (False, now)
        self._last_ok.pop(user_id, None)
        await self._set_membership(user_id, False)
        return Verdict(False, Reason.NOT_MEMBER, status=last_status)

    # ----------------------------------------------------------- enumeration

    async def sync_admins(self, api: Any, group_id: int | None = None) -> int:
        """Copy each group's administrators into the roster.

        The only enumeration Telegram offers a bot. It is not the membership,
        and calling it does not make the roster complete.
        """
        if self.store is None:
            return 0
        getter = getattr(api, "get_chat_administrators", None)
        if getter is None:
            return 0
        targets = (group_id,) if group_id is not None else self.groups
        found = 0
        for target in targets:
            # The whole per-group pass is guarded, not just the call: this runs
            # from an update handler, and an unexpected payload shape must not
            # turn "the bot was added to a group" into a traceback.
            try:
                admins = await getter(target)
                for entry in admins if isinstance(admins, (list, tuple)) else ():
                    ref = user_ref(_field(entry, "user"))
                    if ref is None:
                        continue
                    await self._remember(ref, source="admins_sync")
                    await self._set_membership(ref.user_id, True)
                    await self.store.set_group_admin(ref.user_id, True)
                    found += 1
            except Exception as exc:
                log.warning("could not sync administrators of %s: %s", target, exc)
                continue
        return found

    # ------------------------------------------------------------ bookkeeping
    # The roster is bookkeeping. A broken store must never become a refusal,
    # so every call here swallows its errors and logs them.

    async def _remember(self, user: UserRef, *, source: str) -> None:
        if self.store is None:
            return
        try:
            await self.store.remember_user(user, source=source)
        except Exception as exc:
            log.warning("could not record user %s: %s", user.user_id, exc)

    async def _set_membership(self, user_id: int, member: bool) -> None:
        if self.store is None:
            return
        try:
            await self.store.set_membership(user_id, member)
        except Exception as exc:
            log.warning("could not record membership of %s: %s", user_id, exc)

    async def note_users(self, users: Iterable[UserRef], *, source: str) -> None:
        """Record several people at once — group join messages arrive in batches."""
        for user in users:
            await self._remember(user, source=source)

    async def record_membership(
        self,
        user: UserRef,
        member: bool,
        *,
        source: str = "event",
        forget: bool = True,
    ) -> None:
        """Telegram told us somebody joined or left. Write it down and act on it.

        ``forget`` drops the cached verdict, which is the point: without it the
        cache decides for up to ``cache_ttl`` seconds, so a person removed from
        the group keeps their access and a person just added keeps being
        refused.
        """
        await self._remember(user, source=source)
        await self._set_membership(user.user_id, member)
        if forget:
            self.forget(user.user_id)


def user_ref(user: Any) -> UserRef | None:
    """Build a :class:`UserRef` from any framework's user object or a dict."""
    if user is None:
        return None
    if isinstance(user, UserRef):
        return user
    raw_id = _field(user, "id")
    if raw_id is None:
        return None
    try:
        user_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    return UserRef(
        user_id=user_id,
        username=_field(user, "username"),
        first_name=_field(user, "first_name"),
        last_name=_field(user, "last_name"),
    )


def user_refs(users: Sequence[Any] | None) -> list[UserRef]:
    return [ref for ref in (user_ref(u) for u in users or ()) if ref is not None]

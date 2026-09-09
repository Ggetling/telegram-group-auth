"""Keeping up with the group: bindings, joins, leaves, migrations.

None of this decides access — ``getChatMember`` always does. What it buys:

* the group is bound by adding the bot to it, so nobody has to look up a
  numeric chat id;
* the roster learns about people Telegram volunteers, which is the only way it
  learns anything;
* access appears and disappears at the moment somebody joins or leaves,
  instead of living out the cache.

**Two things fail silently if you skip them.** ``chat_member`` updates are only
delivered to a bot that is an administrator of the group, and they are not in
``allowed_updates`` by default — start polling with
``allowed_updates=dp.resolve_used_update_types()``. The service-message
handlers below are the fallback for the first case; there is no fallback for
the second.

Include this router **first**, before your own, so a group service message is
seen here before anything else looks at it.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import ChatMemberUpdated, Message

from ..membership import MembershipChecker, member_is_allowed, user_ref, user_refs

log = logging.getLogger("group_auth.aiogram3")

GROUP_TYPES = frozenset({"group", "supergroup"})
_BOT_IS_IN = frozenset({"member", "administrator", "creator"})
_BOT_IS_OUT = frozenset({"left", "kicked"})


def lifecycle_router(
    checker: MembershipChecker,
    *,
    name: str = "group_auth.lifecycle",
) -> Router:
    router = Router(name=name)

    async def _may_bind(event: ChatMemberUpdated) -> bool:
        policy = checker.config.bind_on_add
        if policy == "never":
            return False
        if policy == "always":
            return True
        if policy == "first":
            return not checker.groups
        # "admin_only": only somebody on the bot's own admin list may hand a
        # group the power to grant access. Otherwise anyone who adds the bot to
        # a group of their own has just given their people accounts on it.
        adder = getattr(event.from_user, "id", None)
        return adder is not None and checker.is_admin(adder)

    @router.my_chat_member()
    async def bot_membership_changed(event: ChatMemberUpdated) -> None:
        """The bot itself was added to or removed from a chat."""
        if event.chat.type not in GROUP_TYPES:
            return
        status = event.new_chat_member.status
        chat_id = event.chat.id

        if status in _BOT_IS_OUT:
            # The binding is kept on purpose: re-adding the bot restores access
            # without an administrator having to do anything. While it is out,
            # every membership check fails, which the grace window covers for a
            # while and then stops covering.
            log.warning(
                "bot removed from group %s; it is still bound, and membership "
                "checks will fail until the bot is back",
                chat_id,
            )
            return

        if status not in _BOT_IS_IN:
            return

        if chat_id in checker.groups:
            await checker.sync_admins(event.bot, chat_id)
            return

        if not await _may_bind(event):
            adder = getattr(event.from_user, "id", None)
            log.warning(
                "not binding group %s: added by %s, and bind_on_add=%s",
                chat_id,
                adder,
                checker.config.bind_on_add,
            )
            return

        replace = checker.config.bind_on_add == "always"
        await checker.bind(chat_id, replace=replace)
        log.info("group %s bound; its members can now use the bot", chat_id)
        await checker.sync_admins(event.bot, chat_id)

    @router.chat_member()
    async def member_changed(event: ChatMemberUpdated) -> None:
        """Somebody's status in a group changed. Needs the bot to be group admin."""
        if event.chat.id not in checker.groups:
            return
        ref = user_ref(event.new_chat_member.user)
        if ref is None:
            return
        allowed, status = member_is_allowed(event.new_chat_member)
        await checker.record_membership(ref, allowed, source="chat_member")
        log.info(
            "group %s: %s is now %s (access: %s)",
            event.chat.id,
            ref.user_id,
            status,
            "yes" if allowed else "no",
        )

    @router.message(F.new_chat_members)
    async def people_joined(message: Message) -> None:
        """Fallback for a bot that is not a group administrator."""
        if message.chat.id not in checker.groups:
            return
        for ref in user_refs(message.new_chat_members):
            await checker.record_membership(ref, True, source="join")

    @router.message(F.left_chat_member)
    async def person_left(message: Message) -> None:
        if message.chat.id not in checker.groups:
            return
        ref = user_ref(message.left_chat_member)
        if ref is None:
            return
        await checker.record_membership(ref, False, source="leave")

    @router.message(F.migrate_to_chat_id)
    async def group_migrated(message: Message) -> None:
        """A group turned into a supergroup, and its id changed.

        The old id is dead: every check against it would fail from here on. If
        the binding is not moved, access quietly stops working for everybody
        who is not an administrator.
        """
        new_id = message.migrate_to_chat_id
        old_id = message.chat.id
        if new_id is None or old_id not in checker.groups:
            return
        await checker.unbind(old_id)
        await checker.bind(new_id)
        log.warning("group %s became supergroup %s; binding moved", old_id, new_id)

    return router

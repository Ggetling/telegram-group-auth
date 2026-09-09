"""The gate as one aiogram middleware.

Checking access in every handler is how a bot gets opened to everybody:
forget it in one place and the hole is invisible until somebody finds it. So
the check is a layer, and there is exactly one of it.

The layer is registered as an **outer** middleware. Outer runs before filters
and handler resolution, so a person without access is told so even when the
thing they sent matches no handler at all — with an inner middleware their
message would simply vanish.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    Message,
    TelegramObject,
)

from ..membership import MembershipChecker, Verdict, user_ref

log = logging.getLogger("group_auth.aiogram3")

#: Observers that carry a person asking the bot to do something. These get the
#: gate. Names missing from the installed aiogram version are skipped.
GATED_OBSERVERS: tuple[str, ...] = (
    "message",
    "edited_message",
    "callback_query",
    "inline_query",
    "chosen_inline_result",
    "shipping_query",
    "pre_checkout_query",
    "poll_answer",
    "business_message",
    "edited_business_message",
)

#: Deliberately NOT gated, and each for a reason:
#:
#: * ``my_chat_member`` / ``chat_member`` / ``chat_join_request`` — this is how
#:   the bot learns who is in the group. Gating them would gate the mechanism
#:   that decides the gate.
#: * ``channel_post`` / ``edited_channel_post`` / ``message_reaction`` — no
#:   individual asking for anything.
UNGATED_OBSERVERS: tuple[str, ...] = (
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "channel_post",
    "edited_channel_post",
    "message_reaction",
    "message_reaction_count",
)

DenialHandler = Callable[[TelegramObject, Verdict], Awaitable[None]]


def _is_service_membership(event: TelegramObject) -> bool:
    """True for the group service messages the lifecycle router needs to see.

    "X joined", "X left" and "this group became a supergroup" carry no request
    from anybody, and they are the fallback path for keeping the roster honest
    when the bot is not an administrator of the group. If the gate swallowed
    them — and with ``private_only`` it would — that path would be dead.

    **Exactly the three that** :func:`~group_auth.aiogram3.lifecycle_router`
    **handles, and no more.** Every name here is a hole in the gate: the event
    reaches the routers with no membership check and without ``auth`` in the
    handler data. It is only safe because the lifecycle router matches these
    three and aiogram then stops propagating them. ``group_chat_created`` and
    ``supergroup_chat_created`` used to be on this list with no handler behind
    them, which let anyone who created a group containing the bot run the
    bot's own handlers unauthorized.

    So: include the lifecycle router, and never add a name here without
    adding the handler that consumes it.
    """
    if not isinstance(event, Message):
        return False
    return bool(
        event.new_chat_members  # -> people_joined
        or event.left_chat_member  # -> person_left
        or event.migrate_to_chat_id  # -> group_migrated
    )


async def default_on_denied(event: TelegramObject, verdict: Verdict) -> None:
    """What a refused person sees. Replace it — the wording is yours, not ours.

    The numeric id is included on purpose: it is the one thing the person has
    to tell whoever can add them to the group.
    """
    user = getattr(event, "from_user", None)
    user_id = getattr(user, "id", "unknown")
    short = "You do not have access to this bot."
    text = (
        f"{short}\n\n"
        f"Your Telegram id: {user_id}\n"
        f"Reason: {verdict.reason.value}\n\n"
        "Access comes from being a member of the group this bot serves — "
        "ask to be added there."
    )
    if isinstance(event, CallbackQuery):
        await event.answer(short, show_alert=True)
        return
    if isinstance(event, Message):
        await event.answer(text)
        return
    if isinstance(event, InlineQuery):
        await event.answer([], cache_time=1, is_personal=True)
        return
    # Anything else has no way to reply. Dropping it is the whole refusal.
    log.info(
        "denied %s for %s: %s",
        type(event).__name__,
        user_id,
        verdict.reason.value,
    )


class AccessMiddleware(BaseMiddleware):
    """Lets through only the people who may use the bot.

    Puts two things into handler data:

    * ``auth`` — the :class:`~group_auth.membership.Verdict`;
    * ``is_admin`` — whether this person is an administrator of the bot.

    Declare them as handler arguments and aiogram injects them by name.
    """

    def __init__(
        self,
        checker: MembershipChecker,
        *,
        on_denied: DenialHandler | None = None,
        private_only: bool | None = None,
        verdict_key: str = "auth",
        admin_key: str = "is_admin",
    ) -> None:
        self._checker = checker
        self._on_denied = on_denied or default_on_denied
        self._private_only = (
            checker.config.private_only if private_only is None else private_only
        )
        self._verdict_key = verdict_key
        self._admin_key = admin_key

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if _is_service_membership(event):
            return await handler(event, data)

        chat = self._chat_of(event)
        if self._private_only and chat is not None and chat.type != "private":
            # The bot sits in the group to be the access roster, not to talk
            # there. A callback still gets answered, or Telegram leaves the
            # button spinning for the person who pressed it.
            if isinstance(event, CallbackQuery):
                await event.answer()
            return None

        user = getattr(event, "from_user", None)
        if user is None:
            return None

        bot = data.get("bot") or getattr(event, "bot", None)
        if bot is None:  # pragma: no cover - aiogram always supplies it
            log.warning("no bot in context; refusing %s", type(event).__name__)
            return None

        verdict = await self._checker.check(bot, user.id, user=user_ref(user))
        if verdict.allowed:
            data[self._verdict_key] = verdict
            data[self._admin_key] = verdict.is_admin or self._checker.is_admin(user.id)
            return await handler(event, data)

        log.info("denied user_id=%s: %s", user.id, verdict.reason.value)
        await self._on_denied(event, verdict)
        return None

    @staticmethod
    def _chat_of(event: TelegramObject) -> Any:
        if isinstance(event, Message):
            return event.chat
        if isinstance(event, CallbackQuery):
            return event.message.chat if event.message else None
        return None


def install(
    dispatcher: Any,
    checker: MembershipChecker,
    *,
    on_denied: DenialHandler | None = None,
    private_only: bool | None = None,
) -> AccessMiddleware:
    """Put the gate on every observer that needs it. One line, nothing to forget.

    Attaching by hand means keeping a list of observers up to date, and missing
    one of them means that entry point is open. Buttons are the usual casualty:
    it is enough to have received a message with a button once to keep pressing
    it after losing access.

    Returns the middleware, in case you want it for something else.
    """
    middleware = AccessMiddleware(
        checker, on_denied=on_denied, private_only=private_only
    )
    attached: list[str] = []
    for name in GATED_OBSERVERS:
        observer = getattr(dispatcher, name, None)
        if observer is None:
            continue
        observer.outer_middleware(middleware)
        attached.append(name)
    log.debug("access gate installed on: %s", ", ".join(attached))
    return middleware

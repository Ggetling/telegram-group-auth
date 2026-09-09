from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from aiogram import Dispatcher, Router
from aiogram.types import CallbackQuery, Message, Update

from fakes import chat, message, mocked_bot, user
from group_auth import AuthConfig, MembershipChecker, Reason, Verdict
from group_auth.aiogram3 import (
    GATED_OBSERVERS,
    UNGATED_OBSERVERS,
    AccessMiddleware,
    install,
    lifecycle_router,
)

GROUP = -1001234567890
ADMIN = 111
PERSON = 222


class Handler:
    """Stands in for whatever the bot would have done."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, event: Any, data: dict[str, Any]) -> str:
        self.calls.append(dict(data))
        return "handled"

    @property
    def called(self) -> bool:
        return bool(self.calls)


class Denials:
    def __init__(self) -> None:
        self.verdicts: list[Verdict] = []

    async def __call__(self, event: Any, verdict: Verdict) -> None:
        self.verdicts.append(verdict)


def build(**kwargs: Any) -> MembershipChecker:
    cfg = AuthConfig(admin_ids=frozenset({ADMIN}), group_ids=(GROUP,), **kwargs)
    return MembershipChecker(cfg)


async def test_a_group_member_gets_through_and_the_verdict_is_injected() -> None:
    bot, session = mocked_bot()
    session.members[(GROUP, PERSON)] = "member"
    handler, denied = Handler(), Denials()
    mw = AccessMiddleware(build(), on_denied=denied)

    result = await mw(handler, message(from_user=user(PERSON)), {"bot": bot})

    assert result == "handled"
    assert denied.verdicts == []
    data = handler.calls[0]
    assert data["auth"].reason is Reason.MEMBER
    assert data["is_admin"] is False


async def test_an_administrator_gets_through_without_asking_telegram() -> None:
    bot, session = mocked_bot()
    handler = Handler()
    mw = AccessMiddleware(build())

    await mw(handler, message(from_user=user(ADMIN)), {"bot": bot})

    assert handler.calls[0]["is_admin"] is True
    assert "GetChatMember" not in session.method_names()


async def test_an_outsider_is_stopped_and_the_handler_never_runs() -> None:
    bot, session = mocked_bot()
    session.members[(GROUP, PERSON)] = "left"
    handler, denied = Handler(), Denials()
    mw = AccessMiddleware(build(), on_denied=denied)

    result = await mw(handler, message(from_user=user(PERSON)), {"bot": bot})

    assert result is None
    assert handler.called is False
    assert denied.verdicts[0].reason is Reason.NOT_MEMBER


async def test_the_default_refusal_tells_the_person_their_own_id() -> None:
    """The id is the one thing they have to pass on to get added to the group."""
    bot, session = mocked_bot()
    session.members[(GROUP, PERSON)] = "left"
    mw = AccessMiddleware(build())

    await mw(Handler(), message(from_user=user(PERSON)).as_(bot), {"bot": bot})

    assert "SendMessage" in session.method_names()
    sent = session.sent_texts[-1]
    assert str(PERSON) in sent
    assert Reason.NOT_MEMBER.value in sent


async def test_the_bot_stays_silent_in_the_group_it_guards() -> None:
    bot, _ = mocked_bot()
    handler, denied = Handler(), Denials()
    mw = AccessMiddleware(build())

    result = await mw(
        handler,
        message(from_user=user(PERSON), in_chat=chat(GROUP, "supergroup")),
        {"bot": bot},
    )

    assert result is None
    assert handler.called is False
    assert denied.verdicts == []  # dropped, not refused: nobody asked the bot anything


async def test_private_only_can_be_switched_off() -> None:
    bot, session = mocked_bot()
    session.members[(GROUP, PERSON)] = "member"
    handler = Handler()
    mw = AccessMiddleware(build(), private_only=False)

    result = await mw(
        handler,
        message(from_user=user(PERSON), in_chat=chat(GROUP, "supergroup")),
        {"bot": bot},
    )

    assert result == "handled"


@pytest.mark.parametrize(
    "extra",
    [
        {"new_chat_members": [user(PERSON)]},
        {"left_chat_member": user(PERSON)},
        {"migrate_to_chat_id": -1009999999999},
    ],
)
async def test_group_membership_notices_are_never_swallowed(
    extra: dict[str, Any],
) -> None:
    """Gating these would kill the path that keeps the roster honest."""
    bot, _ = mocked_bot()
    handler = Handler()
    mw = AccessMiddleware(build())

    result = await mw(
        handler,
        message(from_user=user(999), in_chat=chat(GROUP, "supergroup"), **extra),
        {"bot": bot},
    )

    assert result == "handled"


async def test_a_button_press_is_a_door_too() -> None:
    """Receiving a message with a button once must not outlive losing access."""
    bot, session = mocked_bot()
    session.members[(GROUP, PERSON)] = "left"
    handler, denied = Handler(), Denials()
    mw = AccessMiddleware(build(), on_denied=denied)

    press = CallbackQuery.model_construct(
        id="1",
        from_user=user(PERSON),
        chat_instance="ci",
        data="do:it",
        message=message(),
    )
    result = await mw(handler, press, {"bot": bot})

    assert result is None
    assert handler.called is False
    assert denied.verdicts[0].reason is Reason.NOT_MEMBER


async def test_a_button_pressed_inside_the_group_is_answered_then_dropped() -> None:
    """An unanswered callback leaves the button spinning for whoever pressed it."""
    bot, session = mocked_bot()
    handler = Handler()
    mw = AccessMiddleware(build())

    press = CallbackQuery.model_construct(
        id="1",
        from_user=user(PERSON),
        chat_instance="ci",
        data="do:it",
        message=message(in_chat=chat(GROUP, "supergroup")),
    ).as_(bot)
    result = await mw(handler, press, {"bot": bot})

    assert result is None
    assert handler.called is False
    assert "AnswerCallbackQuery" in session.method_names()


async def test_an_event_with_no_author_is_dropped() -> None:
    bot, _ = mocked_bot()
    handler = Handler()
    mw = AccessMiddleware(build())
    anonymous = Message.model_construct(
        message_id=1, date=datetime.now(timezone.utc), chat=chat(1), text="hi"
    )
    assert await mw(handler, anonymous, {"bot": bot}) is None
    assert handler.called is False


async def test_install_covers_every_entry_point_and_leaves_the_rest_alone() -> None:
    """One line instead of a list of observers nobody keeps up to date."""
    dp = Dispatcher()
    mw = install(dp, build())

    for name in GATED_OBSERVERS:
        observer = getattr(dp, name, None)
        if observer is None:
            continue
        assert mw in observer.outer_middleware._middlewares, name

    for name in UNGATED_OBSERVERS:
        observer = getattr(dp, name, None)
        if observer is None:
            continue
        assert mw not in observer.outer_middleware._middlewares, name


async def test_install_skips_observers_this_aiogram_does_not_have() -> None:
    class Tiny:
        def __init__(self) -> None:
            self.message = Dispatcher().message

    tiny = Tiny()
    mw = install(tiny, build())
    assert mw in tiny.message.outer_middleware._middlewares


@pytest.mark.parametrize(
    "extra",
    [{"group_chat_created": True}, {"supergroup_chat_created": True}],
)
async def test_a_chat_created_notice_is_not_a_way_past_the_gate(
    extra: dict[str, Any],
) -> None:
    """These were on the pass-through list with no handler behind them.

    Nothing consumed them, so they went straight on to the bot's own routers:
    anyone who created a group containing the bot ran its handlers unchecked.
    """
    bot, session = mocked_bot()
    session.members[(GROUP, PERSON)] = "left"
    handler = Handler()
    mw = AccessMiddleware(build())

    result = await mw(
        handler,
        message(from_user=user(PERSON), in_chat=chat(-100999, "group"), **extra),
        {"bot": bot},
    )

    assert result is None
    assert handler.called is False


async def test_an_outsiders_own_group_cannot_reach_the_bots_handlers() -> None:
    """End to end, through a real Dispatcher wired the way the README says."""
    bot, session = mocked_bot()
    session.members[(GROUP, PERSON)] = "left"
    checker = build()
    reached: list[int] = []

    own = Router(name="the bot's own")

    @own.message()
    async def catch_all(event: Message) -> None:
        reached.append(event.chat.id)

    dp = Dispatcher()
    dp.include_router(lifecycle_router(checker))
    install(dp, checker)
    dp.include_router(own)

    created = Message.model_construct(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat(-100999, "group"),
        from_user=user(PERSON),
        text=None,
        group_chat_created=True,
    )
    await dp.feed_update(bot, Update(update_id=1, message=created))

    assert reached == []

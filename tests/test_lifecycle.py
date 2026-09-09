from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from aiogram import Bot
from aiogram.types import ChatMemberUpdated, User

from fakes import chat, message, mocked_bot, user
from group_auth import AuthConfig, MembershipChecker, MemoryStore, Reason
from group_auth.aiogram3 import lifecycle_router

GROUP = -1001234567890
SUPERGROUP = -1009999999999
ADMIN = 111
PERSON = 222
BOT_ID = 42


def member(status: str, who: User, **extra: Any) -> Any:
    from aiogram.types import ChatMemberMember

    return ChatMemberMember.model_construct(status=status, user=who, **extra)


def cmu(
    *,
    bot: Bot,
    old: str,
    new: str,
    who: User,
    by: User,
    in_chat: Any = None,
) -> ChatMemberUpdated:
    return ChatMemberUpdated.model_construct(
        chat=in_chat or chat(GROUP, "supergroup"),
        from_user=by,
        date=datetime.now(timezone.utc),
        old_chat_member=member(old, who),
        new_chat_member=member(new, who),
    ).as_(bot)


def build(
    *,
    groups: tuple[int, ...] = (),
    policy: str = "admin_only",
    store: MemoryStore | None = None,
    **kwargs: Any,
) -> MembershipChecker:
    cfg = AuthConfig(
        admin_ids=frozenset({ADMIN}),
        group_ids=groups,
        bind_on_add=policy,  # type: ignore[arg-type]
        **kwargs,
    )
    return MembershipChecker(cfg, store=store if store is not None else MemoryStore())


# --------------------------------------------------------------- binding


async def test_an_administrator_adding_the_bot_binds_the_group() -> None:
    bot, _ = mocked_bot()
    checker = build()
    router = lifecycle_router(checker)

    await router.my_chat_member.trigger(
        cmu(bot=bot, old="left", new="member", who=user(BOT_ID), by=user(ADMIN))
    )

    assert checker.groups == (GROUP,)
    assert await checker.store.get_bound_groups() == (GROUP,)  # type: ignore[union-attr]


async def test_anyone_else_adding_the_bot_binds_nothing() -> None:
    """Otherwise adding the bot to a group of your own grants your people accounts."""
    bot, _ = mocked_bot()
    checker = build()
    router = lifecycle_router(checker)

    await router.my_chat_member.trigger(
        cmu(bot=bot, old="left", new="member", who=user(BOT_ID), by=user(PERSON))
    )

    assert checker.groups == ()


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("never", ()), ("first", (GROUP,)), ("always", (GROUP,))],
)
async def test_the_other_binding_policies(
    policy: str, expected: tuple[int, ...]
) -> None:
    bot, _ = mocked_bot()
    checker = build(policy=policy)
    router = lifecycle_router(checker)

    await router.my_chat_member.trigger(
        cmu(bot=bot, old="left", new="member", who=user(BOT_ID), by=user(PERSON))
    )

    assert checker.groups == expected


async def test_first_binds_once_and_then_stops() -> None:
    bot, _ = mocked_bot()
    checker = build(groups=(GROUP,), policy="first")
    router = lifecycle_router(checker)

    await router.my_chat_member.trigger(
        cmu(
            bot=bot,
            old="left",
            new="member",
            who=user(BOT_ID),
            by=user(PERSON),
            in_chat=chat(SUPERGROUP, "supergroup"),
        )
    )

    assert checker.groups == (GROUP,)


async def test_always_moves_the_binding_to_the_newest_group() -> None:
    bot, _ = mocked_bot()
    checker = build(groups=(), policy="always")
    router = lifecycle_router(checker)
    await checker.bind(GROUP)

    await router.my_chat_member.trigger(
        cmu(
            bot=bot,
            old="left",
            new="member",
            who=user(BOT_ID),
            by=user(PERSON),
            in_chat=chat(SUPERGROUP, "supergroup"),
        )
    )

    assert checker.groups == (SUPERGROUP,)


async def test_a_private_chat_is_never_bound() -> None:
    bot, _ = mocked_bot()
    checker = build(policy="always")
    router = lifecycle_router(checker)

    await router.my_chat_member.trigger(
        cmu(
            bot=bot,
            old="left",
            new="member",
            who=user(BOT_ID),
            by=user(ADMIN),
            in_chat=chat(555, "private"),
        )
    )

    assert checker.groups == ()


async def test_removing_the_bot_keeps_the_binding() -> None:
    """Re-adding the bot then restores access without anyone granting anything."""
    bot, _ = mocked_bot()
    checker = build(groups=(GROUP,))
    router = lifecycle_router(checker)

    await router.my_chat_member.trigger(
        cmu(bot=bot, old="member", new="kicked", who=user(BOT_ID), by=user(PERSON))
    )

    assert checker.groups == (GROUP,)


async def test_being_added_to_an_already_bound_group_refreshes_the_admins() -> None:
    bot, session = mocked_bot()
    checker = build(groups=(GROUP,))
    router = lifecycle_router(checker)

    await router.my_chat_member.trigger(
        cmu(bot=bot, old="left", new="administrator", who=user(BOT_ID), by=user(PERSON))
    )

    assert "GetChatAdministrators" in session.method_names()


# ------------------------------------------------------------- membership


async def test_leaving_the_group_takes_access_away_at_once() -> None:
    """Not in five minutes, when the cache would have expired on its own."""
    bot, session = mocked_bot()
    session.members[(GROUP, PERSON)] = "member"
    checker = build(groups=(GROUP,), cache_ttl=3600)
    router = lifecycle_router(checker)

    assert (await checker.check(bot, PERSON)).allowed is True

    session.members[(GROUP, PERSON)] = "kicked"
    await router.chat_member.trigger(
        cmu(bot=bot, old="member", new="kicked", who=user(PERSON), by=user(ADMIN))
    )

    verdict = await checker.check(bot, PERSON)
    assert verdict.allowed is False
    assert verdict.reason is Reason.NOT_MEMBER
    row = await checker.store.get_user(PERSON)  # type: ignore[union-attr]
    assert row is not None and row.is_member is False


async def test_joining_the_group_grants_access_at_once() -> None:
    bot, session = mocked_bot()
    session.members[(GROUP, PERSON)] = "left"
    checker = build(groups=(GROUP,), deny_cache_ttl=3600)
    router = lifecycle_router(checker)

    assert (await checker.check(bot, PERSON)).allowed is False

    session.members[(GROUP, PERSON)] = "member"
    await router.chat_member.trigger(
        cmu(bot=bot, old="left", new="member", who=user(PERSON), by=user(ADMIN))
    )

    assert (await checker.check(bot, PERSON)).allowed is True


async def test_changes_in_an_unrelated_chat_are_ignored() -> None:
    bot, _ = mocked_bot()
    checker = build(groups=(GROUP,))
    router = lifecycle_router(checker)

    await router.chat_member.trigger(
        cmu(
            bot=bot,
            old="left",
            new="member",
            who=user(PERSON),
            by=user(ADMIN),
            in_chat=chat(-100777, "supergroup"),
        )
    )

    assert await checker.store.get_user(PERSON) is None  # type: ignore[union-attr]


async def test_service_messages_are_the_fallback_when_the_bot_is_not_group_admin() -> (
    None
):
    bot, _ = mocked_bot()
    checker = build(groups=(GROUP,))
    router = lifecycle_router(checker)
    joined = message(
        from_user=user(ADMIN),
        in_chat=chat(GROUP, "supergroup"),
        new_chat_members=[user(PERSON, username="newcomer")],
    ).as_(bot)

    await router.message.trigger(joined)

    row = await checker.store.get_user(PERSON)  # type: ignore[union-attr]
    assert row is not None
    assert row.is_member is True
    assert row.username == "newcomer"


async def test_a_leave_notice_marks_the_person_gone() -> None:
    bot, _ = mocked_bot()
    checker = build(groups=(GROUP,))
    router = lifecycle_router(checker)
    left = message(
        from_user=user(PERSON),
        in_chat=chat(GROUP, "supergroup"),
        left_chat_member=user(PERSON),
    ).as_(bot)

    await router.message.trigger(left)

    row = await checker.store.get_user(PERSON)  # type: ignore[union-attr]
    assert row is not None and row.is_member is False


# -------------------------------------------------------------- migration


async def test_a_group_becoming_a_supergroup_moves_the_binding() -> None:
    """The old id is dead; leaving it bound stops access for everyone quietly."""
    bot, _ = mocked_bot()
    checker = build(groups=())
    await checker.bind(GROUP)
    router = lifecycle_router(checker)

    migrated = message(
        from_user=user(ADMIN),
        in_chat=chat(GROUP, "group"),
        migrate_to_chat_id=SUPERGROUP,
    ).as_(bot)
    await router.message.trigger(migrated)

    assert checker.groups == (SUPERGROUP,)
    assert await checker.store.get_bound_groups() == (SUPERGROUP,)  # type: ignore[union-attr]


async def test_a_migration_notice_for_an_unrelated_group_is_ignored() -> None:
    bot, _ = mocked_bot()
    checker = build(groups=(GROUP,))
    router = lifecycle_router(checker)

    migrated = message(
        from_user=user(ADMIN),
        in_chat=chat(-100777, "group"),
        migrate_to_chat_id=SUPERGROUP,
    ).as_(bot)
    await router.message.trigger(migrated)

    assert checker.groups == (GROUP,)


async def test_binding_pulls_the_group_administrators_into_the_roster() -> None:
    bot, session = mocked_bot()
    session.administrators[GROUP] = [
        {
            "status": "creator",
            "user": {"id": 501, "username": "boss", "first_name": "B"},
        }
    ]
    checker = build()
    router = lifecycle_router(checker)

    await router.my_chat_member.trigger(
        cmu(bot=bot, old="left", new="member", who=user(BOT_ID), by=user(ADMIN))
    )

    row = await checker.store.get_user(501)  # type: ignore[union-attr]
    assert row is not None
    assert row.is_group_admin is True
    assert row.is_member is True


async def test_a_nonsense_administrator_payload_does_not_break_the_handler() -> None:
    """This runs from an update handler; a traceback there is a dead bot."""

    class Rubbish:
        async def get_chat_administrators(self, chat_id: int) -> Any:
            return True

    checker = build(groups=(GROUP,))
    assert await checker.sync_admins(Rubbish()) == 0


async def test_the_router_makes_chat_member_a_used_update_type() -> None:
    """``allowed_updates`` is the gotcha; this is the half we can enforce."""
    from aiogram import Dispatcher

    dp = Dispatcher()
    dp.include_router(lifecycle_router(build(groups=(GROUP,))))
    used = dp.resolve_used_update_types()
    assert "chat_member" in used
    assert "my_chat_member" in used

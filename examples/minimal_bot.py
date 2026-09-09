"""A whole bot whose users are the members of a group.

Run it from a clone of the repository:

    pip install -e ".[aiogram]"
    export BOT_TOKEN=...            # from @BotFather
    export AUTH_ADMIN_IDS=...       # your own numeric id, from @userinfobot
    python examples/minimal_bot.py

Then add the bot to a group, as an administrator, and every member of that
group can use it. Nobody has to be granted anything, and no list of user ids
is maintained anywhere.
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from group_auth import AuthConfig, MembershipChecker, SqliteStore, Verdict
from group_auth.aiogram3 import AdminFilter, install, lifecycle_router


def build_routers(checker: MembershipChecker, store: SqliteStore) -> list[Router]:
    """Everything past the gate. By this point access is already decided."""
    common = Router(name="common")
    admin = Router(name="admin")

    @common.message(Command("start"))
    async def start(message: Message, auth: Verdict, is_admin: bool) -> None:
        """``auth`` and ``is_admin`` are injected by the gate — just declare them."""
        who = "an administrator" if is_admin else "a member of the group"
        await message.answer(f"You are in, as {who}. (reason: {auth.reason.value})")

    @common.message()
    async def anything_else(message: Message) -> None:
        await message.answer("I only know /start. Access-wise, you are already in.")

    @admin.message(Command("users"), AdminFilter(checker))
    async def users(message: Message) -> None:
        rows = await store.list_users(members_only=True)
        listing = "\n".join(f"{r.user_id} {r.label}" for r in rows) or "(nobody yet)"
        await message.answer(
            f"Groups: {checker.groups or 'none bound yet'}\n"
            f"Known members: {len(rows)}\n\n"
            f"{listing}\n\n"
            "This roster is only what Telegram volunteered — the Bot API "
            "cannot list a group's membership, so it is never complete."
        )

    # Admin router first: /users must reach it before the catch-all below.
    return [admin, common]


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is not set. Get one from @BotFather.")

    config = AuthConfig.from_env()
    if not config.admin_ids and not config.group_ids:
        logging.warning(
            "AUTH_ADMIN_IDS and AUTH_GROUP_IDS are both empty: nobody can use "
            "the bot, and nobody can bind a group either."
        )

    # SqliteStore keeps the roster and the bound group across restarts.
    # Swap in MemoryStore() if you would rather keep no state at all.
    store = SqliteStore(os.environ.get("AUTH_DB", "auth.db"))
    checker = MembershipChecker(config, store=store)
    await checker.load()

    bot = Bot(token=token)
    dp = Dispatcher()

    # Order matters. The lifecycle router goes first, so a group service
    # message is seen there before anything else looks at it.
    dp.include_router(lifecycle_router(checker))
    install(dp, checker)
    for router in build_routers(checker, store):
        dp.include_router(router)

    try:
        # resolve_used_update_types() is not optional: without it Telegram never
        # sends chat_member updates, and the group lifecycle is invisible with
        # nothing in the logs to say why.
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await store.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

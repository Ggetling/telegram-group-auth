# Integration

Three recipes. The first is ready-made; the other two are short because the
core does not care which framework is above it.

Common to all of them: the gate must be **one** layer that every entry point
passes through. A check written into individual handlers is a check that the
next handler somebody adds will not have.

## aiogram 3

Installed with `pip install "telegram-group-auth[aiogram]"`, or by copying
`src/group_auth/` including the `aiogram3/` subdirectory.

```python
from group_auth import AuthConfig, MembershipChecker, SqliteStore, Verdict
from group_auth.aiogram3 import AdminFilter, install, lifecycle_router

checker = MembershipChecker(AuthConfig.from_env(), store=SqliteStore("auth.db"))
await checker.load()  # restores the bound group

dp.include_router(lifecycle_router(checker))  # first: group joins and leaves
install(dp, checker)  # the gate itself
dp.include_router(your_router)  # your handlers, already guarded

await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
```

`install()` attaches the gate as an **outer** middleware to every observer that
carries a request from a person. Outer, not inner, so somebody without access
is told so even when what they sent matches no handler at all — with an inner
middleware the message would simply disappear.

What your handlers can declare:

```python
@router.message(Command("start"))
async def start(message: Message, auth: Verdict, is_admin: bool) -> None: ...


@router.message(Command("stats"), AdminFilter(checker))
async def stats(message: Message) -> None: ...
```

Your own refusal text, instead of the built-in English one:

```python
async def refuse(event, verdict):
    await event.answer("Нет доступа. Попросите добавить вас в рабочую группу.")


install(dp, checker, on_denied=refuse)
```

A complete working bot is in [../examples/minimal_bot.py](../examples/minimal_bot.py).

## python-telegram-bot

No adapter ships for it, because the whole of one is this. In PTB the way to
stop an update before the handlers see it is a handler in a lower group that
raises `ApplicationHandlerStop`.

```python
from telegram import Update
from telegram.ext import Application, ApplicationHandlerStop, TypeHandler

from group_auth import AuthConfig, MembershipChecker, SqliteStore, explain, user_ref

checker = MembershipChecker(AuthConfig.from_env(), store=SqliteStore("auth.db"))


async def gate(update: Update, context) -> None:
    user = update.effective_user
    if user is None:
        raise ApplicationHandlerStop

    chat = update.effective_chat
    if checker.config.private_only and chat is not None and chat.type != "private":
        raise ApplicationHandlerStop

    verdict = await checker.check(context.bot, user.id, user=user_ref(user))
    if not verdict.allowed:
        if update.effective_message is not None:
            await update.effective_message.reply_text(explain(verdict))
        raise ApplicationHandlerStop

    # Available to every handler below as context.user_data["auth"].
    context.user_data["auth"] = verdict
    context.user_data["is_admin"] = verdict.is_admin


app = Application.builder().token(TOKEN).build()
app.add_handler(TypeHandler(Update, gate), group=-1)  # group=-1: before everything
# ... your handlers in group 0 and above ...
await checker.load()
app.run_polling(allowed_updates=Update.ALL_TYPES)  # or list them, with chat_member
```

`Bot.get_chat_member(chat_id, user_id)` satisfies the `MembershipApi` protocol
as it stands, so `context.bot` can be passed straight in.

For the group lifecycle — binding a group by adding the bot, and revoking
access the moment somebody leaves — add two more handlers:

```python
from telegram.ext import ChatMemberHandler
from group_auth.membership import member_is_allowed


async def bot_added(update, context):
    upd = update.my_chat_member
    if upd.chat.type not in ("group", "supergroup"):
        return
    if upd.new_chat_member.status not in ("member", "administrator", "creator"):
        return
    if checker.is_admin(upd.from_user.id):  # the admin_only policy
        await checker.bind(upd.chat.id)
        await checker.sync_admins(context.bot, upd.chat.id)


async def member_changed(update, context):
    upd = update.chat_member
    if upd.chat.id not in checker.groups:
        return
    allowed, _ = member_is_allowed(upd.new_chat_member)
    await checker.record_membership(user_ref(upd.new_chat_member.user), allowed)


app.add_handler(ChatMemberHandler(bot_added, ChatMemberHandler.MY_CHAT_MEMBER))
app.add_handler(ChatMemberHandler(member_changed, ChatMemberHandler.CHAT_MEMBER))
```

**How far this has been checked.** The core was run against real
python-telegram-bot 22.8 chat-member objects — all seven statuses, including
`restricted` both ways — and read every one correctly (checked 2026-09-09, by
constructing the objects and calling `member_is_allowed`; the transcript is
reproducible from `docs/DESIGN.md`). The handler wiring above has **not** been
run against a live bot. Treat it as a starting point, not a tested integration.

## No framework at all

The core needs one method. Anything with this shape will do, including a
hand-rolled HTTP client:

```python
class MyApi:
    async def get_chat_member(self, chat_id: int, user_id: int):
        r = await self._client.get(
            f"https://api.telegram.org/bot{self._token}/getChatMember",
            params={"chat_id": chat_id, "user_id": user_id},
        )
        r.raise_for_status()
        return r.json()["result"]  # a plain dict is fine
```

Fields are read with `getattr` first and mapping lookup second, so objects and
dicts both work. Then:

```python
verdict = await checker.check(MyApi(), user_id, user=UserRef(user_id, username))
if not verdict.allowed:
    ...
```

Two things to get right if you build the layer yourself:

* **Cover button presses and inline queries, not just messages.** It is enough
  to have received a message with a button once to keep pressing it after
  losing access.
* **Do not let a store error become a refusal.** The roster is bookkeeping; the
  authority is `getChatMember`. The core already swallows store errors, so keep
  that property if you replace it.

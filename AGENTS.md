# Instructions for an agent adding this to a bot

You have been pointed at this repository to give some Telegram bot an
authorization system. This file is the whole recipe. Read it, do the six steps,
stop.

**What the system does.** Whoever is in a Telegram group is a user of the bot.
Administrators are configured separately and always get in. There is no list of
allowed users to maintain: the bot asks Telegram whether the person writing to
it is in the group, and caches the answer.

**The one thing you must not promise.** The Bot API has no method that lists a
group's members. `getChatAdministrators` returns administrators only,
`getChatMemberCount` returns a number. So you cannot enumerate the bot's users,
pre-load them, or broadcast to all of them reliably. The roster in this package
holds only who Telegram has volunteered so far and is documented as partial.
If the task you were given depends on a complete user list, say so instead of
building something that looks like one.

## Step 1. Decide how to take the code

| | When | How |
|---|---|---|
| Install | the bot has a `requirements.txt` or `pyproject.toml` | add `telegram-group-auth[aiogram] @ git+https://github.com/OWNER/telegram-group-auth` |
| Copy | vendoring is the house style, or dependencies are frozen | copy `src/group_auth/` into the bot's source tree, unchanged |

Copying works because every import inside the package is relative and the core
has no dependencies at all. Do not copy individual files: `membership.py` needs
`config.py`, `models.py`, `protocols.py` and `_time.py`.

Drop `group_auth/aiogram3/` if the bot is not on aiogram, and see
[docs/INTEGRATION.md](docs/INTEGRATION.md) for the other frameworks.

## Step 2. Wire it in (aiogram 3)

Six lines into wherever the bot builds its `Dispatcher`:

```python
from group_auth import AuthConfig, MembershipChecker, SqliteStore
from group_auth.aiogram3 import install, lifecycle_router

checker = MembershipChecker(AuthConfig.from_env(), store=SqliteStore("auth.db"))
await checker.load()
dp.include_router(lifecycle_router(checker))  # before the bot's own routers
install(dp, checker)  # the gate, on every entry point
```

Then start polling like this, **not** with a bare `start_polling(bot)`:

```python
await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
```

Use `MemoryStore()` instead of `SqliteStore` if the bot should keep no state;
everything works, but the roster and the bound group are lost on restart.

## Step 3. Do not add per-handler checks

`install()` puts the gate on every observer that carries a request — messages,
edited messages, callback queries, inline queries, payments, poll answers. A
handler that runs has already been authorized. Adding your own check inside a
handler is not extra safety, it is a second place to get it wrong.

For a command only administrators may run, use the filter:

```python
from group_auth.aiogram3 import AdminFilter


@router.message(Command("stats"), AdminFilter(checker))
async def stats(message: Message) -> None: ...
```

Handlers can declare `auth: Verdict` and `is_admin: bool` as arguments and
aiogram will inject them.

## Step 4. Set the environment variables

Only two matter:

| Variable | Meaning |
|---|---|
| `AUTH_ADMIN_IDS` | numeric Telegram ids, comma-separated. Unconditional access. Required in practice — see the warning below |
| `AUTH_GROUP_IDS` | groups whose members are users. Optional: leave empty and bind by adding the bot to a group |

Optional, with their defaults: `AUTH_CACHE_TTL=300`, `AUTH_DENY_CACHE_TTL=60`,
`AUTH_GRACE=900`, `AUTH_PRIVATE_ONLY=1`, `AUTH_BIND_ON_ADD=admin_only`.

`AuthConfig.from_env(prefix="MYBOT_")` renames all of them at once if the host
bot's variables are namespaced.

**With `AUTH_ADMIN_IDS` empty and `AUTH_GROUP_IDS` empty, nobody can use the
bot and nobody can bind a group.** The default `bind_on_add=admin_only` only
lets an administrator hand a group the power to grant access; that is
deliberate, because otherwise anyone who adds the bot to a group of their own
has just created accounts for their own people.

## Step 5. Tell the operator what to do

Three things, and they cannot be done from code:

1. Put their own numeric id (from `@userinfobot`) in `AUTH_ADMIN_IDS`.
2. Add the bot to the group, **as an administrator of that group**. Without
   group-admin rights Telegram never sends `chat_member` updates, so joins and
   departures are only seen through service messages — the bot still works,
   just with a lazier roster.
3. Write to the bot in a private chat once. A bot cannot message somebody who
   has never opened the dialogue.

## Step 6. Check it

```bash
pip install -e ".[dev]" && python -m pytest -q && ruff check . && mypy
```

In the bot itself, the behaviour to confirm is: a second account that is in the
group can write to the bot without anybody granting it anything, and loses
access the moment it is removed from the group.

## What this does not do

Not roles, not per-command permissions beyond the admin/everyone split, not a
ban list, not rate limits, not accounting of who did what. If the task asks for
those, build them on top — `is_admin` and the roster are there — but do not
report them as done because this was installed.

## Where the rest is

* [README.md](README.md) — the same thing for a human, plus the design in brief.
* [docs/INTEGRATION.md](docs/INTEGRATION.md) — python-telegram-bot and
  framework-free recipes.
* [docs/DESIGN.md](docs/DESIGN.md) — why the gate behaves this way, the threat
  model, and an explicit list of what has and has not been verified.

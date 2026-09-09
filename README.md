# telegram-group-auth

Authorization for a Telegram bot by membership in a group. Add the bot to a
group and everyone in that group is a user of it. Administrators are configured
separately and always get in.

No list of allowed users to keep. Nobody grants anybody anything. Somebody
joins the group and the bot works for them; somebody is removed and it stops.

* **Core has no dependencies** and no knowledge of any bot framework.
* **aiogram 3 adapter** included: two lines to install the gate, one router for
  the group lifecycle.
* **The roster is optional** — in memory by default, SQLite if you want it to
  survive a restart.
* 93 tests, no network and no token needed to run them.

Written for agents as much as for people: [AGENTS.md](AGENTS.md) is a recipe
for dropping this into an existing bot.

## The limit you need to know before anything else

**A bot cannot list a group's members.** The Bot API has no such method —
`getChatAdministrators` returns administrators only, `getChatMemberCount`
returns a number. So membership is checked one person at a time, at the moment
they write, and cached:

```
person writes  ->  in admin_ids?          -> yes: in, always
                   ->  no  ->  cached verdict?  -> yes: use it
                                -> no  ->  getChatMember(group, person)
                                            -> member: in, cache 5 min
                                            -> not:    out, cache 1 min
                                            -> cannot ask: last good check
                                               within 15 min? in, else out
```

For the person this is exactly the behaviour asked for. What it does not give
you is a list of your users: the roster in this package is only who Telegram
has volunteered so far, and it is never complete. Do not build a broadcast on
it and call it complete.

## Quick start

```bash
pip install "telegram-group-auth[aiogram] @ git+https://github.com/OWNER/telegram-group-auth"
export BOT_TOKEN=...          # from @BotFather
export AUTH_ADMIN_IDS=...     # your numeric id, from @userinfobot
python examples/minimal_bot.py
```

Add the bot to a group as an administrator of that group. Every member can now
write to it.

In an existing aiogram 3 bot:

```python
from group_auth import AuthConfig, MembershipChecker, SqliteStore
from group_auth.aiogram3 import install, lifecycle_router

checker = MembershipChecker(AuthConfig.from_env(), store=SqliteStore("auth.db"))
await checker.load()

dp.include_router(lifecycle_router(checker))  # first, before your own routers
install(dp, checker)  # the gate, on every entry point
dp.include_router(your_router)

# Not optional: without it Telegram never sends chat_member updates.
await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
```

Handlers get the decision injected:

```python
@router.message(Command("start"))
async def start(message: Message, auth: Verdict, is_admin: bool) -> None: ...
```

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `AUTH_ADMIN_IDS` | empty | Numeric ids of the bot's administrators. Unconditional access, no group needed, and the only identity that may bind a group |
| `AUTH_GROUP_IDS` | empty | Groups whose members are users. Membership in any one is enough. Leave empty to bind by adding the bot to a group |
| `AUTH_CACHE_TTL` | `300` | Seconds an approval is reused |
| `AUTH_DENY_CACHE_TTL` | `60` | Seconds a refusal is reused. Short on purpose: the usual fix for a refusal is being added to the group seconds later |
| `AUTH_GRACE` | `900` | Seconds to keep trusting a person's last real check while Telegram is unreachable |
| `AUTH_PRIVATE_ONLY` | `1` | Ignore anything that is not a private chat. The bot sits in the group to be the roster, not to talk |
| `AUTH_BIND_ON_ADD` | `admin_only` | `never`, `admin_only`, `first`, or `always` — who may bind a group by adding the bot |

`AuthConfig.from_env(prefix="MYBOT_")` renames the lot.

## Two things that fail silently

* **`chat_member` updates require the bot to be an administrator of the
  group.** Without that, joins and departures are seen only through service
  messages ("X joined"), which is a slower but working fallback.
* **`chat_member` is not in `allowed_updates` by default.** Start polling with
  `allowed_updates=dp.resolve_used_update_types()` or the group lifecycle is
  invisible and nothing in the logs will say why.

## Security notes worth reading once

* `bind_on_add=admin_only` is the default because anyone who could bind a group
  could grant access to whoever they like. Changing it to `always` hands that
  power to anyone able to add your bot to a chat.
* Administrators bypass everything, including a broken Telegram API. That is
  the point — somebody has to be able to fix the bot when it is broken — but it
  means `AUTH_ADMIN_IDS` is as sensitive as the bot token.
* A person removed from the group keeps access for up to `AUTH_CACHE_TTL`
  unless the bot is a group administrator, in which case the `chat_member`
  update revokes it at once. Set `AUTH_CACHE_TTL` lower if that window matters
  more to you than the API calls it saves.
* The grace window is a deliberate fail-open. `AUTH_GRACE=0` turns it off and
  makes an unreachable Telegram mean nobody but administrators gets in.

The reasoning behind each of these is in [docs/DESIGN.md](docs/DESIGN.md),
along with what has actually been verified and what has not.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q     # no network, no token
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

## Licence

MIT.

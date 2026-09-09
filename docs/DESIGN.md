# Design, limits, and what has actually been checked

Why this is shaped the way it is, what it does not protect against, and — kept
separate on purpose — the difference between what was measured and what was
merely reasoned about.

## The constraint everything follows from

A bot cannot list a group's members. The Bot API offers
`getChatAdministrators` (administrators only), `getChatMemberCount` (a number)
and `getChatMember` (one person, one chat). There is no method that enumerates
membership.

So "everyone in the group is a user of the bot" cannot be implemented by
loading a list. It is implemented by asking about one person at the moment they
write. For the person the result is identical: join the group and the bot
works. Two consequences are not identical, and both are load-bearing:

* **The user list does not exist.** The roster here is whatever Telegram
  volunteered — a join notice, an administrator sync, somebody's first
  message — and it will silently omit anyone who joined the group before the
  bot did and has never written. Do not build a broadcast on it and describe it
  as reaching everybody.
* **Every first contact costs an API call.** Hence the cache, and hence the
  cache's failure modes below.

## How the gate decides

In order, stopping at the first answer:

| | Condition | Verdict | Cached |
|---|---|---|---|
| 1 | in `admin_ids` | in, `admin` | no need |
| 2 | no group configured or bound | out, `no_group` | no |
| 3 | a cached verdict that has not expired | as cached, `cached` | — |
| 4 | `getChatMember` says creator / administrator / member, or restricted with `is_member` | in, `member` | `cache_ttl`, 300 s |
| 5 | `getChatMember` says left / kicked, or errors with an absence marker | out, `not_member` | `deny_cache_ttl`, 60 s |
| 6 | could not ask, and a real check succeeded within `grace` | in, `grace` | no |
| 7 | could not ask, and it did not | out, `api_error` | no |

Four decisions inside that worth stating outright.

**Administrators bypass everything, including a dead API.** Whoever has to
repair the bot must be able to reach it exactly when everything else has fallen
over. The price is that `AUTH_ADMIN_IDS` is as sensitive as the bot token.

**A refusal is cached for a fifth as long as an approval.** The overwhelmingly
common reason for a refusal is "not in the group yet", and the overwhelmingly
common fix is being added seconds later. A refusal cached for five minutes
means a new colleague knocking on a door that is already unlocked.

**An unknown is never cached, and never becomes a "no" that sticks.** If any
configured group failed to answer, "not a member" is not a fact. It is not
written to the cache, and it is not written to the roster.

**The grace window is a deliberate fail-open,** and the one thing here somebody
might reasonably want switched off. Someone else's network hiccup should not
switch the bot off for everybody, so a person verified within the last
`grace` seconds keeps working while Telegram is unreachable. It cannot resurrect
a refusal: only a *successful positive* check arms it. `AUTH_GRACE=0` disables
it, and then an unreachable Telegram means administrators only.

## Threat model

What an attacker gets, honestly.

| Situation | Exposure |
|---|---|
| Removed from the group | Keeps access up to `cache_ttl` (300 s by default). If the bot is a group administrator, the `chat_member` update revokes it immediately instead — this is the main reason to make the bot a group admin. A reported departure also closes that person's grace window, so an outage straight afterwards does not hand the fifteen minutes back |
| Never in the group | Nothing. Refused, and the refusal is cached |
| Can add the bot to a group they control | Nothing under the default `bind_on_add=admin_only`; the binding is refused and logged. Under `always` they have just granted their own group access — do not set that unless the bot is only ever added by people you trust |
| Group turns into a supergroup | The chat id changes. The lifecycle router moves the binding; without it, access would quietly stop for everyone but administrators |
| Bot removed from the bound group | The binding is deliberately kept, so re-adding the bot restores access. Meanwhile every check fails: grace covers the first 15 minutes, then nobody but administrators is in |
| Telegram unreachable | Everyone verified in the last `grace` seconds keeps working. Nobody new gets in |
| Roster database corrupted or deleted | No effect on access at all. The authority is `getChatMember`; the store is bookkeeping, and every store error is swallowed and logged |
| Another local account on the host | The roster file is created `0600`, and an existing one is narrowed to `0600` on open. Before that it was `0644` — every local account could read the names and usernames of everybody the bot had seen |
| Creates their own group containing the bot | Nothing. The gate used to wave through the `group_chat_created` service message, which no handler consumed, so it carried on to the bot's own routers with no membership check and no `auth` in the handler data |
| Group with "hide members" enabled, or an anonymous admin | **unknown** — see below |

Not addressed, by design: roles beyond admin/everyone, per-command
permissions, ban lists, rate limits, and any record of who did what.

## Storage

The roster is optional and behind a protocol, with two implementations that are
run through the same body of tests — two implementations of one interface drift
apart the moment they get separate tests, and the drift shows up as "it worked
in memory".

`SqliteStore` is stdlib `sqlite3`: one long-lived connection,
`check_same_thread=False`, one lock, every public method `async` handing the
blocking call to a thread. Cheaper than an async SQLite dependency on a small
machine.

### The roster is personal data

It holds a telegram id, a username and a name for everybody the bot has seen,
so two things follow.

`SqliteStore` sets the file — and its `-wal` and `-shm` companions, which carry
the same rows until a checkpoint — to `0600` on open, an existing database
included. `sqlite3` would otherwise create it `0644` minus the umask, readable
by every account on the host. Pass `file_mode=None` to manage the permissions
yourself.

`MembershipChecker.erase_user(id)` deletes the row and the cached verdict
together; a deletion that leaves a verdict cached is half a deletion. It is
built on `AuthStore.forget_user`, the one store method the gate itself never
calls, and it is the only place in the package that lets a store error
propagate instead of swallowing it — elsewhere the rule is that a broken store
must not become a refusal, but a deletion that quietly failed must not look
like one that worked. Nothing here expires rows on its own: how long a bot
keeps its roster is the bot's decision, not the library's.

**Schema changes must be additive and go in a new table.** The schema is all
`CREATE TABLE IF NOT EXISTS` and contains no `ALTER`. A column added to an
existing table will never appear in a database that already exists on
somebody's server, and the failure is silent — the code expects a column the
file does not have. Add a side table instead, and check that the previous
version of the host bot still runs against the changed file, or a rollback
becomes impossible exactly when it is needed. A test asserts the absence of
`ALTER`, so this rule cannot rot quietly.

## Verified

Everything in this section was run on 2026-09-09. The command is given so it
can be re-run rather than believed.

| What | How | Result |
|---|---|---|
| The access logic: every status, cache expiry both ways, grace in both directions, immediate revocation, multiple groups, partial API failure | `python -m pytest -q`, 108 tests, injected clock, no network | passes |
| Both stores behave identically | one parametrized test body over `MemoryStore` and `SqliteStore` | passes |
| SQLite survives a restart | reopen the file in a second store, read back roster and binding | passes |
| The gate covers every entry point | asserts `install()` attached to each of `GATED_OBSERVERS` and to none of `UNGATED_OBSERVERS` | passes |
| `chat_member` reaches `allowed_updates` | `dp.resolve_used_update_types()` after including the lifecycle router returns `chat_member`, `my_chat_member`, `message` | passes |
| The core needs no bot framework | `pip install .` into an empty venv, import `group_auth`, assert `aiogram` is absent | passes |
| The core reads a framework's objects, not just its own | a test passes a plain `dict`; separately, all seven real `python-telegram-bot` 22.8 `ChatMember` subclasses were constructed and read correctly, `restricted` both ways | passes |
| Types and style | `mypy` (strict, 13 files), `ruff check`, `ruff format --check` | clean |
| aiogram version | 3.31.0 on Python 3.13.5 locally | passes |
| Python 3.10, 3.11, 3.12, 3.13 | the CI matrix, run 34368856233 on 2026-09-09 — tests, lint, types and the bare-core import on each | passes on all four, for commit f7ca064, the security fixes included |

## Not verified

Written down rather than guessed at. If you resolve one, please replace the row
with a measurement and the date.

| | Question | How it gets resolved |
|---|---|---|
| В-1 | **Nothing here has ever talked to Telegram.** No live bot, no real group, no real token — every test runs against a scripted double | the manual checklist below, run once by a person with a throwaway bot |
| В-2 | Does `getChatMember` return status `left`, or a 400, for somebody who was never in the chat? The absence markers in `NOT_MEMBER_MARKERS` are a **guess** assembled from reported error texts | one live call for a stranger's id. Getting it wrong is not dangerous: an unmatched error becomes `api_error`, which also refuses — it just costs an API call per message from an outsider |
| В-3 | Behaviour on a group with "hide members" enabled, and for an anonymous administrator posting as the channel | a live group with those settings |
| В-4 | Whether `getChatMember` behaves the same way for a channel as for a supergroup. Channels are not a supported use here, but nothing rejects a channel id either | a live channel |
| В-5 | The python-telegram-bot handler wiring in [INTEGRATION.md](INTEGRATION.md) has not been run end to end; only the core's reading of PTB objects was checked | somebody running it against a live bot |
| В-6 | Behaviour under Telegram rate limiting (429) when many strangers write at once. A 429 is not an absence marker, so it becomes `api_error`, which is the safe direction — but the retry behaviour is untested | a load test, or a live incident |

## The manual check that closes В-1

Fifteen minutes with a throwaway bot. This is the only thing that confirms the
actual requirement.

1. Create a bot with `@BotFather`; get your own numeric id from `@userinfobot`.
2. `cp examples/.env.example .env`, fill in `BOT_TOKEN` and `AUTH_ADMIN_IDS`.
3. `python examples/minimal_bot.py`, then write to the bot privately — you are
   let in as an administrator.
4. Create a group, add the bot **as an administrator of the group**, add a
   second account to the group.
5. From the second account, write to the bot privately. **It should be let in,
   with nobody having granted it anything.** This is the requirement.
6. Remove the second account from the group. It should be refused on its next
   message, immediately rather than in five minutes.
7. Delete the throwaway bot in `@BotFather`.

Record the outcome here with the date, and move each row it settles out of
"Not verified".

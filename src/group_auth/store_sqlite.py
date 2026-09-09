"""The roster in a SQLite file. Standard library only, no extra dependency.

Shape of the thing: one long-lived connection, ``check_same_thread=False``, one
lock around it, and every public method is ``async`` that hands the blocking
call to a worker thread. That is cheaper than adding an async SQLite driver to
a bot that may be running on a very small machine, and it is the same pattern
that has been serving a production bot in the project this was extracted from.

**Schema changes must be additive, and go in a new table.** Everything below is
``CREATE TABLE IF NOT EXISTS``; there is no ``ALTER``. A new column added to an
existing table here will simply never appear in a database that already exists
on someone's server, and the failure is silent. Add a side table instead, and
check that the previous version of your bot still runs against the new file —
otherwise a rollback becomes impossible exactly when it is needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ._time import utcnow_iso
from .models import UserRecord, UserRef

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_users (
    user_id        INTEGER PRIMARY KEY,
    username       TEXT,
    first_name     TEXT,
    last_name      TEXT,
    -- NULL means "never checked", which is not the same as "not a member".
    is_member      INTEGER,
    -- Administrator of the Telegram group. NOT an administrator of the bot;
    -- that list lives in configuration and nowhere else.
    is_group_admin INTEGER NOT NULL DEFAULT 0,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS auth_users_member ON auth_users(is_member);

CREATE TABLE IF NOT EXISTS auth_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_BOUND_KEY = "bound_groups"
_VERSION_KEY = "schema_version"


def _record(row: sqlite3.Row) -> UserRecord:
    member = row["is_member"]
    return UserRecord(
        user_id=row["user_id"],
        username=row["username"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        is_member=None if member is None else bool(member),
        is_group_admin=bool(row["is_group_admin"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        source=row["source"],
    )


class SqliteStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if self._path.parent != Path(""):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT INTO auth_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                (_VERSION_KEY, SCHEMA_VERSION),
            )
            self._conn.commit()

    # ------------------------------------------------------------- plumbing

    def _write(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

    def _read(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, args))

    # ---------------------------------------------------------------- users

    def _remember_user(self, user: UserRef, source: str) -> None:
        now = utcnow_iso()
        self._write(
            """
            INSERT INTO auth_users(
                user_id, username, first_name, last_name,
                first_seen, last_seen, source
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                -- COALESCE, not plain assignment: an event that carries no
                -- username must not erase the one already recorded.
                username   = COALESCE(excluded.username,   auth_users.username),
                first_name = COALESCE(excluded.first_name, auth_users.first_name),
                last_name  = COALESCE(excluded.last_name,  auth_users.last_name),
                last_seen  = excluded.last_seen
            """,
            (
                user.user_id,
                user.username,
                user.first_name,
                user.last_name,
                now,
                now,
                source,
            ),
        )

    async def remember_user(self, user: UserRef, *, source: str) -> None:
        await asyncio.to_thread(self._remember_user, user, source)

    def _set_flag(self, user_id: int, column: str, value: bool) -> None:
        now = utcnow_iso()
        # The column name is never user input: both call sites pass a literal.
        self._write(
            f"""
            INSERT INTO auth_users(user_id, {column}, first_seen, last_seen, source)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                {column} = excluded.{column},
                last_seen = excluded.last_seen
            """,
            (user_id, int(value), now, now, column),
        )

    async def set_membership(self, user_id: int, member: bool) -> None:
        await asyncio.to_thread(self._set_flag, user_id, "is_member", member)

    async def set_group_admin(self, user_id: int, is_group_admin: bool) -> None:
        await asyncio.to_thread(
            self._set_flag, user_id, "is_group_admin", is_group_admin
        )

    def _get_user(self, user_id: int) -> UserRecord | None:
        rows = self._read("SELECT * FROM auth_users WHERE user_id = ?", (user_id,))
        return _record(rows[0]) if rows else None

    async def get_user(self, user_id: int) -> UserRecord | None:
        return await asyncio.to_thread(self._get_user, user_id)

    def _list_users(self, members_only: bool) -> list[UserRecord]:
        sql = "SELECT * FROM auth_users"
        if members_only:
            sql += " WHERE is_member = 1"
        sql += " ORDER BY user_id"
        return [_record(row) for row in self._read(sql)]

    async def list_users(self, *, members_only: bool = False) -> list[UserRecord]:
        return await asyncio.to_thread(self._list_users, members_only)

    # --------------------------------------------------------------- binding

    def _get_bound_groups(self) -> tuple[int, ...]:
        rows = self._read("SELECT value FROM auth_meta WHERE key = ?", (_BOUND_KEY,))
        if not rows:
            return ()
        raw = rows[0]["value"].strip()
        if not raw:
            return ()
        return tuple(int(part) for part in raw.split(",") if part)

    async def get_bound_groups(self) -> tuple[int, ...]:
        return await asyncio.to_thread(self._get_bound_groups)

    def _set_bound_groups(self, group_ids: Sequence[int]) -> None:
        value = ",".join(str(g) for g in dict.fromkeys(group_ids))
        self._write(
            "INSERT INTO auth_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_BOUND_KEY, value),
        )

    async def set_bound_groups(self, group_ids: Sequence[int]) -> None:
        await asyncio.to_thread(self._set_bound_groups, group_ids)

    # ----------------------------------------------------------------- close

    def _close(self) -> None:
        with self._lock:
            self._conn.close()

    async def close(self) -> None:
        await asyncio.to_thread(self._close)

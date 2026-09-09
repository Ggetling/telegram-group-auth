"""Both stores are run through the same body of tests.

Two implementations of one protocol drift apart the moment they have separate
tests, and the drift shows up as "it worked in memory".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from group_auth import MemoryStore, SqliteStore, UserRef
from group_auth.protocols import AuthStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AuthStore]:
    if request.param == "memory":
        made: AuthStore = MemoryStore()
    else:
        made = SqliteStore(tmp_path / "roster.db")
    yield made


async def test_a_new_person_is_recorded(store: AuthStore) -> None:
    await store.remember_user(
        UserRef(7, username="seven", first_name="S"), source="contact"
    )
    row = await store.get_user(7)
    assert row is not None
    assert row.username == "seven"
    assert row.is_member is None  # never checked is not the same as not a member
    assert row.first_seen and row.last_seen


async def test_a_later_event_without_a_name_does_not_erase_the_name(
    store: AuthStore,
) -> None:
    await store.remember_user(
        UserRef(7, username="seven", first_name="S"), source="contact"
    )
    await store.remember_user(UserRef(7), source="join")
    row = await store.get_user(7)
    assert row is not None
    assert row.username == "seven"
    assert row.first_name == "S"


async def test_membership_can_be_set_before_the_person_is_known(
    store: AuthStore,
) -> None:
    await store.set_membership(9, True)
    row = await store.get_user(9)
    assert row is not None and row.is_member is True
    await store.set_membership(9, False)
    row = await store.get_user(9)
    assert row is not None and row.is_member is False


async def test_listing_members_only(store: AuthStore) -> None:
    await store.remember_user(UserRef(1), source="contact")
    await store.set_membership(2, True)
    await store.set_membership(3, False)
    assert [r.user_id for r in await store.list_users()] == [1, 2, 3]
    assert [r.user_id for r in await store.list_users(members_only=True)] == [2]


async def test_group_admin_flag_is_separate_from_membership(store: AuthStore) -> None:
    await store.set_group_admin(4, True)
    row = await store.get_user(4)
    assert row is not None
    assert row.is_group_admin is True
    assert row.is_member is None


async def test_unknown_person_is_none(store: AuthStore) -> None:
    assert await store.get_user(12345) is None


async def test_bound_groups_round_trip(store: AuthStore) -> None:
    assert await store.get_bound_groups() == ()
    await store.set_bound_groups([-100, -200, -100])
    assert await store.get_bound_groups() == (-100, -200)
    await store.set_bound_groups([])
    assert await store.get_bound_groups() == ()


async def test_close_is_safe(store: AuthStore) -> None:
    await store.close()


async def test_sqlite_survives_a_restart(tmp_path: Path) -> None:
    """The one behaviour the memory store cannot have, so it gets its own test."""
    path = tmp_path / "roster.db"
    first = SqliteStore(path)
    await first.remember_user(UserRef(7, username="seven"), source="contact")
    await first.set_membership(7, True)
    await first.set_bound_groups([-1001234567890])
    await first.close()

    second = SqliteStore(path)
    row = await second.get_user(7)
    assert row is not None
    assert row.username == "seven"
    assert row.is_member is True
    assert await second.get_bound_groups() == (-1001234567890,)
    await second.close()


async def test_sqlite_creates_missing_directories(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "state" / "nested" / "roster.db")
    await store.remember_user(UserRef(1), source="contact")
    assert (tmp_path / "state" / "nested" / "roster.db").exists()
    await store.close()


async def test_sqlite_schema_is_additive_only() -> None:
    """A guard, not a formality: an ALTER here would never reach a live file."""
    from group_auth.store_sqlite import SCHEMA

    upper = SCHEMA.upper()
    assert "ALTER" not in upper
    assert upper.count("CREATE TABLE") == upper.count("CREATE TABLE IF NOT EXISTS")

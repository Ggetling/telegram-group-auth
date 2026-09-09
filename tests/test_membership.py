from __future__ import annotations

import pytest

from fakes import Boom, Clock, FakeApi
from group_auth import AuthConfig, MembershipChecker, MemoryStore, Reason, UserRef

GROUP = -1001234567890
OTHER_GROUP = -1009999999999
ADMIN = 111
PERSON = 222


def build(
    *,
    groups: tuple[int, ...] = (GROUP,),
    admins: frozenset[int] = frozenset({ADMIN}),
    store: MemoryStore | None = None,
    **kwargs: float,
) -> tuple[MembershipChecker, Clock]:
    clock = Clock()
    cfg = AuthConfig(admin_ids=admins, group_ids=groups, **kwargs)  # type: ignore[arg-type]
    return MembershipChecker(cfg, store=store, clock=clock), clock


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        ("creator", True),
        ("administrator", True),
        ("member", True),
        ("left", False),
        ("kicked", False),
        ("", False),
    ],
)
async def test_every_status_maps_to_a_decision(status: str, allowed: bool) -> None:
    checker, _ = build()
    api = FakeApi({(GROUP, PERSON): status})
    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is allowed
    assert verdict.reason is (Reason.MEMBER if allowed else Reason.NOT_MEMBER)


@pytest.mark.parametrize("is_member", [True, False])
async def test_restricted_depends_on_is_member(is_member: bool) -> None:
    """A restricted user who has left carries the same status as one who has not."""
    checker, _ = build()
    api = FakeApi({(GROUP, PERSON): {"status": "restricted", "is_member": is_member}})
    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is is_member


async def test_a_plain_dict_works_like_a_framework_object() -> None:
    """This is the whole claim of being framework-agnostic, so it gets a test."""

    class Model:
        status = "member"

    checker, _ = build()
    api = FakeApi({(GROUP, PERSON): {"status": "member"}})
    from_dict = await checker.check(api, PERSON)
    checker.forget()
    from_object = await checker.check(FakeApi({(GROUP, PERSON): Model()}), PERSON)
    assert from_dict.allowed is True
    assert from_object.allowed is True


async def test_admin_gets_in_with_no_group_configured() -> None:
    checker, _ = build(groups=())
    verdict = await checker.check(FakeApi(), ADMIN)
    assert verdict.allowed is True
    assert verdict.reason is Reason.ADMIN
    assert verdict.is_admin is True


async def test_admin_gets_in_while_telegram_is_down() -> None:
    """The point of the unconditional rule: repairs happen when things are broken."""
    checker, _ = build()
    api = FakeApi(broken=True)
    verdict = await checker.check(api, ADMIN)
    assert verdict.allowed is True
    assert api.calls == []  # not even asked


async def test_without_a_group_nobody_else_gets_in() -> None:
    checker, _ = build(groups=())
    verdict = await checker.check(FakeApi(), PERSON)
    assert verdict.allowed is False
    assert verdict.reason is Reason.NO_GROUP


async def test_a_positive_verdict_is_not_asked_twice() -> None:
    checker, clock = build(cache_ttl=300)
    api = FakeApi({(GROUP, PERSON): "member"})
    assert (await checker.check(api, PERSON)).reason is Reason.MEMBER
    assert (await checker.check(api, PERSON)).reason is Reason.CACHED
    assert len(api.calls) == 1
    clock.advance(301)
    assert (await checker.check(api, PERSON)).reason is Reason.MEMBER
    assert len(api.calls) == 2


async def test_a_refusal_expires_sooner_than_an_approval() -> None:
    """Somebody just added to the group must not keep hitting a cached refusal."""
    checker, clock = build(cache_ttl=300, deny_cache_ttl=60)
    api = FakeApi({(GROUP, PERSON): "left"})
    assert (await checker.check(api, PERSON)).reason is Reason.NOT_MEMBER
    clock.advance(30)
    assert (await checker.check(api, PERSON)).reason is Reason.CACHED
    clock.advance(31)  # past deny_cache_ttl, far short of cache_ttl
    api.members[(GROUP, PERSON)] = "member"
    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is True
    assert verdict.reason is Reason.MEMBER


async def test_grace_covers_somebody_verified_moments_ago() -> None:
    checker, clock = build(cache_ttl=10, grace=900)
    api = FakeApi({(GROUP, PERSON): "member"})
    assert (await checker.check(api, PERSON)).allowed is True

    api.broken = True
    clock.advance(11)  # cache gone, grace still open
    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is True
    assert verdict.reason is Reason.GRACE
    assert checker.api_broken is True

    clock.advance(900)
    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is False
    assert verdict.reason is Reason.API_ERROR


async def test_grace_does_not_resurrect_somebody_who_was_refused() -> None:
    checker, clock = build(cache_ttl=10, deny_cache_ttl=10, grace=900)
    api = FakeApi({(GROUP, PERSON): "left"})
    assert (await checker.check(api, PERSON)).allowed is False
    api.broken = True
    clock.advance(11)
    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is False
    assert verdict.reason is Reason.API_ERROR


async def test_an_unknown_answer_is_never_cached_as_a_refusal() -> None:
    checker, _ = build()
    api = FakeApi(broken=True)
    assert (await checker.check(api, PERSON)).reason is Reason.API_ERROR
    api.broken = False
    api.members[(GROUP, PERSON)] = "member"
    # If the failure had been cached, this would come back as CACHED and False.
    assert (await checker.check(api, PERSON)).reason is Reason.MEMBER


async def test_forget_takes_access_away_at_once() -> None:
    checker, _ = build(cache_ttl=3600)
    api = FakeApi({(GROUP, PERSON): "member"})
    assert (await checker.check(api, PERSON)).allowed is True
    api.members[(GROUP, PERSON)] = "kicked"
    assert (await checker.check(api, PERSON)).reason is Reason.CACHED  # still in
    checker.forget(PERSON)
    assert (await checker.check(api, PERSON)).allowed is False


async def test_membership_in_any_one_group_is_enough() -> None:
    checker, _ = build(groups=(GROUP, OTHER_GROUP))
    api = FakeApi({(OTHER_GROUP, PERSON): "member"})
    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is True
    assert verdict.group_id == OTHER_GROUP
    assert len(api.calls) == 2


async def test_one_unreachable_group_makes_the_answer_unknown_not_no() -> None:
    """With a group unaccounted for, "not a member" is not a fact."""
    checker, _ = build(groups=(GROUP, OTHER_GROUP), grace=0)
    api = FakeApi({(GROUP, PERSON): Boom("down"), (OTHER_GROUP, PERSON): "left"})
    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is False
    assert verdict.reason is Reason.API_ERROR


async def test_binding_a_group_at_runtime_and_dropping_it() -> None:
    checker, _ = build(groups=())
    store = MemoryStore()
    checker.store = store
    await checker.bind(GROUP)
    assert checker.groups == (GROUP,)
    assert await store.get_bound_groups() == (GROUP,)

    api = FakeApi({(GROUP, PERSON): "member"})
    assert (await checker.check(api, PERSON)).allowed is True

    await checker.unbind(GROUP)
    assert (await checker.check(api, PERSON)).reason is Reason.NO_GROUP


async def test_a_bound_group_survives_a_restart() -> None:
    store = MemoryStore()
    first, _ = build(groups=(), store=store)
    await first.bind(GROUP)

    second, _ = build(groups=(), store=store)
    assert second.groups == ()
    await second.load()
    assert second.groups == (GROUP,)


async def test_rebinding_replaces_and_clears_the_cache() -> None:
    checker, _ = build(groups=())
    await checker.bind(GROUP)
    api = FakeApi({(GROUP, PERSON): "member"})
    assert (await checker.check(api, PERSON)).allowed is True
    await checker.bind(OTHER_GROUP, replace=True)
    assert checker.groups == (OTHER_GROUP,)
    assert (await checker.check(api, PERSON)).allowed is False


async def test_the_roster_records_what_the_gate_learns() -> None:
    store = MemoryStore()
    checker, _ = build(store=store)
    api = FakeApi({(GROUP, PERSON): "member"})
    from group_auth import UserRef

    await checker.check(api, PERSON, user=UserRef(PERSON, username="someone"))
    record = await store.get_user(PERSON)
    assert record is not None
    assert record.is_member is True
    assert record.label == "@someone"


async def test_a_broken_store_cannot_turn_into_a_refusal() -> None:
    class BrokenStore(MemoryStore):
        async def remember_user(self, user, *, source):  # type: ignore[no-untyped-def]
            raise RuntimeError("disk on fire")

        async def set_membership(self, user_id, member):  # type: ignore[no-untyped-def]
            raise RuntimeError("disk on fire")

    from group_auth import UserRef

    checker, _ = build(store=BrokenStore())
    api = FakeApi({(GROUP, PERSON): "member"})
    verdict = await checker.check(api, PERSON, user=UserRef(PERSON))
    assert verdict.allowed is True


async def test_sync_admins_copies_the_one_list_telegram_gives() -> None:
    store = MemoryStore()
    checker, _ = build(store=store)
    api = FakeApi(
        admins={
            GROUP: [
                {"user": {"id": 501, "username": "boss", "first_name": "B"}},
                {"user": {"id": 502, "first_name": "C"}},
            ]
        }
    )
    found = await checker.sync_admins(api)
    assert found == 2
    rows = await store.list_users(members_only=True)
    assert [r.user_id for r in rows] == [501, 502]
    assert all(r.is_group_admin for r in rows)


async def test_sync_admins_is_a_no_op_without_a_store_or_the_method() -> None:
    checker, _ = build()
    assert await checker.sync_admins(FakeApi()) == 0

    store_checker, _ = build(store=MemoryStore())
    assert await store_checker.sync_admins(object()) == 0


async def test_a_400_meaning_not_in_the_chat_is_a_refusal_not_an_outage() -> None:
    """Telegram sometimes reports absence as an error instead of a status."""
    checker, _ = build(deny_cache_ttl=300)
    api = FakeApi({(GROUP, PERSON): Boom("Bad Request: user not found")})
    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is False
    assert verdict.reason is Reason.NOT_MEMBER
    assert checker.api_broken is False
    # And it is cached, so an outsider does not cost a call per message.
    assert (await checker.check(api, PERSON)).reason is Reason.CACHED
    assert len(api.calls) == 1


async def test_an_error_that_means_nothing_of_the_sort_is_still_an_outage() -> None:
    checker, _ = build(grace=0)
    api = FakeApi({(GROUP, PERSON): Boom("Gateway timeout")})
    verdict = await checker.check(api, PERSON)
    assert verdict.reason is Reason.API_ERROR
    assert checker.api_broken is True


async def test_the_absence_detector_can_be_replaced() -> None:
    from group_auth.membership import looks_like_absence

    assert looks_like_absence(Boom("Bad Request: USER_NOT_PARTICIPANT")) is True
    assert looks_like_absence(Boom("connection reset")) is False

    clock = Clock()
    checker = MembershipChecker(
        AuthConfig(group_ids=(GROUP,)),
        clock=clock,
        absence_detector=lambda exc: "nope" in str(exc),
    )
    api = FakeApi({(GROUP, PERSON): Boom("nope")})
    assert (await checker.check(api, PERSON)).reason is Reason.NOT_MEMBER


async def test_a_reported_departure_closes_the_grace_window_too() -> None:
    """Being told somebody left has to beat "we verified them recently".

    ``forget`` used to drop the cached verdict but keep the moment of the last
    successful check, so the first failed check after a departure handed the
    person grace — up to fifteen minutes of access the group had just revoked.
    """
    checker, clock = build()
    api = FakeApi({(GROUP, PERSON): "member"})
    assert (await checker.check(api, PERSON)).allowed

    await checker.record_membership(UserRef(PERSON), False, source="chat_member")
    api.broken = True
    clock.advance(1)

    verdict = await checker.check(api, PERSON)
    assert verdict.allowed is False
    assert verdict.reason is Reason.API_ERROR


async def test_expired_entries_do_not_pile_up_for_the_life_of_the_process() -> None:
    """Every stranger who writes to the bot leaves an entry keyed by their id."""
    checker, clock = build(deny_cache_ttl=1)
    api = FakeApi({})
    # The real threshold is 1024 and the sweep is what is under test, not the
    # size at which it fires.
    checker._sweep_at = 8
    for offset in range(8):
        await checker.check(api, 10_000 + offset)
    assert len(checker._cache) == 8

    clock.advance(3600)
    await checker.check(api, PERSON)

    assert len(checker._cache) == 1
    assert checker._last_ok == {}


async def test_erasing_a_person_takes_the_row_and_the_cached_verdict() -> None:
    """A deletion that leaves a cached verdict behind is half a deletion."""
    store = MemoryStore()
    checker, _ = build(store=store)
    api = FakeApi({(GROUP, PERSON): "member"})
    assert (await checker.check(api, PERSON, user=UserRef(PERSON, "p"))).allowed
    assert await store.get_user(PERSON) is not None

    assert await checker.erase_user(PERSON) is True

    assert await store.get_user(PERSON) is None
    assert checker._cache == {}
    assert await checker.erase_user(PERSON) is False


async def test_erasing_refuses_to_look_like_it_worked_on_a_store_that_cannot() -> None:
    class OldStore:
        """A store written against the protocol before erasure was in it."""

        async def get_bound_groups(self) -> tuple[int, ...]:
            return ()

    checker, _ = build(store=OldStore())  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match="cannot delete users"):
        await checker.erase_user(PERSON)


async def test_erasing_without_a_store_is_not_an_error() -> None:
    checker, _ = build()
    assert await checker.erase_user(PERSON) is False

from __future__ import annotations

import pytest

from group_auth import AuthConfig, ConfigError
from group_auth.config import parse_flag, parse_ids, parse_seconds


def test_reads_the_documented_variables() -> None:
    cfg = AuthConfig.from_env(
        {
            "AUTH_ADMIN_IDS": "111, 222",
            "AUTH_GROUP_IDS": "-1001234567890",
            "AUTH_CACHE_TTL": "60",
            "AUTH_GRACE": "0",
            "AUTH_PRIVATE_ONLY": "no",
            "AUTH_BIND_ON_ADD": "first",
        }
    )
    assert cfg.admin_ids == frozenset({111, 222})
    assert cfg.group_ids == (-1001234567890,)
    assert cfg.cache_ttl == 60.0
    assert cfg.grace == 0.0
    assert cfg.private_only is False
    assert cfg.bind_on_add == "first"


def test_defaults_are_usable_with_nothing_set() -> None:
    cfg = AuthConfig.from_env({})
    assert cfg.admin_ids == frozenset()
    assert cfg.group_ids == ()
    assert cfg.private_only is True
    assert cfg.bind_on_add == "admin_only"
    assert cfg.is_open is False


def test_prefix_lets_the_host_bot_rename_everything() -> None:
    cfg = AuthConfig.from_env({"MYBOT_ADMIN_IDS": "7"}, prefix="MYBOT_")
    assert cfg.admin_ids == frozenset({7})


@pytest.mark.parametrize("raw", ["12,abc", "@someone", "1.5"])
def test_a_mistyped_id_raises_instead_of_vanishing(raw: str) -> None:
    with pytest.raises(ConfigError) as caught:
        parse_ids(raw, name="AUTH_ADMIN_IDS")
    # The message has to name the variable, or the typo is unfindable.
    assert "AUTH_ADMIN_IDS" in str(caught.value)


def test_ids_accept_the_separators_people_actually_type() -> None:
    assert parse_ids("1, 2;3 4", name="X") == (1, 2, 3, 4)
    assert parse_ids("5,5,5", name="X") == (5,)
    assert parse_ids(None, name="X") == ()


def test_flags_and_seconds() -> None:
    assert parse_flag("YES", name="X", default=False) is True
    assert parse_flag(None, name="X", default=True) is True
    assert parse_flag("", name="X", default=True) is False
    with pytest.raises(ConfigError):
        parse_flag("maybe", name="X", default=True)
    assert parse_seconds("2.5", name="X", default=1.0) == 2.5
    with pytest.raises(ConfigError):
        parse_seconds("-1", name="X", default=1.0)
    with pytest.raises(ConfigError):
        parse_seconds("soon", name="X", default=1.0)


def test_unknown_bind_policy_is_rejected_both_ways() -> None:
    with pytest.raises(ConfigError):
        AuthConfig(bind_on_add="whenever")  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        AuthConfig.from_env({"AUTH_BIND_ON_ADD": "whenever"})


def test_config_is_frozen_and_deduplicates_groups() -> None:
    cfg = AuthConfig(group_ids=(5, 5, 6))
    assert cfg.group_ids == (5, 6)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        cfg.cache_ttl = 1  # type: ignore[misc]

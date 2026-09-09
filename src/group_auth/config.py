"""Settings for the access gate.

Two sources of right, in this order:

1. ``admin_ids`` — the bot's own administrators. Access is unconditional:
   independent of any group, and independent of whether the Telegram API is
   answering at all. This is deliberate. An administrator has to be able to
   fix the bot exactly when everything else is broken.
2. Membership in one of ``group_ids``.

With no group configured only the administrators get in. That is a normal
operating mode, not a failure, and the bot should say so plainly.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from .errors import ConfigError

#: What to do when the bot is added to a group. See ``AuthConfig.bind_on_add``.
BindPolicy = Literal["never", "admin_only", "first", "always"]

_BIND_POLICIES: Final = ("never", "admin_only", "first", "always")

_TRUE: Final = frozenset({"1", "true", "yes", "on", "y"})
_FALSE: Final = frozenset({"0", "false", "no", "off", "n", ""})


def parse_ids(raw: str | None, *, name: str) -> tuple[int, ...]:
    """Parse ``"123, -1001234567890"`` into a tuple of ints.

    Separators are commas, semicolons and whitespace. Garbage raises instead
    of being dropped: a mistyped id that vanishes quietly turns into "the bot
    ignores me" with nothing to grep for.
    """
    if not raw:
        return ()
    out: list[int] = []
    for chunk in raw.replace(";", ",").replace(" ", ",").split(","):
        piece = chunk.strip()
        if not piece:
            continue
        try:
            value = int(piece)
        except ValueError:
            raise ConfigError(
                f"{name}: {piece!r} is not a Telegram id. "
                f"Expected numbers separated by commas, e.g. "
                f"{name}=123456789,987654321"
            ) from None
        if value not in out:
            out.append(value)
    return tuple(out)


def parse_flag(raw: str | None, *, name: str, default: bool) -> bool:
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(
        f"{name}: {raw!r} is not a yes/no value. Use one of 1/0, true/false, yes/no."
    )


def parse_seconds(raw: str | None, *, name: str, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name}: {raw!r} is not a number of seconds.") from None
    if value < 0:
        raise ConfigError(f"{name}: {value} is negative; seconds cannot be.")
    return value


@dataclass(frozen=True)
class AuthConfig:
    """How the gate behaves. Every field has a working default except the ids."""

    #: The bot's administrators. Unconditional access, and the only identity
    #: that may bind a group under the default ``bind_on_add`` policy.
    admin_ids: frozenset[int] = field(default_factory=frozenset)

    #: Groups whose members are users of the bot. Member of any one of them is
    #: enough. May be empty when the group is bound at runtime instead.
    group_ids: tuple[int, ...] = ()

    #: How long a positive verdict is trusted without asking Telegram again.
    cache_ttl: float = 300.0

    #: How long a refusal is trusted. Deliberately much shorter than
    #: ``cache_ttl``: the usual reason for a refusal is "not in the group yet",
    #: and the usual fix is being added to it seconds later. A refusal cached
    #: for five minutes means a new colleague knocks on a door that is already
    #: unlocked.
    deny_cache_ttl: float = 60.0

    #: When Telegram stops answering, keep letting in anyone who passed a real
    #: check within this window. Someone else's network hiccup should not
    #: switch the bot off for everybody. See docs/DESIGN.md for the trade-off.
    grace: float = 900.0

    #: Ignore anything that is not a private chat. The bot sits in the group to
    #: be the access roster, not to talk there.
    private_only: bool = True

    #: What happens when the bot is added to a group:
    #:
    #: * ``never`` — nothing; only ``group_ids`` counts.
    #: * ``admin_only`` — bind it if the person who added the bot is in
    #:   ``admin_ids``. The default, because otherwise anyone who adds the bot
    #:   to a group of their own has just granted access to their own people.
    #: * ``first`` — bind the first group offered, then behave like ``never``.
    #: * ``always`` — bind (and switch to) whichever group was offered last.
    bind_on_add: BindPolicy = "admin_only"

    def __post_init__(self) -> None:
        object.__setattr__(self, "admin_ids", frozenset(self.admin_ids))
        object.__setattr__(self, "group_ids", tuple(dict.fromkeys(self.group_ids)))
        if self.bind_on_add not in _BIND_POLICIES:
            raise ConfigError(
                f"bind_on_add: {self.bind_on_add!r} is not one of "
                f"{', '.join(_BIND_POLICIES)}."
            )
        for name in ("cache_ttl", "deny_cache_ttl", "grace"):
            if getattr(self, name) < 0:
                raise ConfigError(f"{name} cannot be negative.")

    @property
    def is_open(self) -> bool:
        """True when anyone at all can get in besides the administrators."""
        return bool(self.group_ids)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        prefix: str = "AUTH_",
    ) -> AuthConfig:
        """Read the configuration from environment variables.

        ``prefix`` lets a host bot name the variables its own way without
        touching this file: ``AuthConfig.from_env(prefix="MYBOT_")`` reads
        ``MYBOT_ADMIN_IDS`` and so on.
        """
        source = os.environ if env is None else env

        def key(name: str) -> str:
            return f"{prefix}{name}"

        def get(name: str) -> str | None:
            return source.get(key(name))

        admins = parse_ids(get("ADMIN_IDS"), name=key("ADMIN_IDS"))
        groups = parse_ids(get("GROUP_IDS"), name=key("GROUP_IDS"))

        policy = (get("BIND_ON_ADD") or "admin_only").strip().lower()
        if policy not in _BIND_POLICIES:
            raise ConfigError(
                f"{key('BIND_ON_ADD')}: {policy!r} is not one of "
                f"{', '.join(_BIND_POLICIES)}."
            )

        return cls(
            admin_ids=frozenset(admins),
            group_ids=groups,
            cache_ttl=parse_seconds(
                get("CACHE_TTL"), name=key("CACHE_TTL"), default=300.0
            ),
            deny_cache_ttl=parse_seconds(
                get("DENY_CACHE_TTL"), name=key("DENY_CACHE_TTL"), default=60.0
            ),
            grace=parse_seconds(get("GRACE"), name=key("GRACE"), default=900.0),
            private_only=parse_flag(
                get("PRIVATE_ONLY"), name=key("PRIVATE_ONLY"), default=True
            ),
            bind_on_add=policy,  # type: ignore[arg-type]
        )

    def with_groups(self, group_ids: Iterable[int]) -> AuthConfig:
        """A copy with a different group list. Used by tests and by rebinding."""
        from dataclasses import replace

        return replace(self, group_ids=tuple(group_ids))

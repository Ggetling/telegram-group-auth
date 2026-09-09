"""Errors this package raises.

A library has no right to kill the host process, so nothing here calls
``sys.exit``. Bad configuration raises ``ConfigError`` and the bot decides
whether that is fatal — most bots want it to be, at startup, loudly.
"""

from __future__ import annotations


class GroupAuthError(Exception):
    """Base class for every error this package raises."""


class ConfigError(GroupAuthError):
    """Configuration is unusable: a missing value, or one that does not parse.

    Raised instead of silently dropping the bad part. A typo in a list of
    numeric ids would otherwise become "this person has no access" weeks
    later, with nothing in the logs pointing at the typo.
    """

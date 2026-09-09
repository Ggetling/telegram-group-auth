"""One timestamp format, so both stores write the same thing."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow_iso() -> str:
    """UTC, seconds precision, fixed width — so string comparison sorts right."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

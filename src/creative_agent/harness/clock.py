"""The one sanctioned wall-clock source (DEC-F8). Everything else injects Clock."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Production clock: aware-UTC now()."""

    def now(self) -> datetime:
        return datetime.now(UTC)  # noqa: TID251 — the single sanctioned call site


class FixedClock:
    """Test clock: returns a pinned instant."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires an aware datetime")
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

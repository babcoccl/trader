"""Time source. All code reads time through a Clock so tests can freeze it."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A clock fixed at `at` (must be timezone-aware), movable only by advance()."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, delta: timedelta) -> None:
        self._at = self._at + delta

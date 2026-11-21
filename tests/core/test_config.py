from datetime import UTC, datetime, timedelta

from trader.core.clock import FrozenClock
from trader.core.config import Settings


def test_settings_default_to_paper() -> None:
    settings = Settings(_env_file=None)
    assert settings.mode == "paper"
    assert settings.live_enabled is False


def test_frozen_clock_advance() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FrozenClock(start)
    assert clock.now() == start
    clock.advance(timedelta(hours=2, minutes=30))
    assert clock.now() == start + timedelta(hours=2, minutes=30)

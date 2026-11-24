from datetime import UTC, datetime, timedelta

import pytest

from trader.core.clock import FrozenClock
from trader.core.config import Settings


def test_settings_default_to_paper() -> None:
    settings = Settings(_env_file=None)
    assert settings.mode == "paper"
    assert settings.live_enabled is False


def test_symbol_list_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.symbol_list == ("BTC-USD", "ETH-USD")


def test_symbol_list_normalized() -> None:
    settings = Settings(_env_file=None, symbols=" btc-usd , eth-usd,sol-usd ")
    assert settings.symbol_list == ("BTC-USD", "ETH-USD", "SOL-USD")


def test_symbol_list_requires_both_baselines() -> None:
    with pytest.raises(ValueError, match="BTC-USD, ETH-USD"):
        _ = Settings(_env_file=None, symbols="btc-usd,sol-usd").symbol_list


def test_symbol_list_empty_raises() -> None:
    with pytest.raises(ValueError):
        _ = Settings(_env_file=None, symbols="").symbol_list


def test_symbol_list_repeated_raises() -> None:
    with pytest.raises(ValueError):
        _ = Settings(_env_file=None, symbols="BTC-USD,ETH-USD,BTC-USD").symbol_list


def test_live_mode_accepts_all_eight_assets() -> None:
    settings = Settings(
        _env_file=None,
        mode="live",
        symbols="BTC-USD,ETH-USD,SOL-USD,XRP-USD,DOGE-USD,ADA-USD,AVAX-USD,LINK-USD",
    )
    assert len(settings.symbol_list) == 8


def test_live_mode_allows_default() -> None:
    settings = Settings(_env_file=None, mode="live")
    assert settings.symbol_list == ("BTC-USD", "ETH-USD")


def test_venue_defaults_to_bitstamp() -> None:
    assert Settings(_env_file=None).venue == "bitstamp"


def test_venue_accepts_coinbase() -> None:
    assert Settings(_env_file=None, venue="coinbase").venue == "coinbase"


def test_repr_hides_coinbase_credentials() -> None:
    settings = Settings(
        _env_file=None, coinbase_key="the-key", coinbase_secret="the-secret"
    )
    text = repr(settings)
    assert "the-key" not in text
    assert "the-secret" not in text


def test_frozen_clock_advance() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FrozenClock(start)
    assert clock.now() == start
    clock.advance(timedelta(hours=2, minutes=30))
    assert clock.now() == start + timedelta(hours=2, minutes=30)

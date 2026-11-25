"""Application settings, loaded from environment variables prefixed TRADER_."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASELINE_SYMBOLS = ("BTC-USD", "ETH-USD")


class Settings(BaseSettings):
    """All runtime configuration. Values may be overridden by TRADER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="TRADER_", env_file=".env", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://trader:trader@localhost:5432/trader"
    mode: Literal["paper", "live"] = "paper"
    live_enabled: bool = False
    log_level: str = "INFO"
    dashboard_password: str = ""
    # Comma-separated assets, e.g. "BTC-USD,ETH-USD,SOL-USD,XRP-USD"; eight
    # assets are supported (see data.sources.base.ASSETS).
    symbols: str = "BTC-USD,ETH-USD"
    # Strategy the daily cycle sizes: the default "trend" keeps the existing
    # 9-rule ensemble; "cross_sectional_momentum" is the validated Run 5
    # strategy (spec W1.2a).
    strategy: Literal["trend", "cross_sectional_momentum"] = "trend"
    # Live venue credentials (spec 5.2); read-only until live_enabled.
    venue: Literal["bitstamp", "coinbase"] = "bitstamp"
    bitstamp_key: str = ""
    bitstamp_secret: str = ""
    bitstamp_base_url: str = "https://www.bitstamp.net/api/v2"
    coinbase_key: str = Field(default="", repr=False)
    coinbase_secret: str = Field(default="", repr=False)
    coinbase_base_url: str = "https://api.coinbase.com"
    # Ping-pong subsidy sleeve (spec W1.2b): capped maker-only BTC-USD
    # volume booster aimed at the next Coinbase fee tier. Off by default.
    subsidy_enabled: bool = False
    subsidy_target_volume_usd: Decimal = Decimal("10000")
    subsidy_daily_cap_usd: Decimal = Decimal("2000")
    subsidy_adx_max: float = 20.0
    subsidy_order_notional_usd: Decimal = Decimal("250")
    # Autonomy Level 2 (spec W1.3): when enabled, pending plans inside both
    # bounds auto-approve as actor "auto" instead of waiting for the owner.
    # Off by default; risk.check_plan and the halt still apply.
    auto_approve_enabled: bool = False
    auto_approve_max_notional_usd: Decimal = Decimal("750")
    auto_approve_max_loss_pct: Decimal = Decimal("0.015")

    @property
    def symbol_list(self) -> tuple[str, ...]:
        """Configured assets, normalized: comma-split, stripped, upper-cased."""
        parts = tuple(s.strip().upper() for s in self.symbols.split(","))
        if not parts or any(not s for s in parts) or len(set(parts)) != len(parts):
            raise ValueError(f"invalid TRADER_SYMBOLS value: {self.symbols!r}")
        missing = [s for s in _BASELINE_SYMBOLS if s not in parts]
        if missing:
            raise ValueError(
                f"TRADER_SYMBOLS must include {', '.join(_BASELINE_SYMBOLS)} "
                "(the comparison baselines)"
            )
        return parts


@lru_cache
def get_settings() -> Settings:
    return Settings()

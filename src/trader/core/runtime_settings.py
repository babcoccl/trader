"""DB-backed runtime overrides for the settings editable through the API
(spec W1.3b).

``load_runtime_overrides`` reads the ``settings`` table for the subsidy and
auto-approve keys and falls back to ``get_settings()`` (the ``.env``
defaults) when a row is absent, so container restarts are not needed for a
dashboard change to take effect. ``get_settings()`` itself is untouched:
with no DB rows every value is exactly the env-var value.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.core.config import get_settings
from trader.ledger.models import Setting

KEYS = (
    "subsidy_enabled",
    "subsidy_target_volume_usd",
    "subsidy_daily_cap_usd",
    "subsidy_adx_max",
    "subsidy_order_notional_usd",
    "subsidy_order_levels",
    "subsidy_spread_bps",
    "subsidy_refresh_seconds",
    "subsidy_auto_seed",
    "auto_approve_enabled",
    "auto_approve_max_notional_usd",
    "auto_approve_max_loss_pct",
)


@dataclass(frozen=True)
class RuntimeConfig:
    subsidy_enabled: bool
    subsidy_target_volume_usd: Decimal
    subsidy_daily_cap_usd: Decimal
    subsidy_adx_max: float
    subsidy_order_notional_usd: Decimal
    subsidy_order_levels: int
    subsidy_spread_bps: float
    subsidy_refresh_seconds: int
    subsidy_auto_seed: bool
    auto_approve_enabled: bool
    auto_approve_max_notional_usd: Decimal
    auto_approve_max_loss_pct: Decimal


def _dec(v: Any) -> Decimal:
    # JSONB round-trips numbers as int/float; Decimal(str(...)) restores the
    # exact decimal the API validated.
    return v if isinstance(v, Decimal) else Decimal(str(v))


def load_runtime_overrides(session: Session) -> RuntimeConfig:
    settings = get_settings()
    rows = {
        r.key: r.value
        for r in session.scalars(select(Setting).where(Setting.key.in_(KEYS)))
    }

    def get(key: str, fallback: Any) -> Any:
        value = rows.get(key)
        return fallback if value is None else value

    return RuntimeConfig(
        subsidy_enabled=bool(get("subsidy_enabled", settings.subsidy_enabled)),
        subsidy_target_volume_usd=_dec(
            get("subsidy_target_volume_usd", settings.subsidy_target_volume_usd)
        ),
        subsidy_daily_cap_usd=_dec(
            get("subsidy_daily_cap_usd", settings.subsidy_daily_cap_usd)
        ),
        subsidy_adx_max=float(get("subsidy_adx_max", settings.subsidy_adx_max)),
        subsidy_order_notional_usd=_dec(
            get("subsidy_order_notional_usd", settings.subsidy_order_notional_usd)
        ),
        subsidy_order_levels=int(
            get("subsidy_order_levels", settings.subsidy_order_levels)
        ),
        subsidy_spread_bps=float(
            get("subsidy_spread_bps", settings.subsidy_spread_bps)
        ),
        subsidy_refresh_seconds=int(
            get("subsidy_refresh_seconds", settings.subsidy_refresh_seconds)
        ),
        subsidy_auto_seed=bool(get("subsidy_auto_seed", settings.subsidy_auto_seed)),
        auto_approve_enabled=bool(
            get("auto_approve_enabled", settings.auto_approve_enabled)
        ),
        auto_approve_max_notional_usd=_dec(
            get("auto_approve_max_notional_usd", settings.auto_approve_max_notional_usd)
        ),
        auto_approve_max_loss_pct=_dec(
            get("auto_approve_max_loss_pct", settings.auto_approve_max_loss_pct)
        ),
    )

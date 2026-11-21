"""Risk limits (spec 2.1). Read from the ``settings`` table under the
``risk.limits.`` prefix; the defaults below apply when a key is absent.
These are veto thresholds — nothing outside ``risk/`` may weaken or bypass
them. Risk engine state (``risk.halted`` etc.) is a different namespace."""

from dataclasses import dataclass, fields

from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.ledger.models import Setting

KEY_PREFIX = "risk.limits."


@dataclass(frozen=True)
class Limits:
    max_gross: float = 1.0
    max_asset_weight: float = 1.0
    drawdown_halt: float = 0.35
    max_plan_loss_fraction: float = 0.25
    stale_candle_seconds: float = 600.0
    price_move_void: float = 0.02

    @classmethod
    def load(cls, session: Session) -> "Limits":
        """Limits with any ``risk.<name>`` settings overrides applied."""
        rows = session.scalars(
            select(Setting).where(Setting.key.like(f"{KEY_PREFIX}%"))
        ).all()
        overrides = {r.key[len(KEY_PREFIX) :]: r.value for r in rows}
        names = {f.name for f in fields(cls)}
        unknown = set(overrides) - names
        if unknown:
            raise ValueError(f"unknown risk setting(s): {sorted(unknown)}")
        return cls(**{k: float(v) for k, v in overrides.items()})

"""GET /api/rules: what the nine trend rules checked and why nothing is
trading (spec D1). Read-only; every number comes from stored decision rows
or the code constants, so the screen cannot drift from the code."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.routes.status import _next_daily_decision
from trader.api.schemas import (
    AssetRulesOut,
    DailyRunOut,
    IntradayAssetOut,
    IntradayRulesOut,
    ParameterOut,
    RegimeOut,
    RuleOut,
    RulesOut,
)
from trader.data.sources.base import SYMBOLS
from trader.engine import daily_cycle
from trader.ledger.models import Decision
from trader.plans import builder
from trader.risk.limits import Limits
from trader.strategy import regime as regime_mod
from trader.strategy import rules as rules_mod

router = APIRouter(dependencies=[AuthRequired])

RULE_NAMES = (
    [f"sma{n}" for n in rules_mod.SMA_PERIODS]
    + [f"momentum{n}" for n in rules_mod.MOMENTUM_PERIODS]
    + [f"breakout{n}" for n in rules_mod.BREAKOUT_PERIODS]
)


def _label(name: str) -> str:
    if name.startswith("sma"):
        return f"Price above its {name[3:]}-day average"
    if name.startswith("momentum"):
        return f"Price above its level {name[8:]} days ago"
    n = int(name[len("breakout") :])
    return f"{n}-day breakout (on at a {n}-day high, off at a {n // 2}-day low)"


def _latest_daily(session: Session, symbol: str) -> Decision | None:
    """Newest daily row for ``symbol`` that recorded rule values."""
    return session.scalars(
        select(Decision)
        .where(
            Decision.cycle == "daily",
            Decision.symbol == symbol,
            Decision.details["rules"].isnot(None),
        )
        .order_by(Decision.ran_at.desc(), Decision.id.desc())
        .limit(1)
    ).first()


def _asset(session: Session, symbol: str) -> AssetRulesOut:
    row = _latest_daily(session, symbol)
    details: dict[str, Any] = (row.details if row else {}) or {}
    stored_rules: dict[str, Any] = details.get("rules") or {}
    stored_levels: dict[str, Any] = details.get("levels") or {}
    last_close = details.get("last_close")
    rule_outs: list[RuleOut] = []
    for name in RULE_NAMES:
        value = stored_rules.get(name)
        level = stored_levels.get(name)
        distance = (
            last_close / level - 1
            if last_close is not None and level is not None and level != 0
            else None
        )
        rule_outs.append(
            RuleOut(
                name=name,
                label=_label(name),
                positive=None if value is None else bool(value == 1.0),
                level=level,
                distance=distance,
            )
        )
    regime = regime_mod.from_details(details.get("regime") or {})
    return AssetRulesOut(
        symbol=symbol,
        decided_at=row.ran_at if row else None,
        action=row.action if row else None,
        reason=row.reason if row else None,
        last_close=last_close,
        rules_positive=(
            sum(1 for v in stored_rules.values() if v == 1.0) if row else None
        ),
        rules=rule_outs,
        annual_vol=details.get("annual_vol"),
        raw_weight=details.get("raw_weight"),
        target_weight=row.target_weight if row else None,
        current_weight=row.current_weight if row else None,
        regime=(
            RegimeOut(**regime.as_details(), text=regime.text())
            if regime is not None
            else None
        ),
    )


def _intraday(session: Session) -> IntradayRulesOut | None:
    row = session.scalars(
        select(Decision)
        .where(Decision.cycle == "intraday")
        .order_by(Decision.ran_at.desc(), Decision.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    per_asset: dict[str, Any] = (row.details or {}).get("per_asset") or {}
    assets: list[IntradayAssetOut] = []
    for symbol in SYMBOLS:
        a = per_asset.get(symbol) or {}
        last_price = a.get("last_price")
        quote = a.get("dip_quote") or {}
        drop_bid = quote.get("bid")
        drop_bid_distance = (
            1 - drop_bid / last_price if drop_bid is not None and last_price else None
        )
        assets.append(
            IntradayAssetOut(
                symbol=symbol,
                last_price=last_price,
                nearest_stop_distance=a.get("nearest_stop_distance"),
                drop_bid=drop_bid,
                drop_bid_distance=drop_bid_distance,
            )
        )
    return IntradayRulesOut(
        ran_at=row.ran_at, action=row.action, reason=row.reason, assets=assets
    )


def _parameters(session: Session) -> list[ParameterOut]:
    limits = Limits.load(session)
    return [
        ParameterOut(name="Assets", value=", ".join(SYMBOLS)),
        ParameterOut(name="Daily decision time", value="00:05 UTC"),
        ParameterOut(name="Five-minute check", value="every 5 minutes"),
        ParameterOut(name="Volatility target", value=f"{daily_cycle.VOL_TARGET:.0%}"),
        ParameterOut(
            name="Trading band",
            value=(f"{daily_cycle.BAND:.0%} of the larger of target and holding"),
        ),
        ParameterOut(
            name="Drawdown halt",
            value=f"{limits.drawdown_halt:.0%} below the account high-water mark",
        ),
        ParameterOut(
            name="Largest loss per plan",
            value=f"{limits.max_plan_loss_fraction:.0%} of equity",
        ),
        ParameterOut(
            name="Approval expiry",
            value=f"{builder.APPROVAL_EXPIRY.total_seconds() / 3600:g} hours",
        ),
        ParameterOut(
            name="Entry order",
            value=(
                "maker-only limit at the best bid, re-priced every "
                f"{builder.REPRICE_SECONDS // 60} minutes, market order after "
                f"{builder.MARKET_AFTER_SECONDS // 60} minutes"
            ),
        ),
        ParameterOut(
            name="Emergency stop",
            value=(
                f"{builder.STOP_SWINGS} {builder.STOP_DAYS}-day swings below "
                "the entry price, checked every 30 seconds"
            ),
        ),
        ParameterOut(
            name="Sudden-drop bids",
            value="Parked. Shown as an observation only; no order is placed",
        ),
    ]


@router.get("/api/rules", response_model=RulesOut)
def rules(session: Session = SessionDep) -> RulesOut:
    latest_daily_row = session.scalars(
        select(Decision)
        .where(Decision.cycle == "daily")
        .order_by(Decision.ran_at.desc(), Decision.id.desc())
        .limit(1)
    ).first()
    return RulesOut(
        next_daily_decision_at=_next_daily_decision(datetime.now(UTC)),
        latest_daily=(
            DailyRunOut(
                ran_at=latest_daily_row.ran_at,
                action=latest_daily_row.action,
                reason=latest_daily_row.reason,
            )
            if latest_daily_row
            else None
        ),
        assets=[_asset(session, symbol) for symbol in SYMBOLS],
        intraday=_intraday(session),
        parameters=_parameters(session),
    )

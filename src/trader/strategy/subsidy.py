"""Ping-pong subsidy sleeve (spec W1.2b): capped maker-only volume booster.

While the account's trailing 30-day traded volume sits below the Coinbase
fee-tier target, sideways (low-ADX) days get small maker-or-cancel BTC-USD
limit orders alternating buy/sell, so the cheaper tier arrives sooner. The
sleeve never runs while halted, never exceeds the daily cap or the
remaining-to-target distance, and every plan still goes through
``risk.check_plan`` and the dashboard's manual approval.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import Clock
from trader.core.config import get_settings
from trader.core.runtime_settings import load_runtime_overrides
from trader.data import candles as candles_mod
from trader.data.sources.base import ASSETS
from trader.ledger import accounting
from trader.ledger.models import Candle, Decision, Fill, Order, Plan
from trader.plans import builder
from trader.risk import engine as risk_engine
from trader.risk.limits import Limits
from trader.strategy import regime

log = logging.getLogger(__name__)

Q18 = Decimal("1e-18")
SLEEVE = "ping_pong"
SYMBOL = "BTC-USD"
ASSET = "BTC"
# Placement floors: the paper adapter rejects orders under $10; Coinbase's
# BTC-USD quote minimum is $1 (enforced again at placement time).
MIN_ORDER_NOTIONAL = {"paper": Decimal("10"), "live": Decimal("1")}
QUOTE_BPS = Decimal("0.001")  # 10 bps off the last close
BOOSTER_WINDOW = timedelta(hours=24)
TRAILING_WINDOW = timedelta(days=30)
ADX_LOOKBACK_DAYS = 120
_OPEN_PLAN_STATUSES = ("pending", "approved")
_OPEN_ORDER_STATUSES = ("new", "open", "partially_filled")
RECENT_FILLS_LIMIT = 25

# (name, maker bps, taker bps) by upper bound of trailing 30d USD volume.
_TIERS: tuple[tuple[Decimal, str, float, float], ...] = (
    (Decimal("10000"), "Tier 1", 40.0, 60.0),
    (Decimal("50000"), "Tier 2", 25.0, 40.0),
    (Decimal("inf"), "Tier 3", 15.0, 25.0),
)


def _q(x: Decimal) -> Decimal:
    return x.quantize(Q18, rounding=ROUND_HALF_UP)


def _round_down(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def _tier(trailing_30d_volume_usd: Decimal) -> tuple[str, float, float]:
    for bound, name, maker_bps, taker_bps in _TIERS:
        if trailing_30d_volume_usd < bound:
            return name, maker_bps, taker_bps
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class SubsidyFill:
    time: datetime
    side: str
    price: Decimal
    quantity: Decimal
    notional_usd: Decimal
    fee_usd: Decimal


@dataclass(frozen=True)
class SubsidySnapshot:
    mode: str
    status: Literal["active", "throttled", "disabled"]
    reason: str
    trailing_30d_volume_usd: Decimal
    today_booster_volume_usd: Decimal
    booster_trades_30d: int
    booster_volume_30d_usd: Decimal
    booster_fees_30d_usd: Decimal
    tier: str
    maker_bps: float
    taker_bps: float
    target_volume_usd: Decimal
    remaining_to_target_usd: Decimal
    progress_pct: float
    daily_cap_usd: Decimal
    adx: float | None
    adx_max: float
    open_orders_usd: Decimal
    booster_net_pnl_30d_usd: Decimal
    recent_fills: list[SubsidyFill]


def _fills_30d(
    session: Session, mode: str, now: datetime
) -> list[tuple[datetime, str, Decimal, Decimal, Decimal, str | None]]:
    """(time, side, price, quantity, fee, sleeve) of non-shadow fills in
    ``mode``, oldest first for FIFO matching."""
    rows = session.execute(
        select(Fill.time, Order.side, Fill.price, Fill.quantity, Fill.fee, Plan.sleeve)
        .join(Order, Fill.order_id == Order.id)
        .join(Plan, Order.plan_id == Plan.id)
        .where(
            Order.mode == mode,
            Order.shadow.is_(False),
            Fill.time >= now - TRAILING_WINDOW,
        )
        .order_by(Fill.time, Fill.id)
    ).all()
    return [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]


def _open_orders_usd(session: Session, mode: str) -> Decimal:
    """USD committed in resting non-shadow ping_pong orders for ``mode``.
    Partially filled orders count only their unfilled remainder."""
    rows = session.execute(
        select(Order.id, Order.price, Order.quantity)
        .join(Plan, Order.plan_id == Plan.id)
        .where(
            Plan.sleeve == SLEEVE,
            Order.mode == mode,
            Order.shadow.is_(False),
            Order.status.in_(_OPEN_ORDER_STATUSES),
            Order.price.isnot(None),
        )
    ).all()
    if not rows:
        return Decimal("0")
    filled = dict(
        session.execute(
            select(Fill.order_id, func.coalesce(func.sum(Fill.quantity), 0))
            .where(Fill.order_id.in_([r[0] for r in rows]))
            .group_by(Fill.order_id)
        ).all()
    )
    return _q(
        sum(
            (
                max(Decimal("0"), qty - filled.get(oid, Decimal("0")))
                * (price if price is not None else Decimal("0"))
                for oid, price, qty in rows
            ),
            Decimal("0"),
        )
    )


def _recent_fills(session: Session, mode: str) -> list[SubsidyFill]:
    """Up to RECENT_FILLS_LIMIT newest non-shadow ping_pong fills."""
    rows = session.execute(
        select(Fill.time, Order.side, Fill.price, Fill.quantity, Fill.fee)
        .join(Order, Fill.order_id == Order.id)
        .join(Plan, Order.plan_id == Plan.id)
        .where(
            Plan.sleeve == SLEEVE,
            Order.mode == mode,
            Order.shadow.is_(False),
        )
        .order_by(Fill.time.desc(), Fill.id.desc())
        .limit(RECENT_FILLS_LIMIT)
    ).all()
    return [
        SubsidyFill(
            time=t,
            side=s,
            price=p,
            quantity=qty,
            notional_usd=_q(p * qty),
            fee_usd=fee,
        )
        for t, s, p, qty, fee in rows
    ]


def _latest_adx(session: Session, now: datetime) -> float | None:
    """ADX of the last daily BTC-USD candle; None when undefined."""
    df = candles_mod.load(
        session,
        SYMBOL,
        "1d",
        now - timedelta(days=ADX_LOOKBACK_DAYS),
        now + timedelta(days=1),
    )
    if df.empty:
        return None
    # One day may exist from several venues: keep one row per open_time,
    # from the source covering the most days (same rule as daily_cycle).
    if df["source"].nunique() > 1:
        preferred = df["source"].value_counts().idxmax()
        df = df.assign(_primary=(df["source"] != preferred))
        df = df.sort_values("_primary", kind="stable")
        df = df[~df.index.duplicated(keep="first")].drop(columns="_primary")
        df = df.sort_index()
    a = regime.adx(df["high"], df["low"], df["close"]).iloc[-1]
    return None if a != a else float(a)  # noqa: PLR0124 (NaN != NaN)


def compute_status(session: Session, clock: Clock, mode: str) -> SubsidySnapshot:
    """Fee-tier progress, 30-day booster spend and active/throttled state."""
    settings = get_settings()
    overrides = load_runtime_overrides(session)
    now = clock.now()
    rows = _fills_30d(session, mode, now)

    trailing = Decimal("0")
    booster_today = Decimal("0")
    booster_trades = 0
    booster_volume = Decimal("0")
    booster_fees = Decimal("0")
    today_start = now - BOOSTER_WINDOW
    net_pnl = Decimal("0")
    buy_lots: deque[list[Decimal]] = deque()  # FIFO [price, remaining qty]
    for time, side, price, quantity, fee, sleeve in rows:
        notional = abs(quantity * price)
        trailing += notional
        if sleeve == SLEEVE:
            booster_trades += 1
            booster_volume += notional
            booster_fees += fee
            # Every ping-pong fee reduces the realized net, even before a
            # round-trip closes.
            net_pnl -= fee
            if side == "buy":
                buy_lots.append([price, quantity])
            else:
                remaining = quantity
                while remaining > 0 and buy_lots:
                    lot = buy_lots[0]
                    take = min(lot[1], remaining)
                    net_pnl += take * (price - lot[0])
                    lot[1] -= take
                    remaining -= take
                    if lot[1] == 0:
                        buy_lots.popleft()
                # A sell with no 30d ping-pong buy left realizes no spread
                # PnL (its cost basis predates the window or came from
                # another sleeve); its fee is still counted above.
            if time >= today_start:
                booster_today += notional

    target = overrides.subsidy_target_volume_usd
    daily_cap = overrides.subsidy_daily_cap_usd
    adx_max = overrides.subsidy_adx_max
    tier, maker_bps, taker_bps = _tier(trailing)
    remaining = max(Decimal("0"), target - trailing)
    progress = min(1.0, float(trailing / target)) if target > 0 else 1.0
    adx = _latest_adx(session, now)

    status: Literal["active", "throttled", "disabled"]
    reason: str
    if not overrides.subsidy_enabled:
        status, reason = "disabled", "Subsidy sleeve is disabled."
    elif settings.venue != "coinbase":
        status, reason = (
            "disabled",
            "Subsidy sleeve only applies to Coinbase venue.",
        )
    elif trailing >= target:
        status, reason = "throttled", "Target fee tier volume reached."
    elif booster_today >= daily_cap:
        status, reason = "throttled", "Daily booster cap reached."
    elif adx is None or adx >= adx_max:
        status, reason = "throttled", "ADX above sideways threshold."
    else:
        status, reason = "active", "Boosting volume toward the next fee tier."

    return SubsidySnapshot(
        mode=mode,
        status=status,
        reason=reason,
        trailing_30d_volume_usd=_q(trailing),
        today_booster_volume_usd=_q(booster_today),
        booster_trades_30d=booster_trades,
        booster_volume_30d_usd=_q(booster_volume),
        booster_fees_30d_usd=_q(booster_fees),
        tier=tier,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        target_volume_usd=target,
        remaining_to_target_usd=_q(remaining),
        progress_pct=progress,
        daily_cap_usd=daily_cap,
        adx=adx,
        adx_max=adx_max,
        open_orders_usd=_open_orders_usd(session, mode),
        booster_net_pnl_30d_usd=_q(net_pnl),
        recent_fills=_recent_fills(session, mode),
    )


def _ping_pong_inventory(session: Session, mode: str) -> Decimal:
    """Net BTC quantity held by ping_pong fills (all time, ``mode`` only)."""
    rows = session.execute(
        select(Order.side, Fill.quantity)
        .join(Order, Fill.order_id == Order.id)
        .join(Plan, Order.plan_id == Plan.id)
        .where(
            Order.mode == mode,
            Order.shadow.is_(False),
            Order.symbol == SYMBOL,
            Plan.sleeve == SLEEVE,
        )
    ).all()
    return sum((qty if side == "buy" else -qty for side, qty in rows), Decimal("0"))


def _latest_closes(session: Session) -> dict[str, Decimal]:
    """Newest stored close per configured symbol, for marking positions."""
    prices: dict[str, Decimal] = {}
    for symbol in get_settings().symbol_list:
        close = session.scalar(
            select(Candle.close)
            .where(Candle.symbol == symbol)
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        if close is not None:
            prices[symbol] = close
    return prices


def propose_ping_pong_plan(session: Session, clock: Clock, mode: str) -> Plan | None:
    """Propose one maker-only BTC-USD order when the sleeve is active.

    Returns ``None`` when the sleeve is not active, a ping_pong plan is
    already open, no 5m close exists, the capped size rounds to zero, or
    ``risk.check_plan`` vetoes the plan.
    """
    settings = get_settings()
    overrides = load_runtime_overrides(session)
    if settings.venue != "coinbase":
        # The sleeve exists to reach a Coinbase fee tier; on any other
        # venue its volume buys nothing.
        return None
    snapshot = compute_status(session, clock, mode)
    if snapshot.status != "active":
        return None
    open_plan = session.scalar(
        select(Plan.id).where(
            Plan.sleeve == SLEEVE, Plan.status.in_(_OPEN_PLAN_STATUSES)
        )
    )
    if open_plan is not None:
        return None
    last_close = session.scalar(
        select(Candle.close)
        .where(Candle.symbol == SYMBOL, Candle.interval == "5m")
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if last_close is None:
        return None

    notional = min(
        overrides.subsidy_order_notional_usd,
        snapshot.remaining_to_target_usd,
        snapshot.daily_cap_usd - snapshot.today_booster_volume_usd,
    )
    if notional < MIN_ORDER_NOTIONAL[mode]:
        return None
    asset = ASSETS[SYMBOL]
    inventory = _ping_pong_inventory(session, mode)
    # The sleeve can only sell BTC the account still holds: an outside sale
    # (a Flatten, a trend-sleeve exit) leaves stale ping_pong inventory.
    holding = min(
        inventory, accounting.positions(session, mode).get(ASSET, Decimal("0"))
    )
    side = "buy" if holding <= 0 else "sell"
    maker_fee = Decimal(str(snapshot.maker_bps)) / Decimal("10000")
    if side == "buy":
        # Leave room for the fill's fee, like plans/builder.py: a buy sized
        # at the whole cash balance would cost more than the cash on hand.
        # Paper fills charge the BITSTAMP model regardless of the Coinbase
        # tier the snapshot reports.
        fee_rate = maker_fee if mode == "live" else Decimal(str(BITSTAMP.maker_fee))
        notional = min(
            notional,
            accounting.cash(session, mode) / (Decimal("1") + fee_rate),
        )
        if notional < MIN_ORDER_NOTIONAL[mode]:
            return None
    if side == "buy":
        price = _round_down(last_close * (1 - QUOTE_BPS), asset.tick)
    else:
        price = _round_down(last_close * (1 + QUOTE_BPS), asset.tick)
    quantity = _round_down(notional / price, asset.lot) if price > 0 else Decimal(0)
    if side == "sell":
        quantity = min(quantity, holding)
    if quantity <= 0:
        return None
    notional = _q(quantity * price)
    if notional < MIN_ORDER_NOTIONAL[mode]:
        return None

    portfolio = accounting.portfolio_view(session, mode, _latest_closes(session))
    equity = portfolio.equity_usd
    current = portfolio.weights.get(SYMBOL, 0.0)
    delta = float(notional / equity) if equity > 0 else 0.0
    target_weight = max(0.0, current + delta if side == "buy" else current - delta)

    est_fees = _q(notional * maker_fee * 2)
    worst_case = _q(notional + est_fees) if side == "buy" else est_fees

    now = clock.now()
    plan = Plan(
        sleeve=SLEEVE,
        symbol=SYMBOL,
        side=side,
        status="pending",
        reference_price=_q(last_close),
        target_weight=_q(Decimal(str(target_weight))),
        entry_orders=[
            {
                "side": side,
                "type": "limit",
                "price": str(price),
                "quantity": str(quantity),
                "maker_or_cancel": True,
            }
        ],
        stop_price=None,
        worst_case_loss=worst_case,
        est_fees=est_fees,
        rationale=(
            f"I would like to {side} ${notional} of {SYMBOL} as a "
            "maker-or-cancel ping-pong order to move the trailing 30-day "
            f"volume toward the {overrides.subsidy_target_volume_usd} fee-tier "
            "target."
        ),
        expires_at=now + builder.APPROVAL_EXPIRY,
    )
    verdict = risk_engine.check_plan(
        plan, portfolio, Limits.load(session), risk_engine.state(session, clock)
    )
    if not verdict.ok:
        log.info("ping-pong subsidy plan vetoed: %s", " ".join(verdict.reasons))
        return None
    decision = Decision(
        cycle="intraday",
        ran_at=now,
        symbol=SYMBOL,
        target_weight=plan.target_weight,
        action="plan_proposed",
        reason=f"Ping-pong subsidy proposes a {side} order. {plan.rationale}",
        details={"sleeve": SLEEVE},
    )
    session.add(decision)
    session.flush()
    plan.decision_id = decision.id
    session.add(plan)
    session.flush()
    return plan

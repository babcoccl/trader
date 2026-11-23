"""Report routes (spec 3.1): equity against baselines, period summary,
daily digests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep, latest_prices
from trader.api.schemas import (
    DigestOut,
    EquityPoint,
    SummaryOut,
    VetoScorecard,
    WeeklyReportOut,
)
from trader.backtest import engine as bt_engine
from trader.backtest.costs import BITSTAMP
from trader.core.config import get_settings
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS
from trader.ledger.models import (
    Digest,
    EquitySnapshot,
    Event,
    Fill,
    Order,
    Plan,
)

router = APIRouter(dependencies=[AuthRequired])

DEAD_STATUSES = ("denied", "expired", "voided")


def _snapshots(
    session: Session, mode: str, start: datetime | None, end: datetime | None
) -> list[EquitySnapshot]:
    stmt = (
        select(EquitySnapshot)
        .where(EquitySnapshot.mode == mode)
        .order_by(EquitySnapshot.time, EquitySnapshot.id)
    )
    if start is not None:
        stmt = stmt.where(EquitySnapshot.time >= start)
    if end is not None:
        stmt = stmt.where(EquitySnapshot.time < end)
    return list(session.scalars(stmt))


def _daily_closes(
    session: Session, symbol: str, start: datetime, end: datetime
) -> pd.Series:
    """Daily closes for ``symbol`` (the best-covered source), as a Series."""
    df = candles_mod.load(session, symbol, "1d", start, end)
    if df.empty:
        return pd.Series(dtype=float)
    if df["source"].nunique() > 1:
        preferred = df["source"].value_counts().idxmax()
        df = df[df["source"] == preferred]
    return df["close"]


BASELINE_SYMBOLS = ("BTC-USD", "ETH-USD")


def _baseline_at(closes: pd.Series, at: pd.Timestamp) -> float | None:
    """Last close at or before ``at``; None when the series starts later."""
    upto = closes.loc[:at]
    if upto.empty:
        return None
    return float(upto.iloc[-1])


@router.get("/api/reports/equity", response_model=list[EquityPoint])
def equity_report(
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    session: Session = SessionDep,
) -> list[EquityPoint]:
    """Account equity plus baselines scaled to the first account value in
    the window: cash (flat), buy-and-hold BTC, and 50/50 BTC+ETH. Baselines
    are None where no daily candle exists yet."""
    mode = get_settings().mode
    snaps = _snapshots(session, mode, from_, to)
    if not snaps:
        return []
    eq0 = float(snaps[0].equity_usd)
    start = snaps[0].time
    end = snaps[-1].time

    # Baselines use daily candles, so widen the window to whole days around
    # the snapshot times ([start, end) would often be empty).
    day_start = start - timedelta(days=2)
    day_end = end + timedelta(days=2)
    # Baselines are always BTC and ETH, whatever TRADER_SYMBOLS holds:
    # they are the comparison the backtest and the paper run are judged on.
    closes = {
        s: _daily_closes(session, s, day_start, day_end) for s in BASELINE_SYMBOLS
    }
    closes = {s: c for s, c in closes.items() if not c.empty}

    base0: dict[str, float] = {}
    for symbol, c in closes.items():
        first = _baseline_at(c, pd.Timestamp(start))
        if first is not None:
            base0[symbol] = first

    points: list[EquityPoint] = []
    for snap in snaps:
        t = pd.Timestamp(snap.time)
        ratios = {}
        for s, c in closes.items():
            if s in base0:
                close = _baseline_at(c, t)
                if close is not None:
                    ratios[s] = close / base0[s]
        btc = Decimal(str(eq0 * ratios["BTC-USD"])) if "BTC-USD" in ratios else None
        btc_eth = (
            Decimal(str(eq0 * sum(ratios.values()) / len(BASELINE_SYMBOLS)))
            if len(ratios) == len(BASELINE_SYMBOLS)
            else None
        )
        points.append(
            EquityPoint(
                time=snap.time,
                account=snap.equity_usd,
                cash=Decimal(str(eq0)),
                btc=btc,
                btc_eth=btc_eth,
            )
        )
    return points


def _veto_scorecard(session: Session, mode: str) -> VetoScorecard:
    """Would-have-been result of plans that died without approval, from
    their shadow fills: net cash moved plus the marked value of units the
    shadow buys still hold."""
    dead = session.scalars(select(Plan.id).where(Plan.status.in_(DEAD_STATUSES))).all()
    if not dead:
        return VetoScorecard(plans=0, shadow_fills=0, result_usd=None)
    rows = session.execute(
        select(Fill, Order)
        .join(Order, Fill.order_id == Order.id)
        .where(Order.plan_id.in_(dead), Order.shadow.is_(True), Order.mode == mode)
    ).all()
    if not rows:
        return VetoScorecard(plans=len(dead), shadow_fills=0, result_usd=None)

    cash = Decimal(0)
    held: dict[str, Decimal] = {}
    for fill, order in rows:
        fee_usd = fill.fee if fill.fee_currency == "USD" else fill.fee * fill.price
        base = order.symbol.split("-", 1)[0]
        if order.side == "buy":
            cash -= fill.price * fill.quantity
            held[base] = held.get(base, Decimal(0)) + fill.quantity
        else:
            cash += fill.price * fill.quantity
            held[base] = held.get(base, Decimal(0)) - fill.quantity
        cash -= fee_usd
    # Marks come from the newest stored candle per held asset, so a
    # symbol removed from TRADER_SYMBOLS still prices its shadow units.
    prices = latest_prices(session, (f"{asset}-USD" for asset in held))
    unpriced = False
    for asset, qty in held.items():
        price = prices.get(f"{asset}-USD")
        if price is not None:
            cash += qty * price
        elif qty != 0:
            # No usable mark: report unavailable rather than count the
            # held units at zero.
            unpriced = True
    return VetoScorecard(
        plans=len(dead),
        shadow_fills=len(rows),
        result_usd=None if unpriced else cash,
    )


def _backtest_gap(session: Session, snaps: list[EquitySnapshot]) -> float | None:
    """Paper-versus-backtest gap in percentage points over the snapshot
    window, or None when daily candles do not cover it."""
    # An asset-list change starts a new portfolio (spec 5.3): compare
    # only over snapshots after the most recent one.
    changed = session.scalar(
        select(func.max(Event.time)).where(Event.kind == "asset_list_changed")
    )
    if changed is not None:
        snaps = [s for s in snaps if s.time > changed]
    if len(snaps) < 2 or snaps[0].equity_usd <= 0:
        return None
    start = snaps[0].time
    end = snaps[-1].time
    frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        df = candles_mod.load(
            session, symbol, "1d", datetime(1970, 1, 1, tzinfo=UTC), end
        )
        if df.empty:
            return None
        if df["source"].nunique() > 1:
            df = df[df["source"] == df["source"].value_counts().idxmax()]
        df = df[~df.index.duplicated(keep="first")].sort_index()
        frames[symbol] = df
    opens = pd.DataFrame({s: f["open"] for s, f in frames.items()}).dropna()
    closes = pd.DataFrame({s: f["close"] for s, f in frames.items()}).dropna()
    start_str = start.date().isoformat()
    end_str = end.date().isoformat()
    try:
        result = bt_engine.run(opens, closes, BITSTAMP, start=start_str, end=end_str)
        bt_equity = result.equity.loc[start_str:end_str]
        if len(bt_equity) < 2:
            return None
        bt_return = float(bt_equity.iloc[-1] / bt_equity.iloc[0] - 1)
    except (KeyError, ValueError, ZeroDivisionError):
        return None
    paper_return = float(snaps[-1].equity_usd / snaps[0].equity_usd - 1)
    return (paper_return - bt_return) * 100


@router.get("/api/reports/summary", response_model=SummaryOut)
def summary(session: Session = SessionDep) -> SummaryOut:
    mode = get_settings().mode
    snaps = _snapshots(session, mode, None, None)

    ret = (
        float(snaps[-1].equity_usd / snaps[0].equity_usd - 1)
        if len(snaps) >= 2 and snaps[0].equity_usd > 0
        else None
    )
    worst = max((float(s.drawdown) for s in snaps), default=None)
    fees = session.scalar(
        select(func.coalesce(func.sum(Fill.fee), 0))
        .join(Order, Fill.order_id == Order.id)
        .where(Order.mode == mode, Order.shadow.is_(False), Fill.fee_currency == "USD")
    ) or Decimal(0)

    return SummaryOut(
        from_at=snaps[0].time if snaps else None,
        to_at=snaps[-1].time if snaps else None,
        return_pct=ret,
        worst_drop_pct=worst,
        fees_usd=fees if fees is not None else Decimal(0),
        vetoes=_veto_scorecard(session, mode),
        paper_vs_backtest_gap_pp=_backtest_gap(session, snaps),
    )


@router.get("/api/reports/weekly", response_model=list[WeeklyReportOut])
def weekly_reports(
    limit: Annotated[int, Query(ge=1, le=104)] = 12,
    session: Session = SessionDep,
) -> list[WeeklyReportOut]:
    """Weekly paper reports, newest first (spec R11). Fields come from
    the ``weekly_report`` event's ``details``."""
    rows = session.scalars(
        select(Event)
        .where(Event.kind == "weekly_report")
        .order_by(Event.time.desc(), Event.id.desc())
        .limit(limit)
    ).all()
    return [
        WeeklyReportOut(
            week_start=e.details["week_start"],
            week_end=e.details["week_end"],
            message=e.message,
            body_md=e.details.get("body_md", ""),
            tracking_gap_points=e.details.get("tracking_gap_points"),
            paper_return=e.details.get("paper_return"),
            backtest_return=e.details.get("backtest_return"),
            mean_uptime=e.details.get("mean_uptime"),
        )
        for e in rows
    ]


@router.get("/api/digests", response_model=list[DigestOut])
def digests(
    limit: Annotated[int, Query(ge=1, le=365)] = 30,
    session: Session = SessionDep,
) -> list[DigestOut]:
    rows = session.scalars(
        select(Digest).order_by(Digest.day.desc()).limit(limit)
    ).all()
    return [
        DigestOut(id=d.id, day=d.day, body_md=d.body_md, stats=d.stats) for d in rows
    ]

"""Ping-pong quoting worker (spec W1.3c): Hummingbot-style multi-level
maker-only quotes around the live BTC-USD bid/ask.

Replaces the 5-minute ``subsidy.propose_ping_pong_plan`` hook: a dedicated
10-second job inside the ``executor`` service fetches the live best
bid/ask from Coinbase's public ticker, rests ``subsidy_order_levels``
maker-or-cancel orders on each side at ``spread_bps * (k + 1)`` offsets,
settles fills in real time, and re-quotes when a level fills or the
``subsidy_refresh_seconds`` timer expires. Filled levels count down
``(buys_remaining, sells_remaining)``; when both reach zero the grid
resets to a full ladder.

Every order rides under a ``Plan(sleeve="ping_pong")`` so the ledger and
``subsidy.compute_status`` attribute volume and fees exactly as before,
and ``risk.check_plan`` still gates each refresh batch. The worker never
runs while halted or while the sleeve is not ``active``.
"""

import json
import logging
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.core.db import session_scope
from trader.core.runtime_settings import RuntimeConfig, load_runtime_overrides
from trader.data.sources.base import ASSETS, Asset
from trader.execution.base import ExecutionAdapter
from trader.execution.paper import PaperAdapter
from trader.ledger import accounting
from trader.ledger.models import Candle, Decision, Event, Fill, Order, Plan
from trader.plans import builder
from trader.risk import engine as risk_engine
from trader.risk.limits import Limits
from trader.strategy import subsidy

log = logging.getLogger(__name__)

TICKER_URL = "https://api.exchange.coinbase.com/products/{symbol}/ticker"
TICKER_TIMEOUT_SECONDS = 3
Q18 = Decimal("1e-18")
_POLL_WINDOW = timedelta(seconds=300)

Ticker = Callable[[str], tuple[Decimal, Decimal] | None]


def _q(x: Decimal) -> Decimal:
    return x.quantize(Q18, rounding=ROUND_HALF_UP)


def _round_down(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def fetch_live_bbo(symbol: str = subsidy.SYMBOL) -> tuple[Decimal, Decimal] | None:
    """(best_bid, best_ask) from Coinbase's public ticker; None on error."""
    url = TICKER_URL.format(symbol=symbol)
    try:
        with urllib.request.urlopen(url, timeout=TICKER_TIMEOUT_SECONDS) as r:
            payload: dict[str, Any] = json.loads(r.read())
        return Decimal(str(payload["bid"])), Decimal(str(payload["ask"]))
    except (OSError, ValueError, KeyError) as e:
        log.info("live ticker fetch failed for %s: %s", symbol, e)
        return None


class PingPongWorker:
    """Multi-level ping-pong quoting loop; one instance per service."""

    def __init__(
        self,
        *,
        ticker: Ticker = fetch_live_bbo,
        clock: Clock | None = None,
        session_factory: Callable[[], AbstractContextManager[Session]] = session_scope,
        make_adapter: Callable[[Session, str], ExecutionAdapter] | None = None,
    ) -> None:
        self._ticker = ticker
        self._clock = clock or SystemClock()
        self._session = session_factory
        self._make_adapter = make_adapter
        # None until the first active tick reads the configured order_levels.
        self.buys_remaining: int | None = None
        self.sells_remaining: int | None = None
        # Last order_levels seen: a settings change reseats the grid.
        self._levels_seen: int | None = None
        # Highest fills.id counted toward the level state so far: fills
        # booked by either loop are caught, and never counted twice.
        self._fill_watermark = 0
        # Orders whose "filled" status already consumed a level: multiple
        # executions on one order must not decrement it more than once.
        self._filled_counted: set[int] = set()

    # -- service entry -----------------------------------------------------

    def tick_safe(self) -> None:
        """Scheduler entry: own session, failures recorded not raised."""
        try:
            assert self._make_adapter is not None
            with self._session() as session:
                mode = get_settings().mode
                self.tick(session, self._make_adapter(session, mode), self._clock, mode)
        except Exception as exc:
            log.exception("ping-pong worker pass failed")
            with self._session() as session:
                session.add(
                    Event(
                        time=self._clock.now(),
                        kind="ping_pong_error",
                        severity="error",
                        message=f"Ping-pong worker pass failed: {exc}",
                        details={},
                    )
                )

    # -- one pass ------------------------------------------------------------

    def tick(
        self, session: Session, adapter: ExecutionAdapter, clock: Clock, mode: str
    ) -> None:
        overrides = load_runtime_overrides(session)
        snapshot = subsidy.compute_status(session, clock, mode)
        open_pp = self._open_ping_pong(session, adapter, mode)

        if risk_engine.state(session, clock).halted or snapshot.status != "active":
            for order in open_pp:
                adapter.cancel(order.venue_order_id or "")
            return

        # Adopt a changed level count immediately; counters only initialize
        # while the sleeve is active.
        if self._levels_seen != overrides.subsidy_order_levels:
            self._levels_seen = overrides.subsidy_order_levels
            self.buys_remaining = self.sells_remaining = overrides.subsidy_order_levels
            # Fills booked before the reseat predate the new grid; they
            # must not consume its levels.
            self._fill_watermark = int(
                session.scalar(select(func.coalesce(func.max(Fill.id), 0))) or 0
            )

        bbo = self._ticker(subsidy.SYMBOL)
        if bbo is None:
            close = self._latest_close(session)
            if close is None:
                return
            bid = ask = close
        else:
            bid, ask = bbo

        now = clock.now()
        spent_all, spent_pp = self._settle(session, adapter, open_pp, bid, ask, now)
        session.flush()
        filled_this_tick = self._count_fills(session, mode, overrides)

        open_pp = self._open_ping_pong(session, adapter, mode)
        oldest = min((o.created_at for o in open_pp), default=None)
        refresh = (
            filled_this_tick > 0
            or not open_pp
            or (
                oldest is not None
                and now - oldest > timedelta(seconds=overrides.subsidy_refresh_seconds)
            )
        )
        if not refresh:
            return
        self._refresh(
            session,
            adapter,
            overrides,
            snapshot,
            bid,
            ask,
            clock,
            now,
            mode,
            spent_all,
            spent_pp,
        )

    # -- steps ----------------------------------------------------------------

    def _settle(
        self,
        session: Session,
        adapter: ExecutionAdapter,
        open_pp: list[Order],
        bid: Decimal,
        ask: Decimal,
        now: datetime,
    ) -> tuple[Decimal, Decimal]:
        """Book new fills. Paper fills a resting ping-pong order the moment
        the live quote touches it; live relies on the venue's fill poll,
        whose results must all be booked (emitted once, whichever loop
        polls first). Returns (all-fill notional, ping-pong notional)
        booked this pass: the fee-tier target counts every fill while the
        daily booster cap counts ping-pong fills only — earlier fills are
        already inside compute_status's totals."""
        booked_all = Decimal("0")
        booked_pp = Decimal("0")
        if isinstance(adapter, PaperAdapter):
            for order in open_pp:
                if (order.side == "buy" and ask <= (order.price or 0)) or (
                    order.side == "sell" and bid >= (order.price or 0)
                ):
                    fill = adapter.fill_now(order, order.price or bid, now, maker=True)
                    session.add(fill)
                    accounting.apply_fill(session, fill)
                    booked_all += fill.price * fill.quantity
                    booked_pp += fill.price * fill.quantity
            return booked_all, booked_pp
        for fill in adapter.poll_fills(now - _POLL_WINDOW):
            filled_order = session.get(Order, fill.order_id)
            try:
                with session.begin_nested():
                    session.add(fill)
                    accounting.apply_fill(session, fill)
                    booked_all += fill.price * fill.quantity
                    plan = (
                        session.get(Plan, filled_order.plan_id)
                        if filled_order is not None
                        else None
                    )
                    if plan is not None and plan.sleeve == subsidy.SLEEVE:
                        booked_pp += fill.price * fill.quantity
            except ValueError as exc:
                if filled_order is not None:
                    filled_order.status = "rejected"
                session.add(
                    Event(
                        time=now,
                        kind="fill_rejected",
                        severity="error",
                        message=f"Rejected fill on order {fill.order_id}: {exc}",
                        details={"order_id": fill.order_id},
                    )
                )
        return booked_all, booked_pp

    def _count_fills(
        self, session: Session, mode: str, overrides: RuntimeConfig
    ) -> int:
        """Decrement the level counters for newly recorded ping_pong fills
        (booked by this worker or by the main executor pass) and reset the
        grid when both sides reach zero. Returns the new-fill count."""
        rows = session.execute(
            select(Fill.id, Order.id, Order.side, Order.status)
            .join(Order, Fill.order_id == Order.id)
            .join(Plan, Order.plan_id == Plan.id)
            .where(
                Plan.sleeve == subsidy.SLEEVE,
                Order.mode == mode,
                Order.shadow.is_(False),
                Fill.id > self._fill_watermark,
            )
            .order_by(Fill.id)
        ).all()
        for fill_id, order_id, side, order_status in rows:
            # A level is consumed once per completed order: partial fills
            # leave it alive, and batched executions of the same completed
            # order count once.
            if order_status == "filled" and order_id not in self._filled_counted:
                self._filled_counted.add(int(order_id))
                if side == "buy":
                    self.buys_remaining = max(0, (self.buys_remaining or 0) - 1)
                else:
                    self.sells_remaining = max(0, (self.sells_remaining or 0) - 1)
            self._fill_watermark = max(self._fill_watermark, int(fill_id))
        if self.buys_remaining == 0 and self.sells_remaining == 0:
            self.buys_remaining = self.sells_remaining = overrides.subsidy_order_levels
        return len(rows)

    def _refresh(
        self,
        session: Session,
        adapter: ExecutionAdapter,
        overrides: RuntimeConfig,
        snapshot: subsidy.SubsidySnapshot,
        bid: Decimal,
        ask: Decimal,
        clock: Clock,
        now: datetime,
        mode: str,
        spent_all: Decimal = Decimal("0"),
        spent_pp: Decimal = Decimal("0"),
    ) -> None:
        for order in self._open_ping_pong(session, adapter, mode):
            adapter.cancel(order.venue_order_id or "")

        remaining_budget = min(
            snapshot.remaining_to_target_usd - spent_all,
            snapshot.daily_cap_usd - snapshot.today_booster_volume_usd - spent_pp,
        )
        if remaining_budget < subsidy.MIN_ORDER_NOTIONAL[mode]:
            return

        asset = ASSETS[subsidy.SYMBOL]
        # Paper fills charge the BITSTAMP model (same rule as subsidy.py).
        fee_rate = (
            Decimal(str(snapshot.maker_bps)) / Decimal("10000")
            if mode == "live"
            else Decimal(str(BITSTAMP.maker_fee))
        )
        cash = accounting.cash(session, mode)
        btc = accounting.positions(session, mode).get(subsidy.ASSET, Decimal("0"))
        min_notional = subsidy.MIN_ORDER_NOTIONAL[mode]

        budget = remaining_budget
        entries: list[dict[str, Any]] = []
        for k in range(self.buys_remaining or 0):
            bps = Decimal(str(overrides.subsidy_spread_bps * (k + 1))) / Decimal(
                "10000"
            )
            price = _round_down(bid * (1 - bps), asset.tick)
            notional = min(
                overrides.subsidy_order_notional_usd,
                remaining_budget,
                cash / (1 + fee_rate),
            )
            quantity = (
                _round_down(notional / price, asset.lot) if price > 0 else Decimal(0)
            )
            if quantity <= 0 or _q(quantity * price) < min_notional:
                continue
            entries.append(self._entry("buy", price, quantity))
            notional = _q(quantity * price)
            cash -= notional * (1 + fee_rate)
            remaining_budget -= notional
        entries += self._sell_entries(
            overrides, remaining_budget, btc, ask, asset, min_notional
        )
        if not entries:
            return

        portfolio = accounting.portfolio_view(
            session, mode, self._latest_prices(session)
        )
        if self._place_batch(
            session,
            adapter,
            overrides,
            snapshot,
            bid,
            ask,
            clock,
            now,
            mode,
            entries,
            portfolio,
        ):
            return
        # A vetoed mixed grid still places its eligible sells so the bot
        # can unwind inventory instead of withdrawing both sides — unless
        # the veto is the halt itself, in which case nothing may quote.
        # The vetoed buys spent nothing, so the sells are re-sized against
        # the untouched budget and BTC balance.
        if risk_engine.state(session, clock).halted:
            return
        if not any(e["side"] == "buy" for e in entries):
            return
        sells = self._sell_entries(overrides, budget, btc, ask, asset, min_notional)
        if sells:
            log.info("buy side vetoed; placing %d eligible sell quotes", len(sells))
            self._place_batch(
                session,
                adapter,
                overrides,
                snapshot,
                bid,
                ask,
                clock,
                now,
                mode,
                sells,
                portfolio,
            )

    def _sell_entries(
        self,
        overrides: RuntimeConfig,
        budget: Decimal,
        btc: Decimal,
        ask: Decimal,
        asset: Asset,
        min_notional: Decimal,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for k in range(self.sells_remaining or 0):
            bps = Decimal(str(overrides.subsidy_spread_bps * (k + 1))) / Decimal(
                "10000"
            )
            price = _round_down(ask * (1 + bps), asset.tick)
            notional = min(overrides.subsidy_order_notional_usd, budget)
            quantity = (
                _round_down(notional / price, asset.lot) if price > 0 else Decimal(0)
            )
            quantity = min(quantity, btc)
            if quantity <= 0 or _q(quantity * price) < min_notional:
                continue
            entries.append(self._entry("sell", price, quantity))
            btc -= quantity
            budget -= _q(quantity * price)
        return entries

    def _place_batch(
        self,
        session: Session,
        adapter: ExecutionAdapter,
        overrides: RuntimeConfig,
        snapshot: subsidy.SubsidySnapshot,
        bid: Decimal,
        ask: Decimal,
        clock: Clock,
        now: datetime,
        mode: str,
        entries: list[dict[str, Any]],
        portfolio: accounting.Portfolio,
    ) -> bool:
        """Risk-check one batch of quote entries and place its orders.
        Returns False when check_plan vetoes it (nothing is placed)."""
        buy_notional = sum(
            (
                _q(Decimal(e["price"]) * Decimal(e["quantity"]))
                for e in entries
                if e["side"] == "buy"
            ),
            Decimal("0"),
        )
        sell_notional = sum(
            (
                _q(Decimal(e["price"]) * Decimal(e["quantity"]))
                for e in entries
                if e["side"] == "sell"
            ),
            Decimal("0"),
        )
        equity = portfolio.equity_usd
        current = portfolio.weights.get(subsidy.SYMBOL, 0.0)
        # A buy-dominant batch must be checked as a buy so check_plan's
        # sell exemption never lets new buy exposure bypass the limits.
        if buy_notional > 0:
            side = "buy"
            delta = float(buy_notional / equity) if equity > 0 else 0.0
            target_weight = max(0.0, current + delta)
        else:
            side = "sell"
            delta = float(sell_notional / equity) if equity > 0 else 0.0
            target_weight = max(0.0, current - delta)
        maker_fee = Decimal(str(snapshot.maker_bps)) / Decimal("10000")
        est_fees = _q((buy_notional + sell_notional) * maker_fee * 2)

        decision = Decision(
            cycle="intraday",
            ran_at=now,
            symbol=subsidy.SYMBOL,
            target_weight=_q(Decimal(str(target_weight))),
            action="plan_proposed",
            reason=(
                f"Ping-pong worker quotes {len(entries)} maker-only levels "
                f"around bid {bid} / ask {ask}."
            ),
            details={"sleeve": subsidy.SLEEVE},
        )
        plan = Plan(
            sleeve=subsidy.SLEEVE,
            symbol=subsidy.SYMBOL,
            side=side,
            status="approved",
            reference_price=_q((bid + ask) / 2),
            target_weight=_q(Decimal(str(target_weight))),
            entry_orders=entries,
            stop_price=None,
            worst_case_loss=_q(buy_notional + est_fees),
            est_fees=est_fees,
            rationale=(
                f"Re-quoting {len(entries)} ping-pong levels at "
                f"{overrides.subsidy_spread_bps} bps spacing to move the "
                f"trailing 30-day volume toward the "
                f"{overrides.subsidy_target_volume_usd} fee-tier target."
            ),
            expires_at=now + builder.APPROVAL_EXPIRY,
            decided_at=now,
            decision_id=0,  # linked to the persisted decision below
        )
        verdict = risk_engine.check_plan(
            plan, portfolio, Limits.load(session), risk_engine.state(session, clock)
        )
        if not verdict.ok:
            log.info("ping-pong refresh vetoed: %s", " ".join(verdict.reasons))
            return False
        # Persist the decision only once the batch passes the veto: a
        # vetoed attempt must not leave an orphaned plan_proposed row.
        session.add(decision)
        session.flush()
        plan.decision_id = decision.id
        session.add(plan)
        session.flush()
        for i, entry in enumerate(entries):
            order = Order(
                plan_id=plan.id,
                venue="paper",
                client_order_id=f"{plan.id}-{i + 1}",
                symbol=plan.symbol,
                side=entry["side"],
                type="limit",
                price=Decimal(entry["price"]),
                quantity=Decimal(entry["quantity"]),
                status="new",
                mode=mode,
                created_at=now,
            )
            session.add(order)
            session.flush()
            try:
                adapter.place(order)
            except Exception as e:
                log.info("ping-pong order rejected by venue: %s", e)
                order.status = "rejected"
        return True

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _entry(side: str, price: Decimal, quantity: Decimal) -> dict[str, Any]:
        return {
            "side": side,
            "type": "limit",
            "price": str(price),
            "quantity": str(quantity),
            "maker_or_cancel": True,
        }

    @staticmethod
    def _open_ping_pong(
        session: Session, adapter: ExecutionAdapter, mode: str
    ) -> list[Order]:
        open_orders = [o for o in adapter.open_orders() if not o.shadow]
        if not open_orders:
            return []
        sleeves = dict(
            session.execute(
                select(Plan.id, Plan.sleeve).where(
                    Plan.id.in_({o.plan_id for o in open_orders})
                )
            ).all()
        )
        return [o for o in open_orders if sleeves.get(o.plan_id) == subsidy.SLEEVE]

    @staticmethod
    def _latest_close(session: Session) -> Decimal | None:
        return session.scalar(
            select(Candle.close)
            .where(Candle.symbol == subsidy.SYMBOL, Candle.interval == "5m")
            .order_by(Candle.open_time.desc())
            .limit(1)
        )

    @staticmethod
    def _latest_prices(session: Session) -> dict[str, Decimal]:
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

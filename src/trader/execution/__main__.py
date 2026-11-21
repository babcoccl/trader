"""Executor service (spec 2.4): drives orders for approved plans through the
paper adapter, simulating against real 5-minute candles.

Every 30 seconds, and once immediately at start-up:

1. Emergency-stop check: for every held asset whose plan carries a
   ``stop_price``, if the latest stored 5m close is at or under it, cancel
   the asset's open orders and sell the whole position at market. No
   approval needed (owner decision 2026-11-20). A ``risk.flatten_requested``
   setting sells every position the same way and clears the flag.
2. Place orders for ``approved`` plans that have none yet, from the plan's
   ``entry_orders`` JSONB list; each entry is
   ``{"type": "limit"|"market"|"stop", "price": str|null,
   "stop_price": str|null, "quantity": str}`` and takes the plan's side.
   ``client_order_id`` is deterministic: ``"{plan_id}-{n}"``.
3. Apply the plan's time rules: an unfilled limit order 1 hour after
   approval converts to market; a buy limit is re-priced to the newest
   close every 5 minutes (the spec-2.2 best-bid rule).
4. Poll fills, apply each to the ledger, snapshot equity through
   ``risk.update_equity``, mark fully-terminal plans ``completed``, and
   write ``events`` rows.

Run: ``python -m trader.execution`` (Compose service ``executor``).
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from apscheduler.schedulers.blocking import (  # type: ignore[import-untyped]
    BlockingScheduler,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP, CostModel
from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.core.db import session_scope
from trader.core.logging import setup as setup_logging
from trader.data.sources.base import SYMBOLS
from trader.execution.paper import PaperAdapter
from trader.ledger import accounting
from trader.ledger.models import (
    AuditLog,
    Candle,
    Decision,
    Event,
    Heartbeat,
    Order,
    Plan,
    Setting,
)
from trader.risk import engine as risk

log = logging.getLogger(__name__)

SERVICE = "executor"
LOOP_SECONDS = 30
MARKET_AFTER = timedelta(hours=1)  # spec 2.2: unfilled limit goes to market
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _latest_prices(session: Session) -> dict[str, Decimal]:
    """Newest stored 5m close per symbol."""
    prices: dict[str, Decimal] = {}
    for symbol in SYMBOLS:
        close = session.scalar(
            select(Candle.close)
            .where(Candle.symbol == symbol, Candle.interval == "5m")
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        if close is not None:
            prices[symbol] = close
    return prices


def _newest_candle(session: Session, symbol: str) -> Candle | None:
    return session.scalars(
        select(Candle)
        .where(Candle.symbol == symbol, Candle.interval == "5m")
        .order_by(Candle.open_time.desc())
        .limit(1)
    ).first()


def _next_n(session: Session, plan_id: int) -> int:
    used = session.scalar(
        select(func.count()).select_from(Order).where(Order.plan_id == plan_id)
    )
    return int(used or 0) + 1


def _approved_at(plan: Plan) -> datetime:
    return plan.decided_at or plan.created_at


class Executor:
    """One polling loop over the orders table; safe to restart at any point."""

    def __init__(
        self,
        cost: CostModel = BITSTAMP,
        clock: Clock | None = None,
        session_factory: Callable[[], AbstractContextManager[Session]] = session_scope,
    ) -> None:
        self._cost = cost
        self._clock = clock or SystemClock()
        self._session = session_factory

    # -- loop ---------------------------------------------------------------

    def tick(self) -> None:
        """One full executor pass; also the start-up emergency check."""
        with self._session() as session:
            mode = get_settings().mode
            adapter = PaperAdapter(session, self._cost, self._clock, mode)
            now = self._clock.now()
            self._emergency_stop(session, adapter, mode, now)
            self._flatten(session, adapter, mode, now)
            halted = risk.state(session, self._clock).halted
            if halted:
                # Buys are vetoed while halted; only exposure-reducing sells
                # may keep working (spec 2.1).
                for order in adapter.open_orders():
                    if order.side == "buy":
                        adapter.cancel(order.venue_order_id or "")
            self._place_approved(session, adapter, mode, halted)
            # Fills before time rules: a candle that traded through a resting
            # limit earns its maker fill before re-pricing or the market
            # conversion can cancel the order.
            self._collect_fills(session, adapter, now)
            self._apply_time_rules(session, adapter, now)
            prices = _latest_prices(session)
            if prices:
                risk.update_equity(
                    session, accounting.equity(session, mode, prices), self._clock
                )
            self._complete_plans(session)
            session.add(
                Heartbeat(
                    service=SERVICE,
                    time=now,
                    status="ok",
                    detail="executor pass",
                )
            )

    def tick_safe(self) -> None:
        """tick() that records failures instead of dying (scheduler entry)."""
        try:
            self.tick()
        except Exception as exc:
            log.exception("executor pass failed")
            with self._session() as session:
                session.add(
                    Event(
                        time=self._clock.now(),
                        kind="executor_error",
                        severity="error",
                        message=f"Executor pass failed: {exc}",
                        details={},
                    )
                )
                session.add(
                    Heartbeat(
                        service=SERVICE,
                        time=self._clock.now(),
                        status="error",
                        detail=str(exc),
                    )
                )

    # -- steps ----------------------------------------------------------------

    def _emergency_stop(
        self, session: Session, adapter: PaperAdapter, mode: str, now: datetime
    ) -> None:
        prices = _latest_prices(session)
        for asset, qty in accounting.positions(session, mode).items():
            symbol = f"{asset}-USD"
            price = prices.get(symbol)
            if price is None:
                continue
            plan = session.scalars(
                select(Plan)
                .where(
                    Plan.symbol == symbol,
                    Plan.stop_price.is_not(None),
                    Plan.status.in_(("approved", "completed")),
                )
                .order_by(Plan.id.desc())
                .limit(1)
            ).first()
            if plan is None or plan.stop_price is None or price > plan.stop_price:
                continue
            self._cancel_symbol_orders(session, adapter, symbol)
            order = self._new_order(
                session, plan, type="market", quantity=qty, side="sell"
            )
            adapter.place(order)
            fill = adapter.fill_now(order, adapter.market_sell_price(price), now)
            session.add(fill)
            accounting.apply_fill(session, fill)
            session.add(
                Event(
                    time=now,
                    kind="emergency_stop",
                    severity="alarm",
                    message=(
                        f"Emergency stop: {symbol} at {price} is at or under "
                        f"the stop {plan.stop_price}; sold {qty} {asset} at "
                        f"market."
                    ),
                    details={
                        "plan_id": plan.id,
                        "order_id": order.id,
                        "price": str(price),
                        "stop_price": str(plan.stop_price),
                        "quantity": str(qty),
                    },
                )
            )

    def _flatten(
        self, session: Session, adapter: PaperAdapter, mode: str, now: datetime
    ) -> None:
        """``risk.flatten_requested`` sells every position at market, then
        clears the flag. The sell needs a plan for the orders FK, so a
        pre-approved system plan is written (exits never need approval)."""
        row = session.get(Setting, "risk.flatten_requested")
        if row is None or not row.value:
            return
        prices = _latest_prices(session)
        positions = accounting.positions(session, mode)
        decision = Decision(
            cycle="intraday",
            ran_at=now,
            action="plan_proposed",
            reason="Flatten requested; selling every position at market.",
            details={},
        )
        session.add(decision)
        session.flush()
        for order in adapter.open_orders():
            adapter.cancel(order.venue_order_id or "")
        for asset, qty in positions.items():
            symbol = f"{asset}-USD"
            price = prices.get(symbol)
            if price is None:
                continue
            plan = Plan(
                sleeve="trend",
                symbol=symbol,
                side="sell",
                status="approved",
                reference_price=price,
                target_weight=Decimal(0),
                entry_orders=[],
                worst_case_loss=Decimal(0),
                est_fees=Decimal(0),
                rationale="Flatten requested by the operator.",
                expires_at=now,
                decided_at=now,
                decision_id=decision.id,
            )
            session.add(plan)
            session.flush()
            order = self._new_order(session, plan, type="market", quantity=qty)
            adapter.place(order)
            fill = adapter.fill_now(order, adapter.market_sell_price(price), now)
            session.add(fill)
            accounting.apply_fill(session, fill)
        row.value = False
        row.updated_at = now
        session.add(
            AuditLog(
                time=now,
                actor="executor",
                action="set risk.flatten_requested",
                before={"risk.flatten_requested": True},
                after={"risk.flatten_requested": False},
            )
        )
        session.add(
            Event(
                time=now,
                kind="flatten",
                severity="alarm",
                message="Flatten executed: all positions sold at market.",
                details={},
            )
        )

    def _place_approved(
        self, session: Session, adapter: PaperAdapter, mode: str, halted: bool
    ) -> None:
        plans = session.scalars(
            select(Plan).where(Plan.status == "approved").order_by(Plan.id)
        ).all()
        for plan in plans:
            if halted and plan.side != "sell":
                continue
            has_orders = session.scalar(
                select(func.count()).select_from(Order).where(Order.plan_id == plan.id)
            )
            if has_orders:
                continue
            for spec in plan.entry_orders:
                order = self._new_order(
                    session,
                    plan,
                    type=spec["type"],
                    price=(
                        Decimal(str(spec["price"]))
                        if spec.get("price") is not None
                        else None
                    ),
                    stop_price=(
                        Decimal(str(spec["stop_price"]))
                        if spec.get("stop_price") is not None
                        else None
                    ),
                    quantity=Decimal(str(spec["quantity"])),
                    side=spec.get("side", plan.side),
                )
                adapter.place(order)

    def _apply_time_rules(
        self, session: Session, adapter: PaperAdapter, now: datetime
    ) -> None:
        plans = {
            p.id: p
            for p in session.scalars(
                select(Plan).where(Plan.status == "approved")
            ).all()
        }
        for order in adapter.open_orders():
            if order.type != "limit" or order.plan_id not in plans:
                continue
            plan = plans[order.plan_id]
            if now > _approved_at(plan) + MARKET_AFTER:
                adapter.cancel(order.venue_order_id or "")
                adapter.place(
                    self._new_order(
                        session, plan, type="market", quantity=order.quantity
                    )
                )
                continue
            if order.side == "buy":
                candle = _newest_candle(session, order.symbol)
                if (
                    candle is not None
                    and candle.open_time >= order.created_at
                    and candle.close != order.price
                ):
                    adapter.cancel(order.venue_order_id or "")
                    price = candle.close.quantize(
                        adapter.symbol_rules(order.symbol).tick,
                        rounding=ROUND_DOWN,
                    )
                    adapter.place(
                        self._new_order(
                            session,
                            plan,
                            type="limit",
                            price=price,
                            quantity=order.quantity,
                        )
                    )

    def _collect_fills(
        self, session: Session, adapter: PaperAdapter, now: datetime
    ) -> None:
        open_orders = adapter.open_orders()
        since = min((o.created_at for o in open_orders), default=now) - timedelta(
            seconds=300
        )
        for fill in adapter.poll_fills(since):
            order = session.get(Order, fill.order_id)
            try:
                with session.begin_nested():
                    session.add(fill)
                    accounting.apply_fill(session, fill)
            except ValueError as exc:
                # Unfillable in the ledger (e.g. a market buy gapping above
                # available cash): reject the order instead of stalling the
                # whole pass, and record why.
                if order is not None:
                    order.status = "rejected"
                session.add(
                    Event(
                        time=now,
                        kind="fill_rejected",
                        severity="error",
                        message=(f"Rejected fill on order {fill.order_id}: {exc}"),
                        details={"order_id": fill.order_id},
                    )
                )
                continue
            session.add(
                Event(
                    time=now,
                    kind="fill",
                    severity="info",
                    message=(
                        f"Filled {order.side if order else '?'} "
                        f"{fill.quantity} {order.symbol if order else ''} at "
                        f"{fill.price} ({fill.liquidity})."
                    ),
                    details={
                        "order_id": fill.order_id,
                        "price": str(fill.price),
                        "quantity": str(fill.quantity),
                        "fee": str(fill.fee),
                    },
                )
            )

    def _complete_plans(self, session: Session) -> None:
        plans = session.scalars(select(Plan).where(Plan.status == "approved")).all()
        for plan in plans:
            statuses = session.scalars(
                select(Order.status).where(Order.plan_id == plan.id)
            ).all()
            if statuses and all(
                s in ("filled", "cancelled", "rejected") for s in statuses
            ):
                plan.status = "completed"
                session.add(
                    Event(
                        time=self._clock.now(),
                        kind="plan_completed",
                        severity="info",
                        message=f"Plan {plan.id} ({plan.side} {plan.symbol}) "
                        "completed.",
                        details={"plan_id": plan.id},
                    )
                )

    # -- helpers ----------------------------------------------------------

    def _cancel_symbol_orders(
        self, session: Session, adapter: PaperAdapter, symbol: str
    ) -> None:
        for order in adapter.open_orders():
            if order.symbol == symbol:
                adapter.cancel(order.venue_order_id or "")

    def _new_order(
        self,
        session: Session,
        plan: Plan,
        *,
        type: str,
        quantity: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        side: str | None = None,
    ) -> Order:
        order = Order(
            plan_id=plan.id,
            venue="paper",
            client_order_id=f"{plan.id}-{_next_n(session, plan.id)}",
            symbol=plan.symbol,
            side=side or plan.side,
            type=type,
            price=price,
            stop_price=stop_price,
            quantity=quantity,
            status="new",
            mode=get_settings().mode,
        )
        session.add(order)
        session.flush()
        return order

    # -- service ----------------------------------------------------------

    def serve(self) -> None:
        self.tick_safe()  # the emergency-stop check also runs at start-up
        scheduler = BlockingScheduler(timezone=UTC)
        scheduler.add_job(
            self.tick_safe,
            "interval",
            seconds=LOOP_SECONDS,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()


def main() -> None:
    settings = get_settings()
    setup_logging(service=SERVICE, level=settings.log_level)
    Executor().serve()


if __name__ == "__main__":
    main()

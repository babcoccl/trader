"""Venue reconciliation (spec 5.2).

``run`` compares the ledger with the live venue once a minute (called
from the watchdog's per-minute pass in live mode) and raises alarms on
any difference worth more than $0.01:

- ``replay`` balances (deposits + fills, independent of the stored
  ``balances`` rows) versus the adapter's ``balances()``;
- the orders table's open live orders versus the venue's open orders.

Crypto differences are valued at the latest stored 5m close so the
$0.01 tolerance means the same thing for every asset.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.core.clock import Clock
from trader.core.config import get_settings
from trader.data.sources.base import SYMBOLS
from trader.execution.base import ExecutionAdapter
from trader.execution.bitstamp import BitstampAdapter
from trader.execution.coinbase import CoinbaseAdvancedAdapter
from trader.ledger import accounting
from trader.ledger.models import Candle, Order
from trader.ops.watchdog import Alarm

TOLERANCE_USD = Decimal("0.01")
_OPEN_STATUSES = ("new", "open", "partially_filled")


def _latest_prices(session: Session) -> dict[str, Decimal]:
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


def _diff_usd(asset: str, diff: Decimal, prices: dict[str, Decimal]) -> Decimal:
    if asset == "USD":
        return abs(diff)
    price = prices.get(f"{asset}-USD")
    if price is None:
        # No candle to value the difference: alarm on any discrepancy.
        return Decimal(0) if diff == 0 else Decimal("Infinity")
    return abs(diff * price)


def run(
    session: Session,
    clock: Clock,
    adapter: ExecutionAdapter | None = None,
    mode: str | None = None,
) -> list[Alarm]:
    """One reconcile pass; returns the alarms that hold right now."""
    mode = mode or get_settings().mode
    if adapter is None:
        try:
            if get_settings().venue == "coinbase":
                adapter = CoinbaseAdvancedAdapter.from_env(session, clock, mode=mode)
            else:
                adapter = BitstampAdapter.from_env(session, mode=mode)
        except Exception as exc:
            return [
                Alarm(
                    key="venue_unreachable",
                    message=f"Cannot reach the live venue adapter: {exc}",
                )
            ]

    alarms: list[Alarm] = []

    # 1. Balances: ledger replayed from deposits and fills vs venue totals.
    ledger_ok = True
    try:
        ledger = accounting.replay(session, mode)
    except ValueError as exc:
        ledger_ok = False
        alarms.append(
            Alarm(
                key="ledger_vs_venue",
                message=f"Ledger cannot be replayed from fills: {exc}",
            )
        )
    if ledger_ok:
        prices = _latest_prices(session)
        try:
            venue = adapter.balances()
        except Exception as exc:
            return alarms + [
                Alarm(
                    key="venue_unreachable",
                    message=f"Cannot read venue balances: {exc}",
                )
            ]
        diffs = {
            asset: (
                ledger.get(asset, Decimal(0)),
                venue.get(asset, Decimal(0)),
            )
            for asset in ledger.keys() | venue.keys()
            if _diff_usd(
                asset,
                ledger.get(asset, Decimal(0)) - venue.get(asset, Decimal(0)),
                prices,
            )
            > TOLERANCE_USD
        }
        if diffs:
            alarms.append(
                Alarm(
                    key="ledger_vs_venue",
                    message=(
                        "Ledger balances differ from the venue: "
                        + ", ".join(
                            f"{a} ledger {p[0]} vs venue {p[1]}"
                            for a, p in sorted(diffs.items())
                        )
                    ),
                    details={
                        "diffs": {a: [str(p[0]), str(p[1])] for a, p in diffs.items()}
                    },
                )
            )

    # 2. Open orders: the orders table vs the venue's open_orders list.
    ours = {
        o.venue_order_id
        for o in session.scalars(
            select(Order)
            .where(
                Order.venue == getattr(adapter, "venue", "bitstamp"),
                Order.mode == mode,
            )
            .where(Order.status.in_(_OPEN_STATUSES), Order.shadow.is_(False))
        )
        if o.venue_order_id
    }
    try:
        theirs = {
            str(o.get("order_id") or o["id"])
            for o in getattr(adapter, "venue_open_orders", lambda: [])()
        }
    except Exception as exc:
        return alarms + [
            Alarm(
                key="venue_unreachable",
                message=f"Cannot read venue open orders: {exc}",
            )
        ]
    missing_at_venue = ours - theirs
    missing_in_ledger = theirs - ours
    if missing_at_venue or missing_in_ledger:
        parts = []
        if missing_at_venue:
            parts.append(
                f"open in the ledger but not at the venue: {sorted(missing_at_venue)}"
            )
        if missing_in_ledger:
            parts.append(
                f"open at the venue but not in the ledger: {sorted(missing_in_ledger)}"
            )
        alarms.append(
            Alarm(
                key="open_orders_vs_venue",
                message="Open orders differ from the venue: " + "; ".join(parts),
                details={
                    "ledger_only": sorted(missing_at_venue),
                    "venue_only": sorted(missing_in_ledger),
                },
            )
        )

    return alarms

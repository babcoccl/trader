"""Coinbase Advanced Trade live execution adapter (spec W1.1b).

Speaks the private REST v3 API under ``https://api.coinbase.com`` through
the ``ExecutionAdapter`` protocol the executor already uses.

Safety: ``place`` raises unless ``mode == "live"`` *and*
``TRADER_LIVE_ENABLED=true`` — the adapter cannot trade by default.
Read endpoints (``balances``, ``open_orders``, ``poll_fills``,
``symbol_rules``, ``fee_summary``) work in any mode. ``stop`` orders are
never sent to the venue: the application runs the disaster stop itself
(spec 2.2/2.4).

Authentication: Coinbase Developer Platform (CDP) ES256 JWT sent as
``Authorization: Bearer <jwt>``. The token is signed with the EC private
key PEM (``TRADER_COINBASE_SECRET``); its ``kid`` and ``sub`` are the key
name (``TRADER_COINBASE_KEY``), ``iss`` is ``"cdp"``, ``uri`` is
``f"{method} {host}{path}"`` and it expires 120 seconds after issue.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.data.sources.coinbase import PRODUCTS
from trader.execution.base import SymbolRules
from trader.ledger.models import Fill, Order

BASE_URL = "https://api.coinbase.com"
_OPEN_STATUSES = ("new", "open", "partially_filled")
_POST_ONLY_RETRIES = 3
_PRODUCTS_TTL_SECONDS = 3600
# Public product metadata, keyed by (base URL, product id) and shared
# across adapters: the executor builds a fresh adapter every pass.
_products_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


class CoinbaseError(Exception):
    """A venue call failed or returned an error payload."""


Http = Callable[[str, str, dict[str, str], str | None], Any]


def _default_http(
    method: str, url: str, headers: dict[str, str], body: str | None
) -> Any:
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8") if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise CoinbaseError(f"HTTP {e.code} for {url}: {e.read()[:500]!r}") from e


def _round_down(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def _dt(value: str) -> datetime:
    """Coinbase timestamps are ISO-8601 UTC."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CoinbaseAdvancedAdapter:
    """Live Coinbase Advanced Trade venue adapter; cannot place orders
    until enabled."""

    venue = "coinbase"

    def __init__(
        self,
        session: Session,
        key: str,
        secret: str,
        clock: Clock | None = None,
        mode: str = "live",
        base_url: str = BASE_URL,
        http: Http | None = None,
        nonce_fn: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._session = session
        self._key = key
        self._secret = secret
        self._clock = clock or SystemClock()
        self._mode = mode
        self._base = base_url.rstrip("/")
        self._host = urllib.parse.urlsplit(self._base).netloc
        self._http = http or _default_http
        self._nonce_fn = nonce_fn

    @classmethod
    def from_env(
        cls,
        session: Session,
        clock: Clock | None = None,
        mode: str = "live",
    ) -> "CoinbaseAdvancedAdapter":
        settings = get_settings()
        if not settings.coinbase_key or not settings.coinbase_secret:
            raise CoinbaseError(
                "TRADER_COINBASE_KEY / TRADER_COINBASE_SECRET are not set"
            )
        return cls(
            session,
            settings.coinbase_key,
            settings.coinbase_secret,
            clock=clock,
            mode=mode,
            base_url=settings.coinbase_base_url,
        )

    # -- transport ---------------------------------------------------------

    def _jwt(self, method: str, path: str) -> str:
        now = self._clock.now()
        return jwt.encode(
            {
                "iss": "cdp",
                "sub": self._key,
                "nbf": int(now.timestamp()),
                "exp": int(now.timestamp()) + 120,
                "uri": f"{method} {self._host}{path}",
            },
            self._secret,
            algorithm="ES256",
            headers={"kid": self._key, "nonce": self._nonce_fn()},
        )

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = self._base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Authorization": f"Bearer {self._jwt(method, path)}",
            "Content-Type": "application/json",
        }
        payload = self._http(
            method, url, headers, json.dumps(body) if body is not None else None
        )
        if isinstance(payload, dict) and (
            payload.get("error") or payload.get("success") is False
        ):
            detail = payload.get("error_response") or payload.get("error")
            raise CoinbaseError(f"{path} rejected: {detail}")
        return payload

    # -- ExecutionAdapter ---------------------------------------------------

    def place(self, order: Order) -> str:
        settings = get_settings()
        if not (settings.mode == "live" and settings.live_enabled):
            raise RuntimeError(
                f"refusing to place a live order: mode={settings.mode}, "
                f"TRADER_LIVE_ENABLED={settings.live_enabled}"
            )
        if order.type == "stop":
            # Coinbase spot stop orders are application-run (spec 2.2).
            raise ValueError("stop orders are application-run, not sent to the venue")
        product = PRODUCTS[order.symbol]
        rules = self.symbol_rules(order.symbol)
        quantity = _round_down(order.quantity, rules.lot)
        price: Decimal | None = None
        if order.type == "limit":
            assert order.price is not None
            price = _round_down(order.price, rules.tick)
            if quantity * price < rules.min_notional:
                raise ValueError(
                    f"order notional {quantity * price} under minimum "
                    f"{rules.min_notional}"
                )
        attempts = 1 if order.type == "market" else _POST_ONLY_RETRIES
        for _ in range(attempts):
            try:
                payload = self._request(
                    "POST",
                    "/api/v3/brokerage/orders",
                    body={
                        "client_order_id": order.client_order_id,
                        "product_id": product,
                        "side": order.side.upper(),
                        "order_configuration": self._configuration(
                            order, quantity, price
                        ),
                    },
                )
            except CoinbaseError as e:
                if "POST_ONLY" in str(e) and order.type == "limit":
                    # Post-only rejection: the limit would have crossed.
                    # Re-quote at the new best bid (buy) or ask (sell),
                    # never past the submitted limit price.
                    assert order.price is not None
                    limit = _round_down(order.price, rules.tick)
                    price = self._maker_price(order, product)
                    if (order.side == "buy" and price > limit) or (
                        order.side == "sell" and price < limit
                    ):
                        raise CoinbaseError(
                            "post-only re-quote would cross the submitted limit price"
                        ) from e
                    continue
                raise
            break
        else:
            raise CoinbaseError(
                f"order rejected by venue {attempts} times (post-only cross)"
            )
        if not isinstance(payload, dict) or not payload.get("success"):
            raise CoinbaseError(f"unexpected place response: {payload!r}")
        venue_id = str(payload["success_response"]["order_id"])
        order.venue = self.venue
        order.venue_order_id = venue_id
        order.price = price
        order.quantity = quantity
        order.status = "open"
        # The venue holds the order now: persist its id at once so a later
        # failure in the same pass cannot roll back the only local record.
        self._session.commit()
        return venue_id

    def cancel(self, venue_order_id: str) -> bool:
        try:
            payload = self._request(
                "POST",
                "/api/v3/brokerage/orders/batch_cancel",
                body={"order_ids": [venue_order_id]},
            )
        except CoinbaseError as e:
            if "not found" in str(e).lower() or "unknown" in str(e).lower():
                return False
            raise
        results = payload.get("results") if isinstance(payload, dict) else None
        if not results:
            return False
        return bool(results[0].get("success"))

    def open_orders(self) -> list[Order]:
        """The orders table's open venue orders (mirror of the venue book).

        A venue-cancelled order stays listed locally until ``poll_fills``
        records its fills with the status, so neither can be committed
        without the other.
        """
        return list(
            self._session.scalars(
                select(Order)
                .where(Order.venue == self.venue, Order.mode == self._mode)
                .where(Order.status.in_(_OPEN_STATUSES))
                .order_by(Order.id)
            )
        )

    def venue_open_orders(self) -> list[dict[str, Any]]:
        """Raw venue rows for every order Coinbase still lists as OPEN."""
        payload = self._request(
            "GET",
            "/api/v3/brokerage/orders/historical/batch",
            {"order_status": "OPEN"},
        )
        return list(payload.get("orders") or [])

    def poll_fills(self, since: datetime) -> list[Fill]:
        """Fetch each working order's fills and emit new Fill rows.

        Fills for an order are cumulative; entries past the stored fill
        count are new, so replays never duplicate. Order status is
        refreshed on the same pass.
        """
        del since  # dedupe is by stored fills, not a time window
        orders = [o for o in self.open_orders() if o.venue_order_id]
        # One batch call gives every working order's venue status.
        statuses = self._venue_statuses([o.venue_order_id or "" for o in orders])
        new_fills: list[Fill] = []
        for order in orders:
            rows = self._fills_for_order(order)
            new_fills.extend(self._new_fills(order, rows))
            self._apply_status(order, rows, statuses.get(order.venue_order_id or ""))
        self._session.flush()
        return new_fills

    def balances(self) -> dict[str, Decimal]:
        payload = self._request("GET", "/api/v3/brokerage/accounts")
        out: dict[str, Decimal] = {}
        for row in payload.get("accounts") or []:
            total = Decimal(
                str((row.get("available_balance") or {}).get("value", "0"))
            ) + Decimal(str((row.get("hold") or {}).get("value", "0")))
            if total != 0:
                out[str(row["currency"]).upper()] = total
        return out

    def symbol_rules(self, symbol: str) -> SymbolRules:
        product = self._product_info(symbol)
        return SymbolRules(
            tick=Decimal(str(product["quote_increment"])),
            lot=Decimal(str(product["base_increment"])),
            min_notional=Decimal(str(product["quote_min_size"])),
        )

    # -- venue-only helpers ---------------------------------------------------

    def fee_summary(self) -> tuple[Decimal, Decimal, Decimal]:
        """(maker_fee, taker_fee, trailing_30d_volume_usd) from
        ``GET /api/v3/brokerage/transaction_summary``."""
        payload = self._request("GET", "/api/v3/brokerage/transaction_summary")
        tier = payload.get("fee_tier") or {}
        return (
            Decimal(str(tier.get("maker_fee_rate", "0"))),
            Decimal(str(tier.get("taker_fee_rate", "0"))),
            Decimal(str(payload.get("total_volume", "0"))),
        )

    # -- internals ----------------------------------------------------------

    def _seen_fills(self, order: Order) -> set[tuple[datetime, Decimal, Decimal]]:
        """Stored fills for the order as (time, price, quantity) keys.

        Coinbase's fill list is cumulative but not guaranteed to stay in
        order, so dedupe is by fill identity rather than position: a new
        fill that lands before a recorded one is still emitted, and a
        recorded one is never emitted twice.
        """
        return {
            (f.time, f.price, f.quantity)
            for f in self._session.scalars(
                select(Fill).where(Fill.order_id == order.id)
            )
        }

    def _venue_statuses(self, order_ids: list[str]) -> dict[str, str]:
        """order_id -> venue status for every working order, one batch call."""
        if not order_ids:
            return {}
        payload = self._request(
            "GET",
            "/api/v3/brokerage/orders/historical/batch",
            {"order_ids": ",".join(order_ids)},
        )
        return {
            str(row["order_id"]): str(row.get("status", "")).upper()
            for row in payload.get("orders") or []
        }

    def _new_fills(self, order: Order, rows: list[dict[str, Any]]) -> list[Fill]:
        seen = self._seen_fills(order)
        out = []
        for row in rows:
            key = (
                _dt(str(row["trade_time"])),
                Decimal(str(row["price"])),
                Decimal(str(row["size"])),
            )
            if key not in seen:
                seen.add(key)  # also dedupe repeats within this fetch
                out.append(self._make_fill(order, row))
        return out

    def _fills_for_order(self, order: Order) -> list[dict[str, Any]]:
        """Every fill the venue reports for the order, paging until the
        cursor is exhausted — a terminal order may hold more than one
        page and never polls again once closed."""
        rows: list[dict[str, Any]] = []
        params: dict[str, Any] = {"order_ids": order.venue_order_id}
        while True:
            payload = self._request(
                "GET",
                "/api/v3/brokerage/orders/historical/fills",
                params,
            )
            rows.extend(payload.get("fills") or [])
            cursor = payload.get("cursor")
            if not cursor:
                return rows
            params["cursor"] = cursor

    def _make_fill(self, order: Order, row: dict[str, Any]) -> Fill:
        quote = order.symbol.split("-", 1)[1]
        return Fill(
            order_id=order.id,
            time=_dt(str(row["trade_time"])),
            price=Decimal(str(row["price"])),
            quantity=Decimal(str(row["size"])),
            fee=Decimal(str(row.get("commission", "0"))),
            fee_currency=quote,
            liquidity=(
                "maker"
                if str(row.get("liquidity_indicator", "")).upper() == "MAKER"
                else "taker"
            ),
        )

    def _apply_status(
        self,
        order: Order,
        rows: list[dict[str, Any]],
        venue_status: str | None,
    ) -> None:
        filled = sum(Decimal(str(r["size"])) for r in rows)
        if venue_status == "FILLED" or filled >= order.quantity:
            order.status = "filled"
        elif venue_status == "CANCELLED":
            order.status = "cancelled"
        elif filled > 0:
            order.status = "partially_filled"

    @staticmethod
    def _configuration(
        order: Order, quantity: Decimal, price: Decimal | None
    ) -> dict[str, Any]:
        if order.type == "limit":
            assert price is not None
            return {
                "limit_limit_gtc": {
                    "base_size": str(quantity),
                    "limit_price": str(price),
                    "post_only": True,
                }
            }
        return {"market_market_ioc": {"base_size": str(quantity)}}

    def _maker_price(self, order: Order, product: str) -> Decimal:
        """The maker-side quote for a re-priced limit order: best bid for a
        buy, best ask for a sell (both join the book without crossing)."""
        payload = self._request(
            "GET",
            "/api/v3/brokerage/best_bid_ask",
            {"product_ids": product},
        )
        books = payload.get("pricebooks") or []
        if not books:
            raise CoinbaseError(f"unexpected best_bid_ask response: {payload!r}")
        book = books[0]
        side = "bids" if order.side == "buy" else "asks"
        price = Decimal(str(book[side][0]["price"]))
        return _round_down(price, self.symbol_rules(order.symbol).tick)

    def _product_info(self, symbol: str) -> dict[str, Any]:
        product = PRODUCTS[symbol]
        key = (self._base, product)
        now = self._clock.now().timestamp()
        cached = _products_cache.get(key)
        if cached is None or not 0 <= now - cached[0] <= _PRODUCTS_TTL_SECONDS:
            payload = self._request("GET", f"/api/v3/brokerage/products/{product}")
            cached = (now, payload)
            _products_cache[key] = cached
        return cached[1]

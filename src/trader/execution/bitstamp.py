"""Bitstamp live execution adapter (spec 5.2).

Speaks the private REST v2 API under ``https://www.bitstamp.net/api/v2``
(all calls ``POST``, form-encoded) through the ``ExecutionAdapter``
protocol the executor already uses. Facts are recorded in
``docs/VENUE.md``.

Safety: ``place`` raises unless ``mode == "live"`` *and*
``TRADER_LIVE_ENABLED=true`` — the adapter cannot trade by default.
Read endpoints (``balances``, ``open_orders``, ``poll_fills``,
``symbol_rules``, ``trading_fee``) work in any mode. ``stop`` orders are
never sent to the venue: Bitstamp's API has no spot stop orders, so the
application runs the disaster stop itself (spec 2.2/2.4).

Authentication (API reference, "Authentication"): every request signs
``"BITSTAMP " + key + verb + host + path + query + content_type + nonce +
timestamp + "v2" + body`` with HMAC-SHA256(hex, secret) and sends
``X-Auth``, ``X-Auth-Signature``, ``X-Auth-Nonce``, ``X-Auth-Timestamp``,
``X-Auth-Version`` headers. Content-Type is omitted from both headers
and signature when the body is empty. Nonce is a lowercase UUID, unique
within 150 seconds; timestamp is UTC milliseconds within 150 seconds of
server time.

Fills: ``order_status/`` returns the order's ``transactions`` list —
each row ``{tid, price, <base>: amount, <quote>: cost, fee, datetime,
type}`` cumulative over the order's life. New fills for an order are the
transactions past the count of its stored ``fills`` rows, so polling is
idempotent across restarts. Liquidity is not reported per fill; a limit
order's fills book as ``maker`` (entries are maker-or-cancel), anything
else ``taker``. Fees are charged in the quote currency (USD).
"""

import hashlib
import hmac
import json
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.data.sources.bitstamp import MARKETS
from trader.execution.base import SymbolRules
from trader.ledger.models import Fill, Order

BASE_URL = "https://www.bitstamp.net/api/v2"
HOST = "www.bitstamp.net"
CONTENT_TYPE = "application/x-www-form-urlencoded"
_OPEN_STATUSES = ("new", "open", "partially_filled")
_MOC_RETRIES = 3
_MARKETS_TTL_SECONDS = 3600
# Public market metadata, keyed by base URL and shared across adapters:
# the executor builds a fresh adapter every pass.
_markets_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


class BitstampError(Exception):
    """A venue call failed or returned an error payload."""


class LiveTradingDisabledError(BitstampError):
    """``place`` was called while the mode/live-enable gate is closed."""


Post = Callable[[str, dict[str, Any], dict[str, str]], Any]
Get = Callable[[str], Any]


def sign(
    api_key: str,
    api_secret: str,
    method: str,
    host: str,
    path: str,
    query: str,
    content_type: str,
    nonce: str,
    timestamp: str,
    body: str,
) -> str:
    """HMAC-SHA256 signature over the documented string_to_sign."""
    message = (
        f"BITSTAMP {api_key}"
        + method
        + host
        + path
        + query
        + content_type
        + nonce
        + timestamp
        + "v2"
        + body
    )
    return hmac.new(
        api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _default_post(url: str, params: dict[str, Any], headers: dict[str, str]) -> Any:
    # Empty body must send no Content-Type (the signature omits it too);
    # urllib adds one when `data` is given, so pass None and force POST.
    body = urllib.parse.urlencode(params).encode("utf-8") or None
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise BitstampError(f"HTTP {e.code} for {url}: {e.read()[:500]!r}") from e


def _default_get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def _dt(value: str) -> datetime:
    """Venue datetimes are UTC in 'YYYY-MM-DD HH:MM:SS' form."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _round_down(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def _check_response(payload: Any, what: str) -> Any:
    if isinstance(payload, dict) and payload.get("status") == "error":
        raise BitstampError(f"{what} rejected: {payload.get('reason')}")
    return payload


class BitstampAdapter:
    """Live Bitstamp venue adapter; cannot place orders until enabled."""

    venue = "bitstamp"

    def __init__(
        self,
        session: Session,
        key: str,
        secret: str,
        clock: Clock | None = None,
        mode: str = "live",
        base_url: str = BASE_URL,
        post: Post | None = None,
        get: Get | None = None,
        nonce_fn: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._session = session
        self._key = key
        self._secret = secret
        self._clock = clock or SystemClock()
        self._mode = mode
        self._base = base_url.rstrip("/")
        parts = urllib.parse.urlsplit(base_url)
        # Private paths already carry "/api/v2"; origin avoids doubling it.
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._host = parts.netloc
        self._post = post or _default_post
        self._get = get or _default_get
        self._nonce_fn = nonce_fn

    @classmethod
    def from_env(
        cls,
        session: Session,
        clock: Clock | None = None,
        mode: str = "live",
    ) -> "BitstampAdapter":
        settings = get_settings()
        if not settings.bitstamp_key or not settings.bitstamp_secret:
            raise BitstampError(
                "TRADER_BITSTAMP_KEY / TRADER_BITSTAMP_SECRET are not set"
            )
        return cls(
            session,
            settings.bitstamp_key,
            settings.bitstamp_secret,
            clock=clock,
            mode=mode,
            base_url=settings.bitstamp_base_url,
        )

    # -- transport ---------------------------------------------------------

    def _request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Signed POST; returns parsed JSON, raises BitstampError on errors."""
        body = urllib.parse.urlencode(params) if params else ""
        content_type = CONTENT_TYPE if body else ""
        nonce = self._nonce_fn()
        timestamp = str(int(self._clock.now().timestamp() * 1000))
        signature = sign(
            self._key,
            self._secret,
            method,
            self._host,
            path,
            "",
            content_type,
            nonce,
            timestamp,
            body,
        )
        headers = {
            "X-Auth": f"BITSTAMP {self._key}",
            "X-Auth-Signature": signature,
            "X-Auth-Nonce": nonce,
            "X-Auth-Timestamp": timestamp,
            "X-Auth-Version": "v2",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return _check_response(
            self._post(f"{self._origin}{path}", params or {}, headers), path
        )

    # -- ExecutionAdapter ---------------------------------------------------

    def place(self, order: Order) -> str:
        settings = get_settings()
        if not (settings.mode == "live" and settings.live_enabled):
            raise LiveTradingDisabledError(
                f"refusing to place a live order: mode={settings.mode}, "
                f"TRADER_LIVE_ENABLED={settings.live_enabled}"
            )
        if order.type == "stop":
            # Bitstamp's API has no spot stop orders; the application runs
            # the disaster stop itself (spec 2.2).
            raise ValueError("stop orders are application-run, not sent to the venue")
        market = MARKETS[order.symbol]
        rules = self.symbol_rules(order.symbol)
        quantity = _round_down(order.quantity, rules.lot)
        price = (
            _round_down(order.price, rules.tick) if order.price is not None else None
        )
        if price is None:
            price_for_notional = self.ticker(order.symbol)["last"]
        else:
            price_for_notional = price
        notional = quantity * price_for_notional
        if notional < rules.min_notional:
            raise ValueError(
                f"order notional {notional} under minimum {rules.min_notional}"
            )
        attempts = 1 if order.type == "market" else _MOC_RETRIES
        for _ in range(attempts):
            payload = self._place_once(order, market, quantity, price)
            if payload is not None:
                venue_id = str(payload["id"])
                order.venue = self.venue
                order.venue_order_id = venue_id
                order.price = price
                order.quantity = quantity
                order.status = "open"
                # The venue holds the order now: persist its id at once so a
                # later failure in the same pass cannot roll back the only
                # local record of it.
                self._session.commit()
                return venue_id
            # Maker-or-cancel rejection: the order would have taken
            # liquidity. Re-quote at the new best bid (buy) or ask (sell).
            price = self._maker_price(order)
        raise BitstampError(
            f"order rejected by venue {attempts} times (maker-or-cancel)"
        )

    def cancel(self, venue_order_id: str) -> bool:
        try:
            self._request("POST", "/api/v2/cancel_order/", {"id": venue_order_id})
        except BitstampError as e:
            if "not found" in str(e).lower():
                return False
            raise
        # The order stays open locally: the next poll_fills sees the venue's
        # "Canceled" status and records any fills executed before the
        # cancel in the same step, so neither can be committed without
        # the other.
        return True

    def open_orders(self) -> list[Order]:
        """The orders table's open venue orders (mirror of the venue book)."""
        return list(
            self._session.scalars(
                select(Order)
                .where(Order.venue == self.venue, Order.mode == self._mode)
                .where(Order.status.in_(_OPEN_STATUSES))
                .order_by(Order.id)
            )
        )

    def poll_fills(self, since: datetime) -> list[Fill]:
        """Fetch each working order's transactions and emit new fills.

        ``order_status`` returns an order's cumulative transaction list;
        entries past the order's stored fill count are new, so replays
        never duplicate. Order status is refreshed on the same pass.
        """
        del since  # dedupe is by stored fill count, not a time window
        new_fills: list[Fill] = []
        for order in self.open_orders():
            payload = self._order_status(order)
            if payload is None:
                continue
            new_fills.extend(self._fills_for(order, payload))
            self._apply_status(order, payload)
        self._session.flush()
        return new_fills

    def balances(self) -> dict[str, Decimal]:
        rows = self._request("POST", "/api/v2/account_balances/")
        out: dict[str, Decimal] = {}
        for row in rows:
            total = Decimal(str(row["total"]))
            if total != 0:
                out[str(row["currency"]).upper()] = total
        return out

    def symbol_rules(self, symbol: str) -> SymbolRules:
        market = self._market_info(symbol)
        base_dp = int(market["base_decimals"])
        counter_dp = int(market["counter_decimals"])
        return SymbolRules(
            tick=Decimal(1).scaleb(-counter_dp),
            lot=Decimal(1).scaleb(-base_dp),
            min_notional=Decimal(str(market["minimum_order_value"])),
        )

    # -- venue-only helpers ---------------------------------------------------

    def ticker(self, symbol: str) -> dict[str, Decimal]:
        """Public ``/ticker/{market}/``: bid, ask, last."""
        market = MARKETS[symbol]
        row = self._get(f"{self._base}/ticker/{market}/")
        return {
            "bid": Decimal(str(row["bid"])),
            "ask": Decimal(str(row["ask"])),
            "last": Decimal(str(row["last"])),
        }

    def trading_fee(self, symbol: str) -> dict[str, Decimal]:
        """The account's actual maker/taker fee for ``symbol``."""
        market = MARKETS[symbol]
        row = self._request("POST", f"/api/v2/fees/trading/{market}")
        return {
            "maker": Decimal(str(row["fees"]["maker"])),
            "taker": Decimal(str(row["fees"]["taker"])),
        }

    def venue_open_orders(self) -> list[dict[str, Any]]:
        """Raw ``open_orders/{market}/`` rows for both markets."""
        out: list[dict[str, Any]] = []
        for market in MARKETS.values():
            rows = self._request("POST", f"/api/v2/open_orders/{market}/")
            out.extend(rows)
        return out

    # -- internals ----------------------------------------------------------

    def _place_once(
        self,
        order: Order,
        market: str,
        quantity: Decimal,
        price: Decimal | None,
    ) -> dict[str, Any] | None:
        """One placement attempt. Returns the order payload, or None on a
        maker-or-cancel rejection that should be re-quoted."""
        params: dict[str, Any] = {
            "amount": str(quantity),
            "client_order_id": order.client_order_id,
        }
        if order.type == "limit":
            assert price is not None
            params["price"] = str(price)
            params["moc_order"] = "true"
            path = f"/api/v2/{order.side}/{market}/"
        else:
            path = f"/api/v2/{order.side}/market/{market}/"
        payload = self._request("POST", path, params)
        if not isinstance(payload, dict) or "id" not in payload:
            raise BitstampError(f"unexpected place response: {payload!r}")
        if str(payload.get("status", "")).lower() == "canceled":
            # MOC order cancelled because it would have crossed.
            return None
        return payload

    def _maker_price(self, order: Order) -> Decimal:
        """The maker-side quote for a re-priced limit order: best bid for a
        buy, best ask for a sell (both join the book without crossing)."""
        t = self.ticker(order.symbol)
        price = t["bid"] if order.side == "buy" else t["ask"]
        return _round_down(price, self.symbol_rules(order.symbol).tick)

    def _fills_for(self, order: Order, payload: dict[str, Any]) -> list[Fill]:
        """New Fill rows from an order_status payload's transactions.

        ``transactions`` are cumulative; entries past the stored fill
        count are new, so callers never duplicate.
        """
        transactions = payload.get("transactions") or []
        seen = int(
            self._session.scalar(
                select(func.count()).select_from(Fill).where(Fill.order_id == order.id)
            )
            or 0
        )
        base, quote = order.symbol.split("-", 1)
        liquidity = "maker" if order.type == "limit" else "taker"
        return [
            Fill(
                order_id=order.id,
                time=_dt(str(tx["datetime"])),
                price=Decimal(str(tx["price"])),
                quantity=Decimal(str(tx[base.lower()])),
                fee=Decimal(str(tx["fee"])),
                fee_currency=quote,
                liquidity=liquidity,
            )
            for tx in transactions[seen:]
        ]

    def _order_status(self, order: Order) -> dict[str, Any] | None:
        params: dict[str, Any] = (
            {"id": order.venue_order_id}
            if order.venue_order_id
            else {"client_order_id": order.client_order_id}
        )
        try:
            payload = self._request("POST", "/api/v2/order_status/", params)
            return payload if isinstance(payload, dict) else None
        except BitstampError as e:
            if "not found" in str(e).lower():
                return None
            raise

    def _apply_status(self, order: Order, payload: dict[str, Any]) -> None:
        status = str(payload.get("status", ""))
        if status == "Finished":
            order.status = "filled"
        elif status == "Canceled":
            order.status = "cancelled"
        elif status in ("Open", "In Queue"):
            remaining = payload.get("amount_remaining")
            if remaining is not None and Decimal(str(remaining)) < order.quantity:
                order.status = "partially_filled"
            else:
                order.status = "open"

    def _market_info(self, symbol: str) -> dict[str, Any]:
        now = self._clock.now().timestamp()
        cached = _markets_cache.get(self._base)
        if cached is None or not 0 <= now - cached[0] <= _MARKETS_TTL_SECONDS:
            rows = self._get(f"{self._base}/markets/")
            cached = (now, {str(r["market_symbol"]): r for r in rows})
            _markets_cache[self._base] = cached
        return cached[1][MARKETS[symbol]]

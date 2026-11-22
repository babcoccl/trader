# Venue: Bitstamp

Facts the adapter relies on, from the official reference at
<https://www.bitstamp.net/api/> (read 2026-11-21). Base URL for all
private calls: `https://www.bitstamp.net/api/v2`.

## Authentication

- Every private request is a POST with an `application/x-www-form-urlencoded`
  body and five headers: `X-Auth`, `X-Auth-Signature`, `X-Auth-Nonce`,
  `X-Auth-Timestamp`, `X-Auth-Version`.
- `X-Auth` = `BITSTAMP <api_key>`. `X-Auth-Version` = `v2`.
- Signature: HMAC-SHA256 (hex, lowercase) over the concatenation
  `'BITSTAMP ' + api_key + HTTP_VERB + host + path + query + content_type +
  nonce + timestamp + 'v2' + body`. The query string is empty for our POSTs;
  `content_type` is omitted from both the headers and the signature when the
  request body is empty (the docs warn it then "might cause issues" if sent).
- `X-Auth-Nonce` is a random lowercase UUID v4, unique within any 150-second
  window (error API0008 / API0015).
- `X-Auth-Timestamp` is UTC Unix milliseconds; requests more than 150 s off
  server time are rejected (API0017).
- When the signature is invalid Bitstamp returns HTTP 200 with
  `{"status": "error", "reason": ...}` — responses must be checked for
  `status: "error"`, not just the HTTP code.

## Endpoints used

| Endpoint (POST unless noted) | Used for |
| --- | --- |
| `buy/{market}/`, `sell/{market}/` | limit orders; fields `amount`, `price`, `moc_order`, `ioc_order`, `client_order_id` (also `daily_order`, `fok_order`, `gtd_order`+`expire_time`, `limit_price`) |
| `buy/market/{market}/`, `sell/market/{market}/` | market orders; fields `amount`, `client_order_id` |
| `cancel_order/` (`id`) | cancel one order |
| `cancel_all_orders/` | returns `{"canceled": [...], "success": true}` |
| `replace_order/` (`id` or `orig_client_order_id`, `amount`, `price`, `client_order_id`) | re-price in place |
| `open_orders/{market}/` | venue open orders for a market |
| `order_status/` (`id` or `client_order_id`) | order status + cumulative `transactions` |
| `account_balances/` | `[{currency, total, available, reserved}]` |
| `fees/trading/{market}` | `{"market": "...", "fees": {"maker": "0.15", "taker": "0.16"}}` (the actual fees for the account's tier) |
| GET `markets/` (public) | per-market `base_decimals`, `counter_decimals`, `minimum_order_value`, `minimum_order_amount`, `tick_size` |
| GET `ticker/{market}/` (public) | `bid`, `ask`, `last` for re-pricing and market-order notional checks |

## Order behaviour

- `moc_order=true` (maker-or-cancel): if the limit order would fill fully or
  partially at placement it is cancelled instead — the placement response
  comes back `status: "Canceled"`. The adapter then re-prices at the current
  best bid (buy) or best ask (sell) and retries, up to 3 attempts.
- `open_orders` rows: `id`, `price`, `amount`, `amount_at_create`, `market`
  (`"BTC/USD"`), `client_order_id`, `type` (`"0"` buy / `"1"` sell),
  `subtype` (`"LIMIT"`).
- `order_status` `status`: `In Queue`, `Open`, `Finished`, `Canceled`,
  `Cancel pending`. `transactions` entries carry `tid`, `price`, the base
  and counter currency amounts (`btc`, `usd`), `fee` (in counter currency)
  and `datetime` `"YYYY-MM-DD HH:MM:SS"` UTC. Transactions are cumulative:
  new fills are the entries beyond the count already stored. Order-status
  data for closed orders is kept ~30 days.
- Limit prices may not deviate more than 20 % from the market price.
- Minimum order: 10 USD notional / 0.0002 BTC / 0.002 ETH; the authoritative
  per-market values come from `GET markets/` (`minimum_order_value`,
  `base_decimals`, `counter_decimals`) and are cached for 1 h.
- Rate limits: 400 requests/second and 10,000 requests per 10 minutes —
  far above our usage; no client-side throttling implemented.
- There is no spot stop-loss order type; stops stay application-side
  (the daily cycle submits limit/market exits itself). Stop orders are
  rejected by the adapter.

## Sandbox

`https://sandbox.bitstamp.net` mirrors the API with paper funds. An opt-in
integration test (`tests/execution/test_bitstamp_sandbox.py`) places a
tiny, unfillable limit buy and cancels it; it runs only when
`TRADER_SANDBOX_TEST=1` with `TRADER_BITSTAMP_KEY`/`TRADER_BITSTAMP_SECRET`
pointing at sandbox keys and `TRADER_BITSTAMP_BASE_URL` at the sandbox.

## API-key permissions

Create the key in Bitstamp → Settings → API access with **only**:

- Account balance
- Open orders
- Order status / transaction history
- User transactions
- Trading fees
- Place and cancel orders (required for live trading)

The key must **not** have the withdrawal permission. `trader` never calls
any withdrawal endpoint. When `TRADER_MODE` is not `live` or
`TRADER_LIVE_ENABLED` is not `true`, `BitstampAdapter.place` raises before
any request is sent, so the key is read-only in practice until both are on.

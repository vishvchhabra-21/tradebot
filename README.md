# TradeBot — CLI Trading Bot for Binance Futures Testnet (USDT-M)

A small, well-structured Python CLI that places **MARKET**, **LIMIT**, and (bonus)
**TWAP** orders — BUY and SELL — on the Binance USDT-M Futures **Testnet**. Built for the
Primetrade.ai "Python Developer Intern — Trading Bot" assessment.

It demonstrates a clean separation between the API/client layer and the CLI layer,
defensive input validation before any network call, structured request/response/error
logging with secret redaction, and distinct exception handling per failure class.

---

## Features

- **MARKET & LIMIT orders** (BUY/SELL) on Binance Futures Testnet — *(BO-01)*
- **Clean layer separation**: `bot/client.py` (signing + HTTP) has zero CLI code;
  `bot/cli.py` has zero signing code — *(BO-02)*
- **Defensive validation** with `Decimal` (never `float`); invalid input is rejected
  before any request is sent — *(BO-03)*
- **Structured logging** of every request/response/error to a rotating file, with API
  secrets and signatures redacted to `***` — *(BO-04)*
- **Typed exceptions** — `ValidationError` / `ConfigError` / `APIError` / `NetworkError` /
  `AmbiguousOrderError` — each mapped to a distinct message and exit code — *(BO-05)*
- **Bonus — TWAP** order type: slices a parent order into N evenly-spaced child MARKET
  orders — *(BO-07)*
- **Bonus — interactive mode**: `--interactive` prompts for any missing field with
  inline validation — *(BO-07)*

## Project structure

```
trading_bot/
├── bot/
│   ├── client.py          # Binance client: HMAC-SHA256 signing, HTTP, error mapping
│   ├── orders.py          # place_order dispatcher (MARKET/LIMIT/TWAP) + history log
│   ├── validators.py      # Decimal-based input validation, typed ValidationError
│   ├── logging_config.py  # dual-sink logger (console + rotating file) + redaction filter
│   └── cli.py             # argparse CLI, terminal output, exit-code mapping
├── tests/                 # pytest suite (mocked HTTP — no real network)
├── logs/                  # runtime logs (git-ignored)
├── sample_logs/           # committed MARKET + LIMIT sample runs (real testnet)
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

1. **Clone** the repo and enter it:
   ```bash
   git clone https://github.com/vishvchhabra-21/tradebot.git
   cd tradebot
   ```
2. **Install dependencies** (a virtualenv is recommended):
   ```bash
   pip install -r requirements.txt
   ```
3. **Get testnet API keys.** Register / log in at
   [testnet.binancefuture.com](https://testnet.binancefuture.com), open **API Key**
   management, and generate a key + secret. **Enable "Futures" trading permission** on the
   key (do not enable withdrawals). These are testnet-only keys — separate from any real
   Binance account keys, no real funds involved.
4. **Configure credentials:**
   ```bash
   cp .env.example .env      # then edit .env and paste your key/secret
   ```
   `.env` is git-ignored and must never be committed.

## How to run

```bash
# MARKET buy
python -m bot.cli --symbol BTCUSDT --side BUY --type MARKET --quantity 0.01

# LIMIT sell (rests until price is reached; price is required)
python -m bot.cli --symbol ETHUSDT --side SELL --type LIMIT --quantity 0.5 --price 4200

# TWAP (bonus): 5 MARKET slices over 60s (price is required by validation)
python -m bot.cli --symbol BTCUSDT --side BUY --type TWAP --quantity 0.05 --price 60000 \
    --twap-duration 60 --twap-slices 5

# Interactive mode (bonus): prompts for any missing field
python -m bot.cli --interactive
```

## CLI reference

| Flag | Required | Description |
|---|---|---|
| `--symbol` | yes | Trading pair, e.g. `BTCUSDT` (5–20 uppercase alphanumerics; lowercase is auto-normalized) |
| `--side` | yes | `BUY` or `SELL` (case-insensitive) |
| `--type` | yes | `MARKET`, `LIMIT`, or `TWAP` (case-insensitive) |
| `--quantity` | yes | Positive decimal, e.g. `0.01` |
| `--price` | for LIMIT/TWAP | Positive decimal; rejected before any network call if missing for LIMIT/TWAP |
| `--twap-duration` | no | TWAP total duration in seconds (default `60`) |
| `--twap-slices` | no | TWAP child-order count (default `5`, max `20`) |
| `--interactive` | no | Prompt for any missing field instead of erroring |

**Exit codes** (for scripted grading): `0` success · `1` validation/config error ·
`2` API error · `3` network error.

## Logging

- **`logs/trading_bot.log`** — rotating file (5 MB × 3 backups), DEBUG and above. One
  `event=request` line before each call and one `event=response`/`event=api_error` line
  after. Console mirrors this at INFO. API secrets and signatures are redacted to `***` by
  a logging filter, so they never reach disk in cleartext.
- **`logs/orders_history.jsonl`** — one normalized JSON record per placed order
  (`timestamp`, `clientOrderId`, `symbol`, `side`, `type`, `quantity`, `price`, `orderId`,
  `status`, `executedQty`, `avgPrice`, `success`).
- **`sample_logs/`** — committed console output + redacted log excerpts from real testnet
  MARKET and LIMIT runs.

Example redacted request line:
```
2026-07-02 02:16:26 | INFO | bot.client | event=request client_order_id=tb_468173a7 method=POST endpoint=/fapi/v1/order params={'symbol': 'BTCUSDT', 'side': 'BUY', 'type': 'MARKET', 'quantity': '0.01', 'newClientOrderId': 'tb_468173a7', 'timestamp': 1782938817646, 'recvWindow': 5000, 'signature': '***'}
```

## Running tests

```bash
pytest tests/ -q
```

38 tests cover validation, signature determinism, error→exception mapping, and
MARKET/LIMIT/TWAP dispatch. **All HTTP is mocked — tests make no real network calls.**

> Note: if your environment has a globally-installed `pytest-asyncio` that is newer than
> the pinned `pytest`, run with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -q` (a
> clean virtualenv from `requirements.txt` does not need this).

## Design notes / key decisions

- **Signing.** Every signed request's parameters are sorted, URL-encoded into a
  deterministic query string, signed with HMAC-SHA256, and that *exact* string is sent
  verbatim in the URL (signature appended last) — guaranteeing Binance verifies the same
  bytes we signed (avoids `-1022 invalid signature`). The API key travels only in the
  `X-MBX-APIKEY` header; the secret never leaves the machine.
- **Clock skew.** Server time is fetched once at startup (`GET /fapi/v1/time`) and the
  offset is applied to every `timestamp`, avoiding `-1021` errors; if the fetch fails it
  falls back to offset 0 (non-fatal).
- **Ambiguous 503.** An HTTP 503 *"unknown error"* means execution status is UNKNOWN — it
  raises `AmbiguousOrderError` and is resolved with a single order-status lookup by
  `origClientOrderId` rather than blindly re-sending, to avoid duplicate orders (BR-08). A
  plain *"Service Unavailable."* 503 is treated as a normal failure.
- **Decimal everywhere.** Quantities/prices are parsed with `Decimal` to avoid binary
  float artifacts that Binance's `LOT_SIZE`/`PRICE_FILTER` would reject.
- **Testnet by construction.** `BINANCE_BASE_URL` is a single `.env` value defaulting to
  the testnet host; the production host is never a fallback.

## Assumptions

- The reviewer uses their own Binance Futures **Testnet** keys with Futures permission
  enabled; no production keys or real funds are involved anywhere.
- `BTCUSDT`-style USDT-M perpetual symbols are tradable on the testnet at evaluation time.
- A default `recvWindow` of 5000 ms is acceptable for signed requests.
- If Binance redirects `testnet.binancefuture.com`, the working host is set via
  `BINANCE_BASE_URL` (the docs also list `https://demo-fapi.binance.com`) — no code change
  needed.

## Known limitations / bonus status

- ✅ **TWAP** order type implemented (bonus) — validated by unit tests; child slices sum
  exactly to the parent quantity and the average fill price is quantity-weighted.
- ✅ **Interactive CLI** mode implemented (bonus).
- ❌ Lightweight Streamlit UI — intentionally not built; this is a CLI-only submission per
  the assessment scope.
- On the testnet, a MARKET order's create-response sometimes returns `status=NEW` with
  `executedQty=0` (the fill settles a moment later); this is normal exchange ACK behavior,
  not a bug. The order is genuinely placed (see `sample_logs/`).

## Submission notes

This repository satisfies the BRD deliverables checklist: source code, `README.md`,
`requirements.txt`, `.env.example`, and `sample_logs/` with a real MARKET and a real LIMIT
order. Per the assignment instructions, the submission is delivered via the **Primetrade.ai
Google Form, not email**.

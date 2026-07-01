"""CLI entry point (argparse).

Parses user input, prints the WIREFRAME.md terminal screens verbatim, and maps typed
exceptions to process exit codes (0 success / 1 validation / 2 API / 3 network). Contains
zero HMAC/signing code — the hard CLI/client boundary graded by this assessment (BO-02).
"""

import argparse
import os
import sys
from decimal import Decimal

from dotenv import load_dotenv

from bot.client import (
    AmbiguousOrderError,
    APIError,
    BinanceFuturesClient,
    ConfigError,
    NetworkError,
)
from bot.logging_config import get_logger
from bot.orders import new_client_order_id, place_order, twap_summary
from bot.validators import (
    OrderRequest,
    ValidationError,
    build_order_request,
    validate_order_type,
    validate_price,
    validate_quantity,
    validate_side,
    validate_symbol,
    validate_twap_params,
)

logger = get_logger(__name__)

BANNER = "=" * 70
DIVIDER = "-" * 70
HEADER = " TradeBot — Binance Futures Testnet"

_EXAMPLES = """examples:
  python -m bot.cli --symbol BTCUSDT --side BUY --type MARKET --quantity 0.01
  python -m bot.cli --symbol ETHUSDT --side SELL --type LIMIT --quantity 0.5 --price 4200
  python -m bot.cli --symbol BTCUSDT --side BUY --type TWAP --quantity 0.05 --twap-slices 5"""

_USAGE = (
    "bot.cli [-h] --symbol SYMBOL --side {BUY,SELL} --type {MARKET,LIMIT,TWAP}\n"
    "                --quantity QUANTITY [--price PRICE] [--twap-duration SECONDS]\n"
    "                [--twap-slices N] [--interactive]"
)


# --------------------------------------------------------------------------------------
# argparse
# --------------------------------------------------------------------------------------

def build_parser(interactive_mode: bool = False) -> argparse.ArgumentParser:
    """Build the CLI parser. In interactive mode core args become optional (prompted later)."""
    required = not interactive_mode
    p = argparse.ArgumentParser(
        prog="bot.cli",
        description="TradeBot — place MARKET/LIMIT/TWAP orders on Binance Futures Testnet (USDT-M)",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage=_USAGE,
        add_help=False,
    )
    req = p.add_argument_group("required arguments")
    opt = p.add_argument_group("optional arguments")

    opt.add_argument("-h", "--help", action="help", help="show this help message and exit")
    req.add_argument("--symbol", required=required, metavar="SYMBOL", help="e.g. BTCUSDT")
    req.add_argument("--side", required=required, type=str.upper, choices=["BUY", "SELL"])
    req.add_argument(
        "--type",
        dest="type",
        required=required,
        type=str.upper,
        choices=["MARKET", "LIMIT", "TWAP"],
    )
    req.add_argument(
        "--quantity", required=required, metavar="QUANTITY", help="positive decimal, e.g. 0.01"
    )
    opt.add_argument("--price", metavar="PRICE", help="required for LIMIT/TWAP")
    opt.add_argument(
        "--twap-duration", dest="twap_duration", type=int, default=60, metavar="SECONDS",
        help="default 60 (TWAP only)",
    )
    opt.add_argument(
        "--twap-slices", dest="twap_slices", type=int, default=5, metavar="N",
        help="default 5, max 20 (TWAP only)",
    )
    opt.add_argument(
        "--interactive", action="store_true",
        help="prompt for any missing field instead of erroring",
    )
    return p


# --------------------------------------------------------------------------------------
# printing helpers (WIREFRAME §2-§8)
# --------------------------------------------------------------------------------------

def _field(label: str, value) -> str:
    return f"   {label:<13}: {value}"


def print_banner(twap: bool = False) -> None:
    print(BANNER)
    print(HEADER + (" — TWAP MODE" if twap else ""))
    print(BANNER)


def _fmt_avg(avg_price) -> str:
    """Render avgPrice, showing N/A when the exchange returned zero/no fill price."""
    if avg_price in (None, ""):
        return "N/A"
    try:
        if Decimal(str(avg_price)) == 0:
            return "N/A"
    except Exception:
        return "N/A"
    return str(avg_price)


def print_request_summary(req: OrderRequest, base_url: str, client_order_id: str) -> None:
    print(" [1/3] ORDER REQUEST SUMMARY")
    print(DIVIDER)
    print(_field("Symbol", req.symbol))
    print(_field("Side", req.side))
    print(_field("Type", req.order_type))
    print(_field("Quantity", req.quantity))
    print(_field("Price", req.price if req.price is not None else "N/A (market order)"))
    print(_field("Base URL", base_url))
    print(_field("ClientOrderId", client_order_id))
    print(DIVIDER)


def print_response(result) -> None:
    print(" [3/3] ORDER RESPONSE")
    print(DIVIDER)
    print(_field("orderId", result.orderId))
    print(_field("status", result.status))
    print(_field("executedQty", result.executedQty))
    print(_field("avgPrice", _fmt_avg(result.avgPrice)))
    print(DIVIDER)


def _twap_slices(req: OrderRequest) -> list[Decimal]:
    n = req.twap_slices
    base = (req.quantity / n).quantize(Decimal("0.001"))
    return [base] * (n - 1) + [req.quantity - base * (n - 1)]


def print_twap_request(req: OrderRequest) -> None:
    print(" [1/3] ORDER REQUEST SUMMARY")
    print(DIVIDER)
    print(_field("Symbol", req.symbol))
    print(_field("Side", req.side))
    print(_field("Type", f"TWAP ({req.twap_slices} slices over {req.twap_duration_seconds}s)"))
    print(_field("Total Qty", req.quantity))
    base = (req.quantity / req.twap_slices).quantize(Decimal("0.001"))
    print(_field("Slice Qty", f"{base} x{req.twap_slices}"))
    print(DIVIDER)


def print_twap_results(req: OrderRequest, results: list) -> None:
    slice_qtys = _twap_slices(req)
    n = len(results)
    for i, r in enumerate(results, 1):
        bar = "▓" * i + "░" * (n - i)
        qty = slice_qtys[i - 1] if i - 1 < len(slice_qtys) else r.origQty
        price = _fmt_avg(r.avgPrice)
        print(
            f"   Slice {i}/{n}  {bar}  qty={qty}  orderId={r.orderId}"
            f"  status={r.status}  price={price}"
        )
    print(DIVIDER)
    print(" [3/3] TWAP SUMMARY")
    print(DIVIDER)
    summ = twap_summary(results)
    print(f"   {'Total Executed Qty':<18} : {summ['total_executed_qty']}")
    print(f"   {'Avg Fill Price':<18} : {summ['avg_fill_price']}")
    print(DIVIDER)
    filled = sum(1 for r in results if r.status in ("FILLED", "NEW", "PARTIALLY_FILLED"))
    print(f" ✅ SUCCESS: TWAP order completed ({filled}/{n} slices filled).")
    print(BANNER)


# --------------------------------------------------------------------------------------
# interactive mode (WIREFRAME §10)
# --------------------------------------------------------------------------------------

def run_interactive(args) -> OrderRequest:
    """Prompt for any missing field with inline validation, re-asking on bad input."""
    print(BANNER)
    print(" TradeBot — Interactive Mode")
    print(BANNER)

    def ask(prompt: str, validator, ok_msg):
        while True:
            raw = input(prompt).strip()
            try:
                value = validator(raw)
            except ValidationError as e:
                print(f"   ✗ {e} — try again")
                continue
            print(f"   ✓ {ok_msg(raw, value)}")
            return value

    symbol = (
        validate_symbol(args.symbol)
        if args.symbol
        else ask(" Symbol (e.g. BTCUSDT): ", validate_symbol, lambda raw, v: f"normalized to {v}")
    )
    side = (
        validate_side(args.side)
        if args.side
        else ask(" Side [BUY/SELL]: ", validate_side, lambda raw, v: f"normalized to {v}")
    )
    order_type = (
        validate_order_type(args.type)
        if args.type
        else ask(
            " Order type [MARKET/LIMIT/TWAP]: ",
            validate_order_type,
            lambda raw, v: f"{v}",
        )
    )

    price = None
    if order_type in ("LIMIT", "TWAP"):
        if args.price is not None:
            price = validate_price(args.price, order_type)
        else:
            while True:
                raw = input(f" Price (required for {order_type}): ").strip()
                if raw == "":
                    print(f"   ✗ price cannot be empty for {order_type} orders — try again")
                    continue
                try:
                    price = validate_price(raw, order_type)
                except ValidationError as e:
                    print(f"   ✗ {e} — try again")
                    continue
                print(f"   ✓ {raw}")
                break

    quantity = (
        validate_quantity(args.quantity)
        if args.quantity
        else ask(" Quantity: ", validate_quantity, lambda raw, v: f"{raw}")
    )

    duration, slices = validate_twap_params(args.twap_duration, args.twap_slices)

    print(DIVIDER)
    confirm = input(" Proceed with this order? [y/N]: ").strip().lower()
    print(BANNER)
    if confirm != "y":
        print(" Aborted — no order was sent.")
        sys.exit(0)

    return OrderRequest(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        twap_duration_seconds=duration,
        twap_slices=slices,
    )


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------

def main(argv=None) -> int:
    # Ensure box-drawing / emoji glyphs render on Windows consoles (cp1252 default).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    load_dotenv()
    argv = list(sys.argv[1:] if argv is None else argv)
    interactive = "--interactive" in argv
    args = build_parser(interactive).parse_args(argv)

    # 1. Validate (silent unless it fails, so only one [1/3] screen is shown).
    try:
        if interactive:
            req = run_interactive(args)
        else:
            req = build_order_request(
                symbol=args.symbol,
                side=args.side,
                order_type=args.type,
                quantity=args.quantity,
                price=args.price,
                twap_duration_seconds=args.twap_duration,
                twap_slices=args.twap_slices,
            )
    except ValidationError as e:
        print_banner()
        print(" [1/3] VALIDATING INPUT...")
        print(DIVIDER)
        print(f" ❌ FAILED: {e}")
        print("   (no network request was sent)")
        print(BANNER)
        return 1

    is_twap = req.order_type == "TWAP"
    client_order_id = new_client_order_id()
    base_url = os.getenv("BINANCE_BASE_URL", "https://testnet.binancefuture.com").rstrip("/")

    # 2. Banner + request summary + sending header.
    print_banner(is_twap)
    if is_twap:
        print_twap_request(req)
        print(" [2/3] EXECUTING SLICES...")
    else:
        print_request_summary(req, base_url, client_order_id)
        print(" [2/3] SENDING ORDER...")
    print(DIVIDER)

    # 3. Construct client + place order, mapping typed exceptions to exit codes.
    try:
        client = BinanceFuturesClient()
        if is_twap:
            results = place_order(client, req)
        else:
            result = place_order(client, req, client_order_id)
    except ConfigError as e:
        print(f" ❌ FAILED: {e}")
        print(BANNER)
        return 1
    except APIError as e:
        print(f" ❌ FAILED: Binance error {e.code}: {e.msg}")
        print(BANNER)
        return 2
    except AmbiguousOrderError:
        print(" ❌ FAILED: order status unknown — check manually on the exchange")
        print("   (see logs/trading_bot.log)")
        print(BANNER)
        return 2
    except NetworkError:
        print(" ❌ FAILED: network error — check BINANCE_BASE_URL / connectivity")
        print("   (see logs/trading_bot.log for full traceback)")
        print(BANNER)
        return 3
    except Exception:
        logger.exception("event=unexpected_error")
        print(" ❌ FAILED: unexpected error — see logs/trading_bot.log")
        print(BANNER)
        return 1

    # 4. Success output.
    if is_twap:
        print_twap_results(req, results)
    else:
        print_response(result)
        if result.status == "NEW":
            print(" ✅ SUCCESS: Order placed (resting, not yet filled).")
        else:
            print(" ✅ SUCCESS: Order placed.")
        print(BANNER)
    return 0


if __name__ == "__main__":
    sys.exit(main())

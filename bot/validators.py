"""Pure, network-free validation of CLI order input.

Raises :class:`ValidationError` (and only :class:`ValidationError`) on any bad
field, never touches the network. Every value is parsed with :class:`decimal.Decimal`
rather than ``float`` to avoid binary rounding artifacts that Binance's ``LOT_SIZE`` /
``PRICE_FILTER`` rules would reject (SRS FR-VALID-01..08).
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class ValidationError(Exception):
    """Raised for any invalid CLI order input. The single exception type this module emits."""


_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")
VALID_SIDES = {"BUY", "SELL"}
VALID_TYPES = {"MARKET", "LIMIT", "TWAP"}
_MAX_TWAP_SLICES = 20


@dataclass
class OrderRequest:
    """A fully validated, normalized order request handed from the CLI to the order layer."""

    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    twap_duration_seconds: int = 60
    twap_slices: int = 5


def validate_symbol(raw: str) -> str:
    """Normalize to uppercase and validate as 5-20 uppercase alphanumeric chars."""
    if raw is None:
        raise ValidationError("Invalid symbol: symbol is required, e.g. BTCUSDT")
    symbol = raw.strip().upper()
    if not _SYMBOL_RE.match(symbol):
        raise ValidationError(
            f"Invalid symbol '{raw}': expected 5-20 uppercase alphanumeric chars, e.g. BTCUSDT"
        )
    return symbol


def validate_side(raw: str) -> str:
    """Normalize case and validate side is one of BUY/SELL."""
    if raw is None:
        raise ValidationError("Invalid side: side is required (BUY or SELL)")
    side = raw.strip().upper()
    if side not in VALID_SIDES:
        raise ValidationError(f"Invalid side '{raw}': must be one of BUY, SELL")
    return side


def validate_order_type(raw: str) -> str:
    """Normalize case and validate type is one of MARKET/LIMIT/TWAP."""
    if raw is None:
        raise ValidationError("Invalid type: order type is required (MARKET, LIMIT or TWAP)")
    order_type = raw.strip().upper()
    if order_type not in VALID_TYPES:
        raise ValidationError(f"Invalid type '{raw}': must be one of MARKET, LIMIT, TWAP")
    return order_type


def validate_quantity(raw: str) -> Decimal:
    """Validate quantity as a positive Decimal; reject zero, negative and non-numeric input."""
    if raw is None:
        raise ValidationError("Invalid quantity: quantity is required and must be greater than 0")
    try:
        qty = Decimal(str(raw))
    except InvalidOperation:
        raise ValidationError(f"Invalid quantity '{raw}': must be a positive number")
    if not qty.is_finite() or qty <= 0:
        raise ValidationError(f"Invalid quantity '{raw}': must be greater than 0")
    return qty


def validate_price(raw: str | None, order_type: str) -> Decimal | None:
    """Validate/require price per order type.

    For LIMIT/TWAP a positive price is mandatory (raises if missing). For MARKET the
    price is ignored and ``None`` is returned even when a value is supplied.
    """
    if order_type in ("LIMIT", "TWAP"):
        if raw is None:
            raise ValidationError(f"price is required for {order_type} orders")
        try:
            price = Decimal(str(raw))
        except InvalidOperation:
            raise ValidationError(f"Invalid price '{raw}': must be a positive number")
        if not price.is_finite() or price <= 0:
            raise ValidationError(f"Invalid price '{raw}': must be greater than 0")
        return price
    return None  # MARKET: price ignored even if supplied


def validate_twap_params(duration_seconds, slices) -> tuple[int, int]:
    """Validate TWAP duration/slices as positive ints; cap slices at 20 (FR-VALID-08)."""
    try:
        duration = int(duration_seconds)
        n_slices = int(slices)
    except (TypeError, ValueError):
        raise ValidationError("Invalid TWAP params: duration and slices must be positive integers")
    if duration <= 0:
        raise ValidationError(f"Invalid TWAP duration '{duration_seconds}': must be greater than 0")
    if n_slices <= 0:
        raise ValidationError(f"Invalid TWAP slices '{slices}': must be greater than 0")
    if n_slices > _MAX_TWAP_SLICES:
        raise ValidationError(
            f"Invalid TWAP slices '{slices}': maximum is {_MAX_TWAP_SLICES} to avoid excessive child orders"
        )
    return duration, n_slices


def build_order_request(
    symbol: str,
    side: str,
    order_type: str,
    quantity: str,
    price: str | None = None,
    twap_duration_seconds=60,
    twap_slices=5,
) -> OrderRequest:
    """Validate every field and assemble a typed :class:`OrderRequest` (fail-fast)."""
    normalized_type = validate_order_type(order_type)
    duration, n_slices = validate_twap_params(twap_duration_seconds, twap_slices)
    return OrderRequest(
        symbol=validate_symbol(symbol),
        side=validate_side(side),
        order_type=normalized_type,
        quantity=validate_quantity(quantity),
        price=validate_price(price, normalized_type),
        twap_duration_seconds=duration,
        twap_slices=n_slices,
    )

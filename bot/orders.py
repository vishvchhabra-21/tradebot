"""Order construction + placement logic.

Dispatches a validated :class:`~bot.validators.OrderRequest` to the correct client
method (MARKET / LIMIT / TWAP), normalizes raw Binance JSON into :class:`OrderResult`,
and appends one audit record per placed order to ``logs/orders_history.jsonl``
(SRS FR-ORDER-01..06, FR-LOG-06, DESIGN §7).
"""

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from bot.client import AmbiguousOrderError
from bot.logging_config import get_logger

logger = get_logger(__name__)

_HISTORY_PATH = os.path.join("logs", "orders_history.jsonl")


@dataclass
class OrderResult:
    """Normalized order outcome returned to the CLI (SRS FR-ORDER-06)."""

    symbol: str
    side: str
    type: str
    orderId: int | None
    status: str
    executedQty: str
    avgPrice: str | None
    origQty: str
    price: str | None
    success: bool
    clientOrderId: str | None = None


def new_client_order_id() -> str:
    """Generate a locally-unique correlation id (Critical Detail 5): ``tb_`` + 8 hex chars."""
    return f"tb_{uuid.uuid4().hex[:8]}"


def _to_result(raw: dict, order_type: str, client_order_id: str | None = None) -> OrderResult:
    """Normalize a raw Binance order response into an :class:`OrderResult`."""
    return OrderResult(
        symbol=raw["symbol"],
        side=raw["side"],
        type=order_type,
        orderId=raw.get("orderId"),
        status=raw.get("status", "UNKNOWN"),
        executedQty=raw.get("executedQty", "0"),
        avgPrice=raw.get("avgPrice"),
        origQty=raw.get("origQty", "0"),
        price=raw.get("price"),
        success=True,
        clientOrderId=raw.get("clientOrderId", client_order_id),
    )


def _append_history(result: OrderResult, request_price) -> None:
    """Append one JSON line describing a successfully placed order (DESIGN §7 schema)."""
    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "clientOrderId": result.clientOrderId,
        "symbol": result.symbol,
        "side": result.side,
        "type": result.type,
        "quantity": result.origQty,
        "price": str(request_price) if request_price is not None else None,
        "orderId": result.orderId,
        "status": result.status,
        "executedQty": result.executedQty,
        "avgPrice": result.avgPrice,
        "success": result.success,
    }
    os.makedirs("logs", exist_ok=True)
    with open(_HISTORY_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def place_order(client, req, client_order_id: str | None = None):
    """Dispatch a validated order request to the correct client method.

    Returns a single :class:`OrderResult` for MARKET/LIMIT, or a list of them for TWAP.
    Typed client exceptions (APIError/NetworkError/AmbiguousOrderError) propagate
    unchanged to the CLI boundary — no blanket ``except`` here (SRS FR-ORDER-05).
    """
    if req.order_type == "TWAP":
        return _place_twap(client, req)

    coid = client_order_id or new_client_order_id()
    if req.order_type == "MARKET":
        try:
            raw = client.place_market_order(req.symbol, req.side, req.quantity, coid)
        except AmbiguousOrderError:
            logger.warning("event=ambiguous_recovery_attempt client_order_id=%s", coid)
            raw = client.get_order_status(req.symbol, coid)
        result = _to_result(raw, "MARKET", coid)
    elif req.order_type == "LIMIT":
        try:
            raw = client.place_limit_order(req.symbol, req.side, req.quantity, req.price, coid)
        except AmbiguousOrderError:
            logger.warning("event=ambiguous_recovery_attempt client_order_id=%s", coid)
            raw = client.get_order_status(req.symbol, coid)
        result = _to_result(raw, "LIMIT", coid)
    else:  # pragma: no cover — unreachable if validators ran first
        raise ValueError(f"Unsupported order_type: {req.order_type}")

    _append_history(result, req.price)
    return result


def _place_twap(client, req) -> list[OrderResult]:
    """Slice ``quantity`` into N MARKET child orders spaced evenly over the duration.

    The last slice absorbs any Decimal rounding remainder so the child quantities sum
    exactly to the parent quantity, and the interval stays a float so fractional seconds
    are not truncated (Critical Detail 6, DESIGN §2.3.2).
    """
    n = req.twap_slices
    base = (req.quantity / n).quantize(Decimal("0.001"))
    slices = [base] * (n - 1) + [req.quantity - base * (n - 1)]
    interval = req.twap_duration_seconds / n

    results: list[OrderResult] = []
    for i, qty in enumerate(slices):
        coid = new_client_order_id()
        try:
            raw = client.place_market_order(req.symbol, req.side, qty, coid)
        except AmbiguousOrderError:
            logger.warning("event=ambiguous_recovery_attempt client_order_id=%s", coid)
            raw = client.get_order_status(req.symbol, coid)
        result = _to_result(raw, "MARKET", coid)
        results.append(result)
        _append_history(result, None)
        logger.info(
            "event=twap_slice index=%s/%s qty=%s orderId=%s status=%s",
            i + 1,
            n,
            qty,
            result.orderId,
            result.status,
        )
        if i < n - 1:
            time.sleep(interval)
    return results


def twap_summary(results: list[OrderResult]) -> dict:
    """Aggregate TWAP slices: total executed qty and executed-qty-weighted avg fill price."""
    total_qty = Decimal("0")
    weighted = Decimal("0")
    for r in results:
        executed = Decimal(str(r.executedQty or "0"))
        avg = Decimal(str(r.avgPrice)) if r.avgPrice not in (None, "", "0", "0.00000") else Decimal("0")
        total_qty += executed
        weighted += executed * avg
    avg_fill = (weighted / total_qty) if total_qty > 0 else Decimal("0")
    return {"total_executed_qty": total_qty, "avg_fill_price": avg_fill}

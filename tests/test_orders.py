"""Tests for the order dispatch layer using a mocked client — no real network (SRS §7)."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from bot.client import AmbiguousOrderError
from bot.orders import OrderResult, place_order, twap_summary
from bot.validators import OrderRequest


@pytest.fixture(autouse=True)
def _isolate_history(monkeypatch):
    """Redirect orders_history.jsonl writes to a no-op so tests don't touch disk."""
    monkeypatch.setattr("bot.orders._append_history", lambda *a, **k: None)


def _market_req():
    return OrderRequest("BTCUSDT", "BUY", "MARKET", Decimal("0.01"))


def _limit_req():
    return OrderRequest("ETHUSDT", "SELL", "LIMIT", Decimal("0.5"), price=Decimal("4200"))


def _twap_req(qty="0.05", slices=5, duration=5):
    return OrderRequest(
        "BTCUSDT", "BUY", "TWAP", Decimal(qty), twap_duration_seconds=duration, twap_slices=slices
    )


def test_market_dispatch_calls_market_method():
    client = MagicMock()
    client.place_market_order.return_value = {
        "symbol": "BTCUSDT", "side": "BUY", "status": "FILLED",
        "orderId": 1, "executedQty": "0.01", "avgPrice": "43000", "origQty": "0.01",
    }
    result = place_order(client, _market_req(), "tb_fixed")
    assert isinstance(result, OrderResult)
    assert result.type == "MARKET"
    assert result.orderId == 1
    client.place_market_order.assert_called_once()
    client.place_limit_order.assert_not_called()
    # The CLI-provided client_order_id must be threaded through.
    assert client.place_market_order.call_args.args[3] == "tb_fixed"


def test_limit_dispatch_calls_limit_method():
    client = MagicMock()
    client.place_limit_order.return_value = {
        "symbol": "ETHUSDT", "side": "SELL", "status": "NEW",
        "orderId": 2, "executedQty": "0", "avgPrice": "0", "origQty": "0.5", "price": "4200",
    }
    result = place_order(client, _limit_req(), "tb_lim")
    assert result.type == "LIMIT"
    assert result.status == "NEW"
    client.place_limit_order.assert_called_once()
    client.place_market_order.assert_not_called()


def test_twap_places_n_slices_summing_to_total(monkeypatch):
    monkeypatch.setattr("bot.orders.time.sleep", lambda *_: None)
    client = MagicMock()
    client.place_market_order.return_value = {
        "symbol": "BTCUSDT", "side": "BUY", "status": "FILLED",
        "orderId": 9, "executedQty": "0.01", "avgPrice": "43000", "origQty": "0.01",
    }
    results = place_order(client, _twap_req(qty="0.05", slices=5))
    assert isinstance(results, list)
    assert len(results) == 5
    assert client.place_market_order.call_count == 5
    # The child slice quantities (4th positional arg) must sum exactly to the parent.
    total = sum(call.args[2] for call in client.place_market_order.call_args_list)
    assert total == Decimal("0.05")


def test_twap_last_slice_absorbs_remainder(monkeypatch):
    monkeypatch.setattr("bot.orders.time.sleep", lambda *_: None)
    client = MagicMock()
    client.place_market_order.return_value = {
        "symbol": "BTCUSDT", "side": "BUY", "status": "FILLED", "orderId": 1,
        "executedQty": "0", "avgPrice": "0", "origQty": "0",
    }
    # 0.10 / 3 -> 0.033 base, last slice = 0.10 - 0.066 = 0.034
    place_order(client, _twap_req(qty="0.10", slices=3))
    qtys = [call.args[2] for call in client.place_market_order.call_args_list]
    assert sum(qtys) == Decimal("0.10")
    assert qtys[-1] == Decimal("0.10") - Decimal("0.033") * 2


def test_twap_sleeps_between_slices_not_after_last(monkeypatch):
    sleeps = []
    monkeypatch.setattr("bot.orders.time.sleep", lambda s: sleeps.append(s))
    client = MagicMock()
    client.place_market_order.return_value = {
        "symbol": "BTCUSDT", "side": "BUY", "status": "FILLED", "orderId": 1,
        "executedQty": "0.01", "avgPrice": "43000", "origQty": "0.01",
    }
    place_order(client, _twap_req(qty="0.05", slices=5, duration=60))
    assert len(sleeps) == 4  # N-1 sleeps
    assert all(s == 12.0 for s in sleeps)  # 60/5 float, not truncated


def test_twap_ambiguous_slice_recovers_via_status_lookup(monkeypatch):
    monkeypatch.setattr("bot.orders.time.sleep", lambda *_: None)
    client = MagicMock()
    client.place_market_order.side_effect = AmbiguousOrderError("503")
    client.get_order_status.return_value = {
        "symbol": "BTCUSDT", "side": "BUY", "status": "FILLED", "orderId": 5,
        "executedQty": "0.01", "avgPrice": "43000", "origQty": "0.01",
    }
    results = place_order(client, _twap_req(qty="0.01", slices=1))
    assert results[0].orderId == 5
    client.get_order_status.assert_called_once()


def test_market_ambiguous_recovers_via_status_lookup():
    client = MagicMock()
    client.place_market_order.side_effect = AmbiguousOrderError("503")
    client.get_order_status.return_value = {
        "symbol": "BTCUSDT", "side": "BUY", "status": "FILLED", "orderId": 7,
        "executedQty": "0.01", "avgPrice": "43000", "origQty": "0.01",
    }
    result = place_order(client, _market_req(), "tb_amb")
    assert result.orderId == 7
    client.get_order_status.assert_called_once_with("BTCUSDT", "tb_amb")


def test_twap_summary_is_quantity_weighted():
    results = [
        OrderResult("BTCUSDT", "BUY", "MARKET", 1, "FILLED", "0.01", "100", "0.01", None, True),
        OrderResult("BTCUSDT", "BUY", "MARKET", 2, "FILLED", "0.03", "200", "0.03", None, True),
    ]
    summ = twap_summary(results)
    assert summ["total_executed_qty"] == Decimal("0.04")
    # weighted: (0.01*100 + 0.03*200) / 0.04 = (1 + 6)/0.04 = 175
    assert summ["avg_fill_price"] == Decimal("175")


def test_unsupported_order_type_raises():
    client = MagicMock()
    bad = OrderRequest("BTCUSDT", "BUY", "STOP", Decimal("0.01"))
    with pytest.raises(ValueError):
        place_order(client, bad)

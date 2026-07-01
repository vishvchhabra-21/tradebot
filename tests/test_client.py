"""Tests for the API client layer using mocked HTTP — no real network access (SRS §7).

Fake literal key/secret strings ("k"/"s") are used only to exercise signing logic; no
real credential ever appears in tests.
"""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from bot.client import (
    AmbiguousOrderError,
    APIError,
    BinanceFuturesClient,
    ConfigError,
    NetworkError,
)

ENV = {"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s"}


def _mock_time(mock_get):
    mock_get.return_value = MagicMock(json=lambda: {"serverTime": 0})


def _sent_params(mock_request) -> dict:
    """Parse the query string off the URL the client actually sent."""
    url = mock_request.call_args.args[1]
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


@patch.dict("os.environ", {}, clear=True)
def test_config_error_when_keys_missing():
    with pytest.raises(ConfigError):
        BinanceFuturesClient()


@patch.dict("os.environ", ENV)
@patch("bot.client.requests.get")
def test_signature_is_deterministic_and_order_independent(mock_get):
    _mock_time(mock_get)
    client = BinanceFuturesClient()
    sig_a = client._sign({"b": "2", "a": "1", "type": "MARKET"})
    sig_b = client._sign({"type": "MARKET", "a": "1", "b": "2"})
    # Same params in different insertion order must produce an identical signature.
    assert sig_a == sig_b
    assert len(sig_a) == 64  # SHA-256 hex digest


@patch.dict("os.environ", ENV)
@patch("bot.client.requests.get")
@patch("bot.client.requests.request")
def test_place_market_order_success(mock_request, mock_get):
    _mock_time(mock_get)
    mock_request.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "orderId": 1,
            "symbol": "BTCUSDT",
            "side": "BUY",
            "status": "FILLED",
            "executedQty": "0.01",
            "avgPrice": "43000",
            "origQty": "0.01",
        },
    )
    client = BinanceFuturesClient()
    result = client.place_market_order("BTCUSDT", "BUY", "0.01", "tb_test")
    assert result["orderId"] == 1
    assert result["status"] == "FILLED"
    # newClientOrderId must be forwarded for traceability (Critical Detail 5).
    sent_params = _sent_params(mock_request)
    assert sent_params["newClientOrderId"] == "tb_test"
    assert sent_params["type"] == "MARKET"
    # signature must be present on the wire (and last).
    assert "signature" in sent_params


@patch.dict("os.environ", ENV)
@patch("bot.client.requests.get")
@patch("bot.client.requests.request")
def test_place_limit_order_sends_price_and_tif(mock_request, mock_get):
    _mock_time(mock_get)
    mock_request.return_value = MagicMock(
        status_code=200,
        json=lambda: {"orderId": 2, "symbol": "ETHUSDT", "side": "SELL", "status": "NEW"},
    )
    client = BinanceFuturesClient()
    client.place_limit_order("ETHUSDT", "SELL", "0.5", "4200", "tb_lim")
    sent = _sent_params(mock_request)
    assert sent["type"] == "LIMIT"
    assert sent["price"] == "4200"
    assert sent["timeInForce"] == "GTC"


@patch.dict("os.environ", ENV)
@patch("bot.client.requests.get")
@patch("bot.client.requests.request")
def test_api_error_raised_on_invalid_symbol(mock_request, mock_get):
    _mock_time(mock_get)
    mock_request.return_value = MagicMock(
        status_code=400, json=lambda: {"code": -1121, "msg": "Invalid symbol."}
    )
    client = BinanceFuturesClient()
    with pytest.raises(APIError) as exc:
        client.place_market_order("BAD", "BUY", "0.01", "tb_test")
    assert exc.value.code == -1121
    assert "Invalid symbol." in exc.value.msg


@patch.dict("os.environ", ENV)
@patch("bot.client.requests.get")
@patch("bot.client.time.sleep")
@patch("bot.client.requests.request")
def test_network_error_after_retries(mock_request, mock_sleep, mock_get):
    import requests

    _mock_time(mock_get)
    mock_request.side_effect = requests.ConnectionError("boom")
    client = BinanceFuturesClient()
    with pytest.raises(NetworkError):
        client.place_market_order("BTCUSDT", "BUY", "0.01", "tb_test")
    # 1 initial attempt + 2 retries = 3 send attempts.
    assert mock_request.call_count == 3


@patch.dict("os.environ", ENV)
@patch("bot.client.requests.get")
@patch("bot.client.requests.request")
def test_503_unknown_error_is_ambiguous(mock_request, mock_get):
    _mock_time(mock_get)
    mock_request.return_value = MagicMock(
        status_code=503, text="Unknown error, please check your request or try again later."
    )
    client = BinanceFuturesClient()
    with pytest.raises(AmbiguousOrderError):
        client.place_market_order("BTCUSDT", "BUY", "0.01", "tb_test")


@patch.dict("os.environ", ENV)
@patch("bot.client.requests.get")
@patch("bot.client.requests.request")
def test_503_service_unavailable_is_network_error(mock_request, mock_get):
    _mock_time(mock_get)
    mock_request.return_value = MagicMock(status_code=503, text="Service Unavailable.")
    client = BinanceFuturesClient()
    with pytest.raises(NetworkError):
        client.place_market_order("BTCUSDT", "BUY", "0.01", "tb_test")


@patch.dict("os.environ", ENV)
@patch("bot.client.requests.get")
@patch("bot.client.requests.request")
def test_server_time_offset_applied_to_timestamp(mock_request, mock_get):
    # Server clock is 10s ahead of local; offset must be added to the sent timestamp.
    import time

    mock_get.return_value = MagicMock(
        json=lambda: {"serverTime": int(time.time() * 1000) + 10_000}
    )
    mock_request.return_value = MagicMock(
        status_code=200, json=lambda: {"symbol": "BTCUSDT", "side": "BUY", "status": "FILLED"}
    )
    client = BinanceFuturesClient()
    assert client._time_offset_ms >= 9_000  # ~10s ahead (allow scheduling jitter)
    client.place_market_order("BTCUSDT", "BUY", "0.01", "tb_test")
    sent_ts = int(_sent_params(mock_request)["timestamp"])
    assert sent_ts > int(time.time() * 1000)

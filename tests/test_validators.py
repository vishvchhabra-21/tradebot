"""Tests for the pure validation layer (SRS §7, TAD §9). No network access."""

from decimal import Decimal

import pytest

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


def test_validate_symbol_normalizes_case():
    assert validate_symbol("btcusdt") == "BTCUSDT"


def test_validate_symbol_rejects_invalid_characters():
    with pytest.raises(ValidationError):
        validate_symbol("!!")


def test_validate_symbol_rejects_too_short():
    with pytest.raises(ValidationError):
        validate_symbol("BTC")


def test_validate_side_normalizes_case():
    assert validate_side("buy") == "BUY"
    assert validate_side("Sell") == "SELL"


def test_validate_side_rejects_unknown():
    with pytest.raises(ValidationError):
        validate_side("HOLD")


def test_validate_order_type_normalizes_case():
    assert validate_order_type("market") == "MARKET"
    assert validate_order_type("twap") == "TWAP"


def test_validate_order_type_rejects_unknown():
    with pytest.raises(ValidationError):
        validate_order_type("STOP")


def test_validate_quantity_rejects_zero():
    with pytest.raises(ValidationError):
        validate_quantity("0")


def test_validate_quantity_rejects_negative():
    with pytest.raises(ValidationError):
        validate_quantity("-1")


def test_validate_quantity_rejects_non_numeric():
    with pytest.raises(ValidationError):
        validate_quantity("abc")


def test_validate_quantity_uses_decimal_precision():
    qty = validate_quantity("0.01")
    assert qty == Decimal("0.01")
    assert isinstance(qty, Decimal)


def test_validate_price_required_for_limit():
    with pytest.raises(ValidationError):
        validate_price(None, "LIMIT")


def test_validate_price_required_for_twap():
    with pytest.raises(ValidationError):
        validate_price(None, "TWAP")


def test_validate_price_none_for_market():
    assert validate_price("100", "MARKET") is None


def test_validate_price_positive_for_limit():
    assert validate_price("42000", "LIMIT") == Decimal("42000")


def test_validate_price_rejects_zero_for_limit():
    with pytest.raises(ValidationError):
        validate_price("0", "LIMIT")


def test_validate_twap_params_caps_slices():
    with pytest.raises(ValidationError):
        validate_twap_params(60, 21)


def test_validate_twap_params_rejects_zero_duration():
    with pytest.raises(ValidationError):
        validate_twap_params(0, 5)


def test_build_order_request_happy_path():
    req = build_order_request("btcusdt", "buy", "market", "0.01")
    assert isinstance(req, OrderRequest)
    assert req.symbol == "BTCUSDT"
    assert req.side == "BUY"
    assert req.order_type == "MARKET"
    assert req.quantity == Decimal("0.01")
    assert req.price is None


def test_build_order_request_limit_requires_price():
    with pytest.raises(ValidationError):
        build_order_request("BTCUSDT", "BUY", "LIMIT", "0.01", price=None)

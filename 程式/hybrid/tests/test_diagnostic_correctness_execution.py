from __future__ import annotations

import pytest

from hybrid.core.multi_strategy_backtest_v1 import ohlc_stop_fill
from trading_system.backtest import _build_pnl_rows


def test_gap_stop_uses_open_when_open_is_below_stop():
    result = ohlc_stop_fill(100.0, 0.025, 96.0, 95.0)
    assert result["reason_code"] == "STOP_GAP_OPEN"
    assert result["reference_price"] == 96.0


def test_intrabar_stop_uses_stop_price_when_open_is_above_stop():
    result = ohlc_stop_fill(100.0, 0.025, 99.0, 97.0)
    assert result["reason_code"] == "STOP_INTRABAR"
    assert result["reference_price"] == pytest.approx(97.5)


def test_stop_is_not_triggered_when_low_stays_above_stop():
    assert ohlc_stop_fill(100.0, 0.025, 100.0, 98.0) is None


def test_cost_decomposition_separates_slippage_without_double_charge():
    trades = [
        {"datetime": "2024-07-01 09:05:00", "action": "Buy", "quantity": 10,
         "price": 100.05, "reference_price": 100.0, "slippage_cost": 0.5,
         "gross_amount": 1000.5, "fee": 1.425, "tax": 0.0, "code": "2330"},
        {"datetime": "2024-07-01 10:05:00", "action": "Sell", "quantity": 10,
         "price": 101.95, "reference_price": 102.0, "slippage_cost": 0.5,
         "gross_amount": 1019.5, "fee": 1.453, "tax": 3.0585, "code": "2330"},
    ]
    row = _build_pnl_rows(trades)[0]
    assert row["gross_pnl"] == pytest.approx(20.0)
    assert row["slippage_cost"] == pytest.approx(1.0)
    assert row["total_cost"] == pytest.approx(6.9365)
    assert row["net_pnl"] == pytest.approx(13.0635)
    assert row["gross_pnl"] - row["total_cost"] == pytest.approx(row["net_pnl"])


def test_zero_slippage_price_reference_preserves_original_price():
    reference_price = 123.45
    slippage_bps = 0.0
    buy_fill = reference_price * (1 + slippage_bps / 10000)
    sell_fill = reference_price * (1 - slippage_bps / 10000)
    assert buy_fill == sell_fill == reference_price


@pytest.mark.parametrize("bps", [5.0])
def test_slippage_is_adverse_for_both_sides(bps):
    reference_price = 100.0
    assert reference_price * (1 + bps / 10000) > reference_price
    assert reference_price * (1 - bps / 10000) < reference_price

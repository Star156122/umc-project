from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from hybrid.core.multi_strategy_backtest_v1 import (
    DevelopmentBacktestBlocked, assert_no_target_leakage, build_buy_and_hold_result, derive_equity_curve,
    gate_audit_fields, mature_label_mask, open_readonly_sqlite, prediction_precedes_candidate,
    summary_from_curve, validate_request,
)


@pytest.fixture
def config():
    return {
        "data_role": "historical_development",
        "stock_universe": ["2303", "2330"],
        "periods": {
            "training": {"start": "2023-01-01", "end": "2023-12-31"},
            "validation": {"start": "2024-01-01", "end": "2024-06-30"},
            "backtest": {"start": "2024-07-01", "end": "2024-12-31"},
        },
        "data_protection": {"final_oos_used": False},
    }


def test_single_and_multiple_strategy_selection(config):
    assert validate_request("2330", ["rf"], config) == ["rf"]
    assert validate_request("2330", ["technical", "rf", "xgboost", "gru"], config)


def test_unknown_stock_and_strategy_are_blocked(config):
    with pytest.raises(DevelopmentBacktestBlocked, match="UNSUPPORTED_STOCK"):
        validate_request("2881", ["rf"], config)
    with pytest.raises(DevelopmentBacktestBlocked, match="INVALID_STRATEGY"):
        validate_request("2330", ["rf", "rf"], config)


def test_prohibited_dates_are_blocked(config):
    config["periods"]["backtest"]["end"] = "2026-07-01"
    with pytest.raises(DevelopmentBacktestBlocked, match="OUTSIDE_AUTHORIZED"):
        validate_request("2330", ["technical"], config)


def test_missing_database_is_blocked_without_creation(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(DevelopmentBacktestBlocked, match="MISSING_DATABASE"):
        open_readonly_sqlite(missing)
    assert not missing.exists()


def test_mature_labels_do_not_cross_split_boundary():
    meta = pd.DataFrame({
        "signal_date": ["2023-12-27", "2023-12-29", "2024-01-02", "2024-06-27"],
        "target_date": ["2023-12-29", "2024-01-03", "2024-01-04", "2024-07-02"],
    })
    train = {"start": "2023-01-01", "end": "2023-12-31"}
    validation = {"start": "2024-01-01", "end": "2024-06-30"}
    assert mature_label_mask(meta, train).tolist() == [True, False, False, False]
    assert mature_label_mask(meta, validation).tolist() == [False, False, True, False]
    assert_no_target_leakage(meta, train, validation)


def test_equity_curve_includes_fees_position_and_mark_to_market():
    bars = [
        {"time": 1, "datetime": "2024-07-01T09:00:00", "close": 10.0},
        {"time": 2, "datetime": "2024-07-01T09:05:00", "close": 11.0},
        {"time": 3, "datetime": "2024-07-01T09:10:00", "close": 11.0},
    ]
    trades = [{"timestamp": 2, "action": "Buy", "quantity": 2, "net_cash_flow": -20.1,
               "gross_amount": 20.0, "fee": 0.1, "tax": 0.0}]
    curve = derive_equity_curve(bars, trades, 100.0)
    assert curve[0]["cash"] == 100.0
    assert curve[1]["cash"] == pytest.approx(79.9)
    assert curve[1]["position_value"] == 22.0
    assert curve[1]["equity"] == pytest.approx(101.9)
    assert curve[1]["cumulative_return_pct"] == pytest.approx(1.9)
    summary = summary_from_curve(curve, [], 100.0)
    assert summary["final_equity"] == pytest.approx(101.9)
    assert summary["trade_count"] == 0
    assert summary["win_rate_pct"] is None
    assert summary["max_drawdown_pct"] >= 0


def test_no_trade_metrics_are_null():
    bars = [{"time": 1, "datetime": "2024-07-01T09:00:00", "close": 100.0}]
    summary = summary_from_curve(derive_equity_curve(bars, [], 1000), [], 1000)
    assert summary["win_rate_pct"] is None
    assert summary["profit_factor"] is None
    assert summary["net_pnl"] == 0


def test_buy_and_hold_marks_same_period_bars_without_risk_or_exit_cost():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE market_kbars (stock_code TEXT, freq_minutes INTEGER, kbar_timestamp INTEGER, open REAL, close REAL)")
    first_stamp = int(datetime(2024, 7, 1, 9, 0, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())
    connection.executemany("INSERT INTO market_kbars VALUES(?,?,?,?,?)", [
        ("2330", 5, first_stamp + index * 300, opening, closing)
        for index, (opening, closing) in enumerate([(10.0, 10.5), (10.5, 11.0), (11.0, 12.0), (12.0, 100.0)])
    ])
    result = build_buy_and_hold_result(
        connection=connection, stock_code="2330",
        period={"start": "2024-07-01", "end": "2024-07-01"},
        initial_capital=10000.0, commission_rate=0.001,
    )
    connection.close()
    assert result["strategy_id"] == "buy_and_hold"
    assert result["summary"]["final_equity"] == pytest.approx(99900.01)
    assert result["summary"]["total_return_pct"] == pytest.approx(899.0001)
    assert result["summary"]["trade_count"] is None
    assert result["summary"]["win_rate_pct"] is None
    assert result["details"]["transaction_count"] == 1
    assert result["details"]["total_transaction_cost"] == pytest.approx(9.99)
    assert result["details"]["transaction_tax_total"] == 0
    assert result["details"]["risk_contract"] is None
    assert result["audit"]["risk_v1_applied"] is False
    assert len(result["equity_curve"]) == 4


def test_ml_decision_must_be_prior_session_1330_before_candidate():
    candidate = "2024-07-02T09:00:00+08:00"
    prior = {"signal_date": "2024-07-01", "entry_date": "2024-07-02",
             "decision_time": "2024-07-01T13:30:00+08:00"}
    same_day = {"signal_date": "2024-07-02", "entry_date": "2024-07-02",
                "decision_time": "2024-07-02T13:30:00+08:00"}
    late = {"signal_date": "2024-07-02", "entry_date": "2024-07-02",
            "decision_time": "2024-07-02T13:30:00+08:00"}
    assert prediction_precedes_candidate(prior, candidate)
    assert not prediction_precedes_candidate(same_day, candidate)
    assert not prediction_precedes_candidate(late, "2024-07-02T13:00:00+08:00")


def test_gate_audit_distinguishes_pure_technical_from_ml_gate():
    technical = gate_audit_fields("technical", 18, [])
    assert technical["gate_applicable"] is False
    assert technical["gate_entry_count"] is None
    assert technical["technical_candidate_schedule_count"] == 18

    rf = gate_audit_fields("rf", 18, [
        {"hybrid_action": "ENTRY"},
        {"hybrid_action": "BLOCK_HOLD"},
    ])
    assert rf["gate_applicable"] is True
    assert rf["gate_evaluated_candidate_count"] == 2
    assert rf["gate_not_evaluated_candidate_count"] == 16
    assert rf["gate_entry_count"] == 1
    assert rf["gate_block_counts"]["HOLD"] == 1

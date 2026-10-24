from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from hybrid.core.multi_strategy_backtest_v1 import build_exposure_matched_buy_and_hold_result
from hybrid.core.multi_strategy_diagnostics import (
    annotate_technical_candidates, build_diagnostics, build_gate_candidate_rows,
    candidate_event_id, exposure_metrics, merge_technical_execution_rows, pnl_decomposition,
    probability_summary,
)


TAIPEI = ZoneInfo("Asia/Taipei")


def _stamp(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=TAIPEI).timestamp())


def test_pnl_identity_includes_commission_tax_and_slippage_once():
    result = pnl_decomposition([
        {"gross_pnl": 100.0, "fee": 10.0, "tax": 5.0, "slippage": 2.0, "net_pnl": 83.0},
    ])
    assert result == {
        "gross_pnl": 100.0, "commission": 10.0, "tax": 5.0,
        "slippage": 2.0, "total_cost": 17.0, "net_pnl": 83.0,
        "identity_delta": 0.0, "completed_trade_count": 1,
        "average_cost_per_trade": 17.0, "positive_gross_profit": 100.0,
        "cost_to_positive_gross_profit_ratio": 0.17,
        "cost_to_absolute_gross_pnl_ratio": 0.17,
        "cost_to_initial_capital_ratio": None,
    }
    with pytest.raises(ValueError, match="PNL_IDENTITY_MISMATCH"):
        pnl_decomposition([
            {"gross_pnl": 100.0, "fee": 10.0, "tax": 5.0, "net_pnl": 90.0},
        ])


def test_probability_summary_has_requested_buy_probability_quantiles():
    result = probability_summary([0.1, 0.2, 0.3, 0.4])
    assert result["min"] == 0.1
    assert result["max"] == 0.4
    assert result["mean"] == pytest.approx(0.25)
    assert result["median"] == pytest.approx(0.25)
    assert set(("p10", "p25", "p75", "p90")) <= set(result)


def test_candidate_diagnostics_use_next_bar_open_and_cost_aware_return():
    start = _stamp("2024-07-01T09:05:00")
    bars = [
        {"time": start, "datetime": "2024-07-01 09:05:00", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
        {"time": start + 300, "datetime": "2024-07-01 09:10:00", "open": 101.0, "high": 104.0, "low": 100.0, "close": 103.0},
    ]
    source = {
        "candidate_id": "RULE", "stock_code": "2330",
        "earliest_entry_time": "2024-07-01T09:05:00+08:00",
        "signal_time": "2024-07-01T09:04:59.999999+08:00",
        "parameter_fingerprint": "abc", "reason": "breakout",
        "market_regime": "BULL", "market_regime_pass": True,
    }
    result = annotate_technical_candidates(
        [source], bars, horizon_bars=2, commission_rate=0.001,
        transaction_tax_rate=0.003, slippage_rate=0.0,
    )[0]
    assert result["candidate_entry_price"] == 100.0
    assert result["fixed_horizon_gross_return"] == pytest.approx(0.03)
    assert result["fixed_horizon_net_return"] < result["fixed_horizon_gross_return"]
    assert result["fixed_horizon_mfe"] == pytest.approx(0.04)
    assert result["fixed_horizon_mae"] == pytest.approx(-0.01)
    assert result["candidate_id"] == candidate_event_id(source)


def test_same_candidate_id_is_used_before_all_four_gates():
    candidate = {
        "candidate_id": "CAND-stable", "technical_candidate_id": "RULE",
        "stock_code": "2330", "candidate_entry_time": "2024-07-01T09:05:00+08:00",
        "market_regime_pass": True, "fixed_horizon_net_return": 0.01,
    }
    empty = lambda name: {
        "strategy_id": name, "trades": [], "signals": [], "completed_trade_pnl": [],
    }
    prediction = {
        "prediction": "BUY", "prob_sell": 0.1, "prob_hold": 0.2, "prob_buy": 0.7,
    }
    lookups = {
        name: {("2330", "2024-07-01"): prediction}
        for name in ("rf", "xgboost", "gru")
    }
    rows = build_gate_candidate_rows(
        [candidate], [empty(name) for name in ("technical", "rf", "xgboost", "gru")], lookups,
    )
    assert len(rows) == 4
    assert {row["candidate_id"] for row in rows} == {"CAND-stable"}
    assert all(row["gate_decision"] == "ENTRY" for row in rows)


def test_open_entry_is_not_misreported_as_no_entry():
    stamp = _stamp("2024-07-01T09:05:00")
    candidate = {
        "candidate_id": "CAND-open", "technical_candidate_id": "RULE",
        "stock_code": "2330", "candidate_entry_time": "2024-07-01T09:05:00+08:00",
        "market_regime_pass": True,
    }
    result = {
        "strategy_id": "technical",
        "trades": [{"action": "Buy", "timestamp": stamp,
                    "datetime": "2024-07-01 09:05:00", "price": 100.0}],
        "signals": [], "completed_trade_pnl": [],
    }
    row = build_gate_candidate_rows([candidate], [result], {})[0]
    assert row["entered"] is True
    assert row["exit_time"] is None
    assert row["realized_net_pnl"] is None


def test_raw_technical_ledger_keeps_regime_rejection_and_execution_cost_parts():
    raw = [
        {"candidate_id": "CAND-pass", "market_regime_pass": True},
        {"candidate_id": "CAND-block", "market_regime_pass": False},
    ]
    gate = [{
        "candidate_id": "CAND-pass", "strategy_id": "technical",
        "gate_decision": "ENTRY", "entered": True, "not_entered_reason": "ENTERED",
        "realized_gross_pnl": 10.0, "realized_commission": 1.0,
        "realized_tax": 0.5, "realized_slippage": 0.25,
        "realized_cost": 1.75, "realized_net_pnl": 8.25,
    }]
    rows = merge_technical_execution_rows(raw, gate)
    assert rows[0]["realized_commission"] == 1.0
    assert rows[0]["realized_tax"] == 0.5
    assert rows[1]["entered"] is False
    assert rows[1]["not_entered_reason"] == "BLOCK_MARKET_REGIME"


def test_data_flow_keeps_training_samples_separate_from_execution_events():
    technical_rows = [{
        "candidate_id": "CAND-1", "market_regime_pass": True,
        "outcome_status": "EVALUATED", "market_regime": "BULL",
        "fixed_horizon_gross_return": 0.01, "fixed_horizon_net_return": 0.005,
    }]
    gate_rows = [{
        **technical_rows[0], "strategy_id": "gru", "gate_decision": "BLOCK_HOLD",
        "predicted_class": "HOLD", "prob_buy": 0.2, "entered": False,
    }]
    result = {
        "strategy_id": "gru",
        "summary": {"initial_capital": 1000.0, "trade_count": 0,
                    "win_rate_pct": None, "profit_factor": None},
        "details": {"risk_decisions": [], "average_trade_return_pct": None,
                    "average_win": None, "average_loss": None, "payoff_ratio": None},
        "equity_curve": [{"position_value": 0.0, "equity": 1000.0}],
        "completed_trade_pnl": [], "trades": [],
    }
    model_audit = {
        "input_audit": {"sequence_eligible_samples": 12,
                        "nonfinite_feature_exclusions": 2, "model_ready_samples": 10},
        "models": {"gru": {"training_samples": 7,
                            "training_label_counts": {"SELL": 2, "HOLD": 3, "BUY": 2}}},
    }
    diagnostics = build_diagnostics(
        technical_rows, gate_rows, [result], model_audit,
        {"data_checks": [{"role": "technical_bars", "rows": 99}]},
    )
    flow = diagnostics["data_flow"]
    assert flow["ml_training_samples_per_model"] == 7
    assert flow["execution_counts_by_strategy"]["gru"]["actual_entries"] == 0
    assert flow["execution_counts_by_strategy"]["gru"]["completed_closes"] == 0
    assert diagnostics["strategies"]["gru"]["undefined_no_trade_metrics_are_null"] is True


def test_zero_exposure_has_null_return_on_invested_capital():
    result = exposure_metrics(
        [{"position_value": 0.0, "equity": 1000.0}], [], 1000.0,
    )
    assert result["time_in_market"] == 0.0
    assert result["average_exposure"] == 0.0
    assert result["return_on_invested_capital"] is None


def test_exposure_matched_buy_and_hold_keeps_original_cap_and_charges_exit_costs():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE market_kbars (stock_code TEXT, freq_minutes INTEGER, "
        "kbar_timestamp INTEGER, open REAL, high REAL, low REAL, close REAL)"
    )
    start = _stamp("2024-07-01T09:00:00")
    connection.executemany("INSERT INTO market_kbars VALUES(?,?,?,?,?,?,?)", [
        ("2330", 5, start, 100.0, 101.0, 99.0, 100.0),
        ("2330", 5, start + 300, 100.0, 111.0, 99.0, 110.0),
    ])
    result = build_exposure_matched_buy_and_hold_result(
        connection=connection, stock_code="2330",
        period={"start": "2024-07-01", "end": "2024-07-01"},
        initial_capital=100_000.0, commission_rate=0.001,
        transaction_tax_rate=0.003, max_position_fraction=0.4,
    )
    connection.close()
    assert result["strategy_id"] == "exposure_matched_buy_and_hold"
    assert result["details"]["entry_exposure_fraction"] <= 0.4
    assert result["details"]["transaction_count"] == 2
    assert result["details"]["transaction_tax_total"] > 0
    assert len(result["completed_trade_pnl"]) == 1

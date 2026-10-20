from __future__ import annotations

import pandas as pd

from hybrid.core.risk_v1 import calculate_risk_size
from hybrid.core.risk_v2_exp001 import (
    atr_context_for_dates,
    calculate_atr_risk_size,
    compare_gate_decisions,
    entry_candidate_parity,
    execution_fill_check,
    wilder_atr,
)


def daily_rows(count: int = 20) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-02", periods=count)
    closes = [100.0 + i for i in range(count)]
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "high": [value + 2 for value in closes],
        "low": [value - 2 for value in closes],
        "close": closes,
    })


def test_atr14_wilder_seed_and_first_valid_index():
    frame = wilder_atr(daily_rows(15))
    assert frame["atr14"].iloc[:13].isna().all()
    assert frame["atr14"].iloc[13] == 4.0
    assert frame["atr14"].iloc[14] == 4.0


def test_atr_context_uses_only_d_minus_1_and_ignores_future_bars():
    daily = daily_rows(22)
    context = atr_context_for_dates(daily, {"2023-02-01"})
    assert context["2023-02-01"]["atr_as_of_date"] == "2023-01-31"
    changed_future = daily.copy()
    changed_future.loc[changed_future["date"] >= "2023-02-01", ["high", "low", "close"]] = [999, 998, 999]
    context_after_future_change = atr_context_for_dates(changed_future, {"2023-02-01"})
    assert context["2023-02-01"] == context_after_future_change["2023-02-01"]


def test_target_date_does_not_use_same_day_atr():
    context = atr_context_for_dates(daily_rows(20), {"2023-01-30"})
    assert context["2023-01-30"]["atr_as_of_date"] < "2023-01-30"


def test_atr_v1_reproduction_at_fixed_stop_distance():
    common = dict(current_equity=100_000, entry_price=80, original_quantity=1000)
    v1 = calculate_risk_size(**common)
    v2 = calculate_atr_risk_size(**common, stop_loss_pct=0.025)
    assert v1["final_quantity"] == v2["final_quantity"]
    assert v1["position_fraction"] == v2["position_fraction"]
    assert v1["estimated_risk_fraction"] == v2["estimated_risk_fraction"]


def test_high_volatility_cannot_increase_position_and_respects_limits():
    common = dict(current_equity=100_000, entry_price=100, original_quantity=1000)
    v1 = calculate_risk_size(**common)
    v2 = calculate_atr_risk_size(**common, stop_loss_pct=0.08)
    assert v2["position_fraction"] <= v1["position_fraction"]
    assert v2["position_fraction"] <= 0.40
    assert v2["estimated_risk_fraction"] <= 0.01
    assert v2["stop_loss_pct"] >= 0.025


def test_gate_parity_requires_same_candidates_and_payload():
    rows = [{"stock_code": "2303", "fold_id": "WF1", "technical_signal_time": "2023-07-03T10:00:00+08:00",
             "ml_prediction": "BUY", "hybrid_action": "ENTRY", "ml_signal_date": "2023-06-30",
             "prob_sell": 0.1, "prob_hold": 0.2, "prob_buy": 0.7}]
    assert compare_gate_decisions(pd.DataFrame(rows), pd.DataFrame(rows))["status"] == "PASS"
    changed = [dict(rows[0], hybrid_action="BLOCK_HOLD")]
    assert compare_gate_decisions(pd.DataFrame(rows), pd.DataFrame(changed))["status"] == "FAIL"


def test_execution_price_matches_next_bar_open_contract():
    trades = [{"action": "Buy", "code": "2303", "timestamp": 123, "price": 50.0}]
    assert execution_fill_check(trades, {("2303", 123): 50.0})["status"] == "PASS"
    assert execution_fill_check(trades, {("2303", 123): 49.9})["status"] == "FAIL"


def test_both_risk_arms_execute_only_the_same_buy_gated_candidate():
    gates = pd.DataFrame([{"stock_code": "2303", "fold_id": "WF1",
        "technical_signal_time": "2023-07-03T09:05:00+08:00", "hybrid_action": "ENTRY"}])
    trades = [
        {"variant": "RISK_V1", "action": "Buy", "stock_code": "2303", "fold_id": "WF1", "timestamp": 1688346300},
        {"variant": "RISK_V2", "action": "Buy", "stock_code": "2303", "fold_id": "WF1", "timestamp": 1688346300},
    ]
    assert entry_candidate_parity(trades, gates)["status"] == "PASS"


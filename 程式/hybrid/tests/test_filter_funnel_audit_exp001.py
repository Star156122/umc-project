import pandas as pd

from hybrid.scripts.run_filter_funnel_audit_exp001 import _filter_classification, _quality


def test_prediction_quality_uses_only_requested_class_and_candidate_group():
    frame = pd.DataFrame([
        {"model": "random_forest", "prediction": "BUY", "candidate_group": "prediction_class",
         "evaluable": True, "net_trade_return": 0.02, "gross_pnl": 20, "transaction_cost": 2,
         "net_pnl": 18, "holding_bars": 4},
        {"model": "random_forest", "prediction": "HOLD", "candidate_group": "prediction_class",
         "evaluable": True, "net_trade_return": -0.01, "gross_pnl": -8, "transaction_cost": 2,
         "net_pnl": -10, "holding_bars": 6},
    ])
    result = _quality(frame, "random_forest", "BUY", "prediction_class")
    assert result["candidate_count"] == 1
    assert result["net_pnl"] == 18


def test_blocked_set_is_selected_by_group_even_when_prediction_is_hold_or_sell():
    frame = pd.DataFrame([
        {"model": "random_forest", "prediction": "HOLD", "candidate_group": "blocked",
         "evaluable": True, "net_trade_return": 0.01, "gross_pnl": 10, "transaction_cost": 1,
         "net_pnl": 9, "holding_bars": 5},
        {"model": "random_forest", "prediction": "SELL", "candidate_group": "blocked",
         "evaluable": True, "net_trade_return": -0.02, "gross_pnl": -20, "transaction_cost": 1,
         "net_pnl": -21, "holding_bars": 7},
    ])
    result = _quality(frame, "random_forest", "blocked", "blocked")
    assert result["candidate_count"] == 2
    assert result["evaluable_count"] == 2


def test_classification_requires_cross_stock_coverage_and_sample_floor():
    buy = {"evaluable_count": 5, "average_net_trade_return": 0.03, "profit_factor": 3}
    blocked = {"evaluable_count": 9, "average_net_trade_return": -0.01, "profit_factor": 0.8}
    one_stock = pd.DataFrame([{"stock_code": "2330", "block_class": "blocked",
                               "negative_evidence": True, "positive_evidence": False}])
    assert _filter_classification(buy, blocked, one_stock) == "INSUFFICIENT_EVIDENCE"


def test_classification_can_identify_conservative_filter_with_two_stock_support():
    buy = {"evaluable_count": 3, "average_net_trade_return": 0.01, "profit_factor": 1.2}
    blocked = {"evaluable_count": 12, "average_net_trade_return": 0.02, "profit_factor": 1.5}
    evidence = pd.DataFrame([
        {"stock_code": "2330", "block_class": "blocked", "negative_evidence": False, "positive_evidence": True},
        {"stock_code": "2454", "block_class": "blocked", "negative_evidence": False, "positive_evidence": True},
        {"stock_code": "2303", "block_class": "blocked", "negative_evidence": False, "positive_evidence": False},
    ])
    assert _filter_classification(buy, blocked, evidence) == "OVER_CONSERVATIVE_EVIDENCE"

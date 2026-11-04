from __future__ import annotations

import pytest

from hybrid.core.expected_edge_filter_exp001 import (
    estimate_expected_net_edge,
    prior_completed_trade_returns,
    validate_no_lookahead,
)
from hybrid.scripts.run_expected_edge_filter_exp001 import _trade_time_key


def test_prior_training_trades_must_exit_strictly_before_signal():
    history = [
        {"exit_timestamp": 10, "gross_return": 0.02},
        {"exit_timestamp": 20, "gross_return": 0.04},
        {"exit_timestamp": 30, "gross_return": -0.01},
    ]
    assert prior_completed_trade_returns(history, 20) == [{"exit_timestamp": 10, "gross_return": 0.02}]


def test_candidate_is_not_allowed_to_use_its_own_or_future_outcome():
    history = [{"exit_timestamp": 100, "gross_return": 0.5}]
    result = estimate_expected_net_edge(
        completed_trades=history, candidate_timestamp=100, entry_price=100,
        quantity=1000, min_matured_trades=1, commission_rate=0.001425,
        tax_rate=0.003, slippage_bps_per_side=5,
    )
    assert result.status == "INSUFFICIENT_MATURED_BASELINE_TRADES"
    assert result.matured_trade_count == 0
    assert result.estimated_net_return is None


def test_insufficient_history_is_not_misclassified_as_negative_edge():
    result = estimate_expected_net_edge(
        completed_trades=[{"exit_timestamp": 1, "gross_return": 0.2}],
        candidate_timestamp=2, entry_price=100, quantity=1000,
        min_matured_trades=5, commission_rate=0.001425,
        tax_rate=0.003, slippage_bps_per_side=5,
    )
    assert result.status == "INSUFFICIENT_MATURED_BASELINE_TRADES"
    assert result.estimated_gross_return is None
    assert result.estimated_cost is None


def test_round_trip_costs_use_both_commissions_tax_and_two_sided_slippage():
    result = estimate_expected_net_edge(
        completed_trades=[{"exit_timestamp": i, "gross_return": 0.02} for i in range(5)],
        candidate_timestamp=10, entry_price=100, quantity=1000,
        min_matured_trades=5, commission_rate=0.001425,
        tax_rate=0.003, slippage_bps_per_side=5,
    )
    expected_exit = 102.0
    expected_cost = 100_000 * 0.001425 + 102_000 * 0.001425 + 102_000 * 0.003 + 100_000 * 0.0005 + 102_000 * 0.0005
    assert result.estimated_quantity == 1000
    assert result.estimated_gross_pnl == pytest.approx(2000)
    assert result.estimated_cost == pytest.approx(expected_cost)
    assert result.status == "ALLOW_EXPECTED_NET_POSITIVE"


def test_expected_nonpositive_net_edge_is_blocked():
    result = estimate_expected_net_edge(
        completed_trades=[{"exit_timestamp": i, "gross_return": 0.002} for i in range(5)],
        candidate_timestamp=10, entry_price=100, quantity=1000,
        min_matured_trades=5, commission_rate=0.001425,
        tax_rate=0.003, slippage_bps_per_side=5,
    )
    assert result.status == "BLOCK_EXPECTED_NET_NOT_POSITIVE"


def test_saved_decision_audit_rejects_noncausal_timestamp():
    validate_no_lookahead([{"completed_signal_timestamp": 20, "latest_training_exit_timestamp": 19}])
    with pytest.raises(AssertionError, match="not yet mature"):
        validate_no_lookahead([{"completed_signal_timestamp": 20, "latest_training_exit_timestamp": 20}])


def test_invalid_cost_or_quantity_is_rejected():
    with pytest.raises(ValueError):
        estimate_expected_net_edge(
            completed_trades=[], candidate_timestamp=10, entry_price=100, quantity=-1,
            min_matured_trades=5, commission_rate=0.001425,
            tax_rate=0.003, slippage_bps_per_side=5,
        )


def test_candidate_entry_timestamp_matches_backtest_trade_timestamp():
    assert _trade_time_key("2023-06-13T11:50:00.000000+08:00") == "2023-06-13 11:50:00"
    assert _trade_time_key("2023-06-13 11:50:00") == "2023-06-13 11:50:00"

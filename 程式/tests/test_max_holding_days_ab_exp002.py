from hybrid.scripts.run_max_holding_days_ab_exp002 import (
    day_equivalent_to_bars,
    inclusive_sessions,
    pair_baseline_time_exits,
)


def test_session_equivalent_conversion():
    assert [day_equivalent_to_bars(n, 54) for n in (3, 5, 10, 15)] == [162, 270, 540, 810]


def test_cross_day_is_transition_count_and_inclusive_sessions():
    sessions = ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-06"]
    assert inclusive_sessions("2023-01-02", "2023-01-02", sessions) == 1
    assert inclusive_sessions("2023-01-02", "2023-01-04", sessions) == 3


def test_pairing_original_time_exit_outcomes_by_entry_fill():
    baseline = [{"buy_datetime": "2023-01-02T09:05", "exit_type": "time_expiration", "net_pnl": -10}]
    longer = [{"buy_datetime": "2023-01-02T09:05", "exit_type": "technical_exit", "net_pnl": 25}]
    rows = pair_baseline_time_exits(baseline, {"B": longer, "C": [], "D": []})
    assert rows[0]["B_pair_status"] == "MATCHED"
    assert rows[0]["B_net_pnl_change"] == 35
    assert rows[0]["C_pair_status"] == "NO_MATCHED_TRADE"


def test_only_original_time_expirations_are_paired():
    baseline = [
        {"buy_datetime": "t1", "exit_type": "technical_exit", "net_pnl": 1},
        {"buy_datetime": "t2", "exit_type": "time_expiration", "net_pnl": 2},
    ]
    result = pair_baseline_time_exits(baseline, {"B": []})
    assert len(result) == 1
    assert result[0]["baseline_buy_datetime"] == "t2"

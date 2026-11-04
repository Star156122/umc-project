from datetime import datetime
from zoneinfo import ZoneInfo

from hybrid.core.data_eligibility_v1 import (
    EXPECTED_BARS,
    audit_session,
    build_peer_proxy,
    classify_same_session_gap,
    classify_skipped_incomplete_session,
    expected_session_timestamps,
    indicator_window_clear,
    is_newly_eligible,
    market_context_from_proxy,
    proxy_close_for_day,
    signal_and_fill_are_contiguous,
    signal_window_eligible,
    select_unambiguous_valid_bars,
)

TAIPEI = ZoneInfo("Asia/Taipei")


def test_newly_eligible_requires_baseline_excluded_and_candidate_allowed():
    assert is_newly_eligible(False, True)
    assert not is_newly_eligible(True, True)
    assert not is_newly_eligible(False, False)
    assert not is_newly_eligible(None, True)


def bars_for(day="2024-01-02"):
    return [
        {"kbar_timestamp": stamp, "open": 100, "high": 101, "low": 99,
         "close": 100, "volume": 1000}
        for stamp in expected_session_timestamps(day)
    ]


def test_exact_session_passes_and_requires_54_real_bars():
    rows = bars_for()
    audit = audit_session(rows, "2024-01-02")
    assert audit["session_complete"] is True
    assert audit["bar_count"] == EXPECTED_BARS == 54


def test_peer_missing_non_key_intraday_bar_still_has_valid_proxy_close():
    peer_rows = bars_for()
    peer_rows.pop(12)
    assert proxy_close_for_day(peer_rows, "2024-01-02") == 100
    grouped = {(code, "2024-01-02"): bars_for() for code in ("2303", "2330", "2379", "2454", "3034")}
    grouped[("2330", "2024-01-02")] = peer_rows
    proxy, excluded = build_peer_proxy(grouped, ("2330", "2379", "2454", "3034"), ("2024-01-02",))
    assert proxy["2024-01-02"] == 100
    assert not excluded


def test_peer_missing_required_close_excludes_proxy_date():
    grouped = {(code, "2024-01-02"): bars_for() for code in ("2303", "2330", "2379", "2454", "3034")}
    grouped[("2379", "2024-01-02")] = bars_for()[:-1]
    proxy, excluded = build_peer_proxy(grouped, ("2330", "2379", "2454", "3034"), ("2024-01-02",))
    assert not proxy
    assert excluded["2024-01-02"] == "MISSING_OR_INVALID_SAME_DATE_13_25_PEER_CLOSE"


def test_target_missing_noncritical_bar_is_not_filled_and_invalidates_window():
    rows = bars_for()
    rows.pop(10)
    audit = audit_session(rows, "2024-01-02")
    assert audit["session_complete"] is False
    assert audit["bar_count"] == 53
    assert len(audit["missing_timestamps"]) == 1
    assert len(select_unambiguous_valid_bars(rows, "2024-01-02")) == 53
    assert not indicator_window_clear(20, {10}, 24)
    assert indicator_window_clear(40, {10}, 24)


def test_missing_confirmation_or_next_fill_bar_blocks_signal():
    stamps = expected_session_timestamps("2024-01-02")
    # Missing confirmation means there is no signal timestamp in the observed sequence.
    observed = set(stamps) - {stamps[40]}
    assert stamps[40] not in observed
    assert not signal_and_fill_are_contiguous(stamps[39], stamps[41])
    # Signal exists, but its immediately following scheduled bar is absent.
    assert not signal_window_eligible(39, set(), 20, stamps[39], stamps[41])


def test_indicator_window_gap_and_gap_outside_window():
    assert not indicator_window_clear(100, {95}, 10)
    assert indicator_window_clear(100, {90}, 10)


def test_gap_during_open_position_has_explicit_exit_policy():
    stamps = expected_session_timestamps("2024-01-02")
    assert classify_same_session_gap(stamps[10], stamps[12], True) == "DATA_GAP_EXIT_AT_NEXT_OBSERVED_OPEN"
    assert classify_skipped_incomplete_session("2024-01-02", "2024-01-04", {"2024-01-03"}, True) == "DATA_GAP_EXIT_AT_NEXT_OBSERVED_OPEN"
    assert classify_skipped_incomplete_session("2024-01-02", "2024-01-04", {"2024-01-03"}, False) == "DATA_GAP_BLOCK_SIGNAL_WINDOW"


def test_duplicate_out_of_order_invalid_and_misaligned_bars_are_detected():
    rows = bars_for()
    rows[2], rows[3] = rows[3], rows[2]
    rows.append(dict(rows[-1]))
    audit = audit_session(rows, "2024-01-02")
    assert audit["duplicate_timestamps"]
    assert audit["out_of_order"]
    assert len(select_unambiguous_valid_bars(rows, "2024-01-02")) == 53
    bad = bars_for()
    bad[5]["high"] = 98
    assert audit_session(bad, "2024-01-02")["invalid_ohlcv_timestamps"]
    shifted = bars_for()
    shifted[0]["kbar_timestamp"] += 60
    assert audit_session(shifted, "2024-01-02")["unexpected_timestamps"]


def test_proxy_uses_same_date_and_only_strictly_prior_context():
    grouped = {}
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    for code in ("2330", "2379", "2454", "3034"):
        for index, day in enumerate(dates):
            grouped[(code, day)] = bars_for(day)
            for row in grouped[(code, day)]:
                row["close"] += index
                row["open"] += index
                row["high"] += index
                row["low"] += index
    proxy, excluded = build_peer_proxy(grouped, ("2330", "2379", "2454", "3034"), dates)
    assert not excluded
    assert set(proxy) == set(dates)
    context = market_context_from_proxy(proxy, dates, lookback=2)
    assert context["2024-01-04"]["latest_common_prior_date"] == "2024-01-03"
    assert context["2024-01-04"]["history_count"] == 2


def test_complete_data_strict_and_relaxed_proxy_definitions_are_identical():
    dates = [f"2024-01-{day:02d}" for day in range(1, 23)]
    peers = ("2330", "2379", "2454", "3034")
    grouped = {(code, day): bars_for(day) for code in (*peers, "2303") for day in dates}
    relaxed, excluded = build_peer_proxy(grouped, peers, dates)
    strict = {
        day: sum(proxy_close_for_day(grouped[(code, day)], day) for code in peers) / len(peers)
        for day in dates
    }
    assert not excluded
    assert strict == relaxed
    assert market_context_from_proxy(strict, dates, 20) == market_context_from_proxy(relaxed, dates, 20)


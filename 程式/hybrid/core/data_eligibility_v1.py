"""Data eligibility helpers for the isolated relaxed-coverage experiment.

These functions never create or fill market bars.  They distinguish a valid
target session from the smaller set of peer data required by the frozen market
proxy definition.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import math
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(13, 25)
BAR_SECONDS = 300
EXPECTED_BARS = 54


def expected_session_timestamps(day: str | date) -> list[int]:
    """Return the 54 real exchange bar-start timestamps for a Taiwan session."""
    parsed = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(parsed, SESSION_OPEN, tzinfo=TAIPEI)
    return [int((start + timedelta(seconds=BAR_SECONDS * i)).timestamp()) for i in range(EXPECTED_BARS)]


def valid_ohlcv(row: dict[str, Any]) -> bool:
    try:
        opening, high, low, close = (float(row[k]) for k in ("open", "high", "low", "close"))
        volume = int(row["volume"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return (
        all(math.isfinite(v) and v > 0 for v in (opening, high, low, close))
        and low <= min(opening, close) <= max(opening, close) <= high
        and volume >= 0
    )


def audit_session(rows: Iterable[dict[str, Any]], day: str | date) -> dict[str, Any]:
    """Audit one stock-session without filling missing timestamps."""
    materialized = list(rows)
    expected = expected_session_timestamps(day)
    stamps = [int(row["kbar_timestamp"]) for row in materialized]
    counts: dict[int, int] = {}
    for stamp in stamps:
        counts[stamp] = counts.get(stamp, 0) + 1
    actual_set = set(stamps)
    missing = [stamp for stamp in expected if stamp not in actual_set]
    unexpected = [stamp for stamp in actual_set if stamp not in set(expected)]
    duplicate = sorted(stamp for stamp, count in counts.items() if count > 1)
    invalid = [int(row["kbar_timestamp"]) for row in materialized if not valid_ohlcv(row)]
    out_of_order = any(a >= b for a, b in zip(stamps, stamps[1:]))
    complete = not (missing or unexpected or duplicate or invalid or out_of_order) and stamps == expected
    return {
        "bar_count": len(materialized), "expected_bar_count": EXPECTED_BARS,
        "missing_timestamps": missing, "unexpected_timestamps": sorted(unexpected),
        "duplicate_timestamps": duplicate, "invalid_ohlcv_timestamps": invalid,
        "out_of_order": out_of_order, "session_complete": complete,
        "sequence_status": "COMPLETE" if complete else "INCOMPLETE_NO_FILL",
    }


def select_unambiguous_valid_bars(rows: Iterable[dict[str, Any]], day: str | date) -> list[dict[str, Any]]:
    """Keep only real, valid, expected-time bars with a unique timestamp.

    Missing bars remain missing.  If a timestamp is duplicated or a row is
    misaligned/invalid, that row is excluded because its value is ambiguous or
    unsafe; all other real bars in the session remain available for auditing.
    """
    materialized = list(rows)
    expected = set(expected_session_timestamps(day))
    counts: dict[int, int] = {}
    for row in materialized:
        stamp = int(row["kbar_timestamp"])
        counts[stamp] = counts.get(stamp, 0) + 1
    return [
        row for row in materialized
        if int(row["kbar_timestamp"]) in expected
        and counts[int(row["kbar_timestamp"])] == 1
        and valid_ohlcv(row)
    ]


def proxy_close_for_day(rows: Iterable[dict[str, Any]], day: str | date) -> float | None:
    """Return a peer's exact 13:25 bar close only when unique and valid."""
    end_stamp = expected_session_timestamps(day)[-1]
    matching = [row for row in rows if int(row["kbar_timestamp"]) == end_stamp]
    if len(matching) != 1 or not valid_ohlcv(matching[0]):
        return None
    return float(matching[0]["close"])


def build_peer_proxy(
    rows_by_stock_day: dict[tuple[str, str], list[dict[str, Any]]],
    peer_codes: Iterable[str],
    candidate_dates: Iterable[str],
) -> tuple[dict[str, float], dict[str, str]]:
    """Build equal-weight proxy only on same-date valid closes for every peer."""
    peers = tuple(str(code) for code in peer_codes)
    closes: dict[str, float] = {}
    excluded: dict[str, str] = {}
    for day in sorted(set(candidate_dates)):
        values = [proxy_close_for_day(rows_by_stock_day.get((peer, day), []), day) for peer in peers]
        if any(value is None for value in values):
            excluded[day] = "MISSING_OR_INVALID_SAME_DATE_13_25_PEER_CLOSE"
            continue
        closes[day] = sum(float(value) for value in values) / len(values)
    return closes, excluded


def market_context_from_proxy(
    proxy: dict[str, float], candidate_dates: Iterable[str], lookback: int = 20,
) -> dict[str, dict[str, Any]]:
    """Use only strictly prior, date-aligned proxy observations; never backfill."""
    result: dict[str, dict[str, Any]] = {}
    eligible_dates = sorted(proxy)
    for candidate_day in sorted(set(candidate_dates)):
        prior = [day for day in eligible_dates if day < candidate_day]
        window_days = prior[-lookback:]
        values = [proxy[day] for day in window_days]
        ready = len(values) == lookback
        latest = values[-1] if ready else None
        sma = sum(values) / len(values) if ready else None
        result[candidate_day] = {
            "eligible": bool(ready and latest is not None and sma is not None and latest > sma),
            "reason": ("PASS_PRIOR_PROXY_ABOVE_SMA20" if ready and latest > sma else
                       "PROXY_NOT_ABOVE_SMA20" if ready else "INSUFFICIENT_PRIOR_SYNCHRONIZED_SESSIONS"),
            "target_stock_excluded": True,
            "latest_common_prior_date": window_days[-1] if ready else (prior[-1] if prior else None),
            "last_20_proxy_closes": values if ready else [],
            "proxy_sma20": sma,
            "history_count": len(values),
        }
    return result


def signal_and_fill_are_contiguous(signal_timestamp: int, next_timestamp: int | None) -> bool:
    """A signal can fill only at the next scheduled 5-minute bar, not a later bar."""
    return next_timestamp is not None and int(next_timestamp) - int(signal_timestamp) == BAR_SECONDS


def is_newly_eligible(baseline_eligible: bool | None, candidate_eligible: bool | None) -> bool:
    """True only when the relaxed arm admits a date the baseline excludes."""
    return baseline_eligible is False and candidate_eligible is True


def indicator_window_clear(current_slot_index: int, missing_slot_indices: Iterable[int], lookback_bars: int) -> bool:
    """True only when the requested expected-bar window has no missing slots."""
    if lookback_bars <= 0:
        raise ValueError("lookback_bars must be positive")
    first = int(current_slot_index) - int(lookback_bars) + 1
    return not any(first <= int(gap) <= int(current_slot_index) for gap in missing_slot_indices)


def signal_window_eligible(current_slot_index: int, missing_slot_indices: Iterable[int], lookback_bars: int,
                           signal_timestamp: int, next_timestamp: int | None) -> bool:
    """Require both a gap-free indicator window and the actual next bar for fill."""
    return indicator_window_clear(current_slot_index, missing_slot_indices, lookback_bars) and signal_and_fill_are_contiguous(signal_timestamp, next_timestamp)


def classify_same_session_gap(previous_timestamp: int, current_timestamp: int, has_position: bool) -> str:
    """Describe an intraday data gap; an open position requires explicit handling."""
    previous = datetime.fromtimestamp(int(previous_timestamp), TAIPEI)
    current = datetime.fromtimestamp(int(current_timestamp), TAIPEI)
    if previous.date() != current.date() or current_timestamp - previous_timestamp == BAR_SECONDS:
        return "NO_INTRADAY_GAP"
    if current_timestamp <= previous_timestamp:
        return "INVALID_TIMESTAMP_ORDER"
    return "DATA_GAP_EXIT_AT_NEXT_OBSERVED_OPEN" if has_position else "DATA_GAP_BLOCK_SIGNAL_WINDOW"


def classify_skipped_incomplete_session(previous_date: str, current_date: str,
                                        incomplete_dates: Iterable[str], has_position: bool) -> str:
    """Require explicit handling when a position spans an omitted target session."""
    skipped = any(previous_date < day <= current_date for day in incomplete_dates)
    if not skipped:
        return "NO_INCOMPLETE_SESSION_GAP"
    return "DATA_GAP_EXIT_AT_NEXT_OBSERVED_OPEN" if has_position else "DATA_GAP_BLOCK_SIGNAL_WINDOW"

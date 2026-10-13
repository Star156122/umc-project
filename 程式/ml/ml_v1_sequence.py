"""Exact, shared ML V1 intraday sequence eligibility policy."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

SEQUENCE_POLICY = "EXACT_24_BARS_11_35_TO_13_30"
MISSING_SEQUENCE_POLICY = "EXCLUDE_NO_FILL"
SEQUENCE_COMPLETE = "SEQUENCE_COMPLETE"
EXCLUDE_NO_FILL = "EXCLUDE_NO_FILL"
EXACT_SEQUENCE_TIMES = tuple(
    pd.date_range("2000-01-01 11:35", "2000-01-01 13:30", freq="5min").strftime("%H:%M:%S")
)


@dataclass(frozen=True)
class SequenceEligibility:
    status: str
    missing_times: tuple[str, ...]
    duplicate_times: tuple[str, ...]
    selected_index: tuple[int, ...]


def evaluate_exact_sequence(day_bars: pd.DataFrame) -> SequenceEligibility:
    """Require every exact clock time once; never substitute, fill, or interpolate."""
    if "datetime" not in day_bars:
        raise ValueError("day_bars requires a datetime column")
    if day_bars.empty:
        return SequenceEligibility(EXCLUDE_NO_FILL, EXACT_SEQUENCE_TIMES, (), ())
    datetimes = pd.to_datetime(day_bars["datetime"])
    if datetimes.dt.date.nunique() != 1:
        raise ValueError("evaluate_exact_sequence accepts exactly one stock-date")
    if bool((datetimes.dt.dayofweek >= 5).any()):
        return SequenceEligibility(EXCLUDE_NO_FILL, EXACT_SEQUENCE_TIMES, (), ())

    clock = datetimes.dt.strftime("%H:%M:%S")
    missing = tuple(value for value in EXACT_SEQUENCE_TIMES if int((clock == value).sum()) == 0)
    duplicates = tuple(value for value in EXACT_SEQUENCE_TIMES if int((clock == value).sum()) > 1)
    if missing or duplicates:
        return SequenceEligibility(EXCLUDE_NO_FILL, missing, duplicates, ())
    positions = tuple(int(day_bars.index[clock == value][0]) for value in EXACT_SEQUENCE_TIMES)
    return SequenceEligibility(SEQUENCE_COMPLETE, (), (), positions)


def select_exact_sequence(day_bars: pd.DataFrame) -> tuple[pd.DataFrame | None, SequenceEligibility]:
    result = evaluate_exact_sequence(day_bars)
    if result.status != SEQUENCE_COMPLETE:
        return None, result
    selected = day_bars.loc[list(result.selected_index)].copy()
    selected = selected.assign(_clock=pd.to_datetime(selected["datetime"]).dt.strftime("%H:%M:%S"))
    selected = selected.set_index("_clock").loc[list(EXACT_SEQUENCE_TIMES)].reset_index(drop=True)
    return selected, result

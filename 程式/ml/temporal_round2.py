"""Training-only data guards for ML V1 Temporal Context Round 2.

This module constructs no model.  It only verifies and aligns the fixed
three-session candidate against the existing ML V1 baseline sample keys.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.ml_v1_sequence import EXACT_SEQUENCE_TIMES, SEQUENCE_COMPLETE, select_exact_sequence

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "ml_v1_temporal_round2.json"
CONTEXT_ROUND1_FEATURES = {
    "stock_return_5d", "stock_return_20d", "stock_volatility_20d",
    "industry_proxy_return_5d", "industry_proxy_return_20d",
    "stock_vs_industry_proxy_5d", "market_proxy_return_5d",
    "market_proxy_return_20d", "stock_vs_market_proxy_5d",
}


@dataclass(frozen=True)
class TemporalBuildResult:
    sequences: dict[tuple[str, str], np.ndarray]
    common_samples: pd.DataFrame
    exclusions: pd.DataFrame
    audit: dict[str, Any]


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    if config["status"] != "PREFLIGHT_REVIEW_REQUIRED_NOT_AUTHORIZED_FOR_TRAINING":
        raise ValueError("Round 2 is preflight-only and is not authorized for training")
    if config["data"] != {
        "role": "train", "start": "2023-01-01", "end": "2024-12-31",
        "database": "data/market_data.sqlite3", "read_only": True,
    }:
        raise ValueError("Round 2 may only read the fixed 2023-2024 Training dataset")
    fixed = config["fixed_contract"]
    if fixed["decision_time"] != "13:30:00" or fixed["prediction_horizon_sessions"] != 3:
        raise ValueError("Decision time or prediction horizon changed")
    if fixed["context_round1_features_used"] is not False:
        raise ValueError("Context Round 1 features are forbidden")
    candidate = config["candidate"]
    if candidate.get("design_status") != "PROPOSED_OPTION_A_REQUIRES_USER_DECISION":
        raise ValueError("Round 2 representation has not been approved")
    if candidate["historical_sessions"] != 3 or candidate["timesteps"] != 72:
        raise ValueError("Round 2 must use exactly three 24-bar trading-session windows")
    if tuple(candidate["per_session_times"]) != EXACT_SEQUENCE_TIMES:
        raise ValueError("Each historical session must reuse the exact baseline 24-bar window")
    if candidate["missing_policy"] != "EXCLUDE_NO_FILL" or candidate["nonfinite_policy"] != "EXCLUDE_NO_FILL":
        raise ValueError("Round 2 must exclude missing/nonfinite inputs without filling")
    if not all(config["forbidden"].values()):
        raise ValueError("Every Round 2 preflight guard must remain enabled")
    expected_inner = {
        "1": "2023-07-20", "2": "2023-10-26",
        "3": "2024-01-22", "4": "2024-05-09",
    }
    if config["validation"].get("gru_inner_validation_start_by_fold") != expected_inner:
        raise ValueError("GRU chronological inner-validation boundaries changed")


def market_trading_dates(frame: pd.DataFrame) -> list[str]:
    """Return chronological weekday sessions observed in the core universe."""
    dates = pd.to_datetime(frame["trade_date"])
    return sorted(dates.loc[dates.dt.dayofweek < 5].dt.date.astype(str).unique().tolist())


def _sequence_lookup(
    featured: pd.DataFrame,
    feature_names: list[str],
) -> tuple[dict[tuple[str, str], np.ndarray], dict[tuple[str, str], str]]:
    lookup: dict[tuple[str, str], np.ndarray] = {}
    failures: dict[tuple[str, str], str] = {}
    for (code, day), group in featured.groupby(["stock_code", "trade_date"], sort=True):
        key = (str(code), str(day))
        selected, eligibility = select_exact_sequence(group)
        if selected is None or eligibility.status != SEQUENCE_COMPLETE:
            failures[key] = "MISSING_EXACT_24_BAR_SEQUENCE"
            continue
        values = selected[feature_names].to_numpy(dtype=np.float32)
        if values.shape != (24, len(feature_names)):
            failures[key] = "INVALID_SEQUENCE_SHAPE"
            continue
        if not np.isfinite(values).all():
            failures[key] = "NONFINITE_FEATURE_NO_FILL"
            continue
        lookup[key] = values
    return lookup, failures


def build_three_session_intersection(
    baseline_samples: pd.DataFrame,
    featured: pd.DataFrame,
    feature_names: list[str],
) -> TemporalBuildResult:
    """Build T-2/T-1/T sequences and the exact aligned baseline intersection."""
    required = {"stock_code", "decision_date"}
    if not required.issubset(baseline_samples.columns):
        raise ValueError(f"baseline_samples must contain {sorted(required)}")
    baseline = baseline_samples.copy()
    baseline["stock_code"] = baseline["stock_code"].astype(str)
    baseline["decision_date"] = pd.to_datetime(baseline["decision_date"]).dt.date.astype(str)
    if baseline[["stock_code", "decision_date"]].duplicated().any():
        raise ValueError("Duplicate baseline stock-date key")

    work = featured.copy()
    work["stock_code"] = work["stock_code"].astype(str)
    work["trade_date"] = pd.to_datetime(work["trade_date"]).dt.date.astype(str)
    calendar = market_trading_dates(work)
    position = {day: index for index, day in enumerate(calendar)}
    lookup, failures = _sequence_lookup(work, feature_names)

    sequences: dict[tuple[str, str], np.ndarray] = {}
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in baseline.itertuples(index=False):
        code, decision = str(row.stock_code), str(row.decision_date)
        current_pos = position.get(decision)
        if current_pos is None or current_pos < 2:
            excluded.append({
                "stock_code": code, "decision_date": decision,
                "reason": "MISSING_PRIOR_TRADING_SESSIONS",
            })
            continue
        required_days = calendar[current_pos - 2 : current_pos + 1]
        arrays: list[np.ndarray] = []
        failed: list[str] = []
        for day in required_days:
            key = (code, day)
            value = lookup.get(key)
            if value is None:
                failed.append(f"{day}:{failures.get(key, 'MISSING_STOCK_SESSION')}")
            else:
                arrays.append(value)
        if failed:
            excluded.append({
                "stock_code": code, "decision_date": decision,
                "reason": "MISSING_OR_INVALID_PRIOR_SESSION",
                "detail": "|".join(failed),
            })
            continue
        sequence = np.concatenate(arrays, axis=0).astype(np.float32)
        if sequence.shape != (72, len(feature_names)):
            raise RuntimeError(f"Unexpected Round 2 shape for {(code, decision)}: {sequence.shape}")
        sequences[(code, decision)] = sequence
        included.append({
            "stock_code": code, "decision_date": decision,
            "session_t_minus_2": required_days[0],
            "session_t_minus_1": required_days[1],
            "session_t": required_days[2],
            "baseline_eligible": True, "round2_eligible": True,
        })

    common = pd.DataFrame(included)
    exclusions = pd.DataFrame(excluded)
    per_stock = (
        common.groupby("stock_code").size().astype(int).sort_index().to_dict()
        if not common.empty else {}
    )
    reason_counts = (
        exclusions["reason"].value_counts().astype(int).to_dict()
        if not exclusions.empty else {}
    )
    audit = {
        "baseline_eligible": int(len(baseline)),
        "round2_eligible": int(len(common)),
        "common_intersection": int(len(common)),
        "excluded": int(len(exclusions)),
        "excluded_by_reason": reason_counts,
        "per_stock_common_samples": per_stock,
        "candidate_shape": [int(len(common)), 72, int(len(feature_names))],
        "rf_xgb_flat_shape": [int(len(common)), 72 * int(len(feature_names))],
        "sample_keys_identical": len(sequences) == len(common),
        "forward_fill": False,
        "backfill": False,
        "synthetic_fill": False,
        "cross_stock_join": False,
    }
    return TemporalBuildResult(sequences, common, exclusions, audit)


def assert_no_context_round1(feature_names: list[str]) -> None:
    overlap = CONTEXT_ROUND1_FEATURES.intersection(feature_names)
    if overlap:
        raise ValueError(f"Context Round 1 features are forbidden: {sorted(overlap)}")


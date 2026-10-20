"""Fair common-coverage selection helpers for the Hybrid POC.

The common set is a pure intersection of immutable Technical candidates and
valid prior-session, Training-only OOF predictions from every configured ML
model. No model fitting, parameter selection, or strategy changes happen here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from hybrid.core.hybrid_poc_v1 import HybridPOCError, make_alignment_decision, validate_training_only_dates

TAIPEI = ZoneInfo("Asia/Taipei")


def candidate_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["fold_id"]),
        str(row["stock_code"]),
        str(row["signal_time"]),
        str(row["earliest_entry_time"]),
    )


def _is_oof(row: dict[str, Any]) -> bool:
    return str(row.get("is_oof", "")).lower() == "true"


def build_common_coverage(
    technical_candidates: pd.DataFrame,
    oof_predictions: pd.DataFrame,
    *,
    models: Iterable[str],
    training_calendar: Iterable[str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Return the common candidates, their per-model decisions, and audit."""
    model_names = tuple(map(str, models))
    calendar = sorted({pd.Timestamp(day).date().isoformat() for day in training_calendar})
    if not calendar:
        raise HybridPOCError("Training trading calendar is empty")
    if technical_candidates.empty:
        raise HybridPOCError("Technical candidate source is empty")

    tech = technical_candidates.copy()
    tech["stock_code"] = tech["stock_code"].astype(str)
    if tech.duplicated(["fold_id", "stock_code", "signal_time", "earliest_entry_time"]).any():
        raise HybridPOCError("Duplicate Technical candidate identity")
    validate_training_only_dates(tech["signal_time"])

    work = oof_predictions.copy()
    work["stock_code"] = work["stock_code"].astype(str)
    lookups: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for model in model_names:
        selected = work.loc[work["model"].astype(str) == model]
        lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for item in selected.to_dict("records"):
            key = (str(item["stock_code"]), str(item["entry_date"])[:10])
            if key in lookup:
                raise HybridPOCError(f"Duplicate OOF lookup key: {model}/{key}")
            lookup[key] = item
        lookups[model] = lookup

    included: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    valid_candidate_keys_by_model: dict[str, set[tuple[str, str, str, str]]] = {
        model: set() for model in model_names
    }
    for candidate in tech.to_dict("records"):
        signal_time = pd.Timestamp(candidate["signal_time"]).to_pydatetime()
        entry_time = pd.Timestamp(candidate["earliest_entry_time"]).to_pydatetime()
        candidate_date = signal_time.astimezone(TAIPEI).date().isoformat()
        previous_days = [day for day in calendar if day < candidate_date]
        expected_ml_date = previous_days[-1] if previous_days else None
        candidate_decisions: list[dict[str, Any]] = []
        exclusion_reasons: list[str] = []
        for model in model_names:
            prediction = lookups[model].get((str(candidate["stock_code"]), candidate_date))
            if prediction is None:
                exclusion_reasons.append(f"MISSING_{model.upper()}_OOF")
                continue
            if not _is_oof(prediction) or str(prediction.get("data_role")) != "training":
                exclusion_reasons.append(f"INVALID_{model.upper()}_PROVENANCE")
                continue
            if str(prediction.get("signal_date"))[:10] != expected_ml_date:
                exclusion_reasons.append(f"INVALID_{model.upper()}_PRIOR_SESSION")
                continue
            try:
                row = make_alignment_decision(
                    model=model,
                    stock_code=str(candidate["stock_code"]),
                    technical_fold_id=str(candidate["fold_id"]),
                    technical_signal_time=signal_time,
                    technical_earliest_entry_time=entry_time,
                    technical_reason=str(candidate.get("reason", "")),
                    close_at_signal=float(candidate["close_at_signal"]),
                    prediction=prediction,
                )
            except (HybridPOCError, TypeError, ValueError) as exc:
                exclusion_reasons.append(f"INVALID_{model.upper()}_ALIGNMENT: {exc}")
                continue
            row.update({
                "candidate_id": str(candidate["candidate_id"]),
                "technical_candidate_key": "|".join(candidate_identity(candidate)),
                "expected_previous_trading_day": expected_ml_date or "",
                "technical_earliest_entry_time": entry_time.isoformat(timespec="microseconds"),
            })
            candidate_decisions.append(row)
            valid_candidate_keys_by_model[model].add(candidate_identity(candidate))
        if exclusion_reasons:
            exclusions.append({
                **candidate,
                "technical_candidate_key": "|".join(candidate_identity(candidate)),
                "exclusion_reason": "; ".join(exclusion_reasons),
            })
            continue
        if len({str(row["ml_fold_id"]) for row in candidate_decisions}) != 1:
            exclusions.append({
                **candidate,
                "technical_candidate_key": "|".join(candidate_identity(candidate)),
                "exclusion_reason": "MODEL_FOLD_MISMATCH",
            })
            continue
        if len(candidate_decisions) != len(model_names):
            raise HybridPOCError("Candidate did not receive one decision per ML model")
        identity = "|".join(candidate_identity(candidate))
        candidate_out = {
            **candidate,
            "technical_candidate_key": identity,
            "expected_previous_trading_day": expected_ml_date,
            "common_coverage": True,
        }
        for decision in candidate_decisions:
            model = decision["model"]
            candidate_out[f"{model}_prediction"] = decision["ml_prediction"]
            candidate_out[f"{model}_signal_date"] = decision["ml_signal_date"]
            candidate_out[f"{model}_fold_id"] = decision["ml_fold_id"]
            decisions.append(decision)
        included.append(candidate_out)

    candidate_frame = pd.DataFrame(included)
    decision_frame = pd.DataFrame(decisions)
    if candidate_frame.empty:
        raise HybridPOCError("No Technical candidates have common valid OOF coverage")
    expected_rows = len(candidate_frame) * len(model_names)
    if len(decision_frame) != expected_rows:
        raise HybridPOCError("Common-coverage decision count is incomplete")
    if not decision_frame["alignment_status"].eq("MATCHED").all():
        raise HybridPOCError("Common coverage contains missing ML predictions")
    if not decision_frame["time_order_valid"].astype(bool).all():
        raise HybridPOCError("Common coverage contains invalid time alignment")
    if not decision_frame["is_oof"].astype(bool).all():
        raise HybridPOCError("Common coverage contains non-OOF predictions")
    validate_training_only_dates(candidate_frame["signal_time"])
    validate_training_only_dates(decision_frame["ml_signal_date"])
    audit = {
        "status": "PASS",
        "technical_candidate_count": int(len(tech)),
        "common_candidate_count": int(len(candidate_frame)),
        "excluded_candidate_count": int(len(exclusions)),
        "excluded_reasons": {
            str(key): int(value)
            for key, value in pd.Series(
                [reason.split(":", 1)[0] for row in exclusions for reason in row["exclusion_reason"].split("; ")], dtype="object"
            ).value_counts().items()
        },
        "models": list(model_names),
        "valid_candidate_count_by_model": {
            model: int(len(keys)) for model, keys in valid_candidate_keys_by_model.items()
        },
        "decisions_per_model": {model: int((decision_frame["model"] == model).sum()) for model in model_names},
        "missing": int((decision_frame["alignment_status"] == "MISSING_ML_PREDICTION").sum()),
        "invalid": int((~decision_frame["time_order_valid"].astype(bool)).sum()),
        "non_oof": int((~decision_frame["is_oof"].astype(bool)).sum()),
        "training_only": True,
        "date_min": str(candidate_frame["signal_time"].min())[:10],
        "date_max": str(candidate_frame["signal_time"].max())[:10],
        "previous_actual_trading_session_checked": True,
    }
    return candidate_frame, decision_frame, {**audit, "excluded_candidates": exclusions}


def schedule_by_fold_stock(candidates: pd.DataFrame) -> dict[tuple[str, str], set[str]]:
    schedules: dict[tuple[str, str], set[str]] = {}
    for (fold_id, stock_code), group in candidates.groupby(["fold_id", "stock_code"]):
        schedules[(str(fold_id), str(stock_code))] = {
            pd.Timestamp(value).to_pydatetime().isoformat(timespec="microseconds")
            for value in group["earliest_entry_time"]
        }
    return schedules

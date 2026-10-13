"""Training-only Technical × ML Hybrid POC helpers.

This module deliberately owns no model or strategy search.  It validates legal
OOF predictions, aligns them to the next actual trading session, and gates only
Technical V1 entries.  Technical exits and the existing account simulator stay
in their original implementations.
"""
from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from trading_system.technical_v1 import TechnicalV1Error, TechnicalV1Strategy

TAIPEI = ZoneInfo("Asia/Taipei")
LABELS = ("SELL", "HOLD", "BUY")
GATE_ACTIONS = {
    "BUY": "ENTRY",
    "HOLD": "BLOCK_HOLD",
    "SELL": "BLOCK_SELL",
}
FORBIDDEN_PROVENANCE = ("context_round1", "temporal_round2", "in_sample")


class HybridPOCError(RuntimeError):
    """Raised when the POC would violate provenance, time, or scope rules."""


def validate_technical_signal_value(value: str) -> None:
    if value != "BUY_CANDIDATE":
        raise HybridPOCError("Hybrid POC accepts only Technical BUY_CANDIDATE signals")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def previous_trading_day(calendar: Iterable[str | date], current: str | date) -> str | None:
    days = sorted({pd.Timestamp(item).date() for item in calendar})
    current_day = pd.Timestamp(current).date()
    position = bisect_left(days, current_day)
    return days[position - 1].isoformat() if position > 0 else None


def validate_oof_predictions(
    frame: pd.DataFrame,
    *,
    expected_models: Iterable[str],
    expected_stocks: Iterable[str],
    period_start: str,
    period_end: str,
) -> dict[str, Any]:
    required = {
        "model", "stock_code", "signal_date", "entry_date", "target_date",
        "fold_id", "decision_time", "prediction", "prob_sell", "prob_hold", "prob_buy",
        "is_oof", "data_role", "label_version", "prediction_source",
        "model_parameter_fingerprint", "feature_version",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise HybridPOCError(f"OOF prediction schema missing columns: {missing}")
    work = frame.copy()
    work["stock_code"] = work["stock_code"].astype(str)
    models = set(map(str, expected_models))
    stocks = set(map(str, expected_stocks))
    if set(work["model"].astype(str)) != models:
        raise HybridPOCError("OOF model set does not match the POC model set")
    if not set(work["stock_code"]).issubset(stocks) or set(work["stock_code"]) != stocks:
        raise HybridPOCError("OOF stock universe does not match the Technology universe")
    if not work["is_oof"].map(lambda value: str(value).lower() == "true").all():
        raise HybridPOCError("In-sample or non-OOF prediction detected")
    if set(work["data_role"].astype(str)) != {"training"}:
        raise HybridPOCError("OOF predictions must use Training role only")
    if set(work["label_version"].astype(str)) != {"ml_v1_fixed_1pct"}:
        raise HybridPOCError("Hybrid POC may use only the frozen Fixed ±1% baseline label")
    provenance = " ".join(work["prediction_source"].astype(str).str.lower().unique())
    if any(token in provenance for token in FORBIDDEN_PROVENANCE):
        raise HybridPOCError("Forbidden ML experiment provenance detected")
    if "walk_forward_oof" not in provenance:
        raise HybridPOCError("Prediction source is not identified as walk-forward OOF")
    for column in ("signal_date", "entry_date", "target_date"):
        parsed = pd.to_datetime(work[column], errors="coerce")
        if parsed.isna().any():
            raise HybridPOCError(f"Invalid date in {column}")
        if parsed.min().date() < pd.Timestamp(period_start).date() or parsed.max().date() > pd.Timestamp(period_end).date():
            raise HybridPOCError(f"{column} escaped the 2023-2024 Training boundary")
    if not (pd.to_datetime(work["signal_date"]) < pd.to_datetime(work["entry_date"])).all():
        raise HybridPOCError("OOF entry_date must be after ML signal_date")
    decision_times = pd.to_datetime(work["decision_time"], errors="coerce")
    if decision_times.isna().any() or not (decision_times.dt.strftime("%H:%M:%S") == "13:30:00").all():
        raise HybridPOCError("ML decision_time must be Day T 13:30")
    probabilities = work[["prob_sell", "prob_hold", "prob_buy"]].astype(float)
    if not probabilities.map(math.isfinite).all().all():
        raise HybridPOCError("OOF probabilities contain non-finite values")
    if ((probabilities < 0) | (probabilities > 1)).any().any():
        raise HybridPOCError("OOF probabilities are outside [0, 1]")
    if not ((probabilities.sum(axis=1) - 1.0).abs() < 1e-5).all():
        raise HybridPOCError("OOF class probabilities do not sum to one")
    if not work["prediction"].isin(LABELS).all():
        raise HybridPOCError("Unknown ML class label")
    if work.duplicated(["model", "stock_code", "entry_date"]).any():
        raise HybridPOCError("Duplicate OOF prediction for one model/stock/effective date")
    return {
        "status": "PASS",
        "rows": int(len(work)),
        "models": sorted(models),
        "stocks": sorted(stocks),
        "folds": sorted(work["fold_id"].astype(str).unique().tolist()),
        "date_min": str(work["signal_date"].min()),
        "date_max": str(work["signal_date"].max()),
        "in_sample_predictions_used": False,
        "forbidden_roles_used": False,
        "context_round1_used": False,
        "temporal_round2_used": False,
    }


def build_prediction_lookup(frame: pd.DataFrame, model: str) -> dict[tuple[str, str], dict[str, Any]]:
    selected = frame.loc[frame["model"].astype(str) == model].copy()
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in selected.to_dict("records"):
        key = (str(row["stock_code"]), str(row["entry_date"])[:10])
        if key in lookup:
            raise HybridPOCError(f"Duplicate ML effective-date match: {model}/{key}")
        lookup[key] = row
    return lookup


def make_alignment_decision(
    *,
    model: str,
    stock_code: str,
    technical_fold_id: str,
    technical_signal_time: datetime,
    technical_earliest_entry_time: datetime | None = None,
    technical_reason: str,
    close_at_signal: float,
    prediction: dict[str, Any] | None,
) -> dict[str, Any]:
    validate_technical_signal_value("BUY_CANDIDATE")
    signal_time = technical_signal_time if technical_signal_time.tzinfo else technical_signal_time.replace(tzinfo=TAIPEI)
    candidate_date = signal_time.astimezone(TAIPEI).date().isoformat()
    base = {
        "model": model,
        "ml_model": model,
        "stock_code": str(stock_code),
        "technical_fold_id": str(technical_fold_id),
        "technical_signal": "BUY_CANDIDATE",
        "technical_signal_time": signal_time.isoformat(timespec="microseconds"),
        "technical_candidate_date": candidate_date,
        "technical_signal_date": candidate_date,
        "technical_earliest_entry_time": (
            technical_earliest_entry_time.isoformat(timespec="microseconds")
            if technical_earliest_entry_time is not None else ""
        ),
        "technical_reason": technical_reason,
        "close_at_signal": float(close_at_signal),
    }
    if prediction is None:
        return {
            **base,
            "ml_fold_id": "",
            "ml_signal_date": "",
            "ml_prediction_date": "",
            "ml_decision_time": "",
            "ml_entry_date": "",
            "ml_prediction": "NO_ML_MATCH",
            "ml_predicted_class": "NO_ML_MATCH",
            "prob_sell": None,
            "prob_hold": None,
            "prob_buy": None,
            "is_oof": None,
            "time_order_valid": True,
            "alignment_status": "MISSING_ML_PREDICTION",
            "hybrid_action": "NO_ML_MATCH",
            "hybrid_decision": "NO_ML_MATCH",
        }
    ml_signal_date = str(prediction["signal_date"])[:10]
    ml_entry_date = str(prediction["entry_date"])[:10]
    ml_decision = datetime.combine(pd.Timestamp(ml_signal_date).date(), time(13, 30), tzinfo=TAIPEI)
    if ml_entry_date != candidate_date:
        raise HybridPOCError("ML entry_date does not equal the Technical candidate trading date")
    if ml_decision >= signal_time:
        raise HybridPOCError("Same-day/future ML prediction would create look-ahead leakage")
    if str(prediction.get("is_oof")).lower() != "true":
        raise HybridPOCError("Hybrid gate received a non-OOF prediction")
    label = str(prediction["prediction"])
    return {
        **base,
        "ml_fold_id": str(prediction["fold_id"]),
        "ml_signal_date": ml_signal_date,
        "ml_prediction_date": ml_signal_date,
        "ml_decision_time": ml_decision.isoformat(timespec="seconds"),
        "ml_entry_date": ml_entry_date,
        "ml_prediction": label,
        "ml_predicted_class": label,
        "prob_sell": float(prediction["prob_sell"]),
        "prob_hold": float(prediction["prob_hold"]),
        "prob_buy": float(prediction["prob_buy"]),
        "is_oof": True,
        "time_order_valid": True,
        "alignment_status": "MATCHED",
        "hybrid_action": GATE_ACTIONS[label],
        "hybrid_decision": GATE_ACTIONS[label],
    }


class HybridGateTechnicalV1Strategy(TechnicalV1Strategy):
    """Technical V1 with a prior-session OOF gate applied only at entry."""

    def __init__(
        self,
        *args: Any,
        ml_model: str,
        prediction_lookup: dict[tuple[str, str], dict[str, Any]],
        candidate_schedule: set[str] | None = None,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.ml_model = ml_model
        self.prediction_lookup = prediction_lookup
        self.candidate_schedule = candidate_schedule
        self.hybrid_decisions: list[dict[str, Any]] = []

    def _try_entry(self, code: str, tick_time: datetime, close: float, snapshot: dict[str, float | None]) -> None:
        reasons = self._entry_reasons(close, snapshot)
        if not reasons:
            return
        aware_entry = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TAIPEI)
        signal_time = aware_entry.astimezone(TAIPEI)
        schedule_key = signal_time.isoformat(timespec="microseconds")
        if self.candidate_schedule is not None and schedule_key not in self.candidate_schedule:
            return
        prediction = self.prediction_lookup.get((str(code), signal_time.date().isoformat()))
        decision = make_alignment_decision(
            model=self.ml_model,
            stock_code=code,
            technical_fold_id=self.fold_id,
            technical_signal_time=signal_time,
            technical_earliest_entry_time=signal_time,
            technical_reason=", ".join(reasons),
            close_at_signal=close,
            prediction=prediction,
        )
        self.hybrid_decisions.append(decision)
        if decision["hybrid_action"] == "ENTRY":
            super()._try_entry(code, tick_time, close, snapshot)


def validate_training_only_dates(values: Iterable[Any]) -> None:
    parsed = pd.to_datetime(pd.Series(list(values)), errors="coerce")
    if parsed.isna().any() or (parsed.dt.year > 2024).any() or (parsed.dt.year < 2023).any():
        raise HybridPOCError("Hybrid POC may read only 2023-2024 Training dates")


def html_is_standalone(document: str) -> bool:
    lowered = document.lower()
    forbidden = ("<script src=", "<link rel=", "http://", "https://", "cdn.")
    return "<!doctype html" in lowered and not any(token in lowered for token in forbidden)


def assert_entry_only_change(technical_config: dict[str, Any], poc_config: dict[str, Any]) -> None:
    if not poc_config["technical"].get("entry_only_change"):
        raise HybridPOCError("Hybrid POC must declare entry-only modification")
    unchanged = set(poc_config["technical"].get("unchanged", []))
    required = {"exit_rule", "stop_loss", "max_hold_bars", "cooldown_bars", "capital", "transaction_cost"}
    if not required.issubset(unchanged):
        raise HybridPOCError("Hybrid POC does not lock all required Technical execution rules")
    training = technical_config.get("periods", {}).get("training", {})
    if training.get("start") != "2023-01-01" or training.get("end") != "2024-12-31":
        raise TechnicalV1Error("Technical Training period changed")

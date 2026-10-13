"""Schema validation for ML V1 decision output consumed by Hybrid V1."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "configs/ml_v1_signal_schema.json"


class MlV1SignalError(ValueError):
    pass


def load_signal_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_ml_v1_signal(row: dict[str, Any]) -> None:
    schema = load_signal_schema()
    required = set(schema["required_fields"])
    missing = sorted(required - set(row))
    if missing:
        raise MlV1SignalError("ML V1 signal missing fields: " + ", ".join(missing))
    leaked = sorted(set(schema["forbidden_realtime_fields"]).intersection(row))
    if leaked:
        raise MlV1SignalError("ML V1 signal contains evaluation-only fields: " + ", ".join(leaked))
    if row["prediction"] not in schema["prediction_values"]:
        raise MlV1SignalError("prediction must be BUY, HOLD, or SELL")
    probabilities = [float(row[name]) for name in ("p_buy", "p_hold", "p_sell")]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities):
        raise MlV1SignalError("probabilities must be finite values in [0, 1]")
    if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise MlV1SignalError("p_buy + p_hold + p_sell must equal 1")
    timestamp = datetime.fromisoformat(str(row["decision_time"]))
    if timestamp.utcoffset() != timedelta(hours=8) or timestamp.strftime("%H:%M:%S") != "13:30:00":
        raise MlV1SignalError("decision_time must be 13:30:00+08:00")
    if str(row["decision_date"]) != timestamp.date().isoformat():
        raise MlV1SignalError("decision_date must match decision_time")

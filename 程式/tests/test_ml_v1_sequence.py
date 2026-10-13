from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ml.ml_v1_sequence import (
    EXACT_SEQUENCE_TIMES,
    EXCLUDE_NO_FILL,
    SEQUENCE_COMPLETE,
    evaluate_exact_sequence,
    select_exact_sequence,
)


def frame_for(times: list[str], date: str = "2024-01-10") -> pd.DataFrame:
    return pd.DataFrame({
        "datetime": pd.to_datetime([f"{date} {value}" for value in times]),
        "value": range(len(times)),
    })


def test_a_exact_1135_to_1330_passes():
    selected, result = select_exact_sequence(frame_for(list(EXACT_SEQUENCE_TIMES)))
    assert result.status == SEQUENCE_COMPLETE
    assert selected is not None and len(selected) == 24


def test_b_missing_1200_with_earlier_1130_is_excluded():
    times = ["11:30:00", *[value for value in EXACT_SEQUENCE_TIMES if value != "12:00:00"]]
    selected, result = select_exact_sequence(frame_for(times))
    assert selected is None
    assert result.status == EXCLUDE_NO_FILL
    assert result.missing_times == ("12:00:00",)


def test_c_missing_1330_is_excluded():
    result = evaluate_exact_sequence(frame_for(list(EXACT_SEQUENCE_TIMES[:-1])))
    assert result.status == EXCLUDE_NO_FILL
    assert result.missing_times == ("13:30:00",)


def test_d_missing_1030_does_not_affect_complete_sequence():
    result = evaluate_exact_sequence(frame_for(list(EXACT_SEQUENCE_TIMES)))
    assert result.status == SEQUENCE_COMPLETE


def test_e_sunday_never_becomes_training_sample():
    result = evaluate_exact_sequence(frame_for(list(EXACT_SEQUENCE_TIMES), "2024-12-01"))
    assert result.status == EXCLUDE_NO_FILL


def test_generated_manifest_is_single_shared_complete_sample_set():
    root = Path(__file__).resolve().parents[1]
    output = root / "exports/ml_v1_model_eligible_20261012"
    manifest = pd.read_csv(output / "model_eligible_samples.csv")
    excluded = pd.read_csv(output / "excluded_incomplete_samples.csv")
    report = json.loads((output / "model_eligible_label_audit.json").read_text(encoding="utf-8"))
    assert len(manifest) == report["summary"]["model_eligible_samples"]
    assert len(excluded) == report["summary"]["excluded_incomplete_samples"]
    assert set(manifest["sequence_status"]) == {SEQUENCE_COMPLETE}
    assert set(excluded["sequence_status"]) == {EXCLUDE_NO_FILL}
    assert "2024-12-01" not in set(manifest["decision_date"].astype(str))
    assert manifest[["fixed_label", "volatility_label", "fold_id", "industry"]].notna().all().all()
    assert report["summary"]["shared_by_models"] == ["random_forest", "xgboost", "gru"]

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.data_pipeline import PreparedData

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("compare_ml_signal_anchor", ROOT / "scripts/compare_ml_signal_anchor.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def prepared(label=1, value=.001):
    meta = pd.DataFrame({"stock_code": ["2303"], "signal_date": ["2024-01-01"],
                         "entry_date": ["2024-01-02"], "target_date": ["2024-01-04"],
                         "future_net_return": [value]})
    return PreparedData(X={"train": np.zeros((1, 24, 24), dtype=np.float32)},
        y={"train": np.array([label])}, metadata={"train": meta}, feature_names=[str(i) for i in range(24)],
        scaler_mean=np.zeros(24), scaler_scale=np.ones(24), audit={})


def test_identity_guard_accepts_unchanged_labels_and_rejects_changed_return():
    result = MODULE.assert_identical_labels(prepared(), prepared())
    assert result["labels_identical"] is True
    with pytest.raises(RuntimeError, match="改動了樣本或 Label"):
        MODULE.assert_identical_labels(prepared(), prepared(value=.002))


def test_common_sample_alignment_removes_unmatched_from_both_sides():
    baseline = prepared()
    extra = prepared()
    extra.metadata["train"].loc[0, "signal_date"] = "2024-01-02"
    baseline.X["train"] = np.concatenate([baseline.X["train"], extra.X["train"]])
    baseline.y["train"] = np.concatenate([baseline.y["train"], extra.y["train"]])
    baseline.metadata["train"] = pd.concat([baseline.metadata["train"], extra.metadata["train"]], ignore_index=True)
    left, right, audit = MODULE.align_common_samples(baseline, prepared())
    assert len(left.X["train"]) == len(right.X["train"]) == 1
    assert audit["baseline_only_removed"] == 1


def test_metric_delta_preserves_mean_worst_and_std_direction():
    baseline = {"mean": .4, "standard_deviation": .03, "worst": .35}
    experiment = {"mean": .41, "standard_deviation": .02, "worst": .37}
    delta = MODULE.delta_metric(experiment, baseline)
    assert delta == {"mean": pytest.approx(.01), "standard_deviation": pytest.approx(-.01), "worst": pytest.approx(.02)}


def test_script_is_training_only_and_has_separate_output():
    source = (ROOT / "scripts/compare_ml_signal_anchor.py").read_text(encoding="utf-8")
    assert 'roles=("train",)' in source
    assert '_read_split(DATABASE, plan, "train")' in source
    assert "ml_signal_anchor_compare_20261005" in source
    assert "candidate_overwritten\": False" in source

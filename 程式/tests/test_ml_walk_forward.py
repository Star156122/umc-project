import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.data_pipeline import PreparedData
from ml.models import evaluate_multiclass, fit_multiclass_model
from ml.walk_forward import (aggregate_fold_metrics, build_full_training_data,
    build_walk_forward_fold, load_walk_forward_protocol, select_candidate_model)

ROOT = Path(__file__).resolve().parents[1]


def raw_training():
    dates = ["2023-01-01", "2023-08-31", "2023-09-01", "2023-12-31", "2024-01-01"]
    targets = ["2023-01-02", "2023-09-01", "2023-09-02", "2024-01-01", "2024-01-02"]
    X = np.asarray([[[v], [v + 1]] for v in (1, 100, 10, 200, 300)], dtype=np.float32)
    return PreparedData(X={"train": X}, y={"train": np.array([0, 1, 2, 0, 1])},
        metadata={"train": pd.DataFrame({"stock_code": ["A"] * 5, "signal_date": dates, "target_date": targets})},
        feature_names=["x"], scaler_mean=np.zeros(1), scaler_scale=np.ones(1),
        audit={"loaded_roles": ["train"], "scaler_fit_on": None})


def test_protocol_has_four_ordered_expanding_folds_and_forbidden_roles():
    protocol = load_walk_forward_protocol(ROOT / "configs/ml_training_protocol_v3.json")
    assert len(protocol["walk_forward"]["folds"]) == 4
    assert protocol["walk_forward"]["shuffle"] is False
    assert set(protocol["forbidden_roles"]) == {"holdout", "final_out_of_sample"}


def test_fold_purges_cross_boundary_targets_and_fits_scaler_on_fold_train_only():
    fold = {"fold": 1, "train_start": "2023-01-01", "train_end": "2023-08-31",
            "validation_start": "2023-09-01", "validation_end": "2023-12-31"}
    data = build_walk_forward_fold(raw_training(), fold)
    assert data.audit["train_boundary_targets_removed"] == 1
    assert data.audit["validation_boundary_targets_removed"] == 1
    assert data.audit["scaler_fit_on"] == "fold_train_only"
    assert data.audit["preprocessing_fit_on"] == "fold_train_only"
    np.testing.assert_allclose(data.scaler_mean, [1.5])
    assert data.X["validation"].mean() > 1


def test_full_refit_scaler_uses_all_training_only():
    full = build_full_training_data(raw_training())
    assert full.audit["workflow_stage"] == "candidate_refit"
    assert abs(float(full.X["train"].mean())) < 1e-6


def test_metrics_and_selection_use_worst_mean_stability_not_return():
    y = np.array([0, 1, 2, 0, 1, 2])
    p = np.eye(3)[y] * .8 + .2 / 3
    metric = evaluate_multiclass(y, p)
    assert set(metric["individual_roc_auc_ovr"]) == {"SELL", "HOLD", "BUY"}
    folds = [{**metric, "fold": i} for i in range(1, 5)]
    stable = aggregate_fold_metrics(folds)
    unstable = json.loads(json.dumps(stable))
    unstable["macro_f1"].update({"worst": .2, "mean": .99, "standard_deviation": .4})
    protocol = load_walk_forward_protocol(ROOT / "configs/ml_training_protocol_v3.json")
    selected, decision = select_candidate_model({"random_forest": stable, "xgboost": unstable}, protocol)
    assert selected == "random_forest"
    assert decision["return_used_for_selection"] is False


def test_gru_cannot_early_stop_on_external_validation():
    torch = pytest.importorskip("torch")
    data = raw_training()
    data.X["validation"] = data.X["train"][:2]
    data.y["validation"] = data.y["train"][:2]
    data.metadata["validation"] = data.metadata["train"].iloc[:2]
    data.audit["workflow_stage"] = "controlled_validation"
    params = {"hidden_size": 2, "num_layers": 1, "dropout": 0.0, "batch_size": 2,
              "max_epochs": 1, "patience": 1, "learning_rate": .001, "random_state": 42}
    with pytest.raises(ValueError, match="Fold Validation"):
        fit_multiclass_model("gru", data, params)

import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.data_pipeline import PreparedData
from ml.models import build_gru_multiclass_model, tabular_multiclass_input
from ml.research_features import build_daily_stock_features
from ml.research_universe import (enabled_stock_union, enforce_industry_reliability,
    leave_one_stock_out_industry_return)
from ml.walk_forward import build_walk_forward_fold

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_ml_v4_research", ROOT / "scripts/run_ml_v4_research.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def intraday(values):
    dates = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return pd.DataFrame({"stock_code": ["A"] * len(values), "trade_date": dates.date,
        "open": values, "high": values, "low": values, "close": values,
        "volume": np.arange(len(values)) + 100, "kbar_timestamp": np.arange(len(values)), "datetime": dates})


def raw_prepared(context=True):
    dates = pd.date_range("2023-01-01", periods=8, freq="D")
    metadata = pd.DataFrame({"stock_code": ["2303"] * 8, "signal_date": dates,
        "target_date": dates, "entry_date": dates, "future_net_return": np.arange(8) / 100})
    return PreparedData(X={"train": np.arange(8 * 2 * 2, dtype=np.float32).reshape(8, 2, 2)},
        y={"train": np.arange(8) % 3}, metadata={"train": metadata}, feature_names=["a", "b"],
        scaler_mean=np.zeros(2), scaler_scale=np.ones(2),
        audit={"loaded_roles": ["train"], "scaler_fit_on": None},
        context_X={"train": np.arange(16, dtype=np.float32).reshape(8, 2)} if context else None,
        context_feature_names=["c", "d"] if context else [])


def test_daily_features_are_causal_to_future_close_changes():
    values = np.linspace(100, 129, 30)
    first = build_daily_stock_features(intraday(values))
    changed = values.copy(); changed[-1] = 9999
    second = build_daily_stock_features(intraday(changed))
    columns = ["stock_return_1d", "stock_return_3d", "stock_return_5d", "volatility_20d", "distance_from_high_20d"]
    np.testing.assert_allclose(first.loc[20, columns], second.loc[20, columns])


def test_leave_one_stock_out_excludes_target_stock():
    frame = pd.DataFrame({"stock_code": ["A", "B"], "trade_date": ["2024-01-01"] * 2,
        "stock_return_1d": [.1, .2]})
    result = leave_one_stock_out_industry_return(frame, {"industry_groups": {"technology": ["A", "B"]}}, 1)
    np.testing.assert_allclose(result, [.2, .1])


def test_static_context_is_separate_and_not_repeated_across_timesteps():
    data = raw_prepared()
    assert data.X["train"].shape == (8, 2, 2)
    assert data.context_X["train"].shape == (8, 2)
    assert data.context_X["train"].ndim == 2


def test_rf_xgb_tabular_context_is_appended_once():
    sequence = np.zeros((5, 24, 24), dtype=np.float32)
    context = np.zeros((5, 12), dtype=np.float32)
    assert tabular_multiclass_input(sequence, context).shape == (5, 24 * 24 + 12)


def test_gru_context_is_concatenated_after_encoder():
    torch = pytest.importorskip("torch")
    params = {"hidden_size": 16, "num_layers": 1, "dropout": 0.0}
    model = build_gru_multiclass_model(24, params, context_size=12)
    assert model.gru.input_size == 24
    assert model.head.in_features == 28
    assert model(torch.zeros(3, 24, 24), torch.zeros(3, 12)).shape == (3, 3)


def test_fold_scalers_fit_only_fold_training_for_sequence_and_context():
    data = raw_prepared()
    spec = {"fold": 1, "train_start": "2023-01-01", "train_end": "2023-01-04",
        "validation_start": "2023-01-05", "validation_end": "2023-01-08"}
    fold = build_walk_forward_fold(data, spec)
    np.testing.assert_allclose(fold.X["train"].reshape(-1, 2).mean(axis=0), [0, 0], atol=1e-6)
    np.testing.assert_allclose(fold.context_X["train"].mean(axis=0), [0, 0], atol=1e-6)
    assert fold.audit["scaler_fit_on"] == "fold_train_only"
    assert fold.audit["context_scaler_fit_on"] == "fold_train_only"


def _metric(value):
    return {"macro_f1": {"mean": value, "standard_deviation": .1, "worst": value - .1},
        "macro_auc": {"mean": value, "standard_deviation": .1, "worst": value - .1},
        "individual_auc": {label: {"mean": value} for label in MODULE.LABELS},
        "per_stock": {"2303": {"macro_f1": {"mean": value}, "macro_auc": {"mean": value}}}}


def test_multiple_comparison_groups_are_compared_independently():
    results = {name: {"random_forest": _metric(value)} for name, value in
        [("A", .4), ("B", .5), ("C", .3), ("D", .35)]}
    config = {"comparison_groups": {"one": {"baseline_experiment": "A", "experimental_experiment": "B"},
        "two": {"baseline_experiment": "C", "experimental_experiment": "D"}}}
    compared = MODULE.build_group_comparisons(results, config)
    assert compared["one"]["status"] == compared["two"]["status"] == "completed"
    assert compared["one"]["comparison"]["random_forest"]["macro_f1"]["mean"] == pytest.approx(.1)


def test_config_driven_union_and_training_only_loader(monkeypatch):
    config = {"stock_groups": {"base": ["2303"], "new": ["9999"]},
        "experiments": [{"enabled": True, "stock_group": "base"}, {"enabled": True, "stock_group": "new"}]}
    assert enabled_stock_union(config) == ["2303", "9999"]
    seen = {}
    class Coverage:
        rows = [{"available": True}]
    def fake_coverage(database, plan, roles):
        seen["coverage_codes"], seen["roles"] = plan["stock_codes"], roles
        return Coverage()
    def fake_read(database, plan, role):
        seen["read_codes"], seen["read_role"] = plan["stock_codes"], role
        return pd.DataFrame({"x": [1]})
    monkeypatch.setattr(MODULE, "coverage_report", fake_coverage)
    monkeypatch.setattr(MODULE, "_read_split", fake_read)
    MODULE.load_enabled_training_data(Path("dummy"), {"stock_codes": ["2303"]}, config)
    assert seen == {"coverage_codes": ["2303", "9999"], "roles": ("train",),
        "read_codes": ["2303", "9999"], "read_role": "train"}


def test_formal_industry_experiment_with_insufficient_members_is_blocked():
    config = {"stock_groups": {"technology": ["2303", "2330"]},
        "industry_groups": {"technology": ["2303", "2330"]}}
    experiment = {"formal": True, "stock_group": "technology"}
    resolved = {"industry_features": ["industry_return_1d"], "minimum_reliable_industry_members": 3}
    with pytest.raises(ValueError, match="成員不足"):
        enforce_industry_reliability(config, experiment, resolved)


def test_config_only_enables_a_b_and_forbidden_roles_are_never_loaded():
    config = json.loads((ROOT / "configs/ml_v4_research_matrix.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in config["experiments"] if item["enabled"]] == ["A_all_stocks", "B_all_stocks"]
    source = (ROOT / "scripts/run_ml_v4_research.py").read_text(encoding="utf-8")
    assert '_read_split(database, local_plan, "train")' in source
    assert 'roles=("train",)' in source
    assert "controlled_validation_used\": False" in source
    assert "development_used\": False" in source
    assert "forbidden_roles_used\": False" in source
    assert "ml_v4_research_20261005_fix1" in source


def test_context_feature_count_and_universe_proxy_names():
    config = MODULE.load_matrix(ROOT / "configs/ml_v4_research_matrix.json")
    assert len(MODULE.resolve_feature_set(config, "A")["stock_features"]) == 0
    assert len(MODULE.resolve_feature_set(config, "B")["stock_features"]) == 12
    assert all("vs_market" not in name for name in MODULE.resolve_feature_set(config, "C")["industry_features"])

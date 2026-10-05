import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from ml.data_pipeline import PreparedData

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_ml_v4a_label_compare", ROOT / "scripts/run_ml_v4a_label_compare.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def common_data():
    meta = pd.DataFrame({"stock_code": ["A"] * 3, "signal_date": ["2024-02-01"] * 3,
        "entry_date": ["2024-02-02"] * 3, "target_date": ["2024-02-05"] * 3,
        "future_net_return": [-.02, 0, .02], "sigma_3d": [.02, .02, .02]})
    return PreparedData(X={"train": np.zeros((3, 24, 24), dtype=np.float32)}, y={"train": np.ones(3, dtype=int)},
        metadata={"train": meta}, feature_names=[str(i) for i in range(24)], scaler_mean=np.zeros(24),
        scaler_scale=np.ones(24), audit={"loaded_roles": ["train"], "scaler_fit_on": None})


def test_dynamic_label_uses_only_fixed_k_times_sigma_3d():
    data = MODULE.make_label_version(common_data(), "v4a_k_0_50", .5)
    np.testing.assert_allclose(data.metadata["train"]["label_threshold"], .01)
    assert data.y["train"].tolist() == [0, 1, 2]


def test_fixed_label_remains_one_percent():
    data = MODULE.make_label_version(common_data(), "v3_fixed_1pct", None)
    np.testing.assert_allclose(data.metadata["train"]["label_threshold"], .01)
    assert data.audit["threshold_formula"] == "fixed 0.01"


def test_predeclared_versions_and_model_settings_are_fixed():
    assert MODULE.LABEL_VERSIONS == {"v3_fixed_1pct": None, "v4a_k_0_50": .5, "v4a_k_0_75": .75, "v4a_k_1_00": 1.0}
    assert MODULE.MODEL_SETTINGS["random_forest"]["config_name"] == "flexible"
    assert MODULE.MODEL_SETTINGS["xgboost"]["config_name"] == "baseline"
    assert MODULE.MODEL_SETTINGS["gru"]["config_name"] == "smaller"


def test_rolling_volatility_does_not_change_when_a_future_close_changes():
    dates = pd.date_range("2024-01-01", periods=25, freq="D")
    close = np.linspace(100, 124, 25)
    def frame(values):
        return pd.DataFrame({"stock_code": ["A"] * 25, "trade_date": dates.date,
            "open": values, "high": values, "low": values, "close": values,
            "volume": np.ones(25), "kbar_timestamp": np.arange(25)})
    metadata = pd.DataFrame({"stock_code": ["A"], "signal_date": [str(dates[20].date())]})
    first, _ = MODULE.attach_past_volatility(metadata, frame(close))
    changed = close.copy(); changed[-1] = 9999
    second, _ = MODULE.attach_past_volatility(metadata, frame(changed))
    np.testing.assert_allclose(first["sigma_20d"], second["sigma_20d"])


def test_script_is_training_only_and_keeps_anchor():
    source = (ROOT / "scripts/run_ml_v4a_label_compare.py").read_text(encoding="utf-8")
    assert 'roles=("train",)' in source
    assert '_read_split(DATABASE, plan, "train")' in source
    assert '"signal_anchor_changed": False' in source
    assert '"controlled_validation_started": False' in source
    assert "ml_v4a_label_compare_20261005" in source

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audit_ml_features", ROOT / "scripts/audit_ml_features.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_feature_stats_detect_all_zero_and_nan():
    result = MODULE.quality_flags(np.array([0.0, 0.0, np.nan]))
    assert result["all_zero"] is True
    assert result["constant_feature"] is True
    assert result["nan_ratio"] == 1 / 3


def test_raw_ohlcv_audit_detects_flat_and_invalid_rows():
    frame = pd.DataFrame({"stock_code": ["A", "A"], "open": [10, 0], "high": [10, 2],
                          "low": [10, 1], "close": [10, 0], "volume": [1, -1]})
    result = MODULE.raw_ohlcv_audit(frame)["A"]
    assert result["high_equals_low_count"] == 1
    assert result["close_zero_count"] == 1
    assert result["invalid_ohlcv_rows"] == 1


def test_flat_bar_time_audit_identifies_closing_auction_bar():
    frame = pd.DataFrame({"stock_code": ["A", "A"], "high": [10, 11], "low": [10, 10],
                          "datetime": pd.to_datetime(["2024-01-02 13:30", "2024-01-02 13:25"])})
    result = MODULE.flat_bar_time_audit(frame)
    assert result["13_30_flat_bars"] == 1
    assert result["13_30_flat_bars_by_stock"] == {"A": 1}


def test_script_is_training_only_and_uses_expected_sequence_layout():
    source = (ROOT / "scripts/audit_ml_features.py").read_text(encoding="utf-8")
    assert 'roles=("train",)' in source
    assert '_read_split(DATABASE, plan, "train")' in source
    assert "EXPECTED_SEQUENCE_BARS = 24" in source
    assert "EXPECTED_FEATURES = 24" in source
    assert "ml_feature_audit_20261005" in source

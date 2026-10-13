from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.run_ml_v1_phase_b import indices_for_industry_fold
from scripts.run_ml_industry_generalization import indices_for_outer_fold

ROOT = Path(__file__).resolve().parents[1]


def test_phase_b_is_confirmatory_training_only():
    config = json.loads((ROOT / "configs/ml_v1_phase_b.json").read_text(encoding="utf-8"))
    assert config["status"] == "AUTHORIZED"
    assert config["data_role"] == "train"
    assert config["training_period"] == {"start": "2023-01-01", "end": "2024-12-31"}
    assert config["labels"] == ["fixed_1pct", "volatility_k_0_50"]
    assert config["models"] == ["random_forest", "xgboost", "gru"]
    assert config["expected_sequence_eligible_samples"] == 4542
    assert config["feature_finite_policy"] == "EXCLUDE_NONFINITE_FEATURE_NO_FILL"
    assert all(config["forbidden"].values())


def test_industry_fold_keeps_target_inside_partition():
    meta = pd.DataFrame({
        "stock_code": ["2303", "2303", "2330", "2881"],
        "signal_date": ["2023-08-29", "2023-08-31", "2023-09-01", "2023-08-29"],
        "target_date": ["2023-08-31", "2023-09-05", "2023-09-05", "2023-08-31"],
    })
    fold = {"fold": 1, "train_start": "2023-01-01", "train_end": "2023-08-31",
            "validation_start": "2023-09-01", "validation_end": "2023-12-31"}
    train, validation = indices_for_industry_fold(meta, ["2303", "2330"], fold)
    assert train.tolist() == [0]
    assert validation.tolist() == [2]


def test_loso_never_puts_heldout_stock_in_training():
    meta = pd.DataFrame({
        "stock_code": ["2303", "2330", "2379", "2303"],
        "signal_date": ["2023-08-01", "2023-08-01", "2023-08-01", "2023-09-01"],
        "target_date": ["2023-08-04", "2023-08-04", "2023-08-04", "2023-09-06"],
    })
    fold = {"fold": 1, "train_start": "2023-01-01", "train_end": "2023-08-31",
            "validation_start": "2023-09-01", "validation_end": "2023-12-31"}
    train, test = indices_for_outer_fold(meta, ["2330", "2379"], "2303", fold)
    assert set(meta.iloc[train]["stock_code"]) == {"2330", "2379"}
    assert set(meta.iloc[test]["stock_code"]) == {"2303"}


def test_phase_b_source_has_no_trading_selection_or_locked_period_literals():
    source = (ROOT / "scripts/run_ml_v1_phase_b.py").read_text(encoding="utf-8")
    assert "Total Return" not in source
    assert "Sharpe" not in source
    assert "Profit Factor" not in source
    assert "2025-" not in source
    assert "2026-01" not in source
    assert '"trading_metrics_used_for_selection": False' in source

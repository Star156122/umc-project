from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_phase_a_config_is_training_only_and_preregistered():
    config = json.loads((ROOT / "configs/ml_v1_research.json").read_text(encoding="utf-8"))
    assert config["data"]["role"] == "train"
    assert (config["data"]["start"], config["data"]["end"]) == ("2023-01-01", "2024-12-31")
    assert config["target"]["candidate"]["k"] == 0.5
    assert config["phase_b"]["k_search"] is False
    assert config["phase_b"]["probability_threshold_search"] is False
    assert config["prediction"]["decision_time"] == "13:30:00"
    assert config["prediction"]["sequence_policy"] == "EXACT_24_BARS_11_35_TO_13_30"
    assert config["prediction"]["missing_sequence_policy"] == "EXCLUDE_NO_FILL"
    assert config["prediction"]["sequence_fill_allowed"] is False
    assert config["historical_experiment"]["classification"] == "Historical Exploratory Experiment"
    assert config["historical_experiment"]["must_not_delete_or_overwrite"] is True


def test_phase_a_script_does_not_train_or_query_locked_periods():
    source = (ROOT / "scripts/audit_ml_v1_labels.py").read_text(encoding="utf-8")
    assert "fit(" not in source
    assert "2025-" not in source
    assert "2026-" not in source
    assert 'phase_b_started": False' in source
    assert "assert_ml_read_period" in source

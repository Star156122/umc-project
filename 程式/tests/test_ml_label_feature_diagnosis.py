import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("diagnose_ml_label_features", ROOT / "scripts/diagnose_ml_label_features.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_label_distribution_uses_fixed_symmetric_threshold_and_flags_imbalance():
    result = MODULE.label_distribution(np.array([-.02, 0, .02]), .01)
    assert [result["classes"][x]["count"] for x in ("SELL", "HOLD", "BUY")] == [1, 1, 1]
    imbalanced = MODULE.label_distribution(np.zeros(10), .01)
    assert "HOLD > 70%" in imbalanced["flags"]
    assert "SELL < 10%" in imbalanced["flags"]
    assert "BUY < 10%" in imbalanced["flags"]


def test_return_stats_contains_required_quantiles():
    result = MODULE.return_stats(np.arange(1, 21, dtype=float))
    assert set(result["quantiles"]) == {"5%", "25%", "50%", "75%", "95%"}
    assert result["median"] == result["quantiles"]["50%"]


def test_script_declares_training_only_loader_and_separate_output():
    source = (ROOT / "scripts/diagnose_ml_label_features.py").read_text(encoding="utf-8")
    assert 'roles=("train",)' in source
    assert "ml_label_feature_diagnosis_20261005" in source
    assert "controlled_validation_used\": False" in source
    assert "candidate_overwritten\": False" in source

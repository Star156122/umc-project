from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.ml_v1_sequence import EXACT_SEQUENCE_TIMES
from ml.temporal_round2 import (
    CONTEXT_ROUND1_FEATURES,
    assert_no_context_round1,
    build_three_session_intersection,
    load_config,
)

ROOT = Path(__file__).resolve().parents[1]


def fixture_featured(dates: list[str], stocks: list[str] | None = None, missing: tuple[str, str, str] | None = None) -> pd.DataFrame:
    rows = []
    for stock_index, code in enumerate(stocks or ["2303"]):
        for day_index, day in enumerate(dates):
            for time_index, clock in enumerate(EXACT_SEQUENCE_TIMES):
                if missing == (code, day, clock):
                    continue
                rows.append({
                    "stock_code": code,
                    "trade_date": day,
                    "datetime": pd.Timestamp(f"{day} {clock}"),
                    "f1": float(stock_index * 1000 + day_index * 100 + time_index),
                    "f2": float(time_index + 1),
                })
    return pd.DataFrame(rows)


def test_config_freezes_single_three_session_candidate_and_guards():
    config = load_config()
    assert config["representation_review"]["status"] == "REVIEW_REQUIRED"
    assert config["candidate"]["design_status"] == "PROPOSED_OPTION_A_REQUIRES_USER_DECISION"
    assert config["candidate"]["historical_sessions"] == 3
    assert config["candidate"]["timesteps"] == 72
    assert config["fixed_contract"]["decision_time"] == "13:30:00"
    assert config["fixed_contract"]["prediction_horizon_sessions"] == 3
    assert config["fixed_contract"]["context_round1_features_used"] is False
    assert all(config["forbidden"].values())


def test_full_session_definition_is_55_stored_timestamps():
    config = load_config()
    session = config["session"]
    assert session["expected_raw_bars"] == 55
    assert len(session["raw_times"]) == 55
    assert session["raw_times"][0] == "09:00:00"
    assert session["raw_times"][-1] == "13:30:00"


def test_trading_day_shift_skips_weekend_and_keeps_chronology():
    dates = ["2024-01-05", "2024-01-08", "2024-01-09"]
    baseline = pd.DataFrame({"stock_code": ["2303"], "decision_date": ["2024-01-09"]})
    result = build_three_session_intersection(baseline, fixture_featured(dates), ["f1", "f2"])
    row = result.common_samples.iloc[0]
    assert [row["session_t_minus_2"], row["session_t_minus_1"], row["session_t"]] == dates
    assert result.sequences[("2303", "2024-01-09")].shape == (72, 2)


def test_candidate_never_uses_a_session_after_decision_date():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    baseline = pd.DataFrame({"stock_code": ["2303"], "decision_date": ["2024-01-04"]})
    result = build_three_session_intersection(baseline, fixture_featured(dates), ["f1", "f2"])
    row = result.common_samples.iloc[0]
    assert max(row["session_t_minus_2"], row["session_t_minus_1"], row["session_t"]) == row["decision_date"]


def test_holiday_gap_is_not_synthesized():
    dates = ["2024-02-07", "2024-02-15", "2024-02-16"]
    baseline = pd.DataFrame({"stock_code": ["2303"], "decision_date": ["2024-02-16"]})
    result = build_three_session_intersection(baseline, fixture_featured(dates), ["f1", "f2"])
    assert result.common_samples.iloc[0]["session_t_minus_1"] == "2024-02-15"


def test_missing_session_excludes_without_fill():
    calendar_dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    frame = fixture_featured(calendar_dates, stocks=["2303", "2330"])
    frame = frame.loc[~((frame["stock_code"] == "2303") & (frame["trade_date"] == "2024-01-03"))]
    baseline = pd.DataFrame({"stock_code": ["2303"], "decision_date": ["2024-01-04"]})
    result = build_three_session_intersection(baseline, frame, ["f1", "f2"])
    assert result.common_samples.empty
    assert "MISSING_STOCK_SESSION" in result.exclusions.iloc[0]["detail"]


def test_missing_one_bar_excludes_even_if_other_bars_exist():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    frame = fixture_featured(dates, missing=("2303", "2024-01-03", "12:00:00"))
    baseline = pd.DataFrame({"stock_code": ["2303"], "decision_date": ["2024-01-04"]})
    result = build_three_session_intersection(baseline, frame, ["f1", "f2"])
    assert result.common_samples.empty
    assert "MISSING_EXACT_24_BAR_SEQUENCE" in result.exclusions.iloc[0]["detail"]


def test_nonfinite_bar_excludes_without_forward_or_backfill():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    frame = fixture_featured(dates)
    frame.loc[(frame["trade_date"] == "2024-01-03") & (frame["datetime"].dt.strftime("%H:%M:%S") == "12:00:00"), "f1"] = np.nan
    baseline = pd.DataFrame({"stock_code": ["2303"], "decision_date": ["2024-01-04"]})
    result = build_three_session_intersection(baseline, frame, ["f1", "f2"])
    assert result.common_samples.empty
    assert result.audit["forward_fill"] is False
    assert result.audit["backfill"] is False
    assert result.audit["synthetic_fill"] is False


def test_decision_time_and_label_contract_are_unchanged():
    config = load_config()
    fixed = config["fixed_contract"]
    assert fixed["decision_time"] == "13:30:00"
    assert fixed["prediction_horizon_sessions"] == 3
    assert fixed["target"] == "Day T+1 Open to Day T+3 Close"
    assert fixed["label_thresholds"] == {"SELL": -0.01, "BUY": 0.01}


def test_stocks_never_cross_contaminate():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    baseline = pd.DataFrame({"stock_code": ["2303", "2330"], "decision_date": ["2024-01-04"] * 2})
    result = build_three_session_intersection(baseline, fixture_featured(dates, ["2303", "2330"]), ["f1", "f2"])
    first = result.sequences[("2303", "2024-01-04")]
    second = result.sequences[("2330", "2024-01-04")]
    assert not np.array_equal(first, second)
    assert result.audit["cross_stock_join"] is False


def test_common_intersection_is_exact_subset_of_baseline_keys():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    baseline = pd.DataFrame({"stock_code": ["2303", "2303"], "decision_date": ["2024-01-02", "2024-01-05"]})
    result = build_three_session_intersection(baseline, fixture_featured(dates), ["f1", "f2"])
    assert list(result.common_samples["decision_date"]) == ["2024-01-05"]
    assert result.audit["baseline_eligible"] == 2
    assert result.audit["common_intersection"] == 1
    assert result.audit["sample_keys_identical"] is True


def test_context_round1_features_are_rejected():
    with pytest.raises(ValueError, match="Context Round 1"):
        assert_no_context_round1([next(iter(CONTEXT_ROUND1_FEATURES))])


def test_formal_core_features_do_not_contain_context_round1_features():
    research = json.loads((ROOT / "configs/ml_v1_research.json").read_text(encoding="utf-8"))
    assert CONTEXT_ROUND1_FEATURES.isdisjoint(research["feature_policy"]["core_features"])


def test_runner_is_preflight_only_and_has_no_training_entrypoint():
    source = (ROOT / "scripts/run_ml_v1_temporal_round2.py").read_text(encoding="utf-8")
    assert "run_multiclass_model" not in source
    assert "RandomForestClassifier" not in source
    assert "XGBClassifier" not in source
    assert "--train" not in source
    assert "--preflight" in source
    assert "--verify-features" in source


def test_formal_baseline_contract_and_fold4_inner_date_remain_fixed():
    research = json.loads((ROOT / "configs/ml_v1_research.json").read_text(encoding="utf-8"))
    round2 = load_config()
    assert research["prediction"]["decision_time"] == round2["fixed_contract"]["decision_time"]
    assert research["prediction"]["prediction_horizon_sessions"] == 3
    assert round2["validation"]["expected_fold_4_inner_validation_start"] == "2024-05-09"
    assert research["models"]["random_forest"] == {
        "n_estimators": 200, "max_depth": 10, "min_samples_leaf": 5,
        "class_weight": "balanced", "random_state": 42,
    }
    assert {
        industry: research["stock_universe"][industry]
        for industry in ("technology", "financial")
    } == round2["stock_universe"]
    assert round2["validation"]["gru_inner_validation_start_by_fold"]["4"] == "2024-05-09"


def test_all_three_model_hyperparameters_match_formal_baseline():
    research = json.loads((ROOT / "configs/ml_v1_research.json").read_text(encoding="utf-8"))
    round2 = load_config()
    assert round2["fixed_contract"]["hyperparameters_unchanged"] is True
    assert set(research["models"]) == {"random_forest", "xgboost", "gru"}
    assert research["models"]["xgboost"]["random_state"] == 42
    assert research["models"]["gru"]["hidden_size"] == 16


def test_walk_forward_protocol_is_chronological_and_not_shuffled():
    protocol = json.loads((ROOT / "configs/ml_training_protocol_v3.json").read_text(encoding="utf-8"))
    walk = protocol["walk_forward"]
    assert walk["shuffle"] is False
    assert len(walk["folds"]) == 4
    for fold in walk["folds"]:
        assert pd.Timestamp(fold["train_end"]) < pd.Timestamp(fold["validation_start"])


def test_loso_universe_remains_five_stocks_per_industry():
    config = load_config()
    assert len(config["stock_universe"]["technology"]) == 5
    assert len(config["stock_universe"]["financial"]) == 5
    phase = json.loads((ROOT / "configs/ml_v1_phase_b.json").read_text(encoding="utf-8"))
    assert phase["loso"] == {"enabled": True, "train_stocks": 4, "heldout_stocks": 1}


def test_output_directory_is_new_and_cannot_overwrite_baseline_exports():
    config = load_config()
    output = config["output"]["directory"]
    assert output.startswith("exports/ml_v1_temporal_round2_")
    assert "phase_b" not in output
    assert config["forbidden"]["baseline_export_overwrite"] is True


def test_runner_uses_read_only_database_hash_guard():
    source = (ROOT / "scripts/run_ml_v1_temporal_round2.py").read_text(encoding="utf-8")
    assert "sha256(database)" in source
    assert "market_data.sqlite3 changed" in source
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source


def test_config_never_names_2025_or_2026_as_readable_data():
    config = load_config()
    assert config["data"]["start"] == "2023-01-01"
    assert config["data"]["end"] == "2024-12-31"
    assert config["forbidden"]["validation_2025_h1"] is True
    assert config["forbidden"]["additional_holdout_2025_h2"] is True
    assert config["forbidden"]["development_2026_h1"] is True
    assert config["forbidden"]["final_oos_2026_h2_onward"] is True


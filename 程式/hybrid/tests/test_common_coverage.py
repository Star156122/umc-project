from __future__ import annotations

import inspect
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.common_coverage import build_common_coverage, schedule_by_fold_stock
from hybrid.core.hybrid_poc_v1 import HybridPOCError, validate_training_only_dates
from hybrid.scripts.run_hybrid_common_coverage import TechnicalCommonCoverageStrategy
from trading_system.technical_v1 import TechnicalV1Strategy

TAIPEI = ZoneInfo("Asia/Taipei")
MODELS = ("random_forest", "xgboost", "gru")


def tech(signal_date: str, *, stock: str = "2303", fold: str = "WF1") -> dict:
    return {
        "technical_version": "technical-v1-test", "strategy_family": "breakout",
        "candidate_id": "BR_SENS_RANGE55_BB20_2", "parameter_fingerprint": "fixed",
        "stock_code": stock, "data_role": "training", "fold_id": fold,
        "timezone": "Asia/Taipei",
        "signal_time": f"{signal_date}T10:04:59.999999+08:00",
        "signal_bar_end_time": f"{signal_date}T10:04:59.999999+08:00",
        "earliest_entry_time": f"{signal_date}T10:05:00.000000+08:00",
        "technical_signal": "BUY_CANDIDATE", "entry_rule_id": "fixed-entry",
        "exit_rule_id": "fixed-exit", "close_at_signal": 100.0, "reason": "fixture",
    }


def oof(model: str, signal_date: str, entry_date: str, *, stock: str = "2303", prediction: str = "BUY") -> dict:
    probabilities = {"BUY": (0.1, 0.2, 0.7), "HOLD": (0.1, 0.8, 0.1), "SELL": (0.8, 0.1, 0.1)}[prediction]
    return {
        "model": model, "stock_code": stock, "signal_date": signal_date,
        "decision_time": f"{signal_date}T13:30:00+08:00", "entry_date": entry_date,
        "target_date": "2023-07-12", "fold_id": "ML_WF1", "prediction": prediction,
        "prob_sell": probabilities[0], "prob_hold": probabilities[1], "prob_buy": probabilities[2],
        "is_oof": True, "data_role": "training", "label_version": "ml_v1_fixed_1pct",
        "prediction_source": "ml_v1_baseline_training_walk_forward_oof",
    }


class CommonCoverageTests(unittest.TestCase):
    def fixture(self):
        candidates = pd.DataFrame([tech("2023-07-10"), tech("2023-07-11")])
        predictions = []
        for model in MODELS:
            predictions.extend([
                oof(model, "2023-07-07", "2023-07-10", prediction="BUY"),
                oof(model, "2023-07-10", "2023-07-11", prediction="HOLD"),
            ])
        return candidates, pd.DataFrame(predictions)

    def test_common_coverage_is_intersection_of_technical_and_all_three_oof_models(self):
        candidates, predictions = self.fixture()
        common, decisions, audit = build_common_coverage(
            candidates, predictions, models=MODELS,
            training_calendar=["2023-07-07", "2023-07-10", "2023-07-11"],
        )
        self.assertEqual(len(common), 2)
        self.assertEqual(set(decisions["model"]), set(MODELS))
        self.assertEqual(audit["common_candidate_count"], 2)

    def test_every_common_candidate_has_valid_oof_from_every_model(self):
        candidates, predictions = self.fixture()
        common, decisions, _ = build_common_coverage(
            candidates, predictions, models=MODELS,
            training_calendar=["2023-07-07", "2023-07-10", "2023-07-11"],
        )
        self.assertEqual(len(decisions), len(common) * 3)
        self.assertTrue(decisions["is_oof"].all())
        self.assertTrue(decisions["alignment_status"].eq("MATCHED").all())

    def test_missing_model_prediction_excludes_candidate_from_intersection(self):
        candidates, predictions = self.fixture()
        predictions = predictions.loc[~((predictions["model"] == "gru") & (predictions["entry_date"] == "2023-07-11"))]
        common, decisions, audit = build_common_coverage(
            candidates, predictions, models=MODELS,
            training_calendar=["2023-07-07", "2023-07-10", "2023-07-11"],
        )
        self.assertEqual(len(common), 1)
        self.assertEqual(audit["missing"], 0)
        self.assertEqual(audit["excluded_candidate_count"], 1)
        self.assertEqual(len(decisions), 3)

    def test_invalid_alignment_is_excluded_and_common_invalid_count_is_zero(self):
        candidates, predictions = self.fixture()
        predictions.loc[(predictions["model"] == "xgboost") & (predictions["entry_date"] == "2023-07-11"), "signal_date"] = "2023-07-11"
        predictions.loc[(predictions["model"] == "xgboost") & (predictions["entry_date"] == "2023-07-11"), "decision_time"] = "2023-07-11T13:30:00+08:00"
        common, decisions, audit = build_common_coverage(
            candidates, predictions, models=MODELS,
            training_calendar=["2023-07-07", "2023-07-10", "2023-07-11"],
        )
        self.assertEqual(len(common), 1)
        self.assertEqual(audit["invalid"], 0)
        self.assertFalse(decisions["ml_signal_date"].eq("2023-07-11").any())

    def test_same_day_future_prediction_is_not_used(self):
        candidate = pd.DataFrame([tech("2023-07-10")])
        predictions = pd.DataFrame([oof(model, "2023-07-10", "2023-07-10") for model in MODELS])
        with self.assertRaises(HybridPOCError):
            build_common_coverage(
                candidate, predictions, models=MODELS,
                training_calendar=["2023-07-07", "2023-07-10"],
            )

    def test_only_2023_2024_training_dates_are_accepted(self):
        validate_training_only_dates(["2023-07-10", "2024-12-31"])
        for forbidden in ("2025-01-02", "2026-01-02", "2026-07-02"):
            with self.assertRaises(HybridPOCError):
                validate_training_only_dates(["2024-01-01", forbidden])

    def test_actual_previous_trading_day_is_required(self):
        candidates = pd.DataFrame([tech("2023-07-11")])
        predictions = pd.DataFrame([oof(model, "2023-07-07", "2023-07-11") for model in MODELS])
        with self.assertRaises(HybridPOCError):
            build_common_coverage(
                candidates, predictions, models=MODELS,
                training_calendar=["2023-07-07", "2023-07-10", "2023-07-11"],
            )

    def test_all_models_must_use_the_same_oof_fold_for_a_candidate(self):
        candidates, predictions = self.fixture()
        predictions.loc[(predictions["model"] == "xgboost") & (predictions["entry_date"] == "2023-07-10"), "fold_id"] = "ML_WF2"
        common, decisions, audit = build_common_coverage(
            candidates, predictions, models=MODELS,
            training_calendar=["2023-07-07", "2023-07-10", "2023-07-11"],
        )
        self.assertEqual(len(common), 1)
        self.assertEqual(audit["excluded_reasons"]["MODEL_FOLD_MISMATCH"], 1)
        self.assertEqual(decisions["ml_fold_id"].nunique(), 1)

    def test_schedule_uses_only_common_candidate_entry_times_by_fold_and_stock(self):
        candidates, predictions = self.fixture()
        common, _, _ = build_common_coverage(
            candidates, predictions, models=MODELS,
            training_calendar=["2023-07-07", "2023-07-10", "2023-07-11"],
        )
        schedule = schedule_by_fold_stock(common)[("WF1", "2303")]
        self.assertEqual(schedule, {
            "2023-07-10T10:05:00.000000+08:00", "2023-07-11T10:05:00.000000+08:00",
        })

    def test_technical_common_coverage_filters_only_entry_and_inherits_exit(self):
        self.assertIs(TechnicalCommonCoverageStrategy._exit_signal, TechnicalV1Strategy._exit_signal)
        self.assertIs(TechnicalCommonCoverageStrategy._risk_exit_signal, TechnicalV1Strategy._risk_exit_signal)
        self.assertIsNot(TechnicalCommonCoverageStrategy._try_entry, TechnicalV1Strategy._try_entry)

    def test_entry_schedule_blocks_non_common_candidate_without_changing_entry_logic(self):
        strategy = object.__new__(TechnicalCommonCoverageStrategy)
        strategy.candidate_schedule = {"2023-07-10T10:05:00.000000+08:00"}
        tick_time = datetime(2023, 7, 10, 10, 5, tzinfo=TAIPEI)
        with patch.object(TechnicalV1Strategy, "_try_entry") as original_entry:
            strategy._try_entry("2303", tick_time, 100.0, {})
            original_entry.assert_called_once_with("2303", tick_time, 100.0, {})
            original_entry.reset_mock()
            strategy._try_entry("2303", datetime(2023, 7, 11, 10, 5, tzinfo=TAIPEI), 100.0, {})
            original_entry.assert_not_called()

    def test_existing_poc_output_is_immutable_and_output_creation_fails_if_existing(self):
        source = (ROOT / "hybrid/scripts/run_hybrid_common_coverage.py").read_text(encoding="utf-8")
        self.assertIn('output.mkdir(parents=True, exist_ok=False)', source)
        self.assertIn('"existing_poc_exports_overwritten": False', source)
        self.assertNotIn('poc_run / "', source)

    def test_ml_parameters_and_gate_config_are_read_only_inputs(self):
        source = inspect.getsource(build_common_coverage)
        self.assertNotIn("write_text", source)
        self.assertNotIn("model_parameters", source)
        self.assertNotIn("threshold", source)


if __name__ == "__main__":
    unittest.main()

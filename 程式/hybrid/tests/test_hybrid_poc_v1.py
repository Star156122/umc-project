from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.hybrid_poc_v1 import (
    HybridPOCError,
    assert_entry_only_change,
    build_prediction_lookup,
    html_is_standalone,
    make_alignment_decision,
    previous_trading_day,
    validate_oof_predictions,
    validate_technical_signal_value,
    validate_training_only_dates,
)
from hybrid.core.user_report import render_user_report

TAIPEI = ZoneInfo("Asia/Taipei")


def prediction(label: str = "BUY", *, signal_date: str = "2024-01-05", entry_date: str = "2024-01-08") -> dict:
    probs = {
        "BUY": (0.1, 0.2, 0.7),
        "HOLD": (0.1, 0.8, 0.1),
        "SELL": (0.8, 0.1, 0.1),
    }[label]
    return {
        "model": "random_forest",
        "stock_code": "2303",
        "signal_date": signal_date,
        "decision_time": signal_date + "T13:30:00+08:00",
        "entry_date": entry_date,
        "target_date": "2024-01-10",
        "fold_id": "ML_WF2",
        "prediction": label,
        "prob_sell": probs[0],
        "prob_hold": probs[1],
        "prob_buy": probs[2],
        "is_oof": True,
        "data_role": "training",
        "label_version": "ml_v1_fixed_1pct",
        "prediction_source": "ml_v1_baseline_training_walk_forward_oof",
        "model_parameter_fingerprint": "abc",
        "feature_version": "feature_set_a_v3_24",
    }


def decision(label: str | None) -> dict:
    item = None if label is None else prediction(label)
    return make_alignment_decision(
        model="random_forest",
        stock_code="2303",
        technical_fold_id="WF2",
        technical_signal_time=datetime(2024, 1, 8, 10, 5, tzinfo=TAIPEI),
        technical_reason="breakout",
        close_at_signal=50.0,
        prediction=item,
    )


class HybridPOCV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.poc = json.loads((ROOT / "hybrid/config/hybrid_poc_v1.json").read_text(encoding="utf-8"))
        cls.technical = json.loads((ROOT / "configs/technical_v1_research.json").read_text(encoding="utf-8"))

    def test_only_buy_candidate_is_accepted(self) -> None:
        validate_technical_signal_value("BUY_CANDIDATE")
        for value in ("SELL_CANDIDATE", "HOLD", "BUY"):
            with self.assertRaises(HybridPOCError):
                validate_technical_signal_value(value)

    def test_previous_actual_trading_day_skips_weekend_and_holiday(self) -> None:
        calendar = ["2024-02-07", "2024-02-15", "2024-02-16"]
        self.assertEqual(previous_trading_day(calendar, "2024-02-15"), "2024-02-07")

    def test_buy_allows_entry(self) -> None:
        self.assertEqual(decision("BUY")["hybrid_action"], "ENTRY")

    def test_hold_blocks_entry(self) -> None:
        self.assertEqual(decision("HOLD")["hybrid_action"], "BLOCK_HOLD")

    def test_sell_blocks_entry_without_short(self) -> None:
        self.assertEqual(decision("SELL")["hybrid_action"], "BLOCK_SELL")

    def test_missing_prediction_is_audited_and_blocked(self) -> None:
        result = decision(None)
        self.assertEqual(result["alignment_status"], "MISSING_ML_PREDICTION")
        self.assertEqual(result["hybrid_action"], "NO_ML_MATCH")

    def test_same_day_prediction_is_rejected_as_lookahead(self) -> None:
        item = prediction("BUY", signal_date="2024-01-08", entry_date="2024-01-08")
        with self.assertRaises(HybridPOCError):
            make_alignment_decision(
                model="random_forest", stock_code="2303", technical_fold_id="WF2",
                technical_signal_time=datetime(2024, 1, 8, 10, 5, tzinfo=TAIPEI),
                technical_reason="breakout", close_at_signal=50.0, prediction=item,
            )

    def test_model_variants_keep_separate_lookup(self) -> None:
        rf = prediction("BUY")
        xgb = {**prediction("HOLD"), "model": "xgboost"}
        frame = pd.DataFrame([rf, xgb])
        self.assertEqual(build_prediction_lookup(frame, "random_forest")[("2303", "2024-01-08")]["prediction"], "BUY")
        self.assertEqual(build_prediction_lookup(frame, "xgboost")[("2303", "2024-01-08")]["prediction"], "HOLD")

    def test_in_sample_prediction_is_rejected(self) -> None:
        rows = []
        for model in self.poc["ml"]["models"]:
            for stock in self.poc["stock_universe"]:
                rows.append({**prediction(), "model": model, "stock_code": stock})
        rows[0]["is_oof"] = False
        with self.assertRaises(HybridPOCError):
            validate_oof_predictions(
                pd.DataFrame(rows), expected_models=self.poc["ml"]["models"],
                expected_stocks=self.poc["stock_universe"], period_start="2023-01-01", period_end="2024-12-31",
            )

    def test_temporal_round2_provenance_is_rejected(self) -> None:
        rows = []
        for model in self.poc["ml"]["models"]:
            for stock in self.poc["stock_universe"]:
                rows.append({**prediction(), "model": model, "stock_code": stock,
                             "prediction_source": "temporal_round2_training_walk_forward_oof"})
        with self.assertRaises(HybridPOCError):
            validate_oof_predictions(
                pd.DataFrame(rows), expected_models=self.poc["ml"]["models"],
                expected_stocks=self.poc["stock_universe"], period_start="2023-01-01", period_end="2024-12-31",
            )

    def test_forbidden_year_is_rejected(self) -> None:
        with self.assertRaises(HybridPOCError):
            validate_training_only_dates(["2024-12-31", "2025-01-02"])

    def test_entry_only_contract_locks_existing_exit_risk_and_cost(self) -> None:
        assert_entry_only_change(self.technical, self.poc)
        changed = copy.deepcopy(self.poc)
        changed["technical"]["unchanged"].remove("exit_rule")
        with self.assertRaises(HybridPOCError):
            assert_entry_only_change(self.technical, changed)

    def test_html_must_be_standalone_without_cdn(self) -> None:
        self.assertTrue(html_is_standalone("<!doctype html><html><style>body{}</style><body></body></html>"))
        self.assertFalse(html_is_standalone("<!doctype html><script src='https://cdn.example/x.js'></script>"))

    def test_config_locks_requested_candidate_and_baseline(self) -> None:
        self.assertEqual(self.poc["technical"]["candidate_id"], "BR_SENS_RANGE55_BB20_2")
        self.assertEqual(self.poc["ml"]["label"], "fixed_1pct")
        self.assertFalse(self.poc["selection_or_optimization"])

    def test_user_report_is_standalone_and_uses_supplied_results(self) -> None:
        variants = {}
        result_rows = []
        values = {
            "technical_alone": (-0.02, 0.20, 0.20, 100),
            "hybrid_random_forest": (0.01, 0.35, 0.10, 30),
            "hybrid_xgboost": (0.03, 0.30, 0.08, 25),
            "hybrid_gru": (0.00, 0.25, 0.09, 28),
        }
        for variant, (total_return, win_rate, drawdown, trades) in values.items():
            variants[variant] = {
                "total_return_mean": total_return,
                "win_rate_mean": win_rate,
                "max_drawdown_worst": drawdown,
                "trade_count_total": trades,
                "profit_factor_mean": 1.1,
                "sharpe_mean": 0.2,
                "net_pnl_total": 100.0,
                "gross_pnl_total": 200.0,
                "transaction_cost_total": 100.0,
            }
            result_rows.append({
                "stock_code": "2303", "variant": variant, "fold_id": "WF1",
                "evaluate_start": "2023-07-01", "evaluate_end": "2023-12-31",
                "total_return": total_return, "win_rate": win_rate,
                "max_drawdown": drawdown, "trade_count": trades,
            })
        decisions = pd.DataFrame([
            {"model": "random_forest", "stock_code": "2303", "technical_signal_date": "2023-10-01", "ml_prediction_date": "2023-09-28", "ml_predicted_class": "BUY", "hybrid_action": "ENTRY"},
            {"model": "random_forest", "stock_code": "2303", "technical_signal_date": "2023-10-02", "ml_prediction_date": "2023-09-29", "ml_predicted_class": "HOLD", "hybrid_action": "BLOCK_HOLD"},
            {"model": "random_forest", "stock_code": "2303", "technical_signal_date": "2023-10-03", "ml_prediction_date": "2023-10-02", "ml_predicted_class": "SELL", "hybrid_action": "BLOCK_SELL"},
        ])
        audit = {
            "technical_candidate_count": 100,
            "alignment": {"matched": 3, "missing": 0, "invalid": 0},
            "oof_validation": {"rows": 3, "date_min": "2023-09-28", "date_max": "2023-10-02", "status": "PASS"},
        }
        document = render_user_report(audit, {"variants": variants}, pd.DataFrame(result_rows), decisions)
        self.assertTrue(html_is_standalone(document))
        self.assertIn("AI 股票策略回測結果", document)
        self.assertIn("+3.00%", document)
        self.assertIn("XGBoost", document)
        self.assertIn("hybrid_poc_report.html", document)
        self.assertIn("執行交易", document)


if __name__ == "__main__":
    unittest.main()

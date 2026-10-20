from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.hybrid_poc_v1 import HybridPOCError, html_is_standalone
from hybrid.scripts.run_ml_v2_exp001 import (
    MODEL_VARIANT, MODELS, _metrics_frame, build_comparisons,
    classify_incremental_value, render_html, select_common_coverage,
)


def _prediction(model: str, signal_date: str = "2023-07-03") -> dict:
    return {"model": model, "stock_code": "2303", "signal_date": signal_date,
        "entry_date": "2023-07-04", "target_date": "2023-07-07", "fold_id": "ML_WF1",
        "decision_time": f"{signal_date}T13:30:00+08:00", "prediction": "BUY",
        "prob_sell": 0.1, "prob_hold": 0.2, "prob_buy": 0.7, "is_oof": "true",
        "data_role": "training"}


class MLV2Experiment001Tests(unittest.TestCase):
    def setUp(self):
        self.candidates = pd.DataFrame([{
            "stock_code": "2303", "fold_id": "WF1",
            "signal_time": "2023-07-04T12:00:00+08:00",
            "earliest_entry_time": "2023-07-04T12:05:00+08:00", "close_at_signal": 50.0,
        }])
        self.lookups = {model: {("2303", "2023-07-04"): _prediction(model)} for model in MODELS}
        self.context = {("2303", "2023-07-04"): {
            "eligible": True, "reason": "PASS_PRIOR_PROXY_ABOVE_SMA20",
            "last_20_proxy_closes": [float(i) for i in range(1, 21)],
        }}

    def test_common_coverage_requires_all_models_and_previous_actual_session(self):
        frame, rejected = select_common_coverage(
            self.candidates, self.lookups, {("2303", "2023-07-04"): "2023-07-03"}, self.context)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["previous_actual_session"], "2023-07-03")
        self.assertEqual(rejected["missing_model_oof"], 0)

    def test_common_coverage_excludes_candidate_when_any_model_oof_missing(self):
        lookups = {model: dict(values) for model, values in self.lookups.items()}
        lookups["gru"].clear()
        with self.assertRaises(HybridPOCError):
            select_common_coverage(self.candidates, lookups,
                {("2303", "2023-07-04"): "2023-07-03"}, self.context)

    def test_common_coverage_rejects_same_day_prediction(self):
        lookups = {model: dict(values) for model, values in self.lookups.items()}
        lookups["xgboost"][("2303", "2023-07-04")] = _prediction("xgboost", "2023-07-04")
        with self.assertRaises(HybridPOCError):
            select_common_coverage(self.candidates, lookups,
                {("2303", "2023-07-04"): "2023-07-03"}, self.context)

    def test_supported_classification_requires_cross_stock_and_cross_fold_evidence(self):
        baseline = {"average_return": 0.0, "profit_factor": 1.0, "worst_mdd": 0.1}
        candidate = {"average_return": 0.1, "profit_factor": 1.1, "worst_mdd": 0.08}
        stocks = pd.DataFrame([{"average_return_change": 0.01, "profit_factor_change": 0.1,
            "worst_mdd_change": -0.01}] * 3)
        folds = pd.DataFrame([{"average_return_change": 0.01, "profit_factor_change": 0.1,
            "worst_mdd_change": -0.01}] * 2)
        self.assertEqual(classify_incremental_value(baseline, candidate, stocks, folds), "INCREMENTAL_VALUE_SUPPORTED")

    def test_fold_metrics_retain_each_stock_and_all_stock_aggregate(self):
        rows = []
        for variant, model in (("TECH_V2_ALONE", "technical_only"), ("TECH_V2_RF", "random_forest")):
            for stock in ("2303", "2330"):
                for fold in ("WF1", "WF2", "WF3"):
                    rows.append({"variant": variant, "model": model, "stock_code": stock,
                        "fold_id": fold, "total_return": 0.01, "trade_count": 1,
                        "profit_factor": 1.0, "max_drawdown": 0.02, "win_rate": 0.5,
                        "sharpe_ratio": 0.1, "net_pnl": 100, "gross_pnl": 150,
                        "transaction_cost": 50, "final_equity": 100100, "median_return": 0.01})
        _, _, per_fold, _ = _metrics_frame(pd.DataFrame(rows))
        self.assertEqual(len(per_fold), 2 * 3 * 3)
        self.assertEqual(int((per_fold.stock_code == "ALL_STOCKS").sum()), 2 * 3)
        self.assertEqual(int((per_fold.stock_code == "2303").sum()), 2 * 3)

    def test_model_comparison_builds_metrics_without_series_method_collisions(self):
        rows = []
        variants = [("TECH_V2_ALONE", "technical_only"), *[(v, m) for m, v in MODEL_VARIANT.items()]]
        for variant, model in variants:
            for stock in ("2303", "2330", "2379", "2454", "3034"):
                for fold in ("WF1", "WF2", "WF3"):
                    rows.append({"variant": variant, "model": model, "stock_code": stock,
                        "fold_id": fold, "total_return": 0.01, "trade_count": 1,
                        "profit_factor": 1.2, "max_drawdown": 0.02, "win_rate": 0.5,
                        "sharpe_ratio": 0.1, "net_pnl": 100, "gross_pnl": 150,
                        "transaction_cost": 50, "final_equity": 100100, "median_return": 0.01})
        overall, per_stock, per_fold, _ = _metrics_frame(pd.DataFrame(rows))
        comparison, _ = build_comparisons(overall, per_stock, per_fold)
        self.assertEqual(len(comparison), 3)
        self.assertIn("sharpe_change", comparison.columns)
        self.assertIn("profit_factor_change", comparison.columns)

    def test_single_html_is_offline_and_has_both_filters_and_warning(self):
        audit = {"gate_metrics": {}, "stock_comparison": {}, "interpretations": {}}
        empty = pd.DataFrame()
        page = render_html(audit, empty, empty, empty, empty, empty)
        self.assertTrue(html_is_standalone(page))
        self.assertIn("id=\"scope\"", page)
        self.assertIn("id=\"model\"", page)
        self.assertIn("不是 Final OOS", page)
        self.assertIn("不是 Portfolio Backtest", page)


if __name__ == "__main__":
    unittest.main()

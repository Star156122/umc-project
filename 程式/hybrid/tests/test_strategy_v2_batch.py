from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.strategy_v2_batch import (
    EXPERIMENTS,
    build_plan,
    regime_confirmation_passes,
    volume_confirmation_passes,
)
from hybrid.scripts.run_strategy_v2_batch import create_comparison, gate_parity


class StrategyV2BatchPlanTests(unittest.TestCase):
    def test_all_preregistered_experiments_can_be_planned_independently(self):
        plan = build_plan(ROOT, ["CONTROL", "TECH_V2_A", "TECH_V2_B", "TECH_V2_C"])
        self.assertEqual(len(plan), 4)
        self.assertTrue(all(item["preregistration_exists"] for item in plan))
        self.assertTrue(all(item["auto_rank_or_select_winner"] is False for item in plan))
        self.assertTrue(all(item["performance_authorized_by_this_runner"] is False for item in plan))

    def test_each_technical_experiment_has_its_own_preregistration(self):
        plan = build_plan(ROOT, ["TECH_V2_A", "TECH_V2_B", "TECH_V2_C"])
        paths = [item["preregistration"] for item in plan]
        self.assertEqual(len(paths), len(set(paths)))
        for item in plan:
            text = (ROOT / str(item["preregistration"])).read_text(encoding="utf-8")
            self.assertIn("PREREGISTERED", text)
            self.assertIn("No performance run", text)

    def test_empty_or_duplicate_plan_is_rejected(self):
        with self.assertRaises(ValueError):
            build_plan(ROOT, [])
        with self.assertRaises(ValueError):
            build_plan(ROOT, ["TECH_V2_A", "TECH_V2_A"])

    def test_unknown_experiment_is_rejected(self):
        with self.assertRaises(ValueError):
            build_plan(ROOT, ["AUTO_PICK_BEST"])

    def test_registry_contains_only_explicit_experiment_identifiers(self):
        self.assertEqual(set(EXPERIMENTS), {"CONTROL", "TECH_V2_A", "TECH_V2_B", "TECH_V2_C"})

    def test_v2a_requires_strictly_more_than_exactly_twenty_prior_volumes(self):
        prior = [100.0] * 20
        self.assertTrue(volume_confirmation_passes(101, prior))
        self.assertFalse(volume_confirmation_passes(100, prior))
        self.assertFalse(volume_confirmation_passes(99, prior))
        self.assertFalse(volume_confirmation_passes(1000, prior[:-1]))
        self.assertFalse(volume_confirmation_passes(float("nan"), prior))

    def test_v2b_requires_latest_prior_proxy_close_above_its_twenty_day_sma(self):
        self.assertTrue(regime_confirmation_passes([float(v) for v in range(1, 21)]))
        self.assertFalse(regime_confirmation_passes([float(v) for v in range(2, 22)][::-1]))
        self.assertFalse(regime_confirmation_passes([1.0] * 19))
        self.assertFalse(regime_confirmation_passes([1.0] * 19 + [float("inf")]))

    def test_gate_parity_aligns_using_signal_time_as_index(self):
        rows = []
        for version in EXPERIMENTS:
            rows.append({
                "version": version, "model": "random_forest", "stock_code": "2303",
                "fold_id": "WF1", "technical_signal_time": "2023-09-05T11:25:00+08:00",
                "ml_prediction": "BUY", "hybrid_action": "ENTRY", "ml_signal_date": "2023-09-04",
                "prob_sell": 0.1, "prob_hold": 0.2, "prob_buy": 0.7,
            })
        result = gate_parity(__import__("pandas").DataFrame(rows))
        self.assertTrue(result["random_forest"]["TECH_V2_A"]["matches_on_shared_decisions"])

    def test_classification_aggregates_stock_and_fold_results_without_series_method_collision(self):
        import pandas as pd

        overall, per_stock, per_fold = [], [], []
        for version in EXPERIMENTS:
            for model in ("random_forest", "xgboost", "gru"):
                overall.append({"version": version, "model": model, "average_return": 0.0,
                    "median_return": 0.0, "worst_mdd": 0.1, "win_rate": 0.0,
                    "profit_factor": 1.0, "sharpe": 0.0, "trade_count": 1,
                    "net_pnl": 0.0, "gross_pnl": 0.0, "transaction_cost": 0.0,
                    "final_equity_mean": 100000.0})
                for stock in ("2303", "2330", "2379", "2454", "3034"):
                    per_stock.append({"version": version, "model": model, "stock_code": stock,
                        "profit_factor": 1.0, "worst_mdd": 0.1})
                for fold in ("WF1", "WF2", "WF3"):
                    per_fold.append({"version": version, "model": model, "fold_id": fold,
                        "profit_factor": 1.0, "worst_mdd": 0.1})
        _, classifications = create_comparison(pd.DataFrame(overall), pd.DataFrame(per_stock), pd.DataFrame(per_fold))
        self.assertEqual(classifications, {"TECH_V2_A": "MIXED", "TECH_V2_B": "MIXED", "TECH_V2_C": "MIXED"})


if __name__ == "__main__":
    unittest.main()

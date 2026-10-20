from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.hybrid_poc_v1 import HybridPOCError
from hybrid.scripts.run_strategy_v2_candidate_qualification import (
    check_no_future_leakage,
    compare_reproduction,
)


class CandidateQualificationGuardTests(unittest.TestCase):
    def test_proxy_context_must_use_strictly_prior_session(self):
        context = {("2303", "2023-10-10"): {
            "latest_common_prior_date": "2023-10-09", "history_count": 20,
            "last_20_proxy_closes": [100.0] * 20,
        }}
        result = check_no_future_leakage(context, {"2023-10-10"})
        self.assertEqual(result["status"], "PASS")

    def test_proxy_context_rejects_same_day_or_future_date(self):
        context = {("2303", "2023-10-10"): {
            "latest_common_prior_date": "2023-10-10", "history_count": 20,
            "last_20_proxy_closes": [100.0] * 20,
        }}
        with self.assertRaises(HybridPOCError):
            check_no_future_leakage(context, {"2023-10-10"})

    def test_candidate_reproduction_checks_each_result_cell_and_metric(self):
        rows = []
        for model in ("random_forest", "xgboost", "gru"):
            for stock in ("2303", "2330", "2379", "2454", "3034"):
                for fold in ("WF1", "WF2", "WF3"):
                    rows.append({"version": "TECH_V2_B", "model": model, "stock_code": stock,
                        "fold_id": fold, "total_return": 0.1, "max_drawdown": 0.05,
                        "win_rate": 0.5, "profit_factor": 1.2, "sharpe_ratio": 0.7,
                        "trade_count": 2, "gross_pnl": 300, "net_pnl": 200,
                        "transaction_cost": 100, "final_equity": 100200})
        frame = pd.DataFrame(rows)
        report = compare_reproduction(frame, frame.copy(), version="TECH_V2_B")
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(all(report["metrics_match"].values()))


if __name__ == "__main__":
    unittest.main()

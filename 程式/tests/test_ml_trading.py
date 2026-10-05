import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.trading_backtest import run_probability_backtest, run_signal_backtest
from ml.trading_pipeline import coverage_report, load_trading_plan
from trading_system.research_guard import HoldoutLockedError


class MlTradingTests(unittest.TestCase):
    def test_fixed_plan_uses_six_stocks_and_locked_holdout(self):
        plan = load_trading_plan(ROOT / "configs/ml_trading_v1_20261003.json")
        self.assertEqual(plan["stock_codes"], ["2303", "2330", "2412", "2881", "2882", "2002"])
        self.assertEqual(plan["periods"]["development"], {"start": "2026-01-01", "end": "2026-06-30"})
        self.assertNotIn("test", plan["periods"])
        self.assertEqual(plan["holdout"]["access"], "forbidden")

    def test_holdout_cannot_be_used_as_a_split(self):
        source = ROOT / "configs/ml_trading_v1_20261003.json"
        plan = json.loads(source.read_text(encoding="utf-8"))
        plan["periods"]["validation"] = {"start": "2025-07-01", "end": "2025-12-31"}
        temp = ROOT / "configs/_test_ml_trading_holdout.json"
        temp.write_text(json.dumps(plan), encoding="utf-8")
        try:
            with self.assertRaises(HoldoutLockedError):
                load_trading_plan(temp)
        finally:
            temp.unlink(missing_ok=True)

    def test_final_oos_cannot_be_used_as_a_split(self):
        source = ROOT / "configs/ml_trading_v1_20261003.json"
        plan = json.loads(source.read_text(encoding="utf-8"))
        plan["periods"]["development"] = {"start": "2026-07-01", "end": "2026-10-05"}
        temp = ROOT / "configs/_test_ml_final_oos.json"
        temp.write_text(json.dumps(plan), encoding="utf-8")
        try:
            with self.assertRaises(HoldoutLockedError):
                load_trading_plan(temp)
        finally:
            temp.unlink(missing_ok=True)

    def test_final_oos_is_blocked_before_database_connection(self):
        plan = json.loads((ROOT / "configs/ml_trading_v1_20261003.json").read_text(encoding="utf-8"))
        plan["periods"]["development"] = {"start": "2026-07-01", "end": "2026-10-05"}
        with patch("ml.trading_pipeline.sqlite3.connect") as connect:
            with self.assertRaises(HoldoutLockedError):
                coverage_report(ROOT / "data/market_data.sqlite3", plan)
            connect.assert_not_called()

    def test_probability_signal_executes_at_next_open(self):
        market = pd.DataFrame([
            {"stock_code": "2303", "trade_date": "2026-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"stock_code": "2303", "trade_date": "2026-01-05", "open": 101, "high": 104, "low": 100, "close": 103, "volume": 1},
            {"stock_code": "2303", "trade_date": "2026-01-06", "open": 103, "high": 105, "low": 102, "close": 104, "volume": 1},
            {"stock_code": "2303", "trade_date": "2026-01-07", "open": 104, "high": 106, "low": 103, "close": 105, "volume": 1},
            {"stock_code": "2303", "trade_date": "2026-01-08", "open": 106, "high": 107, "low": 105, "close": 106, "volume": 1},
        ])
        metadata = pd.DataFrame([
            {"stock_code": "2303", "signal_date": "2026-01-02"},
            {"stock_code": "2303", "signal_date": "2026-01-05"},
            {"stock_code": "2303", "signal_date": "2026-01-06"},
            {"stock_code": "2303", "signal_date": "2026-01-07"},
        ])
        rules = {"initial_cash": 100000, "capital_fraction": .95, "max_shares": 1000,
                 "buy_probability": .60, "sell_probability": .45, "stop_loss": .03,
                 "commission_rate": .001425, "transaction_tax_rate": .003,
                 "max_holding_sessions": 3}
        result = run_probability_backtest(market, metadata, np.array([.7, .5, .5, .5]), rules)["2303"]
        self.assertEqual(result["trades"][0]["side"], "BUY")
        self.assertEqual(result["trades"][0]["date"], "2026-01-05")
        self.assertEqual(result["trades"][0]["price"], 101)
        self.assertEqual(result["trades"][1]["date"], "2026-01-08")
        metrics = result["metrics"]
        self.assertGreater(metrics["transaction_cost"], 0)
        self.assertAlmostEqual(
            metrics["gross_pnl_before_costs"] - metrics["transaction_cost"],
            metrics["net_pnl_after_costs"],
            places=8,
        )
        self.assertIn("profit_factor", metrics)
        self.assertIn("sharpe_ratio", metrics)

    def test_multiclass_signal_executes_at_next_open(self):
        market = pd.DataFrame([
            {"stock_code": "2303", "trade_date": "2026-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"stock_code": "2303", "trade_date": "2026-01-05", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1},
            {"stock_code": "2303", "trade_date": "2026-01-06", "open": 102, "high": 103, "low": 101, "close": 102, "volume": 1},
        ])
        metadata = pd.DataFrame([{"stock_code": "2303", "signal_date": "2026-01-02"}])
        rules = {"initial_cash": 100000, "capital_fraction": .95, "max_shares": 1000,
                 "stop_loss": .03, "commission_rate": .001425, "transaction_tax_rate": .003,
                 "max_holding_sessions": 3}
        result = run_signal_backtest(market, metadata, ["BUY"], rules)["2303"]
        self.assertEqual(result["trades"][0]["date"], "2026-01-05")
        self.assertEqual(result["trades"][0]["price"], 101)


if __name__ == "__main__":
    unittest.main()

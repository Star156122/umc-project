from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.risk_v1 import (  # noqa: E402
    EXISTING_STOP_LOSS_PCT, MAX_POSITION_FRACTION, RISK_PER_TRADE,
    RiskV1HybridStrategy, calculate_risk_size, risk_sizing_allowed,
)
from hybrid.core.hybrid_poc_v1 import HybridGateTechnicalV1Strategy  # noqa: E402
from hybrid.scripts.run_hybrid_risk_v1 import (  # noqa: E402
    compare_reproduction, validate_risk_config, validate_trade_timing,
)
from trading_system.technical_v1 import TechnicalV1Strategy  # noqa: E402


class RiskV1Tests(unittest.TestCase):
    def test_fixed_risk_and_exposure_parameters(self):
        self.assertEqual(RISK_PER_TRADE, 0.01)
        self.assertEqual(MAX_POSITION_FRACTION, 0.40)

    def test_existing_stop_loss_remains_two_point_five_percent(self):
        self.assertEqual(EXISTING_STOP_LOSS_PCT, 0.025)
        self.assertEqual(calculate_risk_size(current_equity=100_000, entry_price=100, original_quantity=1000)["stop_loss_pct"], 0.025)

    def test_risk_size_uses_floor_and_both_caps(self):
        result = calculate_risk_size(current_equity=100_000, entry_price=100, original_quantity=1000)
        self.assertEqual(result["quantity_by_risk"], 400)
        self.assertEqual(result["quantity_by_exposure"], 400)
        self.assertEqual(result["final_quantity"], 400)
        self.assertLessEqual(result["final_quantity"], result["quantity_by_risk"])
        self.assertLessEqual(result["final_quantity"], result["quantity_by_exposure"])

    def test_existing_engine_buy_limit_is_respected(self):
        result = calculate_risk_size(current_equity=100_000, entry_price=100, original_quantity=125)
        self.assertEqual(result["final_quantity"], 125)
        self.assertEqual(result["decision"], "ALLOW")

    def test_zero_quantity_is_blocked_without_creating_a_trade(self):
        result = calculate_risk_size(current_equity=0, entry_price=100, original_quantity=1000)
        self.assertEqual(result["decision"], "BLOCK_ZERO_QUANTITY")
        self.assertEqual(result["final_quantity"], 0)

    def test_invalid_and_search_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            calculate_risk_size(current_equity=100_000, entry_price=100, original_quantity=1000, risk_per_trade=0.02)
        config = json.loads((ROOT / "hybrid/config/risk_v1.json").read_text(encoding="utf-8"))
        validate_risk_config(config)
        config["parameter_search"] = True
        with self.assertRaises(Exception):
            validate_risk_config(config)

    def test_risk_strategy_inherits_technical_exit_and_stop_logic_unchanged(self):
        self.assertIs(RiskV1HybridStrategy._exit_signal, TechnicalV1Strategy._exit_signal)
        self.assertIs(RiskV1HybridStrategy._risk_exit_signal, TechnicalV1Strategy._risk_exit_signal)
        self.assertIs(RiskV1HybridStrategy._try_exit, TechnicalV1Strategy._try_exit)

    def test_risk_strategy_retains_existing_hybrid_gate_alignment_decision(self):
        source = RiskV1HybridStrategy._try_entry.__code__.co_names
        self.assertIn("make_alignment_decision", source)
        self.assertIn("hybrid_decisions", source)
        code = (ROOT / "hybrid/core/risk_v1.py").read_text(encoding="utf-8")
        self.assertIn('risk_sizing_allowed(str(gate_decision["hybrid_action"]))', code)

    def test_hold_and_sell_never_reach_risk_sizing(self):
        self.assertTrue(risk_sizing_allowed("ENTRY"))
        self.assertFalse(risk_sizing_allowed("BLOCK_HOLD"))
        self.assertFalse(risk_sizing_allowed("BLOCK_SELL"))
        self.assertFalse(risk_sizing_allowed("NO_ML_MATCH"))

    def test_current_equity_is_computed_from_current_cash_and_position_only(self):
        source = (ROOT / "hybrid/core/risk_v1.py").read_text(encoding="utf-8")
        self.assertIn("self.local_cash", source)
        self.assertIn("self.local_position", source)
        self.assertNotIn("final_equity", source)
        self.assertNotIn("fold_end", source)

    def test_gate_and_candidate_signal_implementation_are_not_modified(self):
        self.assertTrue(issubclass(RiskV1HybridStrategy, HybridGateTechnicalV1Strategy))
        self.assertIs(RiskV1HybridStrategy.record_signal, TechnicalV1Strategy.record_signal)

    def test_common_coverage_and_oof_sources_are_read_only(self):
        source = (ROOT / "hybrid/scripts/run_hybrid_risk_v1.py").read_text(encoding="utf-8")
        self.assertIn("common_coverage_source", source)
        self.assertIn("directory_hashes(baseline_dir)", source)
        self.assertIn("directory_hashes(reference_dir)", source)
        self.assertIn("directory_hashes(poc_run)", source)

    def test_training_date_guard_blocks_2025_2026_and_final_oos(self):
        source = (ROOT / "hybrid/scripts/run_hybrid_risk_v1.py").read_text(encoding="utf-8")
        self.assertIn('"2023-01-01"', source)
        self.assertIn('"2024-12-31"', source)
        self.assertIn("tv1.assert_execution_ready(technical_config, \"training\")", source)
        self.assertIn('"forbidden_roles"', source)

    def test_outputs_are_timestamped_and_never_overwrite(self):
        source = (ROOT / "hybrid/scripts/run_hybrid_risk_v1.py").read_text(encoding="utf-8")
        self.assertIn('f"hybrid_risk_v1_{timestamp}"', source)
        self.assertIn("output.mkdir(parents=True, exist_ok=False)", source)

    def test_no_grid_or_random_search_in_config_or_runner(self):
        config = json.loads((ROOT / "hybrid/config/risk_v1.json").read_text(encoding="utf-8"))
        self.assertFalse(config["parameter_search"])
        self.assertFalse(config["optimization_performed"])
        source = (ROOT / "hybrid/scripts/run_hybrid_risk_v1.py").read_text(encoding="utf-8")
        self.assertNotIn("GridSearch", source)
        self.assertNotIn("RandomizedSearch", source)

    def test_entry_and_exit_timestamps_must_match_when_trade_is_filled_in_both(self):
        baseline = [
            {"variant": "hybrid_random_forest_common", "fold_id": "WF1", "stock_code": "2303", "action": "Buy", "datetime": "2023-07-10 10:00:00", "timestamp": 1},
            {"variant": "hybrid_random_forest_common", "fold_id": "WF1", "stock_code": "2303", "action": "Sell", "datetime": "2023-07-12 11:00:00", "timestamp": 2},
        ]
        risk = [
            {"variant": "hybrid_random_forest_risk_v1", "fold_id": "WF1", "stock_code": "2303", "action": "Buy", "datetime": "2023-07-10 10:00:00", "timestamp": 1},
            {"variant": "hybrid_random_forest_risk_v1", "fold_id": "WF1", "stock_code": "2303", "action": "Sell", "datetime": "2023-07-12 11:00:00", "timestamp": 2},
        ]
        self.assertEqual(validate_trade_timing(baseline, risk)["matched_entry_exit_pairs"], 1)
        risk[1]["datetime"] = "2023-07-12 11:05:00"
        with self.assertRaisesRegex(RuntimeError, "RISK_V1_LOGIC_MISMATCH"):
            validate_trade_timing(baseline, risk)

    def test_baseline_guard_compares_all_required_metrics(self):
        import pandas as pd

        baseline = pd.DataFrame([{
            "variant": "hybrid_random_forest_common", "average_return": 0.1,
            "trade_count": 4, "net_pnl": 100.0, "transaction_cost": 20.0,
            "worst_mdd": 0.05,
        }])
        self.assertTrue(all(v["match"] for v in compare_reproduction(baseline, baseline.copy())["hybrid_random_forest_common"].values()))
        changed = baseline.copy()
        changed.loc[0, "net_pnl"] = 99.0
        with self.assertRaisesRegex(RuntimeError, "RISK_V1_BASELINE_MISMATCH"):
            compare_reproduction(baseline, changed)


if __name__ == "__main__":
    unittest.main()

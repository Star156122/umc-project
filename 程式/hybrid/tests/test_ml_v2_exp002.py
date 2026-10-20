from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.hybrid_poc_v1 import HybridPOCError, html_is_standalone, make_alignment_decision
from hybrid.scripts.run_ml_v2_exp002 import (
    BUY_ONLY, MODELS, SELL_VETO, classify_sell_veto, gate_allows,
    reproduce_controls, same_common_coverage, sell_veto_lookup,
    validate_decision_parity, validate_execution_contract,
    validate_risk_contract,
)


class HybridV2Experiment002Tests(unittest.TestCase):
    def test_buy_hold_sell_gate_matrix(self):
        expected = {
            "BUY": (True, True),
            "HOLD": (False, True),
            "SELL": (False, False),
        }
        for label, (buy_only, sell_veto) in expected.items():
            self.assertEqual(gate_allows(label, "BUY_ONLY"), buy_only)
            self.assertEqual(gate_allows(label, "SELL_VETO"), sell_veto)

    def test_invalid_gate_or_class_fails_closed(self):
        with self.assertRaises(ValueError):
            gate_allows("BUY", "OTHER")
        with self.assertRaises(HybridPOCError):
            gate_allows("UNKNOWN", "SELL_VETO")

    def test_sell_veto_preserves_oof_prediction_except_hold_gate_mapping(self):
        original = {("2303", "2023-07-03"): {"prediction": "HOLD", "prob_hold": .8, "is_oof": "true"},
                    ("2330", "2023-07-03"): {"prediction": "SELL", "prob_sell": .7, "is_oof": "true"}}
        transformed = sell_veto_lookup(original)
        self.assertEqual(transformed[("2303", "2023-07-03")]["prediction"], "BUY")
        self.assertEqual(transformed[("2303", "2023-07-03")]["prob_hold"], .8)
        self.assertEqual(original[("2303", "2023-07-03")]["prediction"], "HOLD")
        self.assertEqual(transformed[("2330", "2023-07-03")]["prediction"], "SELL")

    def test_previous_session_oof_alignment_rejects_same_day_future(self):
        pred = {"model": "random_forest", "stock_code": "2303", "signal_date": "2023-07-03",
                "entry_date": "2023-07-04", "fold_id": "ML_WF1", "prediction": "HOLD",
                "prob_sell": .1, "prob_hold": .8, "prob_buy": .1, "is_oof": "true", "data_role": "training"}
        decision = make_alignment_decision(model="random_forest", stock_code="2303", technical_fold_id="WF1",
            technical_signal_time=pd.Timestamp("2023-07-04 11:00", tz="Asia/Taipei").to_pydatetime(),
            technical_earliest_entry_time=pd.Timestamp("2023-07-04 11:05", tz="Asia/Taipei").to_pydatetime(),
            technical_reason="frozen candidate", close_at_signal=50, prediction=pred)
        self.assertTrue(decision["time_order_valid"])
        pred["signal_date"] = "2023-07-04"
        with self.assertRaises(HybridPOCError):
            make_alignment_decision(model="random_forest", stock_code="2303", technical_fold_id="WF1",
                technical_signal_time=pd.Timestamp("2023-07-04 11:00", tz="Asia/Taipei").to_pydatetime(),
                technical_earliest_entry_time=pd.Timestamp("2023-07-04 11:05", tz="Asia/Taipei").to_pydatetime(),
                technical_reason="frozen candidate", close_at_signal=50, prediction=pred)

    def test_control_reproduction_checks_frozen_metrics_and_candidate_count(self):
        rows, old = [], []
        for variant, model, prior in [("TECH_V2_ALONE", "technical_only", "TECH_V2_ALONE"),
            ("RF_BUY_ONLY", "random_forest", "TECH_V2_RF"),
            ("XGB_BUY_ONLY", "xgboost", "TECH_V2_XGB"), ("GRU_BUY_ONLY", "gru", "TECH_V2_GRU")]:
            row = {"variant": variant, "model": model, "candidate_count": 138, "trade_count": 20,
                   "average_return": .01, "worst_mdd": .02, "profit_factor": 1.2, "net_pnl": 1000}
            rows.append(row)
            old.append({**row, "variant": prior})
        result = reproduce_controls(pd.DataFrame(rows), pd.DataFrame(old))
        self.assertEqual(result["status"], "PASS")
        rows[1]["trade_count"] = 21
        self.assertEqual(reproduce_controls(pd.DataFrame(rows), pd.DataFrame(old))["status"], "FAIL")

    def test_sell_veto_classification_requires_all_registered_directional_conditions(self):
        baseline = {"trade_count": 20, "average_return": .01, "profit_factor": 1.2, "worst_mdd": .03}
        candidate = {"trade_count": 30, "average_return": .011, "profit_factor": 1.3, "worst_mdd": .02}
        stocks = pd.DataFrame({"average_return_change": [.01, .01, .01, -.01, -.01]})
        folds = pd.DataFrame({"average_return_change": [.01, .01, -.01]})
        self.assertEqual(classify_sell_veto(baseline, candidate, stocks, folds), "BALANCED_GATE_SUPPORTED")
        folds.loc[2, "average_return_change"] = .01
        self.assertEqual(classify_sell_veto(baseline, candidate, stocks, folds), "BALANCED_GATE_SUPPORTED")
        candidate["worst_mdd"] = .04
        self.assertEqual(classify_sell_veto(baseline, candidate, stocks, folds), "BALANCED_GATE_MIXED")

    def test_common_model_and_variant_inventory_is_fixed(self):
        self.assertEqual(set(MODELS), {"random_forest", "xgboost", "gru"})
        self.assertEqual(set(BUY_ONLY), set(MODELS))
        self.assertEqual(set(SELL_VETO), set(MODELS))

    def test_exp001_common_coverage_must_be_identical(self):
        frame = pd.DataFrame([{"stock_code": "2303", "fold_id": "WF1", "signal_time": "2023-07-04T11:00",
            "earliest_entry_time": "2023-07-04T11:05"}])
        self.assertTrue(same_common_coverage(frame, frame.copy()))
        changed = frame.copy()
        changed.loc[0, "signal_time"] = "2023-07-05T11:00"
        self.assertFalse(same_common_coverage(frame, changed))

    def test_candidate_decisions_preserve_technical_coverage_and_previous_oof(self):
        rows = []
        for variant, model in [("TECH_V2_ALONE", "technical_only"),
            *[(v, m) for m, v in BUY_ONLY.items()], *[(v, m) for m, v in SELL_VETO.items()]]:
            rows.append({"variant": variant, "model": model, "technical_candidate_date": "2023-07-05",
                "ml_signal_date": "2023-07-04", "is_oof": True, "hybrid_action": "ENTRY"})
        audit = validate_decision_parity(pd.DataFrame(rows), 1)
        self.assertEqual(audit["status"], "PASS")
        self.assertFalse(audit["same_day_or_future_prediction_used"])
        rows[-1]["ml_signal_date"] = "2023-07-06"
        with self.assertRaises(HybridPOCError):
            validate_decision_parity(pd.DataFrame(rows), 1)

    def test_risk_and_execution_contracts_remain_frozen(self):
        self.assertTrue(validate_risk_contract({"risk_per_trade": .01, "max_position_fraction": .40, "stop_loss_pct": .025}))
        self.assertFalse(validate_risk_contract({"risk_per_trade": .02, "max_position_fraction": .40, "stop_loss_pct": .025}))
        self.assertTrue(validate_execution_contract({"execution_mode": "next_tradable_bar_open"}))
        self.assertFalse(validate_execution_contract({"execution_mode": "same_bar_close"}))

    def test_user_html_is_offline_single_document(self):
        document = "<!doctype html><html><style>body{color:#fff}</style><script>const x=1</script></html>"
        self.assertTrue(html_is_standalone(document))
        self.assertFalse(html_is_standalone("<!doctype html><script src='https://example.com'></script>"))


if __name__ == "__main__":
    unittest.main()

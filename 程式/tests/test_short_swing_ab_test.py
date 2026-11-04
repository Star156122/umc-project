import copy
import json
import unittest
from pathlib import Path

from hybrid.core.short_swing_ab_test import (
    EXIT_LOOKBACK, VARIANTS, entry_components, validate_experiment_config,
)
from hybrid.scripts.run_short_swing_ab_test import _variant_candidate
from trading_system import technical_v1 as tv1


ROOT = Path(__file__).resolve().parents[1]


class ShortSwingVariantRuleTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "BREAKOUT_HIGH": 100.0,
            "MA_FAST": 110.0,
            "MA_MID": 105.0,
            "MA_SLOW": 90.0,
            "BB_UPPER": 108.0,
            "TREND_MA": 104.0,
            "TREND_SLOPE": 0.1,
        }
        self.previous = {"CLOSE": 100.0, "BREAKOUT_HIGH": 100.0}

    def test_original_requires_bollinger_and_ma120_slope(self):
        components = entry_components(109.0, self.snapshot, self.previous)
        self.assertTrue(components.passes("A_ORIGINAL_V2B"))
        no_bb = entry_components(107.0, self.snapshot, self.previous)
        self.assertFalse(no_bb.passes("A_ORIGINAL_V2B"))
        self.assertTrue(no_bb.passes("B_NO_BOLLINGER"))

    def test_c_removes_only_ma120_and_slope_after_b(self):
        no_trend = dict(self.snapshot, TREND_MA=111.0, TREND_SLOPE=-0.1)
        components = entry_components(109.0, no_trend, self.previous)
        self.assertFalse(components.passes("B_NO_BOLLINGER"))
        self.assertTrue(components.passes("C_NO_MA120"))
        self.assertTrue(components.passes("D_EXIT55"))
        self.assertTrue(components.passes("E_EXIT80"))

    def test_breakout_cross_and_ma240_still_apply_to_all_versions(self):
        failed_cross = {"CLOSE": 101.0, "BREAKOUT_HIGH": 100.0}
        components = entry_components(109.0, self.snapshot, failed_cross)
        self.assertFalse(any(components.passes(v) for v in VARIANTS))
        below_ma240 = entry_components(89.0, self.snapshot, self.previous)
        self.assertFalse(any(below_ma240.passes(v) for v in VARIANTS))

    def test_exit_variants_only_change_exit_lookback(self):
        config = tv1.read_config(ROOT / "configs/technical_v1_research.json")
        _, _, frozen = tv1.candidate_by_id(config, "BR_SENS_RANGE55_BB20_2")
        original = copy.deepcopy(frozen)
        expected = {"A_ORIGINAL_V2B": 30, "B_NO_BOLLINGER": 30, "C_NO_MA120": 30,
                    "D_EXIT55": 55, "E_EXIT80": 80}
        self.assertEqual(EXIT_LOOKBACK, expected)
        for variant, lookback in expected.items():
            made = _variant_candidate(frozen, variant)
            self.assertEqual(made["params"]["breakout_exit_period"], lookback)
            self.assertEqual(made["params"]["breakout_entry_period"], original["params"]["breakout_entry_period"])
            self.assertEqual(made["params"]["bollinger_period"], original["params"]["bollinger_period"])
            self.assertEqual(made["params"]["ma_fast_period"], original["params"]["ma_fast_period"])
            self.assertEqual(made["params"]["ma_mid_period"], original["params"]["ma_mid_period"])
            self.assertEqual(made["params"]["ma_slow_period"], original["params"]["ma_slow_period"])
            if variant in {"A_ORIGINAL_V2B", "B_NO_BOLLINGER"}:
                self.assertTrue(made["params"]["require_trend_filter"])
            else:
                self.assertFalse(made["params"]["require_trend_filter"])
        self.assertEqual(frozen, original)

    def test_config_protects_period_risk_and_final_oos_policy(self):
        config = json.loads((ROOT / "hybrid/config/short_swing_ab_test_v1.json").read_text(encoding="utf-8"))
        validate_experiment_config(config)
        invalid = copy.deepcopy(config)
        invalid["period"]["end"] = "2026-07-01"
        with self.assertRaises(ValueError):
            validate_experiment_config(invalid)
        invalid = copy.deepcopy(config)
        invalid["risk"]["risk_per_trade"] = 0.02
        with self.assertRaises(ValueError):
            validate_experiment_config(invalid)


if __name__ == "__main__":
    unittest.main()

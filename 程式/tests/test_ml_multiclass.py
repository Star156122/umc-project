import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.models import evaluate_multiclass
from ml.trading_pipeline import load_trading_plan


class MlMulticlassTests(unittest.TestCase):
    def test_v2_plan_has_cost_aware_three_classes(self):
        plan = load_trading_plan(ROOT / "configs/ml_trading_v2_20261004.json")
        self.assertEqual(plan["target"]["classification"], "multiclass")
        self.assertEqual(plan["target"]["labels"], {"0": "SELL", "1": "HOLD", "2": "BUY"})
        self.assertEqual(plan["holdout"]["access"], "forbidden")

    def test_multiclass_metrics_count_predicted_classes(self):
        y = np.array([0, 1, 2])
        probability = np.array([[.8, .1, .1], [.1, .7, .2], [.1, .2, .7]])
        result = evaluate_multiclass(y, probability)
        self.assertEqual(result["accuracy"], 1.0)
        self.assertEqual(result["predicted_class_counts"], {"0": 1, "1": 1, "2": 1})

    def test_v3_changes_features_only(self):
        v2 = load_trading_plan(ROOT / "configs/ml_trading_v2_20261004.json")
        v3 = load_trading_plan(ROOT / "configs/ml_trading_v3_20261004.json")
        self.assertGreater(len(v3["features"]), len(v2["features"]))
        self.assertTrue(set(v2["features"]).issubset(v3["features"]))
        self.assertEqual(v3["target"], v2["target"])
        self.assertEqual(v3["trading"], v2["trading"])
        self.assertEqual(v3["models"], v2["models"])


if __name__ == "__main__":
    unittest.main()

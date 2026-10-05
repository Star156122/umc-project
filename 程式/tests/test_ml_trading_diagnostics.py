import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.trading_diagnostics import prediction_frame, probability_diagnostics


class MlTradingDiagnosticsTests(unittest.TestCase):
    def test_probability_thresholds_create_three_signal_buckets(self):
        meta = pd.DataFrame({"stock_code": ["2303"] * 3, "signal_date": ["a", "b", "c"]})
        frame = prediction_frame("rf", "development", meta, np.array([1, 0, 0]), np.array([.60, .50, .45]), .60, .45)
        self.assertEqual(frame["signal"].tolist(), ["BUY", "HOLD", "SELL"])

    def test_zero_buy_reason_uses_max_probability(self):
        meta = pd.DataFrame({"stock_code": ["2330"] * 2, "signal_date": ["a", "b"]})
        frame = prediction_frame("rf", "development", meta, np.array([0, 1]), np.array([.20, .59]), .60, .45)
        result = probability_diagnostics(frame, .60)["2330"]
        self.assertEqual(result["signal_counts"]["BUY"], 0)
        self.assertIn("0.5900", result["zero_buy_reason"])


if __name__ == "__main__":
    unittest.main()

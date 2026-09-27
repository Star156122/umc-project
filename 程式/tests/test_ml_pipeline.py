import json
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from ml.data_pipeline import engineer_features, load_plan
from trading_system.research_guard import HoldoutLockedError


class MlPipelineTests(unittest.TestCase):
    def test_plan_is_development_and_holdout_declaration_is_not_read(self):
        plan = load_plan(ROOT / "configs/ml_baseline_20260927.json")
        self.assertEqual(plan["data_role"], "ml_development_seen")
        self.assertEqual(plan["holdout"]["access"], "forbidden")

    def test_plan_with_holdout_as_active_period_is_blocked(self):
        path = ROOT / "configs/ml_baseline_20260927.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["period"] = {"start": "2025-07-01", "end": "2025-12-31"}
        temp = ROOT / "configs/_test_ml_holdout_plan.json"
        temp.write_text(json.dumps(plan), encoding="utf-8")
        try:
            with self.assertRaises(HoldoutLockedError): load_plan(temp)
        finally:
            temp.unlink(missing_ok=True)

    def test_invalid_and_duplicate_bars_are_counted(self):
        base = pd.Timestamp("2026-01-02 09:00", tz="Asia/Taipei")
        rows = []
        for i in range(70):
            rows.append({"stock_code": "TEST", "kbar_timestamp": int((base + pd.Timedelta(minutes=5*i)).timestamp()), "datetime": base + pd.Timedelta(minutes=5*i), "open": 100+i*.1, "high": 101+i*.1, "low": 99+i*.1, "close": 100.5+i*.1, "volume": 1000+i})
        rows.append(dict(rows[10]))
        bad = dict(rows[11]); bad["kbar_timestamp"] += 1; bad["high"] = 1; rows.append(bad)
        names = load_plan(ROOT / "configs/ml_baseline_20260927.json")["features"]
        _, audit = engineer_features(pd.DataFrame(rows), names)
        self.assertEqual(audit["duplicates_removed"], 1)
        self.assertEqual(audit["invalid_ohlcv_removed"], 1)


if __name__ == "__main__": unittest.main()


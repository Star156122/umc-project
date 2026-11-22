import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hybrid.core.web_dataset_preflight import MODELS, preflight_dataset


TZ = ZoneInfo("Asia/Taipei")


def bars(day: str, count: int = 54) -> list[tuple]:
    start = datetime.fromisoformat(day + "T09:00:00").replace(tzinfo=TZ)
    return [
        ("2303", 5, "2024-11-29", "2024-12-02", int((start + timedelta(minutes=5 * i)).timestamp()),
         50.0, 51.0, 49.0, 50.5, 100)
        for i in range(count)
    ]


def prediction(model: str, signal_day: str, entry_day: str) -> dict:
    return {
        "stock_code": "2303", "model": model, "signal_date": signal_day,
        "entry_date": entry_day, "decision_time": signal_day + "T13:31:00+08:00",
        "prob_sell": 0.2, "prob_hold": 0.5, "prob_buy": 0.3, "prediction": "HOLD",
        "data_role": "validation", "training_target_max": "2024-08-31",
        "prediction_source": "fixed_2023_2024_new_stock_inference",
    }


class WebDatasetPreflightTests(unittest.TestCase):
    def make_fixture(self, root: Path, *, monday_bars: int = 54) -> dict:
        database = root / "history.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "CREATE TABLE market_kbars(stock_code TEXT,freq_minutes INTEGER,range_start TEXT,"
                "range_end TEXT,kbar_timestamp INTEGER,open REAL,high REAL,low REAL,close REAL,volume INTEGER)"
            )
            connection.executemany("INSERT INTO market_kbars VALUES(?,?,?,?,?,?,?,?,?,?)", bars("2024-11-29"))
            if monday_bars:
                connection.executemany(
                    "INSERT INTO market_kbars VALUES(?,?,?,?,?,?,?,?,?,?)",
                    bars("2024-12-02", monday_bars),
                )
            connection.commit()
        (root / "index.json").write_text(json.dumps([
            {"date": "2024-11-29", "close": 23000},
            {"date": "2024-12-02", "close": 23100},
        ]), encoding="utf8")
        payload = {
            model: [
                prediction(model, "2024-11-28", "2024-11-29"),
                prediction(model, "2024-11-29", "2024-12-02"),
            ]
            for model in MODELS
        }
        (root / "predictions.json").write_text(json.dumps(payload), encoding="utf8")
        spec = {
            "stock_name": "聯電", "warmup_start": "2024-11-29",
            "start": "2024-11-29", "end": "2024-12-02",
            "database": "history.sqlite3", "index": "index.json",
            "prediction_kind": "fixed_json", "predictions": "predictions.json",
            "events": [], "allowed_partial_sessions": [], "allowed_prediction_gaps": [],
        }
        return {"stock_universe": ["2303"], "datasets": {"2303": spec}}

    def test_sunday_is_not_treated_as_a_missing_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = preflight_dataset(root, self.make_fixture(root), "2303")
            self.assertEqual(report["complete_sessions"], 2)
            self.assertNotIn("2024-12-01", report["partial_sessions"])

    def test_missing_official_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "Missing entire official market sessions"):
                preflight_dataset(root, self.make_fixture(root, monday_bars=0), "2303")

    def test_partial_session_must_be_explicitly_declared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.make_fixture(root, monday_bars=53)
            with self.assertRaisesRegex(ValueError, "Partial-session declaration mismatch"):
                preflight_dataset(root, config, "2303")
            config["datasets"]["2303"]["allowed_partial_sessions"] = ["2024-12-02"]
            report = preflight_dataset(root, config, "2303")
            self.assertEqual(report["partial_sessions"]["2024-12-02"]["missing_bar_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

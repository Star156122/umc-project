from __future__ import annotations

import sqlite3
import unittest
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from hybrid.scripts.run_representative_benchmark_exp001 import (
    CORE_UNIVERSE,
    SUBJECTS,
    build_target_regime_context,
    classify_incremental,
    entry_schedule_key,
    read_subject_rows,
    summarize,
)


class RepresentativeBenchmarkExp001Tests(unittest.TestCase):
    def test_proxy_context_is_emitted_only_for_declared_subject_stocks(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE market_kbars (stock_code TEXT, freq_minutes INTEGER, kbar_timestamp INTEGER, close REAL)"
        )
        tz = ZoneInfo("Asia/Taipei")
        days = []
        current = date(2023, 1, 2)
        while len(days) < 23:
            if current.weekday() < 5:
                days.append(current)
            current += timedelta(days=1)
        for day_idx, day in enumerate(days):
            for stock_idx, stock in enumerate(CORE_UNIVERSE):
                for bar_idx in range(54):
                    hh = 9 + (bar_idx * 5) // 60
                    mm = (bar_idx * 5) % 60
                    when = datetime.combine(day, time(hh, mm), tzinfo=tz)
                    close = 50 + stock_idx * 5 + day_idx + bar_idx / 100
                    connection.execute(
                        "INSERT INTO market_kbars VALUES (?,5,?,?)",
                        (stock, int(when.timestamp()), close),
                    )
        date_key = days[-1].isoformat()
        contexts, audit = build_target_regime_context(connection, {date_key})
        self.assertEqual(set(stock for stock, _ in contexts), set(SUBJECTS))
        self.assertEqual(len(contexts), len(SUBJECTS))
        self.assertEqual(audit["proxy_only_codes"], ["2379", "3034"])
        self.assertEqual(contexts[("2330", date_key)]["history_count"], 20)
        self.assertEqual(contexts[("2330", date_key)]["eligible"], True)

    def test_classification_uses_paired_return_drawdown_and_sharpe(self):
        technical = pd.DataFrame([
            {"stock_code": s, "fold_id": f, "total_return": 0.10, "max_drawdown": 0.20, "sharpe_ratio": 0.5}
            for s in SUBJECTS for f in ("WF1", "WF2", "WF3")
        ])
        hybrid = technical.copy()
        hybrid["total_return"] += 0.01
        hybrid["max_drawdown"] -= 0.01
        hybrid["sharpe_ratio"] += 0.1
        classification, evidence = classify_incremental(technical, hybrid)
        self.assertEqual(classification, "CLEARLY_STRONGER")
        self.assertEqual(evidence["paired_cells"], 9)

    def test_common_schedule_normalizes_timestamps_to_taipei(self):
        self.assertEqual(
            entry_schedule_key("2023-07-05T09:05:00+08:00"),
            "2023-07-05T09:05:00.000000+08:00",
        )

    def test_summary_aggregates_metrics_without_dataframe_method_collision(self):
        frame = pd.DataFrame([
            {"strategy": "TECHNICAL_ONLY", "model": "ALL", "stock_code": stock,
             "stock_name": stock, "fold_id": fold, "total_return": 0.1,
             "max_drawdown": 0.2, "sharpe_ratio": 0.5, "trade_count": 2,
             "win_rate": 0.5, "gross_profit": 100.0, "gross_loss": -50.0,
             "net_pnl": 40.0, "gross_pnl": 50.0, "transaction_cost": 10.0,
             "final_equity": 100040.0, "average_capital_exposure": 0.3,
             "maximum_capital_exposure": 0.4}
            for stock in SUBJECTS for fold in ("WF1", "WF2", "WF3")
        ])
        got = summarize(frame, ["strategy", "model"]).iloc[0]
        self.assertEqual(got["account_count"], 9)
        self.assertAlmostEqual(got["worst_max_drawdown"], 0.2)
        self.assertAlmostEqual(got["profit_factor"], 2.0)


if __name__ == "__main__":
    unittest.main()

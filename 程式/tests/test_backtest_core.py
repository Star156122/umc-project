from __future__ import annotations

import importlib
import json
import sys
import tempfile
import types
import unittest
import sqlite3
from pathlib import Path


def install_dependency_stubs() -> None:
    """Allow core logic tests to run even when broker/indicator packages are absent."""

    try:
        importlib.import_module("polars")
    except ModuleNotFoundError:
        sys.modules["polars"] = types.ModuleType("polars")
    try:
        importlib.import_module("polars_talib")
    except ModuleNotFoundError:
        sys.modules["polars_talib"] = types.ModuleType("polars_talib")
    try:
        importlib.import_module("shioaji")
    except ModuleNotFoundError:
        shioaji = types.ModuleType("shioaji")
        shioaji.Shioaji = object
        sys.modules["shioaji"] = shioaji
    try:
        importlib.import_module("dotenv")
    except ModuleNotFoundError:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = dotenv

    try:
        importlib.import_module("tsst.broker")
        importlib.import_module("tsst.main")
        return
    except ModuleNotFoundError:
        pass

    tsst_package = types.ModuleType("tsst")
    tsst_broker = types.ModuleType("tsst.broker")
    tsst_main = types.ModuleType("tsst.main")

    class QuoteManager:
        pass

    class Tsst:
        def __init__(self, **kwargs):
            self.is_backtest = kwargs.get("is_backtest", True)
            self.quote_obj = None

        def on_order(self, *args, **kwargs):
            return None

        def on_deal(self, *args, **kwargs):
            return None

    tsst_broker.QuoteManager = QuoteManager
    tsst_main.Tsst = Tsst
    sys.modules.setdefault("tsst", tsst_package)
    sys.modules.setdefault("tsst.broker", tsst_broker)
    sys.modules.setdefault("tsst.main", tsst_main)


install_dependency_stubs()
main02 = importlib.import_module("main02")
backtest_module = importlib.import_module("trading_system.backtest")
update_data = importlib.import_module("update_data")
import_existing_report = importlib.import_module("import_existing_report")


class CaptureBacktest(main02.BacktestSafeTsst):
    def __init__(self):
        super().__init__(is_backtest=True)
        self.orders = []
        self.deals = []

    def on_order(self, sender, response, **kwargs):
        self.orders.append(response)

    def on_deal(self, sender, response, **kwargs):
        self.deals.append(response)


class BacktestCoreTests(unittest.TestCase):
    def test_existing_report_importer_excludes_test_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            path.write_text(
                json.dumps({"run_name": "one_week_test", "strategy": "ma"}),
                encoding="utf-8",
            )
            self.assertTrue(import_existing_report.is_test_report(path))

            path.write_text(
                json.dumps({"run_name": "2303_ma", "strategy": "ma"}),
                encoding="utf-8",
            )
            self.assertFalse(import_existing_report.is_test_report(path))

    def test_database_upload_is_disabled_by_default(self):
        result = main02.save_backtest_to_database(
            main02.AppConfig(), {}, [], [], [], Path("report.html")
        )
        self.assertIsNone(result)

    def test_equity_curve_accumulates_net_pnl(self):
        curve = main02._build_equity_curve(
            [
                {"sell_datetime": "2026-01-06 10:45:00", "net_pnl": -236.25},
                {"sell_datetime": "2026-01-07 13:20:00", "net_pnl": 500.0},
            ],
            100_000,
        )
        self.assertEqual(curve[0]["assets"], 99_763.75)
        self.assertEqual(curve[1]["assets"], 100_263.75)

    def test_multi_stock_codes_are_trimmed_and_deduplicated(self):
        self.assertEqual(
            update_data.parse_stock_codes("2303", "2303, 2330,2303, 2317"),
            ["2303", "2330", "2317"],
        )

    def test_database_coverage_reports_each_stock_range_and_tick_count(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "coverage.sqlite3"
            connection = sqlite3.connect(database)
            try:
                cursor = connection.cursor()
                cursor.execute(
                    "CREATE TABLE market_ticks (code TEXT, trade_date TEXT)"
                )
                cursor.executemany(
                    "INSERT INTO market_ticks VALUES (?, ?)",
                    [("2303", "2026-01-02"), ("2303", "2026-01-05"), ("2330", "2026-01-03")],
                )
                cursor.close()
                connection.commit()
            finally:
                connection.close()

            coverage = update_data.database_coverage(str(database))

        self.assertEqual(
            coverage,
            [("2303", "2026-01-02", "2026-01-05", 2), ("2330", "2026-01-03", "2026-01-03", 1)],
        )

    def test_all_six_profiles_load_from_json(self):
        profiles = ["2303_ma", "2303_rsi", "2303_macd", "2303_bollinger", "2303_breakout", "2303_vote"]
        strategies = []
        for profile in profiles:
            values = main02.load_backtest_config(main02.CONFIG_FILE, profile)
            strategies.append(values["strategy"])
            self.assertEqual(values["code"], "2303")
            self.assertEqual(values["initial_capital"], 100_000)
            self.assertTrue(values["use_data_cache"])
        self.assertEqual(strategies, ["ma", "rsi", "macd", "bollinger", "breakout", "vote"])

    def test_cash_and_transaction_costs_are_applied_at_fill(self):
        config = main02.AppConfig(initial_capital=100_000)
        engine = CaptureBacktest()
        engine.configure_local_account(config)
        engine.last_tick = {"close": 50.0, "timestamp": 1.0}

        buy = engine._create_local_order(
            "2303",
            "Stock",
            {"action": "Buy", "price_type": "MKT", "price": 0, "quantity": 1000},
        )
        self.assertEqual(buy["operation_status_code"], "BT-00000")
        self.assertAlmostEqual(engine.local_cash, 49_928.75)
        self.assertEqual(engine.local_position, 1000)

        engine.last_tick = {"close": 55.0, "timestamp": 2.0}
        sell = engine._create_local_order(
            "2303",
            "Stock",
            {"action": "Sell", "price_type": "MKT", "price": 0, "quantity": 1000},
        )
        self.assertEqual(sell["operation_status_code"], "BT-00000")
        self.assertAlmostEqual(engine.local_cash, 104_767.875)
        self.assertEqual(engine.local_position, 0)
        self.assertAlmostEqual(engine.local_trades[-1]["tax"], 82.5)

    def test_overnight_sell_uses_general_stock_tax_rate(self):
        config = main02.AppConfig(initial_capital=100_000)
        engine = CaptureBacktest()
        engine.configure_local_account(config)
        engine.last_tick = {
            "close": 50.0,
            "timestamp": main02.taipei_timestamp(main02.datetime(2026, 1, 2, 10, 0)),
        }
        engine._create_local_order(
            "2303",
            "Stock",
            {"action": "Buy", "price_type": "MKT", "price": 0, "quantity": 1000},
        )
        engine.last_tick = {
            "close": 55.0,
            "timestamp": main02.taipei_timestamp(main02.datetime(2026, 1, 5, 10, 0)),
        }
        engine._create_local_order(
            "2303",
            "Stock",
            {"action": "Sell", "price_type": "MKT", "price": 0, "quantity": 1000},
        )

        self.assertAlmostEqual(engine.local_trades[-1]["tax"], 165.0)

    def test_order_is_rejected_when_initial_capital_is_not_enough(self):
        config = main02.AppConfig(initial_capital=100_000)
        engine = CaptureBacktest()
        engine.configure_local_account(config)
        engine.last_tick = {"close": 200.0, "timestamp": 1.0}
        response = engine._create_local_order(
            "2303",
            "Stock",
            {"action": "Buy", "price_type": "MKT", "price": 0, "quantity": 1000},
        )
        self.assertEqual(response["operation_status_code"], "LOCAL-INSUFFICIENT-CASH")
        self.assertEqual(engine.local_cash, 100_000)
        self.assertEqual(engine.local_trades, [])

    def test_performance_formula_includes_fee_and_tax(self):
        rows = [
            {
                "sell_datetime": "2026-01-02 13:20:00",
                "gross_pnl": 5_000.0,
                "fee": 149.625,
                "tax": 165.0,
                "net_pnl": 4_685.375,
            },
            {
                "sell_datetime": "2026-01-03 13:20:00",
                "gross_pnl": -2_000.0,
                "fee": 140.0,
                "tax": 144.0,
                "net_pnl": -2_284.0,
            },
        ]
        metrics = main02.calculate_performance_metrics(rows, 100_000)
        self.assertAlmostEqual(metrics["gross_pnl"], 3_000.0)
        self.assertAlmostEqual(metrics["transaction_cost"], 598.625)
        self.assertAlmostEqual(metrics["net_pnl"], 2_401.375)
        self.assertAlmostEqual(metrics["ending_capital"], 102_401.375)
        self.assertGreater(metrics["max_drawdown"], 0)

    def test_sqlite_tick_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "ticks.sqlite3"
            config = main02.AppConfig(
                code="2303",
                backtest_start="2026-01-02",
                backtest_end="2026-01-02",
                data_cache_path=str(database),
                use_data_cache=True,
            )
            ticks = [
                {
                    "timestamp": 1_767_326_400.0,
                    "close": 45.5,
                    "qty": 10,
                    "tick_type": 1,
                    "code": "2303",
                },
                {
                    "timestamp": 1_767_326_401.0,
                    "close": 45.6,
                    "qty": 20,
                    "tick_type": 2,
                    "code": "2303",
                },
            ]
            connection = main02.open_tick_cache(config)
            main02._save_cached_day(connection, "2303", "2026-01-02", ticks)
            connection.close()

            self.assertTrue(main02.is_cache_range_complete(config))
            loaded = main02.fetch_sinopac_ticks(None, config)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[1]["close"], 45.6)

    def test_report_contains_required_capital_cost_and_date_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_report_dir = backtest_module.REPORT_DIR
            backtest_module.REPORT_DIR = Path(directory)
            try:
                config = main02.AppConfig(
                    code="2303",
                    strategy="ma",
                    run_name="report_test",
                    backtest_start="2026-01-01",
                    backtest_end="2026-06-30",
                )
                engine = CaptureBacktest()
                engine.configure_local_account(config)
                report_path = main02.generate_local_report(engine, config, [])
                report = report_path.read_text(encoding="utf-8")
                summary = (report_path.parent / "summary.json").read_text(encoding="utf-8")
            finally:
                backtest_module.REPORT_DIR = previous_report_dir

            self.assertIn("初始本金", report)
            self.assertIn("回測開始日期", report)
            self.assertIn("手續費合計", report)
            self.assertIn("淨損益公式", report)
            self.assertIn('"final_assets"', summary)

    def test_new_strategy_rules(self):
        config = main02.AppConfig(strategy="bollinger")
        strategy = main02.MovingAverageTsst(config=config, is_backtest=True)
        strategy.previous_snapshot = {"CLOSE": 44.0, "BB_LOWER": 45.0}
        snapshot = {"BB_LOWER": 45.2, "BB_MIDDLE": 47.0, "BB_UPPER": 48.8}
        self.assertTrue(strategy._bollinger_entry_reasons(45.5, snapshot))

        breakout = main02.MovingAverageTsst(
            config=main02.AppConfig(strategy="breakout"),
            is_backtest=True,
        )
        self.assertTrue(
            breakout._breakout_entry_reasons(
                50.1,
                {"BREAKOUT_HIGH": 50.0, "BREAKOUT_LOW": 45.0},
            )
        )

        vote = main02.MovingAverageTsst(
            config=main02.AppConfig(strategy="vote", use_rsi_block=False),
            is_backtest=True,
        )
        reasons = vote._vote_entry_reasons(
            51.0,
            {
                "MA_FAST": 50.0,
                "MA_MID": 49.0,
                "MA_SLOW": 48.0,
                "RSI": 65.0,
                "MACD": -1.0,
                "MACD_SIGNAL": 0.0,
            },
        )
        self.assertTrue(reasons)


if __name__ == "__main__":
    unittest.main()

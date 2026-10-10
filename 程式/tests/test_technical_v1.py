from __future__ import annotations

import copy
import contextlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from trading_system.backtest import AppConfig, add_indicators, taipei_timestamp
from trading_system.technical_v1 import (
    TechnicalV1Error,
    TechnicalV1Strategy,
    assert_complete_execution_matrix,
    assert_execution_ready,
    assert_role_can_execute,
    assert_shared_parameter_assignments,
    build_app_config,
    candidate_by_id,
    candidate_fingerprint,
    export_candidate_signals,
    fingerprint,
    initialize_market_schema,
    inspect_training_market_data,
    read_config,
    run_training_workflow,
    validate_candidate_signal_row,
    validate_config,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs/technical_v1_research.json"
TAIPEI = ZoneInfo("Asia/Taipei")


def strategy_for(config: dict, candidate_id: str) -> TechnicalV1Strategy:
    family_name, family, candidate = candidate_by_id(config, candidate_id)
    app = build_app_config(config, candidate, "2303", "2023-01-01", "2024-12-31")
    strategy = TechnicalV1Strategy(
        app,
        study_config=config,
        family_name=family_name,
        family=family,
        candidate=candidate,
        data_role="training",
        fold_id="WF1",
        is_backtest=True,
        is_simulation=True,
        local_only=True,
    )
    strategy.configure_local_account(app)
    return strategy


def snapshot(**overrides: float) -> dict[str, float]:
    values = {
        "OPEN": 100.0,
        "HIGH": 102.0,
        "LOW": 99.0,
        "CLOSE": 101.5,
        "VOLUME": 1200.0,
        "MA_FAST": 101.0,
        "MA_MID": 100.5,
        "MA_SLOW": 100.0,
        "TREND_MA": 99.0,
        "TREND_SLOPE": 1.0,
        "RSI": 62.0,
        "MACD": 0.20,
        "MACD_SIGNAL": 0.10,
        "MACD_HIST": 0.10,
        "BB_MIDDLE": 99.0,
        "BB_UPPER": 101.0,
        "BB_LOWER": 97.0,
        "BREAKOUT_HIGH": 100.8,
        "BREAKOUT_LOW": 96.0,
        "VOLUME_MA": 1000.0,
    }
    values.update(overrides)
    return values


def compact_preflight_config(config: dict) -> dict:
    changed = copy.deepcopy(config)
    changed["data_quality"].update({
        "minimum_warmup_bars": 10,
        "minimum_evaluation_bars": 10,
        "minimum_training_trading_days": 4,
        "minimum_evaluation_trading_days": 1,
    })
    return changed


def create_compact_market_database(path: Path, config: dict) -> None:
    with contextlib.closing(sqlite3.connect(path)):
        pass
    initialize_market_schema(path)
    trading_days = (
        date(2023, 1, 3),
        date(2023, 7, 3),
        date(2024, 1, 3),
        date(2024, 7, 3),
    )
    rows = []
    for stock in config["stock_universe"]["stock_codes"]:
        for trading_day in trading_days:
            first = datetime.combine(trading_day, time(9, 0), tzinfo=TAIPEI)
            for index in range(54):
                stamp = int((first + timedelta(minutes=5 * index)).timestamp())
                price = 100.0 + index / 100
                rows.append((
                    stock, 5, "2023-01-01", "2024-12-31", stamp,
                    price, price + 1, price - 1, price + 0.2, 1000 + index,
                ))
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.executemany(
            """INSERT INTO market_kbars(
                   stock_code,freq_minutes,range_start,range_end,kbar_timestamp,
                   open,high,low,close,volume
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        connection.commit()


class TechnicalV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = read_config(CONFIG_PATH)

    def test_config_is_technical_only_and_execution_ready_for_training(self) -> None:
        self.assertEqual(self.config["scope"], "technical_strategy_only")
        self.assertEqual(
            tuple(self.config["strategy_families"]),
            ("trend_momentum", "breakout", "majority_vote"),
        )
        self.assertEqual(self.config["stock_universe"]["status"], "confirmed")
        assert_execution_ready(self.config)

    def test_locked_roles_are_always_rejected(self) -> None:
        for role in ("additional_holdout", "final_out_of_sample"):
            with self.assertRaises(TechnicalV1Error):
                assert_role_can_execute(self.config, role)

    def test_validation_requires_frozen_manifest_authorization_and_access_log(self) -> None:
        with self.assertRaises(TechnicalV1Error):
            assert_role_can_execute(self.config, "validation")
        frozen = copy.deepcopy(self.config)
        frozen["status"] = "FROZEN_RESEARCH_CANDIDATE"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "freeze.json"
            access_log = root / "access.log"
            manifest.write_text(json.dumps({
                "status": "FROZEN_RESEARCH_CANDIDATE",
                "config_fingerprint": fingerprint(frozen),
            }), encoding="utf-8")
            access_log.write_text("prepared\n", encoding="utf-8")
            assert_role_can_execute(
                frozen,
                "validation",
                validation_authorized=True,
                freeze_manifest_path=manifest,
                access_log_path=access_log,
            )

    def test_development_seen_is_forbidden_this_round(self) -> None:
        with self.assertRaises(TechnicalV1Error):
            assert_role_can_execute(self.config, "development_seen")

    def test_runner_rejects_every_non_training_role(self) -> None:
        for role in ("validation", "development_seen", "additional_holdout", "final_out_of_sample"):
            with self.assertRaises(TechnicalV1Error):
                assert_execution_ready(self.config, role)

    def test_walk_forward_rejects_random_or_overlapping_time(self) -> None:
        changed = copy.deepcopy(self.config)
        changed["walk_forward_folds"][1]["evaluate_start"] = "2023-12-01"
        with self.assertRaises(TechnicalV1Error):
            validate_config(changed)

    def test_missing_database_is_blocked_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "missing.sqlite3"
            report = inspect_training_market_data(root, self.config, database)
            self.assertEqual(report["status"], "BLOCKED_MISSING_DATA")
            self.assertFalse(database.exists())
            self.assertIn("import_technical_v1_training_kbars.py", report["import_command"])

    def test_existing_empty_database_can_initialize_schema_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "empty.sqlite3"
            with contextlib.closing(sqlite3.connect(database)):
                pass
            first = inspect_training_market_data(
                Path(directory), self.config, database, initialize_schema=True
            )
            second = inspect_training_market_data(
                Path(directory), self.config, database, initialize_schema=True
            )
            self.assertTrue(first["schema_initialized"])
            self.assertEqual(first["status"], "BLOCKED_MISSING_DATA")
            self.assertEqual(second["status"], "BLOCKED_MISSING_DATA")
            with contextlib.closing(sqlite3.connect(database)) as connection:
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertIn("market_kbars", tables)

    def test_preflight_checks_daily_shape_folds_and_ohlcv(self) -> None:
        config = compact_preflight_config(self.config)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            create_compact_market_database(database, config)
            report = inspect_training_market_data(Path(directory), config, database)
            self.assertTrue(report["complete"])
            self.assertEqual(report["query_role"], "training")
            self.assertEqual(report["forbidden_roles_queried"], [])
            self.assertTrue(all(check["complete"] for check in report["stock_checks"]))

    def test_preflight_rejects_partial_day_duplicate_and_bad_ohlcv(self) -> None:
        config = compact_preflight_config(self.config)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            create_compact_market_database(database, config)
            with contextlib.closing(sqlite3.connect(database)) as connection:
                stamp = connection.execute(
                    "SELECT MIN(kbar_timestamp) FROM market_kbars WHERE stock_code='2303'"
                ).fetchone()[0]
                connection.execute(
                    "DELETE FROM market_kbars WHERE stock_code='2303' AND kbar_timestamp=?",
                    (stamp,),
                )
                original = connection.execute(
                    """SELECT stock_code,freq_minutes,kbar_timestamp,open,high,low,close,volume
                       FROM market_kbars WHERE stock_code='2330' LIMIT 1"""
                ).fetchone()
                connection.execute(
                    """INSERT INTO market_kbars VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        original[0], original[1], "duplicate", "duplicate", original[2],
                        original[3], original[4], original[5], original[6], original[7],
                    ),
                )
                connection.execute(
                    "UPDATE market_kbars SET high=1 WHERE stock_code='2454' AND kbar_timestamp=?",
                    (stamp,),
                )
                connection.execute(
                    """DELETE FROM market_kbars
                       WHERE stock_code='2317' AND kbar_timestamp>=? AND kbar_timestamp<?""",
                    (stamp, stamp + 86400),
                )
                connection.commit()
            report = inspect_training_market_data(Path(directory), config, database)
            self.assertFalse(report["complete"])
            checks = {row["stock_code"]: row for row in report["stock_checks"]}
            self.assertTrue(checks["2303"]["incomplete_trading_days"])
            self.assertGreater(checks["2330"]["duplicate_timestamp_count"], 0)
            self.assertGreater(checks["2454"]["invalid_ohlcv_count"], 0)
            self.assertTrue(checks["2317"]["missing_trading_days"])

    def test_missing_data_workflow_writes_audit_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "outputs"
            audit, run_directory = run_training_workflow(
                ROOT,
                CONFIG_PATH,
                market_data_path=root / "missing.sqlite3",
                preflight_only=True,
                output_root=output,
            )
            self.assertEqual(audit["status"], "BLOCKED_MISSING_DATA")
            self.assertEqual(
                [path.name for path in run_directory.iterdir()],
                ["technical_v1_audit.json"],
            )

    def test_each_family_has_only_baseline_and_one_sensitivity(self) -> None:
        for family in self.config["strategy_families"].values():
            self.assertLessEqual(len(family["candidates"]), 2)
            self.assertEqual(
                sum(candidate["kind"] == "baseline" for candidate in family["candidates"]),
                1,
            )

    def test_same_candidate_must_share_fingerprint_across_stocks(self) -> None:
        _, _, candidate = candidate_by_id(self.config, "TM_BASE_20_60_120_RSI55_70")
        candidate_hash = candidate_fingerprint(self.config, candidate)
        rows = [
            {
                "candidate_id": candidate["id"],
                "fold_id": "WF1",
                "stock_code": stock,
                "parameter_fingerprint": candidate_hash,
            }
            for stock in self.config["stock_universe"]["stock_codes"]
        ]
        assert_shared_parameter_assignments(rows, self.config["stock_universe"]["stock_codes"])
        rows[0]["parameter_fingerprint"] = "per-stock-override"
        with self.assertRaises(TechnicalV1Error):
            assert_shared_parameter_assignments(rows, self.config["stock_universe"]["stock_codes"])

    def test_complete_matrix_rejects_unseen_candidate_fold_combinations(self) -> None:
        rows = [{
            "candidate_id": "A",
            "fold_id": "WF1",
            "stock_code": stock,
            "parameter_fingerprint": "same",
            "status": "COMPLETED",
        } for stock in self.config["stock_universe"]["stock_codes"]]
        with self.assertRaises(TechnicalV1Error):
            assert_complete_execution_matrix(
                rows,
                expected_candidates=["A", "B"],
                expected_folds=["WF1"],
                expected_stocks=self.config["stock_universe"]["stock_codes"],
            )

    def test_legacy_baseline_preserves_original_risk_and_exit(self) -> None:
        candidate = self.config["legacy_baseline"]["candidate"]
        self.assertEqual(candidate["params"]["vote_exit_required"], 3)
        self.assertEqual(candidate["params"]["stop_loss_pct"], 0.03)
        self.assertEqual(candidate["params"]["max_hold_bars"], 0)
        app = build_app_config(self.config, candidate, "2303", "2023-07-01", "2023-12-31")
        self.assertEqual(app.vote_exit_required, 3)
        self.assertEqual(app.stop_loss_pct, 0.03)
        self.assertEqual(app.max_hold_bars, 0)
        _, _, majority = candidate_by_id(self.config, "MV_BASE_2_OF_3")
        self.assertEqual(majority["params"]["vote_exit_required"], 2)
        self.assertEqual(self.config["common_backtest"]["stop_loss_pct"], 0.025)

    def test_trend_momentum_requires_all_three_indicator_roles(self) -> None:
        strategy = strategy_for(self.config, "TM_BASE_20_60_120_RSI55_70")
        self.assertTrue(strategy._trend_momentum_entry_reasons(101.5, snapshot()))
        self.assertEqual(
            strategy._trend_momentum_entry_reasons(101.5, snapshot(RSI=75.0)),
            [],
        )
        self.assertEqual(
            strategy._trend_momentum_entry_reasons(
                101.5,
                snapshot(MACD=0.05, MACD_SIGNAL=0.10),
            ),
            [],
        )

    def test_breakout_uses_upper_band_not_lower_band_reversion(self) -> None:
        strategy = strategy_for(self.config, "BR_BASE_RANGE60_BB20_2")
        strategy.previous_snapshot = snapshot(CLOSE=100.0, BREAKOUT_HIGH=100.8)
        self.assertTrue(strategy._breakout_family_entry_reasons(101.5, snapshot()))
        self.assertEqual(
            strategy._breakout_family_entry_reasons(
                101.5,
                snapshot(BB_UPPER=102.0, BB_LOWER=101.4),
            ),
            [],
        )

    def test_breakout_reference_excludes_current_bar_high(self) -> None:
        frame = pl.DataFrame(
            {
                "Open": [9.5, 10.5, 14.0],
                "High": [10.0, 11.0, 15.0],
                "Low": [9.0, 10.0, 13.0],
                "Close": [9.8, 10.8, 14.8],
                "Volume": [100, 100, 100],
            }
        )
        app = AppConfig(
            ma_fast_period=1,
            ma_mid_period=2,
            ma_slow_period=3,
            trend_ma_period=2,
            trend_slope_lookback=1,
            breakout_entry_period=2,
            breakout_exit_period=2,
            bollinger_period=2,
        )
        result = add_indicators(frame, app)
        self.assertEqual(result["BREAKOUT_HIGH"][2], 11.0)
        self.assertNotEqual(result["BREAKOUT_HIGH"][2], 15.0)

    def test_majority_vote_keeps_two_of_three_baseline(self) -> None:
        strategy = strategy_for(self.config, "MV_BASE_2_OF_3")
        self.assertIn("長期趨勢", self.config["strategy_families"]["majority_vote"]["display_name"])
        reasons = strategy._entry_reasons(
            101.5,
            snapshot(MACD=0.05, MACD_SIGNAL=0.10),
        )
        self.assertTrue(reasons)
        self.assertIn("2/3", reasons[0])

    def test_maximum_holding_period_is_a_real_exit(self) -> None:
        strategy = strategy_for(self.config, "TM_BASE_20_60_120_RSI55_70")
        strategy.entry_price = 100.0
        strategy.bars_since_entry = 162
        signal = strategy._exit_signal(101.0, snapshot())
        self.assertIsNotNone(signal)
        self.assertEqual(signal[0], "max_holding_period")

    def test_stop_loss_uses_completed_bar_close_not_intrabar_low(self) -> None:
        strategy = strategy_for(self.config, "TM_BASE_20_60_120_RSI55_70")
        strategy.entry_price = 100.0
        strategy.bars_since_entry = 3
        self.assertIsNone(strategy._risk_exit_signal(100.0))
        self.assertIsNone(strategy._exit_signal(100.0, snapshot(LOW=90.0)))
        stop = strategy._risk_exit_signal(97.5)
        self.assertIsNotNone(stop)
        self.assertEqual(stop[0], "stop_loss")

    def test_warmup_bar_updates_indicator_state_without_trading(self) -> None:
        strategy = strategy_for(self.config, "TM_BASE_20_60_120_RSI55_70")
        evaluation_start = datetime(2023, 7, 1, tzinfo=TAIPEI)
        strategy.evaluation_start_timestamp = int(evaluation_start.timestamp())
        completed = {
            "kbar_timestamp": int(datetime(2023, 6, 30, 13, 25, tzinfo=TAIPEI).timestamp()),
            "Open": 100.0,
            "High": 102.0,
            "Low": 99.0,
            "Close": 101.5,
            "Volume": 1200,
            **{
                key: value
                for key, value in snapshot().items()
                if key not in {"OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"}
            },
        }
        strategy.process_completed_kbar("2303", evaluation_start, completed)
        self.assertIsNotNone(strategy.previous_snapshot)
        self.assertEqual(strategy.local_signals, [])
        self.assertEqual(strategy.local_trades, [])

    def test_candidate_signal_uses_completed_bar_then_next_bar_entry(self) -> None:
        strategy = strategy_for(self.config, "TM_BASE_20_60_120_RSI55_70")
        decision_time = datetime(2024, 1, 2, 9, 5, tzinfo=TAIPEI)
        strategy.last_tick = {
            "timestamp": taipei_timestamp(decision_time),
            "market_type": "Stock",
            "code": "2303",
            "close": 101.6,
            "qty": 0,
        }
        completed = {
            "Open": 100.0,
            "High": 102.0,
            "Low": 99.0,
            "Close": 101.5,
            "Volume": 1200,
            **{key: value for key, value in snapshot().items() if key not in {"OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"}},
        }
        strategy.process_completed_kbar("2303", decision_time, completed)
        self.assertEqual(len(strategy.candidate_signals), 1)
        row = strategy.candidate_signals[0]
        validate_candidate_signal_row(row)
        self.assertLess(row["signal_time"], row["earliest_entry_time"])
        self.assertEqual(row["close_at_signal"], 101.5)
        self.assertEqual(row["timezone"], "Asia/Taipei")
        self.assertTrue(row["signal_time"].endswith("+08:00"))
        self.assertFalse({"forward_return", "label", "target"}.intersection(row))

    def test_candidate_signal_rejects_future_evaluation_fields(self) -> None:
        row = {
            "technical_version": "test",
            "strategy_family": "trend_momentum",
            "candidate_id": "candidate",
            "parameter_fingerprint": "fingerprint",
            "stock_code": "2303",
            "data_role": "training",
            "fold_id": "WF1",
            "timezone": "Asia/Taipei",
            "signal_time": "2024-01-02T09:04:59.999999+08:00",
            "signal_bar_end_time": "2024-01-02T09:04:59.999999+08:00",
            "earliest_entry_time": "2024-01-02T09:05:00.000000+08:00",
            "technical_signal": "BUY_CANDIDATE",
            "entry_rule_id": "entry",
            "exit_rule_id": "exit",
            "close_at_signal": 100.0,
            "reason": "test",
            "forward_return": 0.01,
        }
        with self.assertRaises(TechnicalV1Error):
            validate_candidate_signal_row(row)

    def test_candidate_signal_export_refuses_overwrite(self) -> None:
        row = {
            "technical_version": "test",
            "strategy_family": "trend_momentum",
            "candidate_id": "candidate",
            "parameter_fingerprint": "fingerprint",
            "stock_code": "2303",
            "data_role": "training",
            "fold_id": "WF1",
            "timezone": "Asia/Taipei",
            "signal_time": "2024-01-02T09:04:59.999999+08:00",
            "signal_bar_end_time": "2024-01-02T09:04:59.999999+08:00",
            "earliest_entry_time": "2024-01-02T09:05:00.000000+08:00",
            "technical_signal": "BUY_CANDIDATE",
            "entry_rule_id": "entry",
            "exit_rule_id": "exit",
            "close_at_signal": 100.0,
            "reason": "test",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.csv"
            export_candidate_signals(path, [row])
            with self.assertRaises(FileExistsError):
                export_candidate_signals(path, [row])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from trading_system.technical_generalization import (
    CANONICAL_STOCKS,
    RESULT_FIELDS,
    GeneralizationError,
    aggregate_candidate,
    aggregate_training,
    assert_shared_parameter_assignments,
    candidate_fingerprint,
    inspect_market_cache,
    mark_parameter_spikes,
    parameter_fingerprints,
    planned_periods,
    planned_result_rows,
    read_config,
    resolve_market_data_path,
    run_workflow,
    select_candidate,
    validate_config,
    write_outputs,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/technical_generalization_20261006.json"


class TechnicalGeneralizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = read_config(CONFIG)

    def _rows(
        self,
        candidate_id: str = "B_ma_10_30_120",
        version: str = "B",
        trades: int = 5,
        returns: list[float] | None = None,
        pnl: list[float] | None = None,
    ) -> list[dict]:
        returns = returns or [0.03, 0.02, 0.01, -0.01, 0.0]
        pnl = pnl or [300, 200, 100, -50, 0]
        fingerprint = parameter_fingerprints(self.config)[candidate_id]
        rows = []
        for fold_index, fold in enumerate(("WF1", "WF2", "WF3")):
            for stock_index, stock in enumerate(CANONICAL_STOCKS):
                value = returns[stock_index] + fold_index * 0.001
                net = pnl[stock_index] + fold_index
                rows.append({
                    "period_role": "training",
                    "fold_id": fold,
                    "version": version,
                    "candidate_id": candidate_id,
                    "stock_code": stock,
                    "status": "COMPLETED",
                    "parameter_fingerprint": fingerprint,
                    "total_return": value,
                    "net_pnl": net,
                    "gross_pnl": net + 10,
                    "gross_profit": max(net + 20, 0),
                    "gross_loss": min(net - 20, -1),
                    "transaction_cost": 10,
                    "sharpe_ratio": value * 10,
                    "profit_factor": 1.2,
                    "max_drawdown": 0.08,
                    "win_rate": 0.5,
                    "completed_trades": trades,
                })
        return rows

    def _aggregate(self, rows: list[dict], **overrides) -> dict:
        selection = self.config["selection"]
        return aggregate_candidate(
            rows,
            overrides.get("per_fold", selection["minimum_trades_per_stock_fold"]),
            overrides.get("per_stock", selection["minimum_training_trades_per_stock"]),
            overrides.get("total", selection["minimum_total_trades"]),
            overrides.get("concentration", selection["maximum_single_stock_profit_concentration"]),
        )

    def test_roles_and_walk_forward_are_chronological(self):
        periods = self.config["periods"]
        self.assertEqual(periods["training"]["start"], "2023-01-01")
        self.assertEqual(periods["validation"]["end"], "2025-06-30")
        self.assertEqual(periods["development_seen"]["start"], "2026-01-01")
        previous_eval = ""
        for fold in self.config["walk_forward_folds"]:
            self.assertLess(fold["train_end"], fold["evaluate_start"])
            self.assertGreater(fold["evaluate_start"], previous_eval)
            previous_eval = fold["evaluate_end"]

    def test_locked_periods_never_enter_execution_plan(self):
        plans = planned_periods(self.config)
        self.assertEqual(
            {row["period_role"] for row in plans},
            {"training", "validation", "development_seen"},
        )
        for row in plans:
            self.assertFalse("2025-07-01" <= row["warmup_start"] <= "2025-12-31")
            self.assertLess(row["period_end"], "2026-07-01")

    def test_exact_five_stock_universe_is_required(self):
        changed = copy.deepcopy(self.config)
        changed["stock_codes"] = ["2303", "2330"]
        with self.assertRaises(GeneralizationError):
            validate_config(changed)

    def test_all_stocks_share_parameter_fingerprint(self):
        assert_shared_parameter_assignments(planned_result_rows(self.config, "NOT_RUN"))

    def test_single_fold_fingerprint_difference_is_detected(self):
        rows = self._rows()
        target = next(
            row for row in rows
            if row["fold_id"] == "WF1" and row["stock_code"] == "2303"
        )
        target["parameter_fingerprint"] = "different"
        with self.assertRaises(GeneralizationError):
            assert_shared_parameter_assignments(rows, expected_folds=("WF1", "WF2", "WF3"))

    def test_candidate_fold_missing_stock_is_detected(self):
        rows = [
            row for row in self._rows()
            if not (row["fold_id"] == "WF2" and row["stock_code"] == "2382")
        ]
        with self.assertRaises(GeneralizationError):
            assert_shared_parameter_assignments(rows, expected_folds=("WF1", "WF2", "WF3"))

    def test_parameter_change_changes_fingerprint(self):
        candidate = self.config["versions"]["B"]["candidates"][0]
        changed = copy.deepcopy(candidate)
        changed["params"]["ma_fast_period"] += 1
        self.assertNotEqual(
            candidate_fingerprint(self.config, candidate),
            candidate_fingerprint(self.config, changed),
        )

    def test_per_stock_per_fold_minimum_trades(self):
        rows = self._rows()
        target = next(
            row for row in rows
            if row["fold_id"] == "WF1" and row["stock_code"] == "2303"
        )
        target["completed_trades"] = 4
        result = self._aggregate(rows)
        self.assertFalse(result["eligible"])
        warning = next(
            item for item in result["trade_count_warnings"]
            if item["level"] == "stock_fold" and item["stock_code"] == "2303"
        )
        self.assertEqual((warning["actual_trades"], warning["required_trades"]), (4, 5))

    def test_training_total_minimum_per_stock(self):
        result = self._aggregate(self._rows(trades=3))
        warning = next(
            item for item in result["trade_count_warnings"]
            if item["level"] == "stock_training_total" and item["stock_code"] == "2303"
        )
        self.assertEqual((warning["actual_trades"], warning["required_trades"]), (9, 10))

    def test_total_minimum_trades(self):
        result = self._aggregate(self._rows(trades=1))
        warning = next(
            item for item in result["trade_count_warnings"]
            if item["level"] == "all_stocks_folds"
        )
        self.assertEqual((warning["actual_trades"], warning["required_trades"]), (15, 30))

    def test_profit_concentration_above_limit_is_not_selectable(self):
        result = self._aggregate(self._rows(
            pnl=[10000, 10, 10, 10, 10],
            returns=[0.08, 0.02, 0.02, 0.02, 0.02],
        ))
        self.assertGreater(result["single_stock_profit_concentration"], 0.60)
        self.assertTrue(result["profit_concentration_exceeded"])
        self.assertFalse(result["eligible"])
        self.assertIsNone(select_candidate("B", [result], self.config))
        self.assertAlmostEqual(result["return_median_excluding_2303"], 0.021)

    def test_training_aggregate_keeps_period_role(self):
        aggregates = aggregate_training(self._rows(), self.config)
        self.assertEqual(aggregates[0]["period_role"], "training")

    def test_possible_parameter_spike_is_marked_and_rejected(self):
        strong = self._aggregate(
            self._rows(returns=[0.20] * 5, pnl=[2000] * 5), concentration=1.0
        )
        neighbour = self._aggregate(
            self._rows(
                candidate_id="B_ma_20_60_240",
                returns=[0.01] * 5,
                pnl=[100] * 5,
            ),
            concentration=1.0,
        )
        mark_parameter_spikes([strong, neighbour], self.config)
        self.assertTrue(strong["possible_parameter_spike"])
        self.assertNotEqual(
            select_candidate("B", [strong, neighbour], self.config)["candidate_id"],
            strong["candidate_id"],
        )

    def test_development_seen_warmup_does_not_cross_locked_period(self):
        development = next(
            row for row in planned_periods(self.config)
            if row["period_role"] == "development_seen"
        )
        self.assertEqual(development["warmup_start"], "2026-01-01")
        self.assertEqual(development["warmup_mode"], "period_prefix")

    def test_single_alternative_database_is_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            archive = data / "archive"
            archive.mkdir()
            candidate = archive / "market_data_backup.sqlite3"
            candidate.touch()
            result = resolve_market_data_path(root, self.config)
            self.assertTrue(result["available"])
            self.assertEqual(Path(result["path"]), candidate)
            self.assertEqual(result["selection"], "single_discovered_alternative")

    def test_multiple_databases_require_explicit_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            first = data / "market_data_a.sqlite3"
            second = data / "market_data_b.sqlite3"
            first.touch()
            second.touch()
            result = resolve_market_data_path(root, self.config)
            self.assertFalse(result["available"])
            self.assertEqual(result["selection"], "ambiguous")
            self.assertIn("--market-data-path", result["reason"])
            explicit = resolve_market_data_path(root, self.config, first)
            self.assertTrue(explicit["available"])
            self.assertEqual(Path(explicit["path"]), first)

    def test_invalid_database_schema_is_failed_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            database = data / "market_data.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE market_kbars(stock_code TEXT)")
                connection.commit()
            finally:
                connection.close()
            market = inspect_market_cache(root, self.config)
            self.assertFalse(market["available"])
            self.assertEqual(market["failure_status"], "FAILED_EXECUTION")
            self.assertIn("缺少欄位", market["reason"])
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(self.config, ensure_ascii=False), encoding="utf-8"
            )
            audit, paths = run_workflow(root, config_path)
            self.assertEqual(audit["status"], "FAILED_EXECUTION")
            self.assertTrue(all(Path(path).is_file() for path in paths.values()))

    def test_empty_valid_schema_fails_bar_and_warmup_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            database = data / "market_data.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """CREATE TABLE market_kbars(
                    stock_code TEXT, freq_minutes INTEGER, kbar_timestamp INTEGER,
                    open REAL, high REAL, low REAL, close REAL, volume INTEGER)"""
                )
                connection.commit()
            finally:
                connection.close()
            market = inspect_market_cache(root, self.config)
            self.assertFalse(market["available"])
            self.assertEqual(market["failure_status"], "FAILED_EXECUTION")
            self.assertEqual(len(market["checks"]), 25)
            first = market["checks"][0]
            self.assertIn("評估 K 棒 0 < 1000", first["problems"])
            self.assertIn("暖機 K 棒 0 < 400", first["problems"])

    def test_blocked_missing_data_still_writes_three_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(self.config, ensure_ascii=False), encoding="utf-8"
            )
            audit, paths = run_workflow(root, config_path)
            self.assertEqual(audit["status"], "BLOCKED_MISSING_DATA")
            self.assertEqual(set(paths), {"csv", "audit_json", "html"})
            self.assertTrue(all(Path(path).is_file() for path in paths.values()))
            self.assertEqual(len(audit["planned_results"]), 225)
            self.assertFalse(any(row["total_return"] for row in audit["planned_results"]))

    def test_csv_contains_complete_performance_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            rows = planned_result_rows(self.config, "BLOCKED_MISSING_DATA")
            output_config = copy.deepcopy(self.config)
            output_config["output_directory"] = "."
            output_config["outputs"] = {
                "csv": path.name,
                "audit_json": "audit.json",
                "html": "report.html",
            }
            audit = {
                "status": "BLOCKED_MISSING_DATA",
                "reason": "test",
                "conclusion": "test",
                "config": output_config,
                "config_fingerprint": "test",
                "market_data": {"path": "", "checks": []},
                "aggregates": [],
                "planned_results": rows,
                "atr_triggered": False,
                "atr_decision": output_config["versions"]["D"]["decision"],
            }
            paths = write_outputs(Path(directory), output_config, audit, rows)
            with Path(paths["csv"]).open(encoding="utf-8-sig", newline="") as stream:
                fields = csv.DictReader(stream).fieldnames
            self.assertEqual(tuple(fields or ()), RESULT_FIELDS)

    def test_training_aggregate_is_rendered_in_html(self):
        rows = self._rows()
        aggregate = self._aggregate(rows)
        audit = {
            "status": "COMPLETED",
            "reason": "",
            "conclusion": "test",
            "config": self.config,
            "config_fingerprint": "test",
            "market_data": {"path": "test.sqlite3", "checks": []},
            "aggregates": [aggregate],
            "results": rows,
            "selections": {},
            "validation_comparisons": [],
            "atr_triggered": False,
            "atr_decision": self.config["versions"]["D"]["decision"],
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = write_outputs(Path(directory), self.config, audit, rows)
            report = Path(paths["html"]).read_text(encoding="utf-8")
        self.assertIn("Training Walk-forward", report)
        self.assertIn("B_ma_10_30_120", report)
        self.assertIn("排除2303中位數", report)
        self.assertIn("LOCKED／NOT USED", report)


if __name__ == "__main__":
    unittest.main()

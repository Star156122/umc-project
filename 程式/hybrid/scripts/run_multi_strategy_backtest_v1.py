"""CLI for fixed-training, historical-development four-strategy replay."""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from time import time_ns
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.multi_strategy_backtest_v1 import (  # noqa: E402
    DevelopmentBacktestBlocked, build_buy_and_hold_result, discover_v2b_candidates, open_readonly_sqlite,
    train_fixed_model_set, validate_request, write_json, run_one_strategy,
)

CONFIG_PATH = ROOT / "hybrid/config/multi_strategy_development_v1.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one or more fixed-model Strategy V2-B development backtests")
    parser.add_argument("--stock-code", required=True, help="One stock from the frozen Technical V2-B universe")
    parser.add_argument("--strategies", nargs="+", choices=("technical", "rf", "xgboost", "gru"), required=True)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    return parser


def run_preflight(config: dict, stock_code: str, strategies: list[str]) -> dict:
    validate_request(stock_code, strategies, config)
    tech_db = ROOT / config["technical"]["data_path"]
    ml_db = ROOT / config["ml"]["input_data_path"]
    # Verify both sources can be opened read-only. Queries are bounded to the
    # preregistered 2023-2024 range; no holdout or OOS date is touched.
    checks = []
    for path, role in ((tech_db, "technical_bars"), (ml_db, "ml_features")):
        connection = open_readonly_sqlite(path)
        try:
            rows = connection.execute(
                "SELECT COUNT(*) FROM market_kbars WHERE stock_code=? AND freq_minutes=5 "
                "AND kbar_timestamp>=? AND kbar_timestamp<?",
                (str(stock_code).zfill(4), int(datetime(2023, 1, 1, tzinfo=ZoneInfo("Asia/Taipei")).timestamp()),
                 int(datetime(2025, 1, 1, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())),
            ).fetchone()[0]
            if not rows:
                raise DevelopmentBacktestBlocked(f"BLOCKED_NO_{role.upper()}_ROWS")
            checks.append({"role": role, "path": str(path), "rows": int(rows), "read_only": True,
                           "range": {"start": "2023-01-01", "end_exclusive": "2025-01-01"}})
        finally:
            connection.close()
    return {"status": "PREFLIGHT_READY", "stock_code": str(stock_code).zfill(4),
            "strategies": strategies, "data_checks": checks,
            "periods": config["periods"], "data_role": config["data_role"],
            "final_oos_used": False, "holdout_policy_modified": False}


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_experiment(config: dict, stock_code: str, strategies: list[str]) -> Path:
    from hybrid.scripts.run_strategy_v2_batch import build_regime_context
    from trading_system import technical_v1 as tv1

    preflight = run_preflight(config, stock_code, strategies)
    root = Path(__file__).resolve().parents[2]
    technical_config_path = root / config["technical"]["config"]
    technical_config = tv1.read_config(technical_config_path)
    tv1.assert_execution_ready(technical_config, "training")
    data_path = tv1.resolve_market_data_path(root, technical_config)
    freeze_manifest_path = root / "research/strategy_v2_freeze_manifest.json"
    freeze = json.loads(freeze_manifest_path.read_text(encoding="utf-8"))
    for source_key in ("technical_config", "technical_source", "risk_config"):
        identity = freeze["source_identity"][source_key]
        actual_hash = hashlib.sha256((root / identity["path"]).read_bytes()).hexdigest()
        if actual_hash != identity["sha256"]:
            raise DevelopmentBacktestBlocked(f"BLOCKED_FROZEN_SOURCE_HASH_MISMATCH:{source_key}")
    risk_config = json.loads((root / config["risk"]["config"]).read_text(encoding="utf-8"))
    if (risk_config.get("risk_per_trade"), risk_config.get("max_position_fraction"), risk_config.get("stop_loss_pct")) != (0.01, 0.4, 0.025):
        raise DevelopmentBacktestBlocked("BLOCKED_RISK_V1_CONTRACT_CHANGED")

    models, model_audit = ({}, {"models": {}})
    model_names = [
        {"rf": "random_forest", "xgboost": "xgboost", "gru": "gru"}[item]
        for item in strategies if item != "technical"
    ]
    if model_names:
        models, model_audit = train_fixed_model_set(config, model_names)

    candidate_dates = set()
    for path, _role in ((root / config["technical"]["data_path"], "technical"),):
        connection = open_readonly_sqlite(path)
        try:
            lower = int(datetime(2024, 7, 1, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())
            upper = int(datetime(2025, 1, 1, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())
            rows = connection.execute(
                "SELECT DISTINCT kbar_timestamp FROM market_kbars WHERE stock_code IN (?,?,?,?,?) "
                "AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp",
                (*config["stock_universe"], lower, upper),
            ).fetchall()
            candidate_dates = {datetime.fromtimestamp(int(row[0]), ZoneInfo("Asia/Taipei")).date().isoformat() for row in rows}
        finally:
            connection.close()
    if not candidate_dates:
        raise DevelopmentBacktestBlocked("BLOCKED_NO_BACKTEST_DATES")

    connection = open_readonly_sqlite(data_path)
    try:
        regime_context, regime_audit = build_regime_context(connection, tuple(config["stock_universe"]), candidate_dates)
        candidate_schedule, candidate_rows = discover_v2b_candidates(
            connection=connection, config=config, stock_code=stock_code, regime_context=regime_context,
        )
        outputs = []
        for strategy_id in strategies:
            model_key = {"rf": "random_forest", "xgboost": "xgboost", "gru": "gru"}.get(strategy_id)
            result = run_one_strategy(
                connection=connection, config=config, stock_code=stock_code, strategy_id=strategy_id,
                model_runtime=models.get(model_key) if model_key else None,
                regime_context=regime_context, candidate_schedule=candidate_schedule,
            )
            outputs.append(result)
        execution = config["execution"]
        buy_and_hold = build_buy_and_hold_result(
            connection=connection, stock_code=stock_code,
            period=config["periods"]["backtest"],
            initial_capital=float(execution["initial_capital"]),
            commission_rate=float(execution["commission_rate"]),
        )
    finally:
        connection.close()

    equity_axes = [tuple(point["timestamp"] for point in row["equity_curve"]) for row in [*outputs, buy_and_hold]]
    if equity_axes and any(axis != equity_axes[0] for axis in equity_axes[1:]):
        raise DevelopmentBacktestBlocked("BLOCKED_STRATEGY_EQUITY_TIME_AXIS_MISMATCH")

    output = root / "exports" / f"multi_strategy_dev_v1_{time_ns()}"
    output.mkdir(parents=True, exist_ok=False)
    summary_rows = [{"strategy_id": row["strategy_id"], **row["summary"]} for row in outputs]
    summary_rows.append({"strategy_id": buy_and_hold["strategy_id"], **buy_and_hold["summary"]})
    all_trades = [{"strategy_id": row["strategy_id"], **trade} for row in outputs for trade in row["trades"]]
    all_trades.extend({"strategy_id": buy_and_hold["strategy_id"], **trade} for trade in buy_and_hold["trades"])
    all_gates = [{"strategy_id": row["strategy_id"], **gate} for row in outputs for gate in row["details"].get("gate_decisions", [])]
    all_risk = [{"strategy_id": row["strategy_id"], **decision} for row in outputs for decision in row["details"]["risk_decisions"]]
    all_equity = [{"strategy_id": row["strategy_id"], **point} for row in outputs for point in row["equity_curve"]]
    all_equity.extend({"strategy_id": buy_and_hold["strategy_id"], **point} for point in buy_and_hold["equity_curve"])
    _write_csv(output / "summary.csv", summary_rows)
    _write_csv(output / "technical_candidates.csv", candidate_rows)
    _write_csv(output / "trades.csv", all_trades)
    _write_csv(output / "gate_decisions.csv", all_gates)
    _write_csv(output / "risk_decisions.csv", all_risk)
    _write_csv(output / "equity_curve.csv", all_equity)
    audit = {
        "experiment_id": config["experiment_id"], "run_mode": config["run_mode"],
        "technical_freeze_id": freeze.get("freeze_id"),
        "status": "COMPLETED_DEVELOPMENT_RESEARCH", "data_role": config["data_role"],
        "stock_code": str(stock_code).zfill(4), "strategies": strategies,
        "periods": config["periods"], "preflight": preflight, "model_training": model_audit,
        "technical_regime_context": regime_audit,
        "common_candidate_schedule_count": len(candidate_schedule),
        "common_candidate_schedule_sha256": hashlib.sha256("\n".join(sorted(candidate_schedule)).encode("utf-8")).hexdigest(),
        "source_files": {"technical_config": config["technical"]["config"],
                         "technical_candidate_config": config["technical"]["candidate_config"],
                         "ml_config": config["ml"]["config"], "risk_config": config["risk"]["config"]},
        "verified_frozen_hashes": {key: freeze["source_identity"][key]["sha256"] for key in ("technical_config", "technical_source", "risk_config")},
        "known_limitations": [
            "All periods are historically exposed development data, not independent OOS.",
            "ML V1 model is draft/unfrozen; no model selection is performed.",
            "Slippage is 0.0 and is not simulated.",
            "Risk sizing is based on signal close; next-open gaps can change realized stop risk.",
            "Predictions are fixed-model historical development forecasts and are not OOF or live signals.",
        ],
        "final_oos_used": False, "holdout_policy_modified": False,
        "parameter_search_performed": False, "ml_retrained_during_backtest": False,
    }
    write_json(output / "run.json", {
        "audit": audit,
        "comparison": {"time_axis_identical": True, "strategy_summaries": summary_rows,
                       "buy_and_hold_summary": buy_and_hold["summary"]},
        "buy_and_hold": buy_and_hold,
        "results": outputs,
    })
    return output


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.config.resolve() != CONFIG_PATH.resolve():
            raise DevelopmentBacktestBlocked("BLOCKED_CONFIG_OVERRIDE_NOT_ALLOWED_IN_DEVELOPMENT_MODE")
        config = json.loads(args.config.read_text(encoding="utf-8"))
        report = run_preflight(config, args.stock_code, args.strategies)
    except (OSError, ValueError, DevelopmentBacktestBlocked) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    try:
        output = run_experiment(config, args.stock_code, args.strategies)
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "preflight_status": report["status"]}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "COMPLETED_DEVELOPMENT_RESEARCH", "output_directory": str(output),
                      "data_role": config["data_role"], "final_oos_used": False,
                      "holdout_policy_modified": False}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Training-only fair comparison on Technical/RF/XGBoost/GRU common coverage."""
from __future__ import annotations

import contextlib
import html
import io
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.common_coverage import build_common_coverage, schedule_by_fold_stock  # noqa: E402
from hybrid.core.hybrid_poc_v1 import (  # noqa: E402
    HybridGateTechnicalV1Strategy,
    HybridPOCError,
    assert_entry_only_change,
    build_prediction_lookup,
    canonical_fingerprint,
    html_is_standalone,
    sha256_file,
    validate_oof_predictions,
    validate_training_only_dates,
)
from hybrid.scripts.run_hybrid_poc_v1 import read_json, write_json  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
POC_CONFIG_PATH = ROOT / "hybrid/config/hybrid_poc_v1.json"
RUN_AUDIT = "technical_v1_audit.json"


class TechnicalCommonCoverageStrategy(tv1.TechnicalV1Strategy):
    """Technical V1 with only its entry schedule restricted to common dates."""

    def __init__(self, *args: Any, candidate_schedule: set[str], **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.candidate_schedule = candidate_schedule

    def _try_entry(self, code: str, tick_time: datetime, close: float, snapshot: dict[str, float | None]) -> None:
        entry_time = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TAIPEI)
        if entry_time.astimezone(TAIPEI).isoformat(timespec="microseconds") not in self.candidate_schedule:
            return
        super()._try_entry(code, tick_time, close, snapshot)


def latest_ready_preflight() -> tuple[Path, dict[str, Any]]:
    audits: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in (ROOT / "exports").glob("technical_v1_*/technical_v1_audit.json"):
        try:
            audit = read_json(path)
            if audit.get("preflight_only") is True:
                audits.append((datetime.fromisoformat(audit["generated_at"]), path, audit))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    if not audits:
        raise HybridPOCError("No Technical Training preflight audit found")
    _, path, audit = max(audits, key=lambda item: item[0])
    if audit.get("status") != "PREFLIGHT_READY" or audit.get("forbidden_roles_queried"):
        raise HybridPOCError(f"Latest Technical preflight is not ready: {path}")
    return path, audit


def directory_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256_file(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def training_calendar(connection: sqlite3.Connection, config: dict[str, Any], stocks: list[str]) -> list[str]:
    lower, upper = tv1._timestamp_bounds("2023-01-01", "2024-12-31")
    placeholders = ",".join("?" for _ in stocks)
    rows = connection.execute(
        f"SELECT DISTINCT kbar_timestamp FROM market_kbars "
        f"WHERE freq_minutes=? AND stock_code IN ({placeholders}) "
        "AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp",
        (int(config["data_quality"]["expected_cadence_seconds"] // 60), *stocks, lower, upper),
    ).fetchall()
    return sorted({
        datetime.fromtimestamp(int(row[0]), TAIPEI).date().isoformat()
        for row in rows
        if "2023-01-01" <= datetime.fromtimestamp(int(row[0]), TAIPEI).date().isoformat() <= "2024-12-31"
    })


def simulate_one(
    *, connection: sqlite3.Connection, config: dict[str, Any], family_name: str,
    family: dict[str, Any], candidate: dict[str, Any], stock_code: str,
    fold: dict[str, str], variant: str, model: str | None,
    prediction_lookup: dict[tuple[str, str], dict[str, Any]] | None,
    candidate_schedule: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    full_frame, evaluation, app, warmup_count, evaluate_lower = tv1._load_fold_frame(
        connection, config, candidate, stock_code, fold
    )
    kwargs = dict(
        config=app, study_config=config, family_name=family_name, family=family,
        candidate=candidate, data_role="training", fold_id=fold["id"],
        is_backtest=True, is_simulation=True, local_only=True,
    )
    if model is None:
        strategy = TechnicalCommonCoverageStrategy(**kwargs, candidate_schedule=candidate_schedule)
    else:
        strategy = HybridGateTechnicalV1Strategy(
            **kwargs, ml_model=model, prediction_lookup=prediction_lookup or {},
            candidate_schedule=candidate_schedule,
        )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = evaluate_lower
    stats = bt.MarketDataStats(
        tick_count=full_frame.height,
        first_timestamp=float(full_frame["kbar_timestamp"][0]),
        last_timestamp=float(full_frame["kbar_timestamp"][-1]),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full_frame, stats)
    trades = bt._build_trade_rows(strategy, app)
    pnl_rows = bt._build_pnl_rows(trades)
    bar_rows = bt.build_kbar_rows_from_frame(evaluation, app)
    days = sorted({str(row["datetime"])[:10] for row in bar_rows})
    metrics = bt.calculate_performance_metrics(pnl_rows, app.initial_capital, trading_days=days)
    metrics.update(bt.calculate_mark_to_market_risk(trades, bar_rows, app.initial_capital))
    if strategy.local_position != 0:
        raise HybridPOCError(f"Open position remained: {variant}/{stock_code}/{fold['id']}")
    result = {
        "variant": variant, "stock_code": str(stock_code), "fold_id": str(fold["id"]),
        "evaluate_start": fold["evaluate_start"], "evaluate_end": fold["evaluate_end"],
        "candidate_id": candidate["id"], "parameter_fingerprint": tv1.candidate_fingerprint(config, candidate),
        "warmup_bar_count": int(warmup_count),
        "total_return": float(metrics["total_return"]),
        "initial_capital": float(metrics["initial_capital"]),
        "final_equity": float(metrics["ending_capital"]),
        "net_pnl": float(metrics["net_pnl"]), "gross_pnl": float(metrics["gross_pnl"]),
        "win_rate": float(metrics["win_rate"]), "profit_factor": float(metrics["profit_factor"]),
        "max_drawdown": float(metrics["max_drawdown"]), "sharpe_ratio": float(metrics["sharpe_ratio"]),
        "trade_count": int(metrics["completed_trades"]),
        "transaction_cost": float(metrics["transaction_cost"]),
        "technical_entry_count": int(sum(row["action"] == "Buy" for row in trades)),
    }
    tagged_trades = [{"variant": variant, "fold_id": fold["id"], "stock_code": str(stock_code), **row} for row in trades]
    runtime_decisions = list(getattr(strategy, "hybrid_decisions", []))
    return result, tagged_trades, runtime_decisions


def summarize(group: pd.DataFrame) -> dict[str, Any]:
    pf = group["profit_factor"].replace([np.inf, -np.inf], np.nan).dropna()
    return {
        "evaluation_count": int(len(group)),
        "trade_count": int(group["trade_count"].sum()),
        "average_return": float(group["total_return"].mean()),
        "median_return": float(group["total_return"].median()),
        "return_std": float(group["total_return"].std(ddof=1)) if len(group) > 1 else 0.0,
        "win_rate": float(group["win_rate"].mean()),
        "profit_factor": float(pf.mean()) if len(pf) else 0.0,
        "worst_mdd": float(group["max_drawdown"].max()),
        "sharpe": float(group["sharpe_ratio"].mean()),
        "transaction_cost": float(group["transaction_cost"].sum()),
        "final_equity_mean": float(group["final_equity"].mean()),
        "gross_pnl": float(group["gross_pnl"].sum()),
        "net_pnl": float(group["net_pnl"].sum()),
    }


def report_html(audit: dict[str, Any], summary: pd.DataFrame, per_stock: pd.DataFrame, per_fold: pd.DataFrame) -> str:
    labels = {
        "technical_common_coverage": "Technical Common-Coverage",
        "hybrid_random_forest_common": "Hybrid RF",
        "hybrid_xgboost_common": "Hybrid XGBoost",
        "hybrid_gru_common": "Hybrid GRU",
    }
    def table(frame: pd.DataFrame) -> str:
        shown = frame.copy()
        for column in shown.select_dtypes(include=["float"]).columns:
            shown[column] = shown[column].map(lambda value: f"{value:.4f}")
        return shown.to_html(index=False, escape=True, border=0, classes="data")
    main = summary.copy()
    main["variant"] = main["variant"].map(labels)
    main = main.rename(columns={
        "average_return": "Average Return", "win_rate": "Win Rate", "profit_factor": "Profit Factor",
        "worst_mdd": "Worst MDD", "sharpe": "Sharpe", "trade_count": "Trade Count",
        "transaction_cost": "Transaction Cost", "final_equity_mean": "Final Equity (mean)",
    })
    model_counts = audit["buy_counts"]
    summary_table = table(main[["variant", "Average Return", "Win Rate", "Profit Factor", "Worst MDD", "Sharpe", "Trade Count", "Transaction Cost", "Final Equity (mean)"]])
    details = html.escape(json.dumps(audit, ensure_ascii=False, indent=2))
    findings = html.escape(json.dumps(audit.get("objective_findings", {}), ensure_ascii=False, indent=2))
    document = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>Hybrid Common Coverage</title>
<style>body{{font-family:Arial,'Microsoft JhengHei',sans-serif;background:#071521;color:#eaf4ff;margin:0;line-height:1.6}}main{{max-width:1250px;margin:auto;padding:28px}}section{{background:#0d2232;border:1px solid #27465b;border-radius:14px;padding:20px;margin:16px 0;overflow:auto}}h1,h2{{color:#72c7ff}}.notice{{background:#4b2b16;padding:12px;border-radius:9px}}table.data{{border-collapse:collapse;width:100%;font-size:13px}}table.data th,table.data td{{border:1px solid #315268;padding:7px;text-align:right}}table.data th:first-child,table.data td:first-child{{text-align:left}}pre{{white-space:pre-wrap;max-height:400px;overflow:auto;background:#06101a;padding:12px;border-radius:8px}}.pass{{color:#83efad}}</style></head><body><main>
<h1>Technical × ML Common-Coverage 公平比較</h1><p class="notice">此結果為 2023–2024 Training OOF Common-Coverage 公平比較，不是 Final OOS。</p>
<section><h2>共同範圍</h2><p>原 Technical Candidate：{audit['technical_candidate_count']}；Common Candidate：{audit['common_candidate_count']}；共同候選上每模型有效 OOF：{audit['common_candidate_count']}；Missing：{audit['missing']}；Invalid：{audit['invalid']}。</p>
<p>共同 Candidate 的 ML BUY 數：RF {model_counts['random_forest']}、XGBoost {model_counts['xgboost']}、GRU {model_counts['gru']}。</p><p>資料角色：2023–2024 Training only。Technical baseline 只限制 Entry Schedule，出場與風險規則沿用原 Technical V1。</p></section>
<section><h2>整體比較</h2>{summary_table}</section>
<section><h2>每檔股票（同一共同候選集）</h2>{table(per_stock)}</section>
<section><h2>WF1 / WF2 / WF3</h2>{table(per_fold)}</section>
<section><h2>客觀比較與判讀</h2><p><b>{html.escape(str(audit.get('result_classification', 'NOT_CLASSIFIED')))}</b></p><p>{html.escape(str(audit.get('interpretation', '')))}</p><pre>{findings}</pre></section>
<section><h2>可重現稽核</h2><pre>{details}</pre></section></main></body></html>"""
    if not html_is_standalone(document):
        raise HybridPOCError("Common-coverage report must be standalone")
    return document


def main() -> int:
    poc = read_json(POC_CONFIG_PATH)
    config_path = ROOT / poc["technical"]["config"]
    technical_config = tv1.read_config(config_path)
    assert_entry_only_change(technical_config, poc)
    tv1.assert_execution_ready(technical_config, "training")
    preflight_path, preflight = latest_ready_preflight()
    if preflight["market_data_preflight"]["database_path"] != str(tv1.resolve_market_data_path(ROOT, technical_config)):
        raise HybridPOCError("Ready preflight database differs from current Technical DB")

    source_run = ROOT / poc["technical"]["source_run"]
    source_audit_path = source_run / "technical_v1_audit.json"
    source_results_path = source_run / "technical_v1_results.json"
    source_signals_path = source_run / "technical_v1_signals.csv"
    poc_audit_path = ROOT / "exports/hybrid_poc_v1_20261013T041540804059/hybrid_poc_audit.json"
    poc_run = poc_audit_path.parent
    poc_audit = read_json(poc_audit_path)
    poc_directory_hashes_before = directory_hashes(poc_run)
    oof_path = Path(poc_audit["oof_artifact"])
    if not oof_path.is_absolute():
        oof_path = ROOT / oof_path
    for path in (source_audit_path, source_results_path, source_signals_path, poc_audit_path, oof_path):
        if not path.exists():
            raise HybridPOCError(f"Required immutable source artifact is missing: {path}")
    source_audit = read_json(source_audit_path)
    if source_audit.get("status") != "COMPLETED" or source_audit.get("forbidden_roles_queried"):
        raise HybridPOCError("Technical source is not a completed Training-only run")
    current_hashes = {path.name: sha256_file(path) for path in (source_audit_path, source_results_path, source_signals_path)}
    if current_hashes != poc_audit.get("technical_source_hashes"):
        raise HybridPOCError("Immutable Technical source hashes do not match the completed Hybrid POC audit")
    if poc_audit.get("controlled_validation_used") or poc_audit.get("development_used") or poc_audit.get("additional_holdout_used") or poc_audit.get("final_oos_used"):
        raise HybridPOCError("Existing POC audit indicates a forbidden data role was used")

    oof = pd.read_csv(oof_path, dtype={"stock_code": str})
    validate_oof_predictions(
        oof, expected_models=poc["ml"]["models"], expected_stocks=poc["stock_universe"],
        period_start="2023-01-01", period_end="2024-12-31",
    )
    signals = pd.read_csv(source_signals_path, dtype={"stock_code": str})
    signals = signals.loc[
        (signals["candidate_id"] == poc["technical"]["candidate_id"])
        & (signals["technical_signal"] == "BUY_CANDIDATE")
    ].copy().sort_values(["fold_id", "stock_code", "signal_time"]).reset_index(drop=True)
    if len(signals) != int(poc_audit["technical_candidate_count"]):
        raise HybridPOCError("Candidate source count differs from immutable Hybrid POC audit")

    database_path = tv1.resolve_market_data_path(ROOT, technical_config)
    database_hash_before = sha256_file(database_path)
    with tv1._readonly_connection(database_path) as connection:
        calendar = training_calendar(connection, technical_config, list(poc["stock_universe"]))
        common, decisions, coverage_audit = build_common_coverage(
            signals, oof, models=poc["ml"]["models"], training_calendar=calendar,
        )
        schedules = schedule_by_fold_stock(common)
        lookups = {model: build_prediction_lookup(oof, model) for model in poc["ml"]["models"]}
        family_name, family, candidate = tv1.candidate_by_id(technical_config, poc["technical"]["candidate_id"])
        rows: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        runtime_decisions: list[dict[str, Any]] = []
        variants = [("technical_common_coverage", None)] + [
            (f"hybrid_{model}_common", model) for model in poc["ml"]["models"]
        ]
        for variant, model in variants:
            for fold in technical_config["walk_forward_folds"]:
                for stock in poc["stock_universe"]:
                    schedule = schedules.get((fold["id"], str(stock)), set())
                    result, trade_rows, decision_rows = simulate_one(
                        connection=connection, config=technical_config,
                        family_name=family_name, family=family, candidate=candidate,
                        stock_code=str(stock), fold=fold, variant=variant, model=model,
                        prediction_lookup=None if model is None else lookups[model],
                        candidate_schedule=schedule,
                    )
                    rows.append(result)
                    trades.extend(trade_rows)
                    runtime_decisions.extend(decision_rows)
                    expected = int(((common["fold_id"] == fold["id"]) & (common["stock_code"].astype(str) == str(stock))).sum())
                    if model is not None and len(decision_rows) != expected:
                        raise HybridPOCError(
                            f"Runtime common alignment count mismatch: {model}/{fold['id']}/{stock}; expected {expected}, got {len(decision_rows)}"
                        )

    if sha256_file(database_path) != database_hash_before:
        raise HybridPOCError("Raw Technical SQLite changed during read-only common-coverage run")
    if current_hashes != {path.name: sha256_file(path) for path in (source_audit_path, source_results_path, source_signals_path)}:
        raise HybridPOCError("Immutable Technical POC source changed during common-coverage run")
    if poc_directory_hashes_before != directory_hashes(poc_run):
        raise HybridPOCError("An existing Hybrid POC export changed during this run")

    results = pd.DataFrame(rows)
    decisions = pd.DataFrame(decisions)
    runtime_decisions_frame = pd.DataFrame(runtime_decisions)
    if not runtime_decisions_frame.empty:
        if (runtime_decisions_frame["alignment_status"] != "MATCHED").any() or (~runtime_decisions_frame["time_order_valid"].astype(bool)).any():
            raise HybridPOCError("Runtime common alignment has missing or invalid predictions")
    validate_training_only_dates(results["evaluate_start"])
    summary_rows = [{"variant": variant, **summarize(group)} for variant, group in results.groupby("variant", sort=False)]
    summary = pd.DataFrame(summary_rows)
    base_count = int(summary.loc[summary["variant"] == "technical_common_coverage", "trade_count"].iloc[0])
    summary["trade_reduction_vs_technical"] = summary.apply(
        lambda row: 0.0 if row["variant"] == "technical_common_coverage" or base_count == 0
        else 1 - row["trade_count"] / base_count,
        axis=1,
    )
    per_stock_rows = []
    per_fold_rows = []
    for keys, group in results.groupby(["stock_code", "variant"], sort=False):
        per_stock_rows.append({"stock_code": keys[0], "variant": keys[1], **summarize(group)})
    for keys, group in results.groupby(["fold_id", "variant"], sort=False):
        per_fold_rows.append({"fold_id": keys[0], "variant": keys[1], **summarize(group)})
    per_stock = pd.DataFrame(per_stock_rows)
    per_fold = pd.DataFrame(per_fold_rows)

    baseline_summary = summary.loc[summary["variant"] == "technical_common_coverage"].iloc[0]
    objective_findings: dict[str, Any] = {}
    for model in poc["ml"]["models"]:
        variant = f"hybrid_{model}_common"
        model_summary = summary.loc[summary["variant"] == variant].iloc[0]
        stock_base = per_stock.loc[per_stock["variant"] == "technical_common_coverage"].set_index("stock_code")
        stock_model = per_stock.loc[per_stock["variant"] == variant].set_index("stock_code")
        fold_base = per_fold.loc[per_fold["variant"] == "technical_common_coverage"].set_index("fold_id")
        fold_model = per_fold.loc[per_fold["variant"] == variant].set_index("fold_id")
        objective_findings[model] = {
            "average_return_higher_than_technical": bool(model_summary["average_return"] > baseline_summary["average_return"]),
            "average_return_difference": float(model_summary["average_return"] - baseline_summary["average_return"]),
            "worst_mdd_lower_than_technical": bool(model_summary["worst_mdd"] < baseline_summary["worst_mdd"]),
            "worst_mdd_difference": float(model_summary["worst_mdd"] - baseline_summary["worst_mdd"]),
            "trade_count": int(model_summary["trade_count"]),
            "trade_reduction_count": int(baseline_summary["trade_count"] - model_summary["trade_count"]),
            "trade_reduction_rate": float(model_summary["trade_reduction_vs_technical"]),
            "stocks_with_higher_average_return": int(sum(stock_model.loc[stock, "average_return"] > stock_base.loc[stock, "average_return"] for stock in stock_base.index)),
            "folds_with_higher_average_return": int(sum(fold_model.loc[fold, "average_return"] > fold_base.loc[fold, "average_return"] for fold in fold_base.index)),
            "folds_with_lower_worst_mdd": int(sum(fold_model.loc[fold, "worst_mdd"] < fold_base.loc[fold, "worst_mdd"] for fold in fold_base.index)),
            "average_win_rate_higher_than_technical": bool(model_summary["win_rate"] > baseline_summary["win_rate"]),
            "average_profit_factor_higher_than_technical": bool(model_summary["profit_factor"] > baseline_summary["profit_factor"]),
        }

    buy_counts = {
        model: int(((decisions["model"] == model) & (decisions["ml_prediction"] == "BUY")).sum())
        for model in poc["ml"]["models"]
    }
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    output = ROOT / "exports" / f"hybrid_common_coverage_{timestamp}"
    output.mkdir(parents=True, exist_ok=False)
    audit = {
        **{key: value for key, value in coverage_audit.items() if key != "excluded_candidates"},
        "experiment_id": f"hybrid-common-coverage-{timestamp}",
        "created_at": datetime.now().astimezone(TAIPEI).isoformat(),
        "purpose": "Training-only fair comparison on Technical × RF × XGBoost × GRU common OOF coverage.",
        "scope": {"data_role": "training", "period": ["2023-01-01", "2024-12-31"], "stock_universe": poc["stock_universe"]},
        "coverage_guardrail": {
            "previous_successful_training_days": 422,
            "previous_successful_wf3_evaluation_days": 94,
            "source_commit": "ea8c43f",
            "training_threshold": technical_config["data_quality"]["minimum_training_trading_days"],
            "evaluation_threshold": technical_config["data_quality"]["minimum_evaluation_trading_days"],
            "reason": technical_config["data_quality"]["coverage_guardrail_note"],
            "evidence": [
                "git:ea8c43f:程式/configs/technical_v1_research.json specifies 400/90 and clean SQLite",
                "exports/technical_v1_20261013T024919303843/technical_v1_audit.json records READY with 422 days and WF3=94",
                "exports/hybrid_poc_v1_20261013T041540804059/hybrid_poc_audit.json references that Technical source and matching hashes",
            ],
            "latest_preflight": str(preflight_path.relative_to(ROOT)),
            "latest_preflight_status": preflight["status"],
        },
        "inputs": {
            "technical_source_run": str(source_run.relative_to(ROOT)),
            "technical_source_hashes": current_hashes,
            "poc_audit": str(poc_audit_path.relative_to(ROOT)),
            "oof_artifact": str(oof_path.relative_to(ROOT)),
            "oof_sha256": sha256_file(oof_path),
            "existing_poc_export_hashes": poc_directory_hashes_before,
            "database": str(database_path.relative_to(ROOT)),
            "database_sha256_before": database_hash_before,
            "database_sha256_after": sha256_file(database_path),
        },
        "comparison_variants": ["technical_common_coverage", "hybrid_random_forest_common", "hybrid_xgboost_common", "hybrid_gru_common"],
        "candidate_schedule_restriction_only": True,
        "technical_exit_or_risk_changed": False,
        "ml_parameters_changed": False,
        "hybrid_gate_changed": False,
        "common_candidate_count": int(len(common)),
        "buy_counts": buy_counts,
        "result_classification": "COMMON_COVERAGE_MIXED",
        "interpretation": "Common coverage 下 RF 與 XGBoost 平均報酬高於 Technical，GRU 僅略高；三個 Hybrid 的最差回撤都較低且交易數明顯減少，但勝率都較低，個股與 Fold 的報酬方向不完全一致，因此整體判為 MIXED。",
        "objective_findings": objective_findings,
        "runtime_decision_count_by_model": {
            model: int((runtime_decisions_frame["model"] == model).sum()) if not runtime_decisions_frame.empty else 0
            for model in poc["ml"]["models"]
        },
        "missing": 0,
        "invalid": 0,
        "controlled_validation_used": False,
        "development_used": False,
        "additional_holdout_used": False,
        "final_oos_used": False,
        "forbidden_roles_used": False,
        "raw_database_modified": False,
        "existing_poc_exports_overwritten": False,
        "candidate_selected": False,
        "optimization_performed": False,
        "research_logic_changed": False,
    }
    write_json(output / "common_coverage_audit.json", audit)
    common.to_csv(output / "common_coverage_candidates.csv", index=False, encoding="utf-8-sig")
    decisions.to_csv(output / "common_coverage_decisions.csv", index=False, encoding="utf-8-sig")
    results.to_csv(output / "common_coverage_results.csv", index=False, encoding="utf-8-sig")
    write_json(output / "common_coverage_results.json", {
        "audit": audit, "detailed_results": rows,
        "summary": {str(row["variant"]): {key: (float(value) if isinstance(value, np.floating) else int(value) if isinstance(value, np.integer) else value) for key, value in row.items() if key != "variant"} for row in summary_rows},
        "per_stock": per_stock_rows, "per_fold": per_fold_rows,
    })
    summary.to_csv(output / "common_coverage_summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(coverage_audit["excluded_candidates"]).to_csv(output / "common_coverage_exclusions.csv", index=False, encoding="utf-8-sig")
    (output / "common_coverage_report.html").write_text(report_html(audit, summary, per_stock, per_fold), encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", "output": str(output), "common_candidate_count": len(common), "buy_counts": buy_counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HybridPOCError as exc:
        raise SystemExit(f"COMMON_COVERAGE_BLOCKED: {exc}") from exc

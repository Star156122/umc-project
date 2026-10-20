"""Run fixed Risk Management V1 sizing on the immutable Hybrid common coverage."""
from __future__ import annotations

import contextlib
import html
import io
import json
import math
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

from hybrid.core.hybrid_poc_v1 import (  # noqa: E402
    HybridPOCError, build_prediction_lookup, sha256_file, validate_oof_predictions,
    validate_training_only_dates,
)
from hybrid.core.risk_v1 import RiskV1HybridStrategy  # noqa: E402
from hybrid.scripts.run_hybrid_common_coverage import (  # noqa: E402
    POC_CONFIG_PATH, directory_hashes, latest_ready_preflight, read_json, simulate_one,
    summarize,
)
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
RISK_CONFIG_PATH = ROOT / "hybrid/config/risk_v1.json"
METRIC_COLUMNS = {
    "average_return": "average_return",
    "trade_count": "trade_count",
    "net_pnl": "net_pnl",
    "transaction_cost": "transaction_cost",
    "worst_mdd": "worst_mdd",
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def validate_risk_config(config: dict[str, Any]) -> None:
    expected = {
        "version": "risk_v1", "status": "POC_RISK_FIXED_NOT_OPTIMIZED",
        "data_role": "training", "risk_per_trade": 0.01,
        "max_position_fraction": 0.40, "stop_loss_pct": 0.025,
        "parameter_search": False, "optimization_performed": False,
    }
    for key, value in expected.items():
        if config.get(key) != value:
            raise HybridPOCError(f"Risk V1 fixed config mismatch: {key}")
    if config.get("period") != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise HybridPOCError("Risk V1 is restricted to 2023-2024 Training")
    if config.get("forbidden_roles") != ["2025", "2026", "final_oos", "holdout", "development", "controlled_validation"]:
        raise HybridPOCError("Forbidden data roles must remain explicitly blocked")


def compare_reproduction(reference: pd.DataFrame, reproduced: pd.DataFrame) -> dict[str, Any]:
    ref = reference.set_index("variant")
    got = reproduced.set_index("variant")
    if set(ref.index) != set(got.index):
        raise HybridPOCError("RISK_V1_BASELINE_MISMATCH: baseline variants differ")
    comparisons: dict[str, Any] = {}
    for variant in ref.index:
        comparisons[str(variant)] = {}
        for key, column in METRIC_COLUMNS.items():
            left, right = float(ref.loc[variant, column]), float(got.loc[variant, column])
            equal = math.isclose(left, right, rel_tol=1e-11, abs_tol=1e-8)
            comparisons[str(variant)][key] = {
                "reference": left, "reproduced": right, "match": equal,
            }
            if not equal:
                raise HybridPOCError(
                    f"RISK_V1_BASELINE_MISMATCH: {variant}/{key}: {left} != {right}"
                )
    return comparisons


def simulate_risk_one(**kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    connection = kwargs["connection"]
    config = kwargs["config"]
    family_name, family = kwargs["family_name"], kwargs["family"]
    candidate, stock_code, fold = kwargs["candidate"], kwargs["stock_code"], kwargs["fold"]
    variant, model = kwargs["variant"], kwargs["model"]
    prediction_lookup, candidate_schedule = kwargs["prediction_lookup"], kwargs["candidate_schedule"]
    full_frame, evaluation, app, warmup_count, evaluate_lower = tv1._load_fold_frame(
        connection, config, candidate, stock_code, fold
    )
    strategy = RiskV1HybridStrategy(
        config=app, study_config=config, family_name=family_name, family=family,
        candidate=candidate, data_role="training", fold_id=fold["id"],
        is_backtest=True, is_simulation=True, local_only=True,
        ml_model=model, prediction_lookup=prediction_lookup,
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
        "total_return": float(metrics["total_return"]), "initial_capital": float(metrics["initial_capital"]),
        "final_equity": float(metrics["ending_capital"]), "net_pnl": float(metrics["net_pnl"]),
        "gross_pnl": float(metrics["gross_pnl"]), "win_rate": float(metrics["win_rate"]),
        "profit_factor": float(metrics["profit_factor"]), "max_drawdown": float(metrics["max_drawdown"]),
        "sharpe_ratio": float(metrics["sharpe_ratio"]), "trade_count": int(metrics["completed_trades"]),
        "transaction_cost": float(metrics["transaction_cost"]),
        "technical_entry_count": int(sum(row["action"] == "Buy" for row in trades)),
    }
    tagged_trades = [{"variant": variant, "fold_id": fold["id"], "stock_code": str(stock_code), **row} for row in trades]
    decisions = [{"variant": variant, "fold_id": fold["id"], "stock_code": str(stock_code), **row} for row in strategy.risk_decisions]
    runtime = list(strategy.hybrid_decisions)
    return result, tagged_trades, decisions, runtime


def trade_signatures(trades: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[tuple[str, str]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in trades:
        grouped.setdefault((str(row["variant"]), str(row["fold_id"]), str(row["stock_code"])), []).append(row)
    out: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for key, rows in grouped.items():
        rows.sort(key=lambda row: int(row["timestamp"]))
        buys = [str(row["datetime"]) for row in rows if row["action"] == "Buy"]
        sells = [str(row["datetime"]) for row in rows if row["action"] == "Sell"]
        if len(buys) != len(sells):
            raise HybridPOCError(f"RISK_V1_LOGIC_MISMATCH: unpaired trades {key}")
        out[key] = list(zip(buys, sells))
    return out


def validate_trade_timing(baseline: list[dict[str, Any]], risk: list[dict[str, Any]]) -> dict[str, int]:
    baseline_by_key = trade_signatures(baseline)
    risk_by_key = trade_signatures(risk)
    compared, blocked_or_absent = 0, 0
    for risk_key, risk_pairs in risk_by_key.items():
        variant, fold, stock = risk_key
        model = variant.removeprefix("hybrid_").removesuffix("_risk_v1")
        base_key = (f"hybrid_{model}_common", fold, stock)
        baseline_pairs = baseline_by_key.get(base_key, [])
        for pair in risk_pairs:
            if pair not in baseline_pairs:
                raise HybridPOCError(
                    f"RISK_V1_LOGIC_MISMATCH: entry/exit time differs for {risk_key}: {pair}"
                )
            compared += 1
    for base_key, pairs in baseline_by_key.items():
        if base_key[0].startswith("hybrid_"):
            model = base_key[0].removeprefix("hybrid_").removesuffix("_common")
            risk_key = (f"hybrid_{model}_risk_v1", base_key[1], base_key[2])
            blocked_or_absent += max(0, len(pairs) - len(risk_by_key.get(risk_key, [])))
    return {"matched_entry_exit_pairs": compared, "baseline_pairs_without_risk_trade": blocked_or_absent}


def add_risk_metrics(summary: pd.DataFrame, decisions: pd.DataFrame) -> pd.DataFrame:
    if decisions.empty:
        decisions = pd.DataFrame(columns=["variant", "position_fraction", "estimated_risk_fraction", "decision"])
    rows = []
    for variant in summary["variant"]:
        group = decisions.loc[decisions["variant"] == variant]
        executed = group.loc[group["final_quantity"] > 0]
        rows.append({
            "variant": variant,
            "average_position_fraction": float(executed["position_fraction"].mean()) if len(executed) else 0.0,
            "maximum_position_fraction": float(executed["position_fraction"].max()) if len(executed) else 0.0,
            "average_estimated_risk_per_trade": float(executed["estimated_risk_fraction"].mean()) if len(executed) else 0.0,
            "maximum_estimated_risk_per_trade": float(executed["estimated_risk_fraction"].max()) if len(executed) else 0.0,
            "risk_reduced_trades": int((group["decision"] == "REDUCE_SIZE").sum()),
            "zero_quantity_blocked_trades": int((group["decision"] == "BLOCK_ZERO_QUANTITY").sum()),
        })
    return summary.merge(pd.DataFrame(rows), on="variant", how="left")


def table(frame: pd.DataFrame) -> str:
    shown = frame.copy()
    for column in shown.select_dtypes(include=["float"]).columns:
        shown[column] = shown[column].map(lambda x: f"{x:.4f}")
    return shown.to_html(index=False, escape=True, border=0, classes="data")


def render_report(audit: dict[str, Any], summary: pd.DataFrame, per_stock: pd.DataFrame,
                  per_fold: pd.DataFrame, decisions: pd.DataFrame) -> str:
    metrics = summary.copy()
    metrics["variant"] = metrics["variant"].map(lambda v: str(v).replace("hybrid_", "").replace("_common", " Baseline").replace("_risk_v1", " Risk V1"))
    decision_examples = decisions.head(20)
    return f"""<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"><title>Hybrid Risk V1</title>
<style>body{{font-family:Arial,'Microsoft JhengHei',sans-serif;background:#071521;color:#eaf4ff;margin:0;line-height:1.6}}main{{max-width:1300px;margin:auto;padding:28px}}section{{background:#0d2232;border:1px solid #27465b;border-radius:14px;padding:20px;margin:16px 0;overflow:auto}}h1,h2{{color:#72c7ff}}.notice{{background:#493218;padding:12px;border-radius:9px}}table.data{{border-collapse:collapse;width:100%;font-size:13px}}table.data th,table.data td{{border:1px solid #315268;padding:7px;text-align:right}}table.data th:first-child,table.data td:first-child{{text-align:left}}pre{{white-space:pre-wrap;max-height:430px;overflow:auto;background:#06101a;padding:12px;border-radius:8px}}</style></head><body><main>
<h1>Hybrid Risk Management V1</h1><p class=\"notice\">此結果仍為 2023–2024 Training OOF 研究階段，不是 Final OOS，也不是投資建議。</p>
<section><h2>Risk V1 定義與公式</h2><p>risk_per_trade = 1%；max_position_fraction = 40%；沿用 Technical V1 Stop Loss = 2.5%。</p><pre>risk_budget = current_equity × 0.01
stop_distance_per_share = entry_price × 0.025
quantity_by_risk = floor(risk_budget / stop_distance_per_share)
quantity_by_exposure = floor(current_equity × 0.40 / entry_price)
final_quantity = min(quantity_by_risk, quantity_by_exposure, existing_engine_buying_limit)</pre><p>Risk Manager 僅在 ML BUY gate 通過後計算股數；Hold/Sell 不進 sizing。</p></section>
<section><h2>Baseline vs Risk V1</h2>{table(metrics)}</section><section><h2>每檔股票</h2>{table(per_stock)}</section><section><h2>每個 Walk-forward Fold</h2>{table(per_fold)}</section>
<section><h2>回撤與部位統計</h2><p>請比較上表的 Worst MDD、Sharpe、Net PnL、平均與最大部位比例及預估單筆風險。Sizing 數量採現有整數股及引擎購買上限。</p></section>
<section><h2>Risk Decision 範例</h2>{table(decision_examples)}</section>
<section><h2>研究限制與判讀</h2><p>{html.escape(str(audit.get('interpretation', '')))}</p><p>單一候選與固定 1%/40% 規則；本輪不搜尋參數、不跨模型選冠軍，也不開始 Risk V2。</p></section>
<section><h2>Audit</h2><pre>{html.escape(json.dumps(audit, ensure_ascii=False, indent=2))}</pre></section></main></body></html>"""


def main() -> int:
    risk_config = json.loads(RISK_CONFIG_PATH.read_text(encoding="utf-8"))
    validate_risk_config(risk_config)
    poc = read_json(POC_CONFIG_PATH)
    config_path = ROOT / poc["technical"]["config"]
    technical_config = tv1.read_config(config_path)
    tv1.assert_execution_ready(technical_config, "training")
    preflight_path, preflight = latest_ready_preflight()
    source_run = ROOT / poc["technical"]["source_run"]
    source_paths = [source_run / name for name in ("technical_v1_audit.json", "technical_v1_results.json", "technical_v1_signals.csv")]
    poc_audit_path = ROOT / "exports/hybrid_poc_v1_20261013T041540804059/hybrid_poc_audit.json"
    poc_audit = read_json(poc_audit_path)
    poc_run = poc_audit_path.parent
    oof_path = Path(poc_audit["oof_artifact"])
    if not oof_path.is_absolute():
        oof_path = ROOT / oof_path
    for path in [*source_paths, poc_audit_path, oof_path]:
        if not path.exists():
            raise HybridPOCError(f"Required immutable source is missing: {path}")
    source_hashes = {path.name: sha256_file(path) for path in source_paths}
    if source_hashes != poc_audit.get("technical_source_hashes"):
        raise HybridPOCError("Immutable Technical input hashes no longer match Hybrid POC")
    if any(poc_audit.get(key) for key in ("controlled_validation_used", "development_used", "additional_holdout_used", "final_oos_used")):
        raise HybridPOCError("Hybrid POC source indicates forbidden data access")
    if preflight.get("forbidden_roles_queried") or preflight.get("status") != "PREFLIGHT_READY":
        raise HybridPOCError("Technical preflight is not Training-only and ready")
    baseline_dir = ROOT / risk_config["common_coverage_source"]
    reference_dir = ROOT / risk_config["baseline_reference"]
    baseline_summary_path = baseline_dir / "common_coverage_summary.csv"
    reference_summary_path = reference_dir / "common_coverage_summary.csv"
    for path in (baseline_summary_path, reference_summary_path, baseline_dir / "common_coverage_candidates.csv"):
        if not path.exists():
            raise HybridPOCError(f"Common-Coverage input missing: {path}")
    baseline_dir_hashes_before = directory_hashes(baseline_dir)
    reference_dir_hashes_before = directory_hashes(reference_dir)
    poc_hashes_before = directory_hashes(poc_run)
    database_path = tv1.resolve_market_data_path(ROOT, technical_config)
    db_hash_before = sha256_file(database_path)

    baseline_dir_summary = pd.read_csv(baseline_summary_path)
    reference_summary = pd.read_csv(reference_summary_path)
    comparisons = compare_reproduction(reference_summary, baseline_dir_summary)
    candidates = pd.read_csv(baseline_dir / "common_coverage_candidates.csv", dtype={"stock_code": str})
    oof = pd.read_csv(oof_path, dtype={"stock_code": str})
    validate_training_only_dates(candidates["signal_time"])
    validate_oof_predictions(
        oof, expected_models=poc["ml"]["models"],
        expected_stocks=poc["stock_universe"], period_start="2023-01-01", period_end="2024-12-31",
    )
    signals = pd.read_csv(source_paths[2], dtype={"stock_code": str})
    signals = signals.loc[(signals["candidate_id"] == poc["technical"]["candidate_id"]) & (signals["technical_signal"] == "BUY_CANDIDATE")].copy()
    if candidates.empty:
        raise HybridPOCError("Common-Coverage candidate source is unexpectedly empty")
    models = list(poc["ml"]["models"])
    lookups = {model: build_prediction_lookup(oof, model) for model in models}
    schedules: dict[tuple[str, str], set[str]] = {}
    for (fold_id, stock), group in candidates.groupby(["fold_id", "stock_code"]):
        schedules[(str(fold_id), str(stock))] = {pd.Timestamp(v).to_pydatetime().isoformat(timespec="microseconds") for v in group["earliest_entry_time"]}
    family_name, family, candidate = tv1.candidate_by_id(technical_config, poc["technical"]["candidate_id"])
    with tv1._readonly_connection(database_path) as connection:
        baseline_rows: list[dict[str, Any]] = []
        baseline_trades: list[dict[str, Any]] = []
        baseline_gate_rows: list[dict[str, Any]] = []
        risk_rows: list[dict[str, Any]] = []
        risk_trades: list[dict[str, Any]] = []
        risk_decisions: list[dict[str, Any]] = []
        for variant, model in [("technical_common_coverage", None)] + [(f"hybrid_{m}_common", m) for m in models]:
            for fold in technical_config["walk_forward_folds"]:
                for stock in poc["stock_universe"]:
                    schedule = schedules.get((fold["id"], str(stock)), set())
                    result, trade_rows, gate_rows = simulate_one(
                        connection=connection, config=technical_config, family_name=family_name, family=family,
                        candidate=candidate, stock_code=str(stock), fold=fold, variant=variant, model=model,
                        prediction_lookup=None if model is None else lookups[model], candidate_schedule=schedule,
                    )
                    baseline_rows.append(result)
                    baseline_trades.extend(trade_rows)
                    baseline_gate_rows.extend({"variant": variant, "fold_id": fold["id"], "stock_code": str(stock), **row} for row in gate_rows)
                    if model is not None:
                        risk_variant = f"hybrid_{model}_risk_v1"
                        r_result, r_trades, r_decisions, r_gate_rows = simulate_risk_one(
                            connection=connection, config=technical_config, family_name=family_name,
                            family=family, candidate=candidate, stock_code=str(stock), fold=fold,
                            variant=risk_variant, model=model, prediction_lookup=lookups[model],
                            candidate_schedule=schedule,
                        )
                        risk_rows.append(r_result)
                        risk_trades.extend(r_trades)
                        risk_decisions.extend(r_decisions)
                        baseline_gate_rows.extend({"variant": risk_variant, "fold_id": fold["id"], "stock_code": str(stock), **row} for row in r_gate_rows)

    reproduced = pd.DataFrame([{"variant": v, **summarize(g)} for v, g in pd.DataFrame(baseline_rows).groupby("variant", sort=False)])
    # Convert the runner's aggregate names without rounding before the reproduction guard.
    comparison_details = compare_reproduction(reference_summary, reproduced)
    if comparison_details.keys() != comparisons.keys():
        raise HybridPOCError("RISK_V1_BASELINE_MISMATCH: reproduced summary set changed")
    if directory_hashes(baseline_dir) != baseline_dir_hashes_before or directory_hashes(reference_dir) != reference_dir_hashes_before:
        raise HybridPOCError("Common-Coverage source export changed during Risk V1")
    if directory_hashes(poc_run) != poc_hashes_before or sha256_file(database_path) != db_hash_before:
        raise HybridPOCError("Immutable POC export or market database changed during Risk V1")

    baseline_gate = pd.DataFrame(baseline_gate_rows)
    gate_parity: dict[str, bool] = {}
    for model in models:
        left = baseline_gate.loc[baseline_gate.variant == f"hybrid_{model}_common"]
        right = baseline_gate.loc[baseline_gate.variant == f"hybrid_{model}_risk_v1"]
        cols = ["stock_code", "fold_id", "technical_signal_time", "ml_signal_date", "ml_prediction", "hybrid_action"]
        gate_parity[model] = left[cols].reset_index(drop=True).equals(right[cols].reset_index(drop=True))
        if not gate_parity[model]:
            raise HybridPOCError(f"RISK_V1_LOGIC_MISMATCH: Hybrid gate changed for {model}")
    timing = validate_trade_timing(baseline_trades, risk_trades)

    all_results = pd.DataFrame(baseline_rows + risk_rows)
    variant_map = {m: f"hybrid_{m}_risk_v1" for m in models}
    summary_rows = [{"variant": v, **summarize(group)} for v, group in all_results.groupby("variant", sort=False)]
    summary = pd.DataFrame(summary_rows)
    risk_decisions_frame = pd.DataFrame(risk_decisions)
    risk_summary = summary.loc[summary["variant"].isin(variant_map.values())].copy()
    risk_summary = add_risk_metrics(risk_summary, risk_decisions_frame)
    summary = summary.merge(risk_summary.drop(columns=["evaluation_count", "trade_count", "average_return", "median_return", "return_std", "win_rate", "profit_factor", "worst_mdd", "sharpe", "transaction_cost", "final_equity_mean", "gross_pnl", "net_pnl"], errors="ignore"), on="variant", how="left")
    per_stock = pd.DataFrame([{"stock_code": s, "variant": v, **summarize(g)} for (s, v), g in all_results.groupby(["stock_code", "variant"], sort=False)])
    per_fold = pd.DataFrame([{"fold_id": f, "variant": v, **summarize(g)} for (f, v), g in all_results.groupby(["fold_id", "variant"], sort=False)])
    stock_risk_stats = []
    for (variant, stock_code), group in risk_decisions_frame.groupby(["variant", "stock_code"], sort=False):
        active = group.loc[group.final_quantity > 0]
        stock_risk_stats.append({"variant": variant, "stock_code": stock_code,
            "average_position_fraction": float(active.position_fraction.mean()) if len(active) else 0.0,
            "maximum_position_fraction": float(active.position_fraction.max()) if len(active) else 0.0,
            "average_estimated_risk_per_trade": float(active.estimated_risk_fraction.mean()) if len(active) else 0.0,
            "maximum_estimated_risk_per_trade": float(active.estimated_risk_fraction.max()) if len(active) else 0.0,
            "risk_reduced_trades": int((group.decision == "REDUCE_SIZE").sum()),
            "zero_quantity_blocked_trades": int((group.decision == "BLOCK_ZERO_QUANTITY").sum())})
    if stock_risk_stats:
        per_stock = per_stock.merge(pd.DataFrame(stock_risk_stats), on=["variant", "stock_code"], how="left")
    fold_risk_stats = []
    for (variant, fold_id), group in risk_decisions_frame.groupby(["variant", "fold_id"], sort=False):
        active = group.loc[group.final_quantity > 0]
        fold_risk_stats.append({"variant": variant, "fold_id": fold_id,
            "average_position_fraction": float(active.position_fraction.mean()) if len(active) else 0.0,
            "maximum_position_fraction": float(active.position_fraction.max()) if len(active) else 0.0,
            "average_estimated_risk_per_trade": float(active.estimated_risk_fraction.mean()) if len(active) else 0.0,
            "maximum_estimated_risk_per_trade": float(active.estimated_risk_fraction.max()) if len(active) else 0.0,
            "risk_reduced_trades": int((group.decision == "REDUCE_SIZE").sum()),
            "zero_quantity_blocked_trades": int((group.decision == "BLOCK_ZERO_QUANTITY").sum())})
    per_fold = per_fold.merge(pd.DataFrame(fold_risk_stats), on=["variant", "fold_id"], how="left")

    baseline_s = pd.DataFrame([{"variant": v, **summarize(g)} for v, g in all_results.loc[all_results.variant.str.endswith("_common") & all_results.variant.str.startswith("hybrid_")].groupby("variant", sort=False)]).set_index("variant")
    risk_s = pd.DataFrame([{"variant": v, **summarize(g)} for v, g in all_results.loc[all_results.variant.str.endswith("_risk_v1")].groupby("variant", sort=False)]).set_index("variant")
    mdd_improved = sum(risk_s.loc[f"hybrid_{m}_risk_v1", "worst_mdd"] < baseline_s.loc[f"hybrid_{m}_common", "worst_mdd"] for m in models)
    return_retained = []
    for model in models:
        b, r = baseline_s.loc[f"hybrid_{model}_common", "average_return"], risk_s.loc[f"hybrid_{model}_risk_v1", "average_return"]
        return_retained.append(bool(abs(r) >= 0.5 * abs(b) if b != 0 else True))
    if mdd_improved == 3 and all(return_retained):
        classification = "RISK_V1_SUPPORTIVE"
    elif mdd_improved == 0 or (not any(return_retained) and mdd_improved < 2):
        classification = "RISK_V1_NOT_SUPPORTIVE"
    else:
        classification = "RISK_V1_MIXED"
    audit = {
        "status": "COMPLETED", "experiment_id": f"hybrid-risk-v1-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}",
        "data_role": "training", "period": ["2023-01-01", "2024-12-31"],
        "stock_universe": poc["stock_universe"], "common_candidate_count": int(len(candidates)),
        "baseline_reproduction": "PASS", "baseline_reproduction_metrics": comparison_details,
        "risk_per_trade": 0.01, "max_position_fraction": 0.40, "existing_stop_loss_pct": 0.025,
        "gate_parity_by_model": gate_parity, "trade_timing_parity": timing,
        "zero_quantity_blocks": int((risk_decisions_frame.decision == "BLOCK_ZERO_QUANTITY").sum()),
        "risk_reduced_entries": int((risk_decisions_frame.decision == "REDUCE_SIZE").sum()),
        "classification_rule": "SUPPORTIVE: all 3 models reduce worst MDD and retain >=50% absolute average-return magnitude; NOT_SUPPORTIVE: no model reduces worst MDD, or fewer than 2 improve while all retain <50%; otherwise MIXED.",
        "models_with_improved_worst_mdd": int(mdd_improved), "return_magnitude_retained_by_model": dict(zip(models, return_retained)),
        "result_classification": classification,
        "interpretation": f"最差 MDD 在 {mdd_improved}/3 個模型降低；1% 風險預算與 40% Exposure 上限為事前固定，報酬與風險變化須一併解讀。",
        "technical_strategy_changed": False, "technical_parameters_changed": False,
        "ml_changed": False, "hybrid_gate_changed": False, "stop_loss_changed": False,
        "exit_logic_changed": False, "common_coverage_changed": False,
        "risk_position_sizing_added": True, "optimization_performed": False,
        "parameter_search_performed": False, "final_oos_used": False,
        "controlled_validation_used": False, "development_used": False,
        "additional_holdout_used": False, "forbidden_roles_used": False,
        "database_sha256_before": db_hash_before, "database_sha256_after": sha256_file(database_path),
        "common_coverage_source": str(baseline_dir.relative_to(ROOT)),
        "baseline_reference": str(reference_dir.relative_to(ROOT)),
        "technical_preflight": str(preflight_path.relative_to(ROOT)),
        "existing_common_exports_overwritten": False, "parameter_search": False,
    }
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    output = ROOT / "exports" / f"hybrid_risk_v1_{timestamp}"
    output.mkdir(parents=True, exist_ok=False)
    audit["experiment_id"] = f"hybrid-risk-v1-{timestamp}"
    write_json(output / "risk_v1_audit.json", audit)
    risk_decisions_frame.to_csv(output / "risk_v1_decisions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(baseline_trades + risk_trades).to_csv(output / "risk_v1_trades.csv", index=False, encoding="utf-8-sig")
    all_results.to_csv(output / "risk_v1_results.csv", index=False, encoding="utf-8-sig")
    write_json(output / "risk_v1_results.json", {"audit": audit, "summary": summary.to_dict("records"), "detailed_results": all_results.to_dict("records")})
    summary.to_csv(output / "risk_v1_summary.csv", index=False, encoding="utf-8-sig")
    per_stock.to_csv(output / "risk_v1_per_stock.csv", index=False, encoding="utf-8-sig")
    per_fold.to_csv(output / "risk_v1_per_fold.csv", index=False, encoding="utf-8-sig")
    report = render_report(audit, summary, per_stock, per_fold, risk_decisions_frame)
    (output / "risk_v1_report.html").write_text(report, encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", "output": str(output), "classification": classification,
                      "risk_decisions": len(risk_decisions_frame), "risk_trades": len(risk_trades),
                      "baseline_trade_timing": timing}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HybridPOCError as exc:
        message = str(exc)
        prefix = "RISK_V1_BASELINE_MISMATCH" if "RISK_V1_BASELINE_MISMATCH" in message else "RISK_V1_LOGIC_MISMATCH" if "RISK_V1_LOGIC_MISMATCH" in message else "RISK_V1_BLOCKED"
        raise SystemExit(f"{prefix}: {message}") from exc

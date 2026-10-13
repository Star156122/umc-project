"""Run the Training-only Technical × ML Hybrid POC.

No candidate selection or optimization is performed.  If a legal per-sample
ML V1 Baseline OOF artifact is unavailable, the script replays the already
fixed Phase-B baseline pipeline solely to materialize OOF probabilities.
"""
from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.models import run_multiclass_model  # noqa: E402
from scripts.run_ml_industry_generalization import (  # noqa: E402
    make_scaled_data,
    require_three_classes,
    split_gru_inner_validation,
)
from scripts.run_ml_v1_phase_b import (  # noqa: E402
    build_raw_inputs,
    indices_for_industry_fold,
    load_phase_b_config,
    raw_for_label,
)
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402
from hybrid.core.hybrid_poc_v1 import (  # noqa: E402
    HybridGateTechnicalV1Strategy,
    HybridPOCError,
    assert_entry_only_change,
    build_prediction_lookup,
    canonical_fingerprint,
    html_is_standalone,
    make_alignment_decision,
    sha256_file,
    validate_oof_predictions,
    validate_training_only_dates,
)
from hybrid.core.user_report import render_user_report  # noqa: E402

DEFAULT_CONFIG = ROOT / "hybrid/config/hybrid_poc_v1.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def discover_legal_oof(config: dict[str, Any]) -> tuple[pd.DataFrame | None, Path | None, list[dict[str, str]]]:
    attempts: list[dict[str, str]] = []
    for path in sorted((ROOT / "exports").glob("**/*oof*prediction*.csv"), reverse=True):
        try:
            frame = pd.read_csv(path, dtype={"stock_code": str})
            if "decision_time" not in frame.columns and "signal_date" in frame.columns:
                frame["decision_time"] = frame["signal_date"].astype(str).str[:10] + "T13:30:00+08:00"
            validate_oof_predictions(
                frame,
                expected_models=config["ml"]["models"],
                expected_stocks=config["stock_universe"],
                period_start=config["period"]["start"],
                period_end=config["period"]["end"],
            )
            return frame, path, attempts
        except Exception as exc:  # audit rejected artifacts without trusting them
            attempts.append({"path": str(path), "status": "REJECTED", "reason": str(exc)})
    return None, None, attempts


def rebuild_baseline_oof(
    config: dict[str, Any],
    phase: dict[str, Any],
    research: dict[str, Any],
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    base, input_audit = build_raw_inputs(phase, research)
    raw = raw_for_label(base, "fixed_1pct")
    stocks = list(config["stock_universe"])
    rows: list[dict[str, Any]] = []
    fold_runs: list[dict[str, Any]] = []
    for model in config["ml"]["models"]:
        params = research["models"][model]
        for fold in protocol["walk_forward"]["folds"]:
            fold_number = int(fold["fold"])
            train_idx, validation_idx = indices_for_industry_fold(raw["meta"], stocks, fold)
            require_three_classes(raw["y"][train_idx], f"hybrid-poc/{model}/fold{fold_number} train")
            selected_epochs = None
            inner_start = None
            inner_loss = None
            if model == "gru":
                inner_train, inner_val, inner_start = split_gru_inner_validation(
                    raw, train_idx, float(phase["gru_inner_validation_fraction"]), fold_number, "technology_all"
                )
                inner_data = make_scaled_data(
                    raw,
                    inner_train,
                    {"validation": inner_val},
                    include_context=True,
                    stage="walk_forward_fold",
                    audit_extra={
                        "fold": fold_number,
                        "industry": "technology",
                        "label": "fixed_1pct",
                        "heldout_stock": None,
                        "inner_validation_start": inner_start,
                    },
                )
                _, inner_result, _ = run_multiclass_model(
                    "gru", inner_data, params, evaluation_splits=("validation",)
                )
                selected_epochs = int(inner_result["epochs_completed"])
                inner_loss = inner_result.get("best_validation_loss")
            data = make_scaled_data(
                raw,
                train_idx,
                {"validation": validation_idx},
                include_context=model == "gru",
                stage="walk_forward_outer_refit" if model == "gru" else "walk_forward_fold",
                audit_extra={
                    "fold": fold_number,
                    "industry": "technology",
                    "label": "fixed_1pct",
                    "scaler_fit_samples": int(len(train_idx)),
                    "heldout_stock": None,
                },
            )
            _, result, probabilities = run_multiclass_model(
                model,
                data,
                params,
                fixed_epochs=selected_epochs,
                evaluation_splits=("validation",),
            )
            probability = probabilities["validation"]
            meta = data.metadata["validation"].reset_index(drop=True)
            actual = data.y["validation"]
            predicted = probability.argmax(axis=1)
            names = np.asarray(["SELL", "HOLD", "BUY"])
            for index, item in meta.iterrows():
                rows.append({
                    "model": model,
                    "stock_code": str(item["stock_code"]),
                    "signal_date": str(item["signal_date"])[:10],
                    "decision_time": str(item["signal_date"])[:10] + "T13:30:00+08:00",
                    "entry_date": str(item["entry_date"])[:10],
                    "target_date": str(item["target_date"])[:10],
                    "fold_id": f"ML_WF{fold_number}",
                    "fold_train_start": fold["train_start"],
                    "fold_train_end": fold["train_end"],
                    "fold_validation_start": fold["validation_start"],
                    "fold_validation_end": fold["validation_end"],
                    "prediction": str(names[predicted[index]]),
                    "actual_label": str(names[int(actual[index])]),
                    "prob_sell": float(probability[index, 0]),
                    "prob_hold": float(probability[index, 1]),
                    "prob_buy": float(probability[index, 2]),
                    "is_oof": True,
                    "data_role": "training",
                    "label_version": "ml_v1_fixed_1pct",
                    "prediction_source": "ml_v1_baseline_fixed_1pct_training_walk_forward_oof_rebuilt",
                    "model_parameter_fingerprint": canonical_fingerprint(params),
                    "feature_version": "feature_set_a_v3_24+volume_context" if model == "gru" else "feature_set_a_v3_24",
                })
            fold_runs.append({
                "model": model,
                "fold": fold_number,
                "train_samples": int(len(train_idx)),
                "validation_samples": int(len(validation_idx)),
                "selected_epochs": selected_epochs,
                "inner_validation_start": inner_start,
                "inner_best_validation_loss": inner_loss,
                "outer_epochs_completed": result.get("epochs_completed"),
                "macro_f1": result["splits"]["validation"]["macro_f1"],
                "macro_auc": result["splits"]["validation"]["macro_roc_auc_ovr"],
            })
            print(f"[OOF {model}] fold {fold_number}/4 complete", flush=True)
    frame = pd.DataFrame(rows).sort_values(["model", "signal_date", "stock_code"]).reset_index(drop=True)
    audit = {
        "rebuild_reason": "No existing legal per-sample ML V1 Baseline OOF artifact was available.",
        "pipeline": "existing_ml_v1_phase_b_fixed_1pct",
        "input_audit": input_audit,
        "fold_runs": fold_runs,
        "model_parameters_changed": False,
        "hyperparameter_search": False,
        "probability_threshold_search": False,
        "features_changed": False,
        "label_changed": False,
    }
    return frame, audit


def simulate_one(
    *,
    connection: sqlite3.Connection,
    technical_config: dict[str, Any],
    family_name: str,
    family: dict[str, Any],
    candidate: dict[str, Any],
    stock_code: str,
    fold: dict[str, str],
    variant: str,
    model: str | None,
    prediction_lookup: dict[tuple[str, str], dict[str, Any]] | None,
    candidate_schedule: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    full_frame, evaluation, app, warmup_count, evaluate_lower = tv1._load_fold_frame(
        connection, technical_config, candidate, stock_code, fold
    )
    kwargs = dict(
        config=app,
        study_config=technical_config,
        family_name=family_name,
        family=family,
        candidate=candidate,
        data_role="training",
        fold_id=fold["id"],
        is_backtest=True,
        is_simulation=True,
        local_only=True,
    )
    if model is None:
        strategy = tv1.TechnicalV1Strategy(**kwargs)
    else:
        strategy = HybridGateTechnicalV1Strategy(
            **kwargs,
            ml_model=model,
            prediction_lookup=prediction_lookup or {},
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
    trading_days = sorted({str(row["datetime"])[:10] for row in bar_rows})
    metrics = bt.calculate_performance_metrics(pnl_rows, app.initial_capital, trading_days=trading_days)
    metrics.update(bt.calculate_mark_to_market_risk(trades, bar_rows, app.initial_capital))
    if strategy.local_position != 0:
        raise HybridPOCError(f"Open position remained: {variant}/{stock_code}/{fold['id']}")
    result = {
        "variant": variant,
        "ml_model": model or "",
        "candidate_id": candidate["id"],
        "parameter_fingerprint": tv1.candidate_fingerprint(technical_config, candidate),
        "stock_code": stock_code,
        "fold_id": fold["id"],
        "evaluate_start": fold["evaluate_start"],
        "evaluate_end": fold["evaluate_end"],
        "warmup_bar_count": warmup_count,
        "total_return": float(metrics["total_return"]),
        "initial_capital": float(metrics["initial_capital"]),
        "final_equity": float(metrics["ending_capital"]),
        "net_pnl": float(metrics["net_pnl"]),
        "gross_pnl": float(metrics["gross_pnl"]),
        "win_rate": float(metrics["win_rate"]),
        "profit_factor": float(metrics["profit_factor"]),
        "max_drawdown": float(metrics["max_drawdown"]),
        "sharpe_ratio": float(metrics["sharpe_ratio"]),
        "trade_count": int(metrics["completed_trades"]),
        "transaction_cost": float(metrics["transaction_cost"]),
        "technical_entry_count": int(sum(row["action"] == "Buy" for row in trades)),
    }
    tagged_trades = [{"variant": variant, "ml_model": model or "", "fold_id": fold["id"], **row} for row in trades]
    technical_inputs = [{"variant": variant, "ml_model": model or "", **row} for row in strategy.candidate_signals]
    decisions = list(getattr(strategy, "hybrid_decisions", []))
    return result, tagged_trades, technical_inputs, decisions


def aggregate_results(frame: pd.DataFrame, decisions: pd.DataFrame) -> dict[str, Any]:
    variants: dict[str, Any] = {}
    for variant, group in frame.groupby("variant", sort=False):
        returns = group["total_return"].astype(float)
        variants[str(variant)] = {
            "evaluation_count": int(len(group)),
            "trade_count_total": int(group["trade_count"].sum()),
            "total_return_mean": float(returns.mean()),
            "total_return_median": float(returns.median()),
            "total_return_std": float(returns.std(ddof=1)),
            "total_return_worst": float(returns.min()),
            "win_rate_mean": float(group["win_rate"].mean()),
            "profit_factor_mean": float(group["profit_factor"].replace([np.inf, -np.inf], np.nan).fillna(0).mean()),
            "max_drawdown_worst": float(group["max_drawdown"].max()),
            "sharpe_mean": float(group["sharpe_ratio"].mean()),
            "net_pnl_total": float(group["net_pnl"].sum()),
            "gross_pnl_total": float(group["gross_pnl"].sum()),
            "final_equity_mean": float(group["final_equity"].mean()),
            "transaction_cost_total": float(group["transaction_cost"].sum()),
            "profitable_evaluations": int((returns > 0).sum()),
        }
    decision_counts: dict[str, dict[str, int]] = {}
    if not decisions.empty:
        for model, group in decisions.groupby("model"):
            decision_counts[str(model)] = {str(key): int(value) for key, value in Counter(group["hybrid_action"]).items()}
    baseline_trades = variants["technical_alone"]["trade_count_total"]
    for variant, summary in variants.items():
        summary["entry_reduction_vs_technical"] = (
            0.0 if variant == "technical_alone" or baseline_trades == 0
            else 1.0 - summary["trade_count_total"] / baseline_trades
        )
    return {"variants": variants, "decision_counts": decision_counts}


def table_html(frame: pd.DataFrame, columns: list[str]) -> str:
    shown = frame[columns].copy()
    for column in shown.select_dtypes(include=["float"]).columns:
        shown[column] = shown[column].map(lambda value: f"{value:.4f}")
    return shown.to_html(index=False, border=0, escape=True, classes="data")


def bar_chart(title: str, values: dict[str, float], *, percent: bool = False) -> str:
    maximum = max((abs(float(value)) for value in values.values()), default=1.0) or 1.0
    rows = []
    for name, value in values.items():
        number = float(value)
        width = max(2.0, abs(number) / maximum * 100.0) if number else 0.0
        css = "pos" if number >= 0 else "neg"
        label = f"{number:.2%}" if percent else f"{number:.3f}"
        rows.append(
            f'<div class="barrow"><span>{html.escape(name)}</span><div class="track"><i class="{css}" style="width:{width:.2f}%"></i></div><b>{label}</b></div>'
        )
    return f'<div class="chart"><h3>{html.escape(title)}</h3>{"".join(rows)}</div>'


def render_report(audit: dict[str, Any], summary: dict[str, Any], results: pd.DataFrame, decisions: pd.DataFrame) -> str:
    display_names = {
        "technical_alone": "Technical Alone",
        "hybrid_random_forest": "Hybrid RF",
        "hybrid_xgboost": "Hybrid XGBoost",
        "hybrid_gru": "Hybrid GRU",
    }
    summary_rows = [{"variant": display_names.get(key, key), **value} for key, value in summary["variants"].items()]
    summary_frame = pd.DataFrame(summary_rows)
    candidate_count = int(audit["technical_candidate_count"])
    gate_rows = []
    for model, group in decisions.groupby("model", sort=False):
        counts = Counter(group["hybrid_action"])
        allowed = int(counts.get("ENTRY", 0))
        gate_rows.append({
            "model": model,
            "technical_candidates": candidate_count,
            "matched": int((group["alignment_status"] == "MATCHED").sum()),
            "missing": int((group["alignment_status"] == "MISSING_ML_PREDICTION").sum()),
            "ml_buy": allowed,
            "ml_hold": int(counts.get("BLOCK_HOLD", 0)),
            "ml_sell": int(counts.get("BLOCK_SELL", 0)),
            "hybrid_allowed": allowed,
            "hybrid_blocked": candidate_count - allowed,
            "gate_pass_rate": allowed / candidate_count if candidate_count else 0.0,
        })
    gate_frame = pd.DataFrame(gate_rows)
    stock_frame = (
        results.groupby(["stock_code", "variant"], as_index=False)
        .agg(total_return=("total_return", "mean"), win_rate=("win_rate", "mean"),
             profit_factor=("profit_factor", "mean"), max_drawdown=("max_drawdown", "max"),
             trade_count=("trade_count", "sum"))
    )
    stock_frame["variant"] = stock_frame["variant"].map(display_names)
    fold_frame = (
        results.groupby(["fold_id", "variant"], as_index=False)
        .agg(total_return=("total_return", "mean"), win_rate=("win_rate", "mean"),
             profit_factor=("profit_factor", "mean"), max_drawdown=("max_drawdown", "max"),
             trade_count=("trade_count", "sum"))
    )
    fold_frame["variant"] = fold_frame["variant"].map(display_names)
    preview_columns = [
        "stock_code", "technical_signal_time", "technical_earliest_entry_time", "ml_model",
        "ml_prediction_date", "ml_decision_time", "ml_predicted_class", "hybrid_decision", "alignment_status",
    ]
    preview = decisions[preview_columns].head(50)
    variant_values = summary["variants"]
    charts = "".join([
        bar_chart("平均 Total Return", {display_names[k]: v["total_return_mean"] for k, v in variant_values.items()}, percent=True),
        bar_chart("平均 Win Rate", {display_names[k]: v["win_rate_mean"] for k, v in variant_values.items()}, percent=True),
        bar_chart("最差 Maximum Drawdown", {display_names[k]: v["max_drawdown_worst"] for k, v in variant_values.items()}, percent=True),
        bar_chart("Trade Count", {display_names[k]: v["trade_count_total"] for k, v in variant_values.items()}),
        bar_chart("Gate Pass Rate", {str(row["model"]): row["gate_pass_rate"] for row in gate_rows}, percent=True),
    ])
    hybrid_keys = [key for key in variant_values if key != "technical_alone"]
    strictest = min(gate_rows, key=lambda row: row["hybrid_allowed"])["model"]
    most_open = max(gate_rows, key=lambda row: row["hybrid_allowed"])["model"]
    reduced = [variant_values[key]["entry_reduction_vs_technical"] for key in hybrid_keys]
    lower_mdd = [
        display_names[key] for key in hybrid_keys
        if variant_values[key]["max_drawdown_worst"] < variant_values["technical_alone"]["max_drawdown_worst"]
    ]
    improved_return = [
        display_names[key] for key in hybrid_keys
        if variant_values[key]["total_return_mean"] > variant_values["technical_alone"]["total_return_mean"]
    ]
    objective = (
        f"三個 ML Gate 都降低了實際完成交易數，減少幅度介於 {min(reduced):.1%}～{max(reduced):.1%}。"
        f"最嚴格的 Gate 是 {strictest}，放行最多的是 {most_open}。"
        f"相較 Technical Alone，平均報酬較高的版本為 {', '.join(improved_return) or '無'}；"
        f"最差回撤較低的版本為 {', '.join(lower_mdd) or '無'}。"
        "這些都是 Training OOF POC 現象，只能證明串接流程可運作。"
    )
    source_rows = "".join(
        f"<tr><th>{html.escape(key)}</th><td><code>{html.escape(str(value))}</code></td></tr>"
        for key, value in audit["source_files"].items()
    )
    match = int(audit["alignment"]["matched"])
    missing = int(audit["alignment"]["missing"])
    invalid = int(audit["alignment"]["invalid"])
    total_alignment = match + missing + invalid
    match_rate = match / total_alignment if total_alignment else 0.0
    audit_json = html.escape(json.dumps(audit, ensure_ascii=False, indent=2))
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>Hybrid Technical × ML POC</title>
<style>
body{{font-family:Arial,'Microsoft JhengHei',sans-serif;background:#071521;color:#eaf4ff;margin:0;line-height:1.55}}main{{max-width:1220px;margin:auto;padding:28px}}section{{background:#0d2232;border:1px solid #27465b;border-radius:14px;padding:20px;margin:16px 0;overflow:auto}}h1,h2,h3{{color:#72c7ff}}.tag{{display:inline-block;background:#174b68;padding:6px 10px;border-radius:999px;margin:3px}}.pass{{color:#83efad;font-weight:700}}.warn{{background:#4b2b16;border-color:#9b642d}}.flow{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}.flow b{{background:#12344a;border:1px solid #3b6d89;border-radius:10px;padding:12px}}.flow i{{font-style:normal;color:#72c7ff;font-size:24px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}table.data,table.sources{{border-collapse:collapse;width:100%;font-size:13px}}table.data th,table.data td,table.sources th,table.sources td{{border:1px solid #315268;padding:7px;text-align:right}}table.data th:first-child,table.data td:first-child,table.sources th,table.sources td{{text-align:left}}.chart{{background:#091a27;border-radius:10px;padding:12px}}.barrow{{display:grid;grid-template-columns:135px 1fr 70px;gap:8px;align-items:center;margin:8px 0}}.track{{height:14px;background:#183447;border-radius:7px;overflow:hidden}}.track i{{display:block;height:100%;background:#38a7e8}}.track i.neg{{background:#ef6b73}}code,pre{{font-family:Consolas,monospace}}pre{{white-space:pre-wrap;max-height:360px;overflow:auto;background:#06101a;padding:12px;border-radius:8px}}footer{{border-top:1px solid #315268;margin-top:24px;padding-top:16px;color:#a9c6d8}}
</style></head><body><main>
<h1>Hybrid Technical × ML POC</h1><p class="warn" style="padding:12px;border-radius:10px">此版本為流程驗證版，不代表正式 Final OOS 結果。</p>
<p><span class="tag">BR_SENS_RANGE55_BB20_2</span><span class="tag">Breakout</span><span class="tag">RF / XGBoost / GRU</span><span class="tag">2023–2024 Training OOF</span></p>
<section><h2>A. POC 基本資訊</h2><p>股票：2303、2330、2379、2454、3034。Technical BUY Candidate + 前一交易日 ML BUY → ENTRY；ML HOLD / SELL 或沒有合法匹配 → BLOCK。Long-only。</p></section>
<section><h2>B. 流程圖</h2><div class="flow"><b>Technical Indicators</b><i>→</i><b>BUY Candidate</b><i>→</i><b>Previous Trading Day<br>ML Prediction</b><i>→</i><b>BUY: ENTRY<br>HOLD/SELL: BLOCK</b><i>→</i><b>Hybrid Backtest</b></div></section>
<section><h2>C. Alignment Audit <span class="pass">PASS</span></h2><p>Technical Candidate：{candidate_count} 筆／模型；Matched：{match}；Missing：{missing}；Invalid：{invalid}；整體 Match Rate：{match_rate:.2%}。三個模型使用同一批 248 筆不可變 Technical signal，因此總對齊列數為 {total_alignment}。</p></section>
<section><h2>D. Gate 統計</h2>{table_html(gate_frame, ['model','technical_candidates','matched','missing','ml_buy','ml_hold','ml_sell','hybrid_allowed','hybrid_blocked','gate_pass_rate'])}</section>
<section><h2>E. Technical Alone vs Hybrid</h2>{table_html(summary_frame, ['variant','total_return_mean','net_pnl_total','gross_pnl_total','win_rate_mean','profit_factor_mean','max_drawdown_worst','sharpe_mean','trade_count_total','final_equity_mean','transaction_cost_total'])}</section>
<section><h2>F. 圖表</h2><div class="grid">{charts}</div><p>本輪沒有逐日真實 Equity Curve 輸出，因此不以推估資料補畫。</p></section>
<section><h2>G. 每檔股票結果</h2>{table_html(stock_frame, ['stock_code','variant','total_return','win_rate','profit_factor','max_drawdown','trade_count'])}</section>
<section><h2>H. 每個 Fold 結果</h2>{table_html(fold_frame, ['fold_id','variant','total_return','win_rate','profit_factor','max_drawdown','trade_count'])}</section>
<section><h2>I. Hybrid Decision 明細（前 50 筆）</h2>{preview.to_html(index=False, border=0, escape=True, classes='data')}</section>
<section><h2>J. 客觀摘要</h2><p>{html.escape(objective)}</p></section>
<section class="warn"><h2>研究限制</h2><p>結果來自 2023–2024 Training walk-forward OOF。沒有使用 2025、2026、Holdout 或 Final OOS，也沒有依結果調整參數。此處不宣稱最佳模型、正式泛化能力或未來獲利。</p></section>
<section><h2>Data / Source Files</h2><table class="sources">{source_rows}</table></section>
<section><h2>可重現稽核摘要</h2><pre>{audit_json}</pre></section>
<footer><b>Research Scope:</b> Hybrid POC<br><b>Data:</b> 2023–2024 Training OOF Only<br><b>Holdout Used:</b> {'YES' if audit['additional_holdout_used'] else 'NO'}<br><b>Final OOS Used:</b> {'YES' if audit['final_oos_used'] else 'NO'}<br><b>Technical Modified During Hybrid:</b> {'YES' if audit['technical_exit_or_risk_changed'] else 'NO'}<br><b>ML Modified During Hybrid:</b> {'YES' if audit['features_changed'] or audit['label_changed'] or audit['model_parameters_changed'] else 'NO'}<br><b>Hyperparameter Search:</b> {'YES' if audit['hyperparameter_search'] else 'NO'}<br><b>Probability Threshold Search:</b> {'YES' if audit['probability_threshold_search'] else 'NO'}</footer>
</main></body></html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Training-only Technical × ML Hybrid POC V1")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument(
        "--user-report-only",
        type=Path,
        metavar="RUN_DIRECTORY",
        help="Create only hybrid_poc_user_report.html from an existing completed POC run.",
    )
    return parser.parse_args()


def write_user_report_from_existing_run(output: Path) -> Path:
    """Add the user report without changing the existing research report or data."""
    output = output.resolve()
    research_report = output / "hybrid_poc_report.html"
    audit_path = output / "hybrid_poc_audit.json"
    results_path = output / "hybrid_poc_results.json"
    decisions_path = output / "hybrid_poc_decisions.csv"
    for path in (research_report, audit_path, results_path, decisions_path):
        if not path.exists():
            raise HybridPOCError(f"Existing Hybrid POC artifact is missing: {path}")
    research_hash_before = sha256_file(research_report)
    document = read_json(results_path)
    audit = read_json(audit_path)
    results = pd.DataFrame(document["detailed_results"])
    decisions = pd.read_csv(decisions_path, dtype={"stock_code": str})
    report = render_user_report(audit, document["summary"], results, decisions)
    if not html_is_standalone(report):
        raise HybridPOCError("Generated user HTML is not standalone")
    report_path = output / "hybrid_poc_user_report.html"
    report_path.write_text(report, encoding="utf-8")
    if sha256_file(research_report) != research_hash_before:
        raise HybridPOCError("Existing research report changed while writing the user report")
    return report_path


def main() -> int:
    args = parse_args()
    if args.user_report_only:
        report_path = write_user_report_from_existing_run(args.user_report_only)
        print(json.dumps({
            "status": "USER_REPORT_ADDED",
            "output": str(args.user_report_only.resolve()),
            "research_report": str(args.user_report_only.resolve() / "hybrid_poc_report.html"),
            "user_report": str(report_path),
            "research_report_unchanged": True,
        }, ensure_ascii=False, indent=2))
        return 0
    poc_config = read_json(args.config)
    technical_config_path = ROOT / poc_config["technical"]["config"]
    technical_config = tv1.read_config(technical_config_path)
    assert_entry_only_change(technical_config, poc_config)
    tv1.assert_execution_ready(technical_config, "training")
    if poc_config["data_role"] != "training" or not all(poc_config["forbidden"].values()):
        raise HybridPOCError("POC scope/forbidden-role lock is invalid")

    source_run = ROOT / poc_config["technical"]["source_run"]
    source_audit_path = source_run / "technical_v1_audit.json"
    source_results_path = source_run / "technical_v1_results.json"
    source_signals_path = source_run / "technical_v1_signals.csv"
    for path in (source_audit_path, source_results_path, source_signals_path):
        if not path.exists():
            raise HybridPOCError(f"Missing Technical source artifact: {path}")
    source_audit = read_json(source_audit_path)
    if source_audit.get("status") != "COMPLETED" or source_audit.get("forbidden_roles_queried"):
        raise HybridPOCError("Technical source run is not a clean Training-only completed run")

    phase, research, protocol = load_phase_b_config()
    if research["phase_b"].get("result") != "KEEP_FIXED_1PCT":
        raise HybridPOCError("ML V1 Fixed ±1% baseline is not the frozen Phase-B result")
    frame, reused_path, discovery_attempts = discover_legal_oof(poc_config)
    rebuild_audit: dict[str, Any] = {}
    if frame is None:
        if not poc_config["ml"].get("allow_oof_rebuild_with_existing_baseline_pipeline"):
            raise HybridPOCError("No legal OOF artifact and OOF rebuild is disabled")
        frame, rebuild_audit = rebuild_baseline_oof(poc_config, phase, research, protocol)

    oof_validation = validate_oof_predictions(
        frame,
        expected_models=poc_config["ml"]["models"],
        expected_stocks=poc_config["stock_universe"],
        period_start=poc_config["period"]["start"],
        period_end=poc_config["period"]["end"],
    )
    validate_training_only_dates(frame["signal_date"])
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    output = args.output_root or ROOT / "exports" / f"hybrid_poc_v1_{timestamp}"
    output.mkdir(parents=True, exist_ok=False)
    oof_path = output / "ml_v1_baseline_oof_predictions.csv"
    frame.to_csv(oof_path, index=False, encoding="utf-8-sig")

    family_name, family, candidate = tv1.candidate_by_id(technical_config, poc_config["technical"]["candidate_id"])
    if family_name != "breakout" or candidate["base_strategy"] != "breakout":
        raise HybridPOCError("POC Technical candidate is not the requested breakout candidate")
    source_signals = pd.read_csv(source_signals_path, dtype={"stock_code": str})
    source_signals = source_signals.loc[
        (source_signals["candidate_id"] == candidate["id"])
        & (source_signals["technical_signal"] == "BUY_CANDIDATE")
    ].copy()
    source_signals = source_signals.sort_values(["fold_id", "stock_code", "signal_time"]).reset_index(drop=True)
    if len(source_signals) != 248:
        raise HybridPOCError(f"Expected 248 immutable Technical candidates, found {len(source_signals)}")
    validate_training_only_dates(source_signals["signal_time"])
    database_path = tv1.resolve_market_data_path(ROOT, technical_config)
    results: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    runtime_decisions: list[dict[str, Any]] = []
    lookups = {model: build_prediction_lookup(frame, model) for model in poc_config["ml"]["models"]}
    # Alignment is performed against the immutable exported Technical candidate set.
    decisions: list[dict[str, Any]] = []
    for model in poc_config["ml"]["models"]:
        lookup = lookups[model]
        for signal in source_signals.to_dict("records"):
            signal_time = pd.Timestamp(signal["signal_time"]).to_pydatetime()
            earliest_entry = pd.Timestamp(signal["earliest_entry_time"]).to_pydatetime()
            item = lookup.get((str(signal["stock_code"]), signal_time.date().isoformat()))
            row = make_alignment_decision(
                model=model,
                stock_code=str(signal["stock_code"]),
                technical_fold_id=str(signal["fold_id"]),
                technical_signal_time=signal_time,
                technical_earliest_entry_time=earliest_entry,
                technical_reason=str(signal["reason"]),
                close_at_signal=float(signal["close_at_signal"]),
                prediction=item,
            )
            row.update({
                "technical_version": signal["technical_version"],
                "candidate_id": signal["candidate_id"],
                "parameter_fingerprint": signal["parameter_fingerprint"],
                "entry_rule_id": signal["entry_rule_id"],
                "exit_rule_id": signal["exit_rule_id"],
            })
            decisions.append(row)
    schedules: dict[tuple[str, str], set[str]] = {}
    for (fold_id, stock_code), group in source_signals.groupby(["fold_id", "stock_code"]):
        schedules[(str(fold_id), str(stock_code))] = {
            pd.Timestamp(value).to_pydatetime().isoformat(timespec="microseconds")
            for value in group["earliest_entry_time"]
        }
    with tv1._readonly_connection(database_path) as connection:
        for variant, model in [("technical_alone", None), *[(f"hybrid_{name}", name) for name in poc_config["ml"]["models"]]]:
            for fold in technical_config["walk_forward_folds"]:
                for stock_code in poc_config["stock_universe"]:
                    result, variant_trades, variant_inputs, variant_decisions = simulate_one(
                        connection=connection,
                        technical_config=technical_config,
                        family_name=family_name,
                        family=family,
                        candidate=candidate,
                        stock_code=stock_code,
                        fold=fold,
                        variant=variant,
                        model=model,
                        prediction_lookup=None if model is None else lookups[model],
                        candidate_schedule=None if model is None else schedules[(fold["id"], stock_code)],
                    )
                    results.append(result)
                    trades.extend(variant_trades)
                    inputs.extend(variant_inputs)
                    runtime_decisions.extend(variant_decisions)
                    print(f"[backtest] {variant} {fold['id']} {stock_code}", flush=True)

    results_frame = pd.DataFrame(results)
    trades_frame = pd.DataFrame(trades)
    inputs_frame = source_signals.copy()
    decisions_frame = pd.DataFrame(decisions)
    validate_training_only_dates(results_frame["evaluate_start"])
    if not decisions_frame.empty and not decisions_frame["time_order_valid"].all():
        raise HybridPOCError("Alignment audit contains an invalid time order")

    # Re-run must reproduce the immutable source Technical metrics before Hybrid comparisons are trusted.
    source_document = read_json(source_results_path)
    if isinstance(source_document, dict):
        source_rows = source_document.get("detailed_results", source_document.get("results"))
        if source_rows is None:
            raise HybridPOCError("Technical source JSON has no detailed result rows")
    else:
        source_rows = source_document
    expected = pd.DataFrame([row for row in source_rows if row.get("candidate_id") == candidate["id"]])
    actual = results_frame.loc[results_frame["variant"] == "technical_alone"]
    merged = actual.merge(expected, on=["stock_code", "fold_id"], suffixes=("_rerun", "_source"), validate="one_to_one")
    if len(merged) != 15:
        raise HybridPOCError("Technical source comparison is incomplete")
    for metric in ("total_return", "net_pnl", "trade_count", "transaction_cost"):
        if not np.allclose(merged[f"{metric}_rerun"], merged[f"{metric}_source"], rtol=0, atol=1e-9):
            raise HybridPOCError(f"Technical Alone replay differs from source metric: {metric}")

    results_frame.to_csv(output / "hybrid_poc_results.csv", index=False, encoding="utf-8-sig")
    trades_frame.to_csv(output / "hybrid_poc_trades.csv", index=False, encoding="utf-8-sig")
    inputs_frame.to_csv(output / "technical_signal_input.csv", index=False, encoding="utf-8-sig")
    decisions_frame.to_csv(output / "hybrid_poc_decisions.csv", index=False, encoding="utf-8-sig")
    alignment_columns = [
        "stock_code", "technical_fold_id", "technical_signal_time", "technical_signal_date",
        "technical_earliest_entry_time", "ml_model", "ml_fold_id", "ml_prediction_date",
        "ml_decision_time", "ml_entry_date", "ml_predicted_class", "is_oof",
        "time_order_valid", "alignment_status", "hybrid_decision",
    ]
    decisions_frame[alignment_columns].to_csv(output / "hybrid_poc_alignment.csv", index=False, encoding="utf-8-sig")
    summary = aggregate_results(results_frame, decisions_frame)
    summary.update({
        "status": "COMPLETED_POC_ONLY",
        "candidate_id": candidate["id"],
        "technical_alone_reproduced": True,
        "optimization_performed": False,
        "final_candidate_selected": False,
        "training_only": True,
    })
    write_json(output / "hybrid_poc_results.json", {"detailed_results": results, "summary": summary})
    pd.DataFrame([{"variant": key, **value} for key, value in summary["variants"].items()]).to_csv(
        output / "hybrid_poc_summary.csv", index=False, encoding="utf-8-sig"
    )
    audit = {
        "status": "COMPLETED_POC_ONLY",
        "purpose": poc_config["purpose"],
        "config": str(args.config),
        "config_sha256": sha256_file(args.config),
        "technical_config": str(technical_config_path),
        "technical_config_sha256": sha256_file(technical_config_path),
        "technical_source_run": str(source_run),
        "technical_source_hashes": {
            path.name: sha256_file(path) for path in (source_audit_path, source_results_path, source_signals_path)
        },
        "technical_candidate": candidate["id"],
        "technical_candidate_count": int(len(source_signals)),
        "technical_parameter_fingerprint": tv1.candidate_fingerprint(technical_config, candidate),
        "technical_alone_reproduced": True,
        "oof_reused_from": str(reused_path) if reused_path else None,
        "oof_discovery_attempts": discovery_attempts,
        "oof_rebuild": rebuild_audit,
        "oof_artifact": str(oof_path),
        "oof_artifact_sha256": sha256_file(oof_path),
        "oof_validation": oof_validation,
        "alignment": {
            "rows": int(len(decisions_frame)),
            "matched": int((decisions_frame["alignment_status"] == "MATCHED").sum()),
            "missing": int((decisions_frame["alignment_status"] == "MISSING_ML_PREDICTION").sum()),
            "invalid": int((decisions_frame["time_order_valid"] == False).sum()),
        },
        "source_roles": {"technical": "training", "ml": "training_walk_forward_oof"},
        "controlled_validation_used": False,
        "development_used": False,
        "additional_holdout_used": False,
        "final_oos_used": False,
        "in_sample_predictions_used": False,
        "context_round1_used": False,
        "temporal_round2_used": False,
        "label_changed": False,
        "features_changed": False,
        "model_parameters_changed": False,
        "hyperparameter_search": False,
        "probability_threshold_search": False,
        "technical_exit_or_risk_changed": False,
        "database_modified": False,
        "llm_used": False,
        "frontend_api_modified": False,
        "poc_only": True,
        "final_research_conclusion": False,
        "source_files": {
            "technical_signal_source": str(source_signals_path.relative_to(ROOT)),
            "ml_prediction_source": str(oof_path.relative_to(ROOT)),
            "hybrid_config": str(args.config.relative_to(ROOT) if args.config.is_absolute() else args.config),
            "hybrid_audit": str((output / "hybrid_poc_audit.json").relative_to(ROOT)),
            "hybrid_alignment_csv": str((output / "hybrid_poc_alignment.csv").relative_to(ROOT)),
            "hybrid_decision_csv": str((output / "hybrid_poc_decisions.csv").relative_to(ROOT)),
            "hybrid_results_json": str((output / "hybrid_poc_results.json").relative_to(ROOT)),
            "hybrid_summary_csv": str((output / "hybrid_poc_summary.csv").relative_to(ROOT)),
        },
    }
    write_json(output / "hybrid_poc_audit.json", audit)
    report = render_report(audit, summary, results_frame, decisions_frame)
    if not html_is_standalone(report):
        raise HybridPOCError("Generated HTML is not standalone")
    (output / "hybrid_poc_report.html").write_text(report, encoding="utf-8")
    user_report = render_user_report(audit, summary, results_frame, decisions_frame)
    if not html_is_standalone(user_report):
        raise HybridPOCError("Generated user HTML is not standalone")
    (output / "hybrid_poc_user_report.html").write_text(user_report, encoding="utf-8")
    print(json.dumps({
        "status": audit["status"],
        "output": str(output),
        "audit": str(output / "hybrid_poc_audit.json"),
        "report": str(output / "hybrid_poc_report.html"),
        "user_report": str(output / "hybrid_poc_user_report.html"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HybridPOCError, tv1.TechnicalV1Error) as exc:
        raise SystemExit(f"Hybrid POC blocked: {exc}") from exc

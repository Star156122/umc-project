"""Evaluate ML incremental value on the frozen Technical V2 candidate (Training-only)."""
from __future__ import annotations

import contextlib
import html
import io
import json
import math
import sqlite3
import subprocess
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
    HybridPOCError, build_prediction_lookup, html_is_standalone,
    make_alignment_decision, sha256_file, validate_oof_predictions,
    validate_training_only_dates,
)
from hybrid.core.risk_v1 import RiskV1HybridStrategy, calculate_risk_size  # noqa: E402
from hybrid.core.strategy_v2_batch import regime_confirmation_passes  # noqa: E402
from hybrid.scripts.run_hybrid_common_coverage import (  # noqa: E402
    POC_CONFIG_PATH, directory_hashes, latest_ready_preflight, read_json,
)
from hybrid.scripts.run_hybrid_risk_v1 import validate_risk_config  # noqa: E402
from hybrid.scripts.run_strategy_v2_batch import (  # noqa: E402
    COMMON_RUN, MODELS, RISK_RUN, STOCKS, _entry_key, build_regime_context,
    json_safe, simulate_one, summarize_rows, write_json,
)
from hybrid.scripts.run_strategy_v2_candidate_qualification import (  # noqa: E402
    POC_RUN, QUALIFICATION_BATCH,
)
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402
from trading_system.technical_v1 import TechnicalV1Strategy  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
TECH_V2_CONFIG = ROOT / "configs/technical_v2_candidate.json"
FREEZE_DOC = ROOT / "research/strategy_v2_technical_freeze.md"
NEXT_PLAN = ROOT / "research/strategy_v2_ml_hybrid_next_plan.md"
OOF_PATH = POC_RUN / "ml_v1_baseline_oof_predictions.csv"
CANDIDATES_PATH = COMMON_RUN / "common_coverage_candidates.csv"
COMMON_AUDIT_PATH = COMMON_RUN / "common_coverage_audit.json"
TECH_CANDIDATE_AUDIT = ROOT / "exports/strategy_v2_technical_candidate_20261019T103707108020/technical_v2_candidate_audit.json"
TECH_BATCH_DIR = ROOT / "exports/strategy_v2_technical_batch_20261019T094810074002"
RISK_CONFIG = ROOT / "hybrid/config/risk_v1.json"
VARIANTS = ("TECH_V2_ALONE", "TECH_V2_RF", "TECH_V2_XGB", "TECH_V2_GRU")
MODEL_VARIANT = {"random_forest": "TECH_V2_RF", "xgboost": "TECH_V2_XGB", "gru": "TECH_V2_GRU"}
VARIANT_MODEL = {value: key for key, value in MODEL_VARIANT.items()}
METRIC_KEYS = (
    "average_return", "median_return", "worst_mdd", "win_rate", "profit_factor",
    "sharpe", "trade_count", "gross_pnl", "net_pnl", "transaction_cost", "final_equity_mean",
)


def _training_date(value: Any) -> str:
    date = str(value)[:10]
    if not ("2023-01-01" <= date <= "2024-12-31"):
        raise HybridPOCError(f"Training boundary violation: {date}")
    return date


def select_common_coverage(
    candidates: pd.DataFrame,
    lookups: dict[str, dict[tuple[str, str], dict[str, Any]]],
    previous_sessions: dict[tuple[str, str], str | None],
    regime_context: dict[tuple[str, str], dict[str, Any]],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Keep identical Technical V2-B candidates with legal previous-session OOF from all models."""
    if set(lookups) != set(MODELS):
        raise HybridPOCError("Common coverage requires RF, XGBoost and GRU OOF lookups")
    kept: list[dict[str, Any]] = []
    rejected = {"regime_filter_reject": 0, "missing_model_oof": 0, "invalid_previous_session": 0}
    keys = ["stock_code", "fold_id", "signal_time", "earliest_entry_time"]
    if candidates.duplicated(keys).any():
        raise HybridPOCError("Duplicate frozen Technical candidate identity")
    for row in candidates.to_dict("records"):
        stock = str(row["stock_code"])
        candidate_date = _training_date(row["signal_time"])
        context = regime_context.get((stock, candidate_date))
        if not context or context.get("eligible") is not True or not regime_confirmation_passes(context.get("last_20_proxy_closes", [])):
            rejected["regime_filter_reject"] += 1
            continue
        expected_prior = previous_sessions.get((stock, candidate_date))
        if not expected_prior or expected_prior >= candidate_date:
            rejected["invalid_previous_session"] += 1
            continue
        aligned: dict[str, dict[str, Any]] = {}
        invalid = False
        missing_oof = False
        for model in MODELS:
            prediction = lookups[model].get((stock, candidate_date))
            if prediction is None:
                invalid = True
                missing_oof = True
                break
            if (
                str(prediction.get("signal_date", ""))[:10] != expected_prior
                or str(prediction.get("entry_date", ""))[:10] != candidate_date
                or str(prediction.get("is_oof", "")).lower() != "true"
                or str(prediction.get("data_role")) != "training"
            ):
                rejected["invalid_previous_session"] += 1
                invalid = True
                break
            timestamp = pd.Timestamp(row["signal_time"]).to_pydatetime()
            decision = make_alignment_decision(
                model=model, stock_code=stock, technical_fold_id=str(row["fold_id"]),
                technical_signal_time=timestamp, technical_earliest_entry_time=pd.Timestamp(row["earliest_entry_time"]).to_pydatetime(),
                technical_reason="Frozen Technical V2 common-coverage candidate",
                close_at_signal=float(row.get("close_at_signal", 0.0)), prediction=prediction,
            )
            if decision["alignment_status"] != "MATCHED" or decision["time_order_valid"] is not True:
                rejected["invalid_previous_session"] += 1
                invalid = True
                break
            aligned[model] = prediction
        if invalid:
            if missing_oof:
                rejected["missing_model_oof"] += 1
            continue
        result = dict(row)
        result.update({"stock_code": stock, "candidate_date": candidate_date,
                       "previous_actual_session": expected_prior,
                       "v2_b_regime_reason": context.get("reason", "PASS_PRIOR_PROXY_ABOVE_SMA20")})
        for model, prefix in (("random_forest", "rf"), ("xgboost", "xgb"), ("gru", "gru")):
            pred = aligned[model]
            result[f"{prefix}_prediction"] = str(pred["prediction"])
            result[f"{prefix}_signal_date"] = str(pred["signal_date"])[:10]
            result[f"{prefix}_fold_id"] = str(pred["fold_id"])
            result[f"{prefix}_prob_buy"] = float(pred["prob_buy"])
        kept.append(result)
    frame = pd.DataFrame(kept)
    if frame.empty:
        raise HybridPOCError("ML_V2_COMMON_COVERAGE is empty")
    if frame.duplicated(keys).any():
        raise HybridPOCError("ML_V2_COMMON_COVERAGE contains duplicates")
    return frame, rejected


def classify_incremental_value(
    baseline: dict[str, Any], candidate: dict[str, Any], stock_diffs: pd.DataFrame,
    fold_diffs: pd.DataFrame, *, invalid: bool = False,
) -> str:
    if invalid:
        return "INVALID"
    overall_return = candidate["average_return"] > baseline["average_return"]
    overall_pf = candidate["profit_factor"] >= baseline["profit_factor"]
    overall_risk = candidate["worst_mdd"] <= baseline["worst_mdd"]
    stock_return = int((stock_diffs["average_return_change"] > 0).sum())
    stock_pf = int((stock_diffs["profit_factor_change"] > 0).sum())
    stock_risk = int((stock_diffs["worst_mdd_change"] < 0).sum())
    fold_return = int((fold_diffs["average_return_change"] > 0).sum())
    fold_pf = int((fold_diffs["profit_factor_change"] > 0).sum())
    fold_risk = int((fold_diffs["worst_mdd_change"] < 0).sum())
    # Supported requires broad return and trade-quality evidence; reduced trade
    # count alone cannot qualify because PF/risk and stock/fold stability are required.
    if overall_return and overall_pf and overall_risk and stock_return >= 3 and stock_pf >= 3 and fold_return >= 2 and fold_pf >= 2:
        return "INCREMENTAL_VALUE_SUPPORTED"
    if (not overall_return and not overall_pf and not overall_risk
            and stock_return <= 2 and stock_pf <= 2 and fold_return <= 1 and fold_pf <= 1):
        return "INCREMENTAL_VALUE_NOT_SUPPORTED"
    return "INCREMENTAL_VALUE_MIXED"


class TechnicalV2AloneStrategy(RiskV1HybridStrategy):
    """Technical V2-B plus the exact fixed Risk V1 sizing, with no ML gate."""

    def __init__(self, *args: Any, candidate_schedule: set[str], regime_context: dict,
                 **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.candidate_schedule = candidate_schedule
        self.regime_context = regime_context
        self.ml_model = "technical_only"
        self.filter_audit = {"candidate_checks": 0, "rejected": 0, "missing_context": 0}

    def _try_entry(self, code: str, tick_time: datetime, close: float, snapshot: dict[str, float | None]) -> None:
        reasons = self._entry_reasons(close, snapshot)
        if not reasons:
            return
        signal_time = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TAIPEI)
        signal_time = signal_time.astimezone(TAIPEI)
        if signal_time.isoformat(timespec="microseconds") not in self.candidate_schedule:
            return
        self.filter_audit["candidate_checks"] += 1
        context = self.regime_context.get((str(code), signal_time.date().isoformat()))
        if not context or context.get("eligible") is not True or not regime_confirmation_passes(context.get("last_20_proxy_closes", [])):
            self.filter_audit["rejected"] += 1
            self.filter_audit["missing_context"] += int(context is None)
            return
        decision = {
            "model": "technical_only", "ml_model": "technical_only", "stock_code": str(code),
            "technical_fold_id": str(self.fold_id), "technical_signal": "BUY_CANDIDATE",
            "technical_signal_time": signal_time.isoformat(timespec="microseconds"),
            "technical_candidate_date": signal_time.date().isoformat(),
            "technical_earliest_entry_time": signal_time.isoformat(timespec="microseconds"),
            "technical_reason": ", ".join(reasons), "close_at_signal": float(close),
            "ml_prediction": "NOT_USED", "ml_predicted_class": "NOT_USED",
            "alignment_status": "NOT_APPLICABLE", "time_order_valid": True,
            "hybrid_action": "ENTRY", "hybrid_decision": "ENTRY",
            "regime_filter": "PASS_PRIOR_PROXY_ABOVE_SMA20",
        }
        self.hybrid_decisions.append(decision)
        current_equity = float(self.local_cash or 0.0) + int(self.local_position) * float(close)
        original_quantity = int(TechnicalV1Strategy._order_quantity(self, close))
        sizing = calculate_risk_size(current_equity=current_equity, entry_price=float(close),
            original_quantity=original_quantity, stop_loss_pct=float(self.config.stop_loss_pct))
        self.risk_decisions.append({"timestamp": signal_time.isoformat(timespec="microseconds"),
            "stock_code": str(code), "fold_id": str(self.fold_id), "model": "technical_only", **sizing})
        if sizing["final_quantity"] <= 0:
            self.record_signal(code=code, action="Buy", signal_type="entry", signal_time=tick_time,
                price=close, reason=", ".join(reasons), indicators=snapshot)
            return
        self._risk_quantity_for_entry = int(sizing["final_quantity"])
        try:
            TechnicalV1Strategy._try_entry(self, code, tick_time, close, snapshot)
        finally:
            self._risk_quantity_for_entry = None


def _run_alone_one(*, connection, config, family_name, family, candidate, stock, fold,
                   schedule, regime_context):
    full_frame, evaluation, app, warmup_count, evaluate_lower = tv1._load_fold_frame(connection, config, candidate, stock, fold)
    strategy = TechnicalV2AloneStrategy(
        config=app, study_config=config, family_name=family_name, family=family,
        candidate=candidate, data_role="training", fold_id=fold["id"],
        is_backtest=True, is_simulation=True, local_only=True,
        ml_model="technical_only", prediction_lookup={}, candidate_schedule=schedule,
        regime_context=regime_context,
    )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = evaluate_lower
    stats = bt.MarketDataStats(tick_count=full_frame.height,
        first_timestamp=float(full_frame["kbar_timestamp"][0]), last_timestamp=float(full_frame["kbar_timestamp"][-1]))
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full_frame, stats)
    trades = bt._build_trade_rows(strategy, app)
    pnl = bt._build_pnl_rows(trades)
    bars = bt.build_kbar_rows_from_frame(evaluation, app)
    days = sorted({str(row["datetime"])[:10] for row in bars})
    metrics = bt.calculate_performance_metrics(pnl, app.initial_capital, trading_days=days)
    metrics.update(bt.calculate_mark_to_market_risk(trades, bars, app.initial_capital))
    if strategy.local_position != 0:
        raise HybridPOCError(f"Open position remained: TECH_V2_ALONE/{stock}/{fold['id']}")
    result = {
        "variant": "TECH_V2_ALONE", "model": "technical_only", "stock_code": str(stock), "fold_id": str(fold["id"]),
        "evaluate_start": fold["evaluate_start"], "evaluate_end": fold["evaluate_end"],
        "candidate_id": candidate["id"], "parameter_fingerprint": tv1.candidate_fingerprint(config, candidate),
        "warmup_bar_count": int(warmup_count), "total_return": float(metrics["total_return"]),
        "initial_capital": float(metrics["initial_capital"]), "final_equity": float(metrics["ending_capital"]),
        "net_pnl": float(metrics["net_pnl"]), "gross_pnl": float(metrics["gross_pnl"]),
        "win_rate": float(metrics["win_rate"]), "profit_factor": float(metrics["profit_factor"]),
        "max_drawdown": float(metrics["max_drawdown"]), "sharpe_ratio": float(metrics["sharpe_ratio"]),
        "trade_count": int(metrics["completed_trades"]), "transaction_cost": float(metrics["transaction_cost"]),
        "technical_entry_count": int(sum(row["action"] == "Buy" for row in trades)),
        "candidate_count": int(len(schedule)), "allowed_entry_count": int(len(strategy.hybrid_decisions)),
        "blocked_entry_count": 0,
    }
    tagged_trades = [{"variant": "TECH_V2_ALONE", "model": "technical_only", "stock_code": str(stock), "fold_id": fold["id"], **row} for row in trades]
    gate_rows = [{"variant": "TECH_V2_ALONE", "model": "technical_only", "stock_code": str(stock), "fold_id": fold["id"], **row} for row in strategy.hybrid_decisions]
    risk_rows = [{"variant": "TECH_V2_ALONE", "model": "technical_only", "stock_code": str(stock), "fold_id": fold["id"], **row} for row in strategy.risk_decisions]
    return result, tagged_trades, gate_rows, risk_rows


def _metrics_frame(results: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    all_stockfold = pd.DataFrame([{"variant": v, "model": m, **summarize_rows(g)}
        for (v, m), g in results.groupby(["variant", "model"], sort=False)])
    per_stock = pd.DataFrame([{"variant": v, "model": m, "stock_code": s, **summarize_rows(g)}
        for (v, m, s), g in results.groupby(["variant", "model", "stock_code"], sort=False)])
    fold_rows = []
    for (v, m, f, stock), group in results.groupby(["variant", "model", "fold_id", "stock_code"], sort=False):
        fold_rows.append({"variant": v, "model": m, "fold_id": f, "stock_code": stock, **summarize_rows(group)})
    for (v, m, f), group in results.groupby(["variant", "model", "fold_id"], sort=False):
        fold_rows.append({"variant": v, "model": m, "fold_id": f, "stock_code": "ALL_STOCKS", **summarize_rows(group)})
    per_fold = pd.DataFrame(fold_rows)
    stock_fold = results.copy()
    return all_stockfold, per_stock, per_fold, stock_fold


def build_comparisons(overall: pd.DataFrame, per_stock: pd.DataFrame, per_fold: pd.DataFrame):
    comparisons: list[dict[str, Any]] = []
    stability: dict[str, Any] = {}
    base = overall.loc[overall.variant == "TECH_V2_ALONE"].iloc[0].to_dict()
    for model, variant in MODEL_VARIANT.items():
        got = overall.loc[overall.variant == variant].iloc[0].to_dict()
        row: dict[str, Any] = {"model": model, "variant": variant}
        for metric in METRIC_KEYS:
            row[f"{metric}_technical_alone"] = float(base[metric])
            row[f"{metric}_ml_gate"] = float(got[metric])
            row[f"{metric}_change"] = float(got[metric]) - float(base[metric])
        stock_rows = []
        for stock in STOCKS:
            b = per_stock.loc[(per_stock.variant == "TECH_V2_ALONE") & (per_stock.stock_code == stock)].iloc[0]
            m = per_stock.loc[(per_stock.variant == variant) & (per_stock.stock_code == stock)].iloc[0]
            stock_rows.append({"stock_code": stock,
                "average_return_change": float(m.average_return - b.average_return),
                "worst_mdd_change": float(m.worst_mdd - b.worst_mdd),
                "profit_factor_change": float(m["profit_factor"] - b["profit_factor"]),
                "sharpe_change": float(m["sharpe"] - b["sharpe"])})
        stock_diffs = pd.DataFrame(stock_rows)
        fold_rows = []
        for fold in ("WF1", "WF2", "WF3"):
            b = per_fold.loc[(per_fold.variant == "TECH_V2_ALONE") & (per_fold.fold_id == fold)
                             & (per_fold.stock_code == "ALL_STOCKS")].iloc[0]
            m = per_fold.loc[(per_fold.variant == variant) & (per_fold.fold_id == fold)
                             & (per_fold.stock_code == "ALL_STOCKS")].iloc[0]
            fold_rows.append({"fold_id": fold,
                "average_return_change": float(m.average_return - b.average_return),
                "worst_mdd_change": float(m.worst_mdd - b.worst_mdd),
                "profit_factor_change": float(m["profit_factor"] - b["profit_factor"]),
                "sharpe_change": float(m["sharpe"] - b["sharpe"])})
        fold_diffs = pd.DataFrame(fold_rows)
        for metric, col, improves in (("return", "average_return_change", lambda x: x > 0),
            ("mdd", "worst_mdd_change", lambda x: x < 0), ("profit_factor", "profit_factor_change", lambda x: x > 0),
            ("sharpe", "sharpe_change", lambda x: x > 0)):
            row[f"{metric}_improved_stocks"] = int(stock_diffs[col].map(improves).sum())
            row[f"{metric}_improved_stock_codes"] = ",".join(stock_diffs.loc[stock_diffs[col].map(improves), "stock_code"])
            row[f"{metric}_improved_folds"] = int(fold_diffs[col].map(improves).sum())
            row[f"{metric}_improved_fold_ids"] = ",".join(fold_diffs.loc[fold_diffs[col].map(improves), "fold_id"])
        row["classification"] = classify_incremental_value(base, got, stock_diffs, fold_diffs)
        row["trade_reduction_count"] = int(base["trade_count"] - got["trade_count"])
        row["trade_reduction_rate"] = float((base["trade_count"] - got["trade_count"]) / base["trade_count"]) if base["trade_count"] else 0.0
        comparisons.append(row)
        stability[model] = {"stock_differences": stock_diffs.to_dict("records"),
                            "fold_differences": fold_diffs.to_dict("records")}
    return pd.DataFrame(comparisons), stability


def make_interpretation(row: dict[str, Any]) -> str:
    model = {"random_forest": "RF", "xgboost": "XGBoost", "gru": "GRU"}[row["model"]]
    status = row["classification"]
    ret_count = row["return_improved_stocks"]
    fold_count = row["return_improved_folds"]
    if status == "INCREMENTAL_VALUE_SUPPORTED":
        return f"{model} 在五檔股票中有 {ret_count} 檔平均報酬改善，且三個 Fold 中有 {fold_count} 個改善；報酬、Profit Factor、最差回撤與跨股票／Fold 條件同時符合目前支持判準。"
    if status == "INCREMENTAL_VALUE_NOT_SUPPORTED":
        return f"{model} 的整體報酬、Profit Factor 與最差回撤未呈現改善，股票與 Fold 的報酬／Profit Factor 改善數也偏少，目前不支持它在 Technical V2 上提供穩定增量價值。"
    return f"{model} 在五檔股票有 {ret_count} 檔平均報酬改善、三個 Fold 有 {fold_count} 個改善，但報酬、風險或交易品質證據不一致，目前分類為 MIXED。"


def render_html(audit: dict[str, Any], overall: pd.DataFrame, per_stock: pd.DataFrame,
                per_fold: pd.DataFrame, comparison: pd.DataFrame, common: pd.DataFrame) -> str:
    payload = json.dumps(json_safe({"audit": audit, "overall": overall.to_dict("records"),
        "per_stock": per_stock.to_dict("records"), "per_fold": per_fold.to_dict("records"),
        "comparison": comparison.to_dict("records"), "common_count": len(common)}),
        ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hybrid V2 ML Incremental Value</title>
<style>body{{margin:0;background:#08141f;color:#e8f2fb;font:15px/1.65 system-ui,"Microsoft JhengHei",sans-serif}}main{{max-width:1250px;margin:auto;padding:24px}}header,.panel{{background:#102435;border:1px solid #29475e;border-radius:16px;padding:20px;margin:14px 0}}h1,h2{{margin-top:0}}select{{background:#091a28;color:#fff;border:1px solid #45657c;padding:9px 13px;border-radius:8px;margin:4px}}.muted{{color:#a8c0d1}}.warn{{background:#402a19;border:1px solid #94612c;border-radius:10px;padding:13px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px}}.card{{background:#0a1b29;border:1px solid #29475e;border-radius:10px;padding:12px}}.card b{{display:block;color:#a8c0d1;font-size:12px}}.card strong{{font-size:20px}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}}th,td{{border-bottom:1px solid #29475e;padding:8px;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}.badge{{background:#124f3c;color:#a9f1ce;border-radius:99px;padding:4px 10px}}</style></head><body><main>
<header><span class="badge">2023–2024 Training / Development · Long-only · Single-stock</span><h1>ML 是否為凍結的 Technical V2 增加價值？</h1><p>公平比較 Technical V2 Alone 與各模型 Gate；四個版本共用 {len(common)} 個通過 V2-B 且三模型 OOF 都有效的候選。</p><p class="warn"><b>研究限制：</b>這是已曝光的歷史 Training / Development 評估，不是 Final OOS，也不是 Portfolio Backtest；歷史回測不代表未來績效。Technical V2、ML、Risk V1 與門檻均未修改。</p></header>
<section class="panel"><label>檢視範圍 <select id="scope"><option value="overall">Overall（15 個股票 × Fold 組合）</option>{''.join(f'<option value="{s}">{s} {n}</option>' for s,n in [('2303','聯電'),('2330','台積電'),('2379','瑞昱'),('2454','聯發科'),('3034','聯詠')])}</select></label>
<label>比較版本 <select id="model"><option value="technical_only">Technical V2 Alone</option><option value="random_forest">Random Forest</option><option value="xgboost">XGBoost</option><option value="gru">GRU</option></select></label><div id="cards" class="cards"></div><div class="scroll"><table id="metrics"></table></div></section>
<section class="panel"><h2>與 Technical V2 Alone 的直接比較</h2><div id="interpretation"></div><div class="scroll"><table id="compare"></table></div></section>
<section class="panel"><h2>每個 Fold（各 Fold 跨五檔股票彙總）</h2><div class="scroll"><table id="folds"></table></div></section>
<section class="panel"><h2>共同候選樣本</h2><p>Technical V2 B 通過，且 RF、XGBoost、GRU 都有合法 previous actual trading session OOF 的候選才納入。技術單獨版也使用完全相同樣本。</p><p>Technical V2 Alone 為沒有 ML Gate 的對照；模型版本只在進場前套用該模型的前一交易日 OOF BUY 判定。所有版本仍使用固定 Risk V1。</p><p class="muted">離線單檔頁面：CSS 與 JavaScript 內嵌，沒有 CDN、外部 API 或其他網路依賴。</p></section>
<script>const D=__DATA__;const labels={{technical_only:"Technical V2 Alone",random_forest:"V2 + RF",xgboost:"V2 + XGBoost",gru:"V2 + GRU"}};const names={{"2303":"聯電","2330":"台積電","2379":"瑞昱","2454":"聯發科","3034":"聯詠"}};const f=x=>x==null||!Number.isFinite(Number(x))?"—":(Number(x)*100).toFixed(2)+"%";const n=(x,d=2)=>x==null||!Number.isFinite(Number(x))?"—":Number(x).toLocaleString(undefined,{{maximumFractionDigits:d}});const money=x=>x==null||!Number.isFinite(Number(x))?"—":"NT$ "+Number(x).toLocaleString(undefined,{{maximumFractionDigits:0}});function table(el,rows,cols){{el.innerHTML="<thead><tr>"+cols.map(c=>"<th>"+c[0]+"</th>").join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+cols.map(c=>"<td>"+(c[1](r)??"—")+"</td>").join("")+"</tr>").join("")+"</tbody>"}}function draw(){{const scope=document.querySelector('#scope').value;const model=document.querySelector('#model').value;const variant=model==='technical_only'?'TECH_V2_ALONE':({{random_forest:'TECH_V2_RF',xgboost:'TECH_V2_XGB',gru:'TECH_V2_GRU'}}[model]);let rows=(scope==='overall'?D.overall.filter(r=>r.variant===variant):D.per_stock.filter(r=>r.variant===variant&&r.stock_code===scope));const r=rows[0]||{{}};document.querySelector('#cards').innerHTML=[["平均報酬",f(r.average_return)],["最差 MDD",f(r.worst_mdd)],["勝率",f(r.win_rate)],["Profit Factor",n(r.profit_factor)],["Sharpe",n(r.sharpe)],["交易次數",n(r.trade_count,0)],["Net PnL",money(r.net_pnl)],["交易成本",money(r.transaction_cost)],["Gate 通過率",model==='technical_only'?"100.00%":f((D.audit.gate_metrics[model]||{{}}).acceptance_rate)]].map(x=>`<div class="card"><b>${{x[0]}}</b><strong>${{x[1]}}</strong></div>`).join('');table(document.querySelector('#metrics'),rows,[["範圍",x=>scope==='overall'?"Overall (Stock × Fold)":scope+" "+names[scope]],["版本",x=>labels[model]],["平均報酬",x=>f(x.average_return)],["Median Return",x=>f(x.median_return)],["最差 MDD",x=>f(x.worst_mdd)],["勝率",x=>f(x.win_rate)],["Profit Factor",x=>n(x.profit_factor)],["Sharpe",x=>n(x.sharpe)],["交易次數",x=>n(x.trade_count,0)],["Gross PnL",x=>money(x.gross_pnl)],["Net PnL",x=>money(x.net_pnl)],["交易成本",x=>money(x.transaction_cost)],["期末資產平均",x=>money(x.final_equity_mean)]]);let crows=model==='technical_only'?D.comparison:D.comparison.filter(x=>x.model===model);if(scope!=='overall'){{crows=crows.map(x=>{{const s=D.audit.stock_comparison[scope]?.[x.model]||{{}};return {{...x,...s}}}})}}table(document.querySelector('#compare'),crows,[["模型",x=>({{random_forest:'RF',xgboost:'XGBoost',gru:'GRU'}}[x.model])],["分類",x=>x.classification],["技術單獨報酬",x=>f(x.average_return_technical_alone)],["加入 ML 報酬",x=>f(x.average_return_ml_gate)],["報酬差",x=>f(x.average_return_change)],["MDD 前",x=>f(x.worst_mdd_technical_alone)],["MDD 後",x=>f(x.worst_mdd_ml_gate)],["MDD 差",x=>f(x.worst_mdd_change)],["交易數前",x=>n(x.trade_count_technical_alone,0)],["交易數後",x=>n(x.trade_count_ml_gate,0)],["Net PnL 差",x=>money(x.net_pnl_change)],["成本差",x=>money(x.transaction_cost_change)]]);const interp=model==='technical_only'?"Technical V2 Alone 是三個 ML Gate 共用的對照。選擇模型可查看各模型的增量分類與比較。":(D.audit.interpretations[model]||"");document.querySelector('#interpretation').textContent=interp;let folds=D.per_fold.filter(x=>x.variant===variant);if(scope!=='overall')folds=folds.filter(x=>x.stock_code===scope);table(document.querySelector('#folds'),folds,[["版本",x=>labels[model]],["股票",x=>x.stock_code],["Fold",x=>x.fold_id],["平均報酬",x=>f(x.average_return)],["最差 MDD",x=>f(x.worst_mdd)],["勝率",x=>f(x.win_rate)],["PF",x=>n(x.profit_factor)],["Sharpe",x=>n(x.sharpe)],["交易數",x=>n(x.trade_count,0)],["Net PnL",x=>money(x.net_pnl)],["成本",x=>money(x.transaction_cost)]])}}document.querySelector('#scope').addEventListener('change',draw);document.querySelector('#model').addEventListener('change',draw);draw();</script></main></body></html>'''.replace("__DATA__", payload)


def _prior_session_map(connection: sqlite3.Connection, candidates: pd.DataFrame) -> dict[tuple[str, str], str | None]:
    lower, _ = tv1._timestamp_bounds("2023-01-01", "2024-12-31")
    dates: dict[str, set[str]] = {stock: set() for stock in STOCKS}
    placeholders = ",".join("?" for _ in STOCKS)
    rows = connection.execute(
        f"SELECT stock_code,kbar_timestamp FROM market_kbars WHERE stock_code IN ({placeholders}) AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp",
        (*STOCKS, lower, tv1._timestamp_bounds("2023-01-01", "2024-12-31")[1]),
    ).fetchall()
    for stock, stamp in rows:
        dates[str(stock)].add(datetime.fromtimestamp(int(stamp), TAIPEI).date().isoformat())
    output = {}
    for row in candidates.to_dict("records"):
        stock, day = str(row["stock_code"]), _training_date(row["signal_time"])
        previous = [known for known in dates[stock] if known < day]
        output[(stock, day)] = max(previous) if previous else None
    return output


def _write_conclusion(path: Path, audit: dict[str, Any], comparison: pd.DataFrame,
                      stability: dict[str, Any]) -> None:
    lines = ["# Strategy V2 ML / Hybrid Experiment 001 結論", "",
        "本文件只記錄凍結 Technical V2 上的 ML Gate 增量價值評估；不選 Champion、不建立 Ensemble、不修改或訓練 ML。研究區間為已曝光的 2023–2024 Training/Development。", "",
        f"共同候選數：{audit['common_coverage_count']}。四版本使用相同候選、Technical V2-B、Risk V1、成本、執行規則與 WF1–WF3。", "",
        "## 模型結果", ""]
    for row in comparison.to_dict("records"):
        model = row["model"]
        lines.extend([f"### {model}", "",
            f"- Incremental Value：`{row['classification']}`。",
            f"- Technical V2 Alone 平均報酬 {row['average_return_technical_alone']:.2%}；加 Gate 後 {row['average_return_ml_gate']:.2%}。",
            f"- Worst MDD {row['worst_mdd_technical_alone']:.2%} → {row['worst_mdd_ml_gate']:.2%}；交易數 {int(row['trade_count_technical_alone'])} → {int(row['trade_count_ml_gate'])}。",
            f"- 平均報酬改善股票 {row['return_improved_stocks']}/5（{row['return_improved_stock_codes'] or '無'}）；改善 Fold {row['return_improved_folds']}/3（{row['return_improved_fold_ids'] or '無'}）。",
            f"- PF 改善股票 {row['profit_factor_improved_stocks']}/5；改善 Fold {row['profit_factor_improved_folds']}/3。", ""])
    lines.extend(["## Cross-stock / Cross-fold Evidence", "",
        "詳見 `ml_v2_comparison.csv` 及 `ml_v2_per_stock.csv`、`ml_v2_per_fold.csv`。分類規則要求整體報酬、PF、最差 MDD，且多數股票與 Fold 的報酬及 PF 同時提供支持；交易數下降本身不構成支持證據。", "",
        "## ML Gate 弱點", "",
        "Gate 僅使用前一交易日 OOF BUY 放行；其 HOLD/SELL 都會阻擋 Technical 候選。本輪可呈現覆蓋率和跨股票／Fold 差異，但不能由本輪結果事後調整門檻或挑 Champion。", "",
        "## 下一輪可能方向（僅建議，未執行）", "",
        "1. 依本輪穩定性證據，預先定義 ML Gate 的候選規則和最低 coverage。",
        "2. 若未來要測試其他分類或 Ensemble，先登記假設、比較方法與選擇規則。",
        "3. 維持 Technical V2 凍結；另行批准前不做 ML 重訓、門檻搜尋或 Risk V2。", "",
        "## 限制", "",
        "本報告使用已曝光的 2023–2024 Training/Development，不是 Final OOS，且每個 Stock × Fold 使用獨立初始本金，不是 Portfolio Backtest。歷史結果不保證未來表現。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not all(path.is_file() for path in (TECH_V2_CONFIG, FREEZE_DOC, NEXT_PLAN, TECH_CANDIDATE_AUDIT,
            OOF_PATH, CANDIDATES_PATH, COMMON_AUDIT_PATH, RISK_CONFIG)):
        raise HybridPOCError("A required frozen V2 / OOF / Common Coverage artifact is missing")
    frozen_config = read_json(TECH_V2_CONFIG)
    freeze_audit = read_json(TECH_CANDIDATE_AUDIT)
    if frozen_config.get("status") != "FROZEN_CANDIDATE" or freeze_audit.get("qualification") != "QUALIFIED_FOR_TECHNICAL_V2_FREEZE":
        raise HybridPOCError("Technical V2 candidate is not qualified and frozen")
    if frozen_config.get("base_candidate") != "BR_SENS_RANGE55_BB20_2" or frozen_config.get("market_regime_filter", {}).get("enabled") is not True:
        raise HybridPOCError("Frozen Technical V2 identity is not the expected base plus B")
    if frozen_config.get("source_config_sha256") != sha256_file(ROOT / frozen_config["source_config"]):
        raise HybridPOCError("Frozen Technical source config has changed")
    if frozen_config.get("technical_source_sha256") != sha256_file(ROOT / frozen_config["technical_source"]):
        raise HybridPOCError("Frozen Technical source code has changed")
    if freeze_audit.get("parameter_search_performed") or freeze_audit.get("final_oos_used"):
        raise HybridPOCError("Technical V2 freeze audit has forbidden research flags")

    risk_config = read_json(RISK_CONFIG)
    validate_risk_config(risk_config)
    if (risk_config.get("risk_per_trade") != 0.01 or risk_config.get("max_position_fraction") != 0.40
            or risk_config.get("stop_loss_pct") != 0.025):
        raise HybridPOCError("Risk V1 parameters differ from the frozen experiment contract")
    poc = read_json(POC_CONFIG_PATH)
    technical_config_path = ROOT / poc["technical"]["config"]
    config = tv1.read_config(technical_config_path)
    tv1.assert_execution_ready(config, "training")
    if tuple(poc["stock_universe"]) != STOCKS or tuple(frozen_config["stock_universe"]) != STOCKS:
        raise HybridPOCError("Frozen Technical/ML stock universe changed")
    if [f["id"] for f in config["walk_forward_folds"]] != ["WF1", "WF2", "WF3"]:
        raise HybridPOCError("Frozen WF fold definitions changed")
    preflight_path, preflight = latest_ready_preflight()
    if preflight.get("status") != "PREFLIGHT_READY" or preflight.get("forbidden_roles_queried"):
        raise HybridPOCError("Latest Technical Training preflight is not clean")

    common_audit = read_json(COMMON_AUDIT_PATH)
    if common_audit.get("status") != "PASS" or common_audit.get("training_only") is not True or any(common_audit.get(k) for k in
            ("controlled_validation_used", "development_used", "additional_holdout_used", "final_oos_used", "forbidden_roles_used")):
        raise HybridPOCError("Existing Common Coverage is not a clean Training-only audit")
    candidates = pd.read_csv(CANDIDATES_PATH, dtype={"stock_code": str})
    if len(candidates) != 192 or candidates.duplicated(["stock_code", "fold_id", "signal_time", "earliest_entry_time"]).any():
        raise HybridPOCError("Frozen Common Coverage candidate set identity changed")
    validate_training_only_dates(candidates["signal_time"])
    oof = pd.read_csv(OOF_PATH, dtype={"stock_code": str})
    oof_check = validate_oof_predictions(oof, expected_models=MODELS, expected_stocks=STOCKS,
        period_start="2023-01-01", period_end="2024-12-31")
    if oof_check.get("in_sample_predictions_used") or oof_check.get("forbidden_roles_used"):
        raise HybridPOCError("OOF must be Training-only and out-of-fold")
    lookups = {model: build_prediction_lookup(oof, model) for model in MODELS}
    database_path = tv1.resolve_market_data_path(ROOT, config)
    ml_database_path = ROOT / frozen_config["risk_v1"].get("ml_database_path", "data/market_data.sqlite3")
    if not ml_database_path.is_file():
        ml_database_path = database_path
    db_hash = sha256_file(database_path)
    ml_db_hash = sha256_file(ml_database_path)
    oof_hash = sha256_file(OOF_PATH)
    candidates_hash = sha256_file(CANDIDATES_PATH)
    common_hashes = directory_hashes(COMMON_RUN)
    poc_hashes = directory_hashes(POC_RUN)
    technical_batch_hashes = directory_hashes(TECH_BATCH_DIR)
    qualification_dir = TECH_CANDIDATE_AUDIT.parent
    qualification_hashes = directory_hashes(qualification_dir)
    risk_hashes = directory_hashes(RISK_RUN)

    candidate_dates = set(candidates["signal_time"].astype(str).str[:10])
    candidate_contexts = None
    with tv1._readonly_connection(database_path) as connection:
        regime_context, regime_audit = build_regime_context(connection, STOCKS, candidate_dates)
        prior_sessions = _prior_session_map(connection, candidates)
        common, coverage_rejections = select_common_coverage(candidates, lookups, prior_sessions, regime_context)
        if common.duplicated(["stock_code", "fold_id", "signal_time", "earliest_entry_time"]).any():
            raise HybridPOCError("ML_V2_COMMON_COVERAGE identity is not unique")
        schedules = {(str(fold), str(stock)): {_entry_key(str(stock), str(fold), t)[2]
            for t in group["earliest_entry_time"]}
            for (fold, stock), group in common.groupby(["fold_id", "stock_code"])}
        results_rows: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        gates: list[dict[str, Any]] = []
        risks: list[dict[str, Any]] = []
        technical_only_filter = 0
        for fold in config["walk_forward_folds"]:
            for stock in STOCKS:
                result, trade_rows, gate_rows, risk_rows = _run_alone_one(
                    connection=connection, config=config, family_name="breakout",
                    family=tv1.candidate_by_id(config, frozen_config["base_candidate"])[1],
                    candidate=tv1.candidate_by_id(config, frozen_config["base_candidate"])[2],
                    stock=stock, fold=fold, schedule=schedules.get((fold["id"], stock), set()),
                    regime_context=regime_context)
                if result["candidate_count"] != len(schedules.get((fold["id"], stock), set())):
                    raise HybridPOCError(f"Technical Alone candidate schedule mismatch {stock}/{fold['id']}")
                technical_only_filter += result["allowed_entry_count"]
                results_rows.append(result); trades.extend(trade_rows); gates.extend(gate_rows); risks.extend(risk_rows)
        family_name, family, candidate = tv1.candidate_by_id(config, frozen_config["base_candidate"])
        for model, variant in MODEL_VARIANT.items():
            lookup = lookups[model]
            for fold in config["walk_forward_folds"]:
                for stock in STOCKS:
                    result, trade_rows, gate_rows, risk_rows, _ = simulate_one(
                        connection=connection, config=config, family_name=family_name, family=family,
                        candidate=candidate, stock=stock, fold=fold, version="TECH_V2_B", model=model,
                        lookup=lookup, schedule=schedules.get((fold["id"], stock), set()),
                        volume_context={}, regime_context=regime_context)
                    result["variant"] = variant
                    result["candidate_count"] = int(len(schedules.get((fold["id"], stock), set())))
                    result["allowed_entry_count"] = int(sum(row.get("hybrid_action") == "ENTRY" for row in gate_rows))
                    result["blocked_entry_count"] = int(result["candidate_count"] - result["allowed_entry_count"])
                    results_rows.append(result)
                    trades.extend({**row, "variant": variant} for row in trade_rows)
                    gates.extend({**row, "variant": variant} for row in gate_rows)
                    risks.extend({**row, "variant": variant} for row in risk_rows)
        expected = 60
        results = pd.DataFrame(results_rows)
        if len(results) != expected or results.duplicated(["variant", "model", "stock_code", "fold_id"]).any():
            raise HybridPOCError("Experiment result matrix is incomplete")
        if results.groupby("variant")["candidate_count"].sum().nunique() != 1:
            raise HybridPOCError("Four variants did not use identical candidate counts")
        gate_count = pd.DataFrame(gates).groupby(["variant", "model"]).size()
        for model, variant in MODEL_VARIANT.items():
            if int(gate_count.get((variant, model), 0)) != len(common):
                raise HybridPOCError(f"OOF Gate decision count does not match common coverage for {model}")
        if int(gate_count.get(("TECH_V2_ALONE", "technical_only"), 0)) != len(common):
            raise HybridPOCError("Technical Alone decision count does not match common coverage")

    # Recheck immutable inputs after the simulations.
    if sha256_file(database_path) != db_hash or sha256_file(ml_database_path) != ml_db_hash:
        raise HybridPOCError("Technical or ML database content changed during the run")
    if sha256_file(OOF_PATH) != oof_hash or sha256_file(CANDIDATES_PATH) != candidates_hash:
        raise HybridPOCError("Frozen OOF or Common Coverage input changed during the run")
    if (directory_hashes(COMMON_RUN) != common_hashes or directory_hashes(POC_RUN) != poc_hashes
        or directory_hashes(TECH_BATCH_DIR) != technical_batch_hashes or directory_hashes(qualification_dir) != qualification_hashes
        or directory_hashes(RISK_RUN) != risk_hashes):
        raise HybridPOCError("A frozen input artifact changed during the experiment")

    overall, per_stock, per_fold, stock_fold = _metrics_frame(results)
    comparison, stability = build_comparisons(overall, per_stock, per_fold)
    gate_df = pd.DataFrame(gates)
    gate_metrics: dict[str, Any] = {}
    stock_comparison: dict[str, dict[str, Any]] = {}
    for model, variant in MODEL_VARIANT.items():
        group = gate_df.loc[(gate_df.variant == variant) & (gate_df.model == model)]
        count = int(len(group)); accepted = int((group["hybrid_action"] == "ENTRY").sum())
        gate_metrics[model] = {"candidate_count": count, "allowed_entry_count": accepted,
            "blocked_entry_count": count - accepted,
            "acceptance_rate": accepted / count if count else 0.0,
            "trade_reduction_count": int(overall.loc[overall.variant == "TECH_V2_ALONE", "trade_count"].iloc[0]
                - overall.loc[(overall.variant == variant), "trade_count"].iloc[0]),
            "trade_reduction_rate": float((overall.loc[overall.variant == "TECH_V2_ALONE", "trade_count"].iloc[0]
                - overall.loc[(overall.variant == variant), "trade_count"].iloc[0])
                / overall.loc[overall.variant == "TECH_V2_ALONE", "trade_count"].iloc[0])
                if overall.loc[overall.variant == "TECH_V2_ALONE", "trade_count"].iloc[0] else 0.0}
        for stock in STOCKS:
            b = per_stock.loc[(per_stock.variant == "TECH_V2_ALONE") & (per_stock.stock_code == stock)].iloc[0]
            m = per_stock.loc[(per_stock.variant == variant) & (per_stock.stock_code == stock)].iloc[0]
            stock_comparison.setdefault(stock, {})[model] = {
                **{f"{metric}_technical_alone": float(b[metric]) for metric in METRIC_KEYS},
                **{f"{metric}_ml_gate": float(m[metric]) for metric in METRIC_KEYS},
                **{f"{metric}_change": float(m[metric] - b[metric]) for metric in METRIC_KEYS},
            }
    variant_specs = [("TECH_V2_ALONE", "technical_only"), *[(v, m) for m, v in MODEL_VARIANT.items()]]
    for variant, model in variant_specs:
        summary_mask = (overall.variant == variant) & (overall.model == model)
        decision_group = gate_df.loc[(gate_df.variant == variant) & (gate_df.model == model)]
        candidate_count = int(len(common))
        allowed_count = int((decision_group["hybrid_action"] == "ENTRY").sum())
        blocked_count = candidate_count - allowed_count
        base_trades = int(overall.loc[overall.variant == "TECH_V2_ALONE", "trade_count"].iloc[0])
        model_trades = int(overall.loc[summary_mask, "trade_count"].iloc[0])
        overall.loc[summary_mask, "candidate_count"] = candidate_count
        overall.loc[summary_mask, "allowed_entry_count"] = allowed_count
        overall.loc[summary_mask, "blocked_entry_count"] = blocked_count
        overall.loc[summary_mask, "gate_acceptance_rate"] = allowed_count / candidate_count if candidate_count else 0.0
        overall.loc[summary_mask, "trade_reduction_count"] = base_trades - model_trades
        overall.loc[summary_mask, "trade_reduction_rate"] = (base_trades - model_trades) / base_trades if base_trades else 0.0
    interpretations = {row["model"]: make_interpretation(row) for row in comparison.to_dict("records")}
    audit = {
        "status": "COMPLETED", "experiment_id": "strategy-v2-ml-exp001-incremental-value",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip() or "UNKNOWN",
        "working_tree_status": "DIRTY" if subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip() else "CLEAN",
        "technical_v2_frozen": True, "technical_v2_config": str(TECH_V2_CONFIG.relative_to(ROOT)),
        "technical_v2_config_sha256": sha256_file(TECH_V2_CONFIG),
        "technical_v2_candidate": frozen_config["base_candidate"] + " + TECH_V2_B_MARKET_REGIME_FILTER",
        "technical_database_path": str(database_path.relative_to(ROOT)), "technical_database_sha256": db_hash,
        "ml_database_path": str(ml_database_path.relative_to(ROOT)), "ml_database_sha256": ml_db_hash,
        "shared_technical_ml_database": str(database_path) == str(ml_database_path),
        "oof_artifact": str(OOF_PATH.relative_to(ROOT)), "oof_artifact_sha256": oof_hash,
        "common_coverage_source": str(CANDIDATES_PATH.relative_to(ROOT)), "common_coverage_source_count": int(len(candidates)),
        "common_coverage_count": int(len(common)), "common_coverage_rejections": coverage_rejections,
        "coverage_rule": "Frozen Technical V2-B candidates with a legal previous actual session OOF BUY/HOLD/SELL row for all three models; all variants share this exact set.",
        "oof_validation": oof_check, "regime_context_audit": regime_audit,
        "previous_session_alignment": {"status": "PASS", "all_models_aligned_to_previous_actual_session": True,
            "same_day_or_future_oof_used": False, "candidate_rows_checked": int(len(common))},
        "signal_parity": {"status": "PASS", "common_candidate_count_per_variant": int(len(common)),
            "technical_alone_candidate_decisions": int(len(gates) if False else gate_count.get(("TECH_V2_ALONE", "technical_only"), 0)),
            "all_three_model_decision_count": {m: int(gate_count.get((v, m), 0)) for m, v in MODEL_VARIANT.items()}},
        "stock_universe": list(STOCKS), "folds": frozen_config["walk_forward_folds"],
        "period": {"start": "2023-01-01", "end": "2024-12-31", "role": "TRAINING_DEVELOPMENT_EXPOSED"},
        "risk_parameters": {k: risk_config[k] for k in ("risk_per_trade", "max_position_fraction", "stop_loss_pct")},
        "execution_contract": "completed_5_minute_kbar_signal_to_next_tradable_bar_open",
        "ml_models": list(MODELS), "ml_retrained": False, "ml_parameters_changed": False,
        "threshold_changed": False, "parameter_search_performed": False, "champion_selected": False,
        "ensemble_created": False, "technical_changed": False, "risk_parameters_changed": False,
        "2025_used": False, "2026_used": False, "final_oos_used": False,
        "additional_holdout_used": False, "short_implemented": False, "portfolio_implemented": False,
        "fake_data_used": False,
        "classification_rule": "SUPPORTED requires aggregate return up, PF non-degraded, worst MDD non-worse, plus return and PF improvement in >=3/5 stocks and >=2/3 folds; trade count alone cannot qualify. NOT_SUPPORTED requires aggregate return/PF/MDD all non-improving and <=2 stock, <=1 fold improvements for return and PF. Otherwise MIXED.",
        "gate_metrics": gate_metrics, "stock_comparison": stock_comparison,
        "classifications": {row["model"]: row["classification"] for row in comparison.to_dict("records")},
        "interpretations": interpretations,
        "cross_stock_stability": {m: {k: v for k, v in row.items() if k.endswith("_stocks") or k.endswith("_stock_codes")}
            for m, row in zip(comparison["model"], comparison.to_dict("records"))},
        "cross_fold_stability": {m: {k: v for k, v in row.items() if k.endswith("_folds") or k.endswith("_fold_ids")}
            for m, row in zip(comparison["model"], comparison.to_dict("records"))},
    }
    output = ROOT / "exports" / f"strategy_v2_ml_exp001_{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
    output.mkdir(parents=True, exist_ok=False)
    common.to_csv(output / "ml_v2_common_coverage.csv", index=False, encoding="utf-8-sig")
    overall.to_csv(output / "ml_v2_overall.csv", index=False, encoding="utf-8-sig")
    per_stock.to_csv(output / "ml_v2_per_stock.csv", index=False, encoding="utf-8-sig")
    per_fold.to_csv(output / "ml_v2_per_fold.csv", index=False, encoding="utf-8-sig")
    stock_fold.to_csv(output / "ml_v2_stock_fold.csv", index=False, encoding="utf-8-sig")
    gate_df.to_csv(output / "ml_v2_gate_decisions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(trades).to_csv(output / "ml_v2_trades.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(output / "ml_v2_comparison.csv", index=False, encoding="utf-8-sig")
    report_html = "<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><title>ML V2 Experiment 001 Audit</title><body><h1>ML V2 Experiment 001 — Audit</h1><pre>" + html.escape(json.dumps(json_safe(audit), ensure_ascii=False, indent=2)) + "</pre><h2>Overall</h2><pre>" + html.escape(overall.to_string(index=False)) + "</pre><h2>Model comparison</h2><pre>" + html.escape(comparison.to_string(index=False)) + "</pre></body></html>"
    (output / "ml_v2_exp001_report.html").write_text(report_html, encoding="utf-8")
    user_html = render_html(audit, overall, per_stock, per_fold, comparison, common)
    if not html_is_standalone(user_html):
        raise HybridPOCError("User analysis HTML is not standalone/offline")
    (output / "ml_v2_exp001_user_analysis.html").write_text(user_html, encoding="utf-8")
    _write_conclusion(ROOT / "research/strategy_v2_ml_exp001_conclusion.md", audit, comparison, stability)
    audit["offline_html_check"] = "PASS"
    audit["conclusion_document"] = "research/strategy_v2_ml_exp001_conclusion.md"
    audit["output_files"] = sorted(p.name for p in output.iterdir()) + ["ml_v2_exp001_audit.json"]
    write_json(output / "ml_v2_exp001_audit.json", audit)
    print(json.dumps({"status": audit["status"], "output": str(output.relative_to(ROOT)),
        "common_coverage_count": len(common), "classifications": audit["classifications"],
        "offline_html_check": audit["offline_html_check"], "gate_metrics": gate_metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HybridPOCError, sqlite3.Error, OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"ML_V2_EXP001_BLOCKED: {exc}") from exc

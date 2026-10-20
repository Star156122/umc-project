"""Run the frozen Strategy V2 CONTROL/A/B/C Training-only experiments."""
from __future__ import annotations

import contextlib
import csv
import hashlib
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
    HybridPOCError, build_prediction_lookup, sha256_file,
    validate_oof_predictions, validate_training_only_dates,
)
from hybrid.core.risk_v1 import RiskV1HybridStrategy  # noqa: E402
from hybrid.core.strategy_v2_batch import (  # noqa: E402
    EXPERIMENTS, StrategyV2BatchStrategy, build_plan,
)
from hybrid.scripts.run_hybrid_common_coverage import (  # noqa: E402
    POC_CONFIG_PATH, directory_hashes, latest_ready_preflight, read_json, summarize,
)
from hybrid.scripts.run_hybrid_risk_v1 import validate_risk_config  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
RISK_CONFIG_PATH = ROOT / "hybrid/config/risk_v1.json"
COMMON_RUN = ROOT / "exports/hybrid_common_coverage_20261018T121416162413"
RISK_RUN = ROOT / "exports/hybrid_risk_v1_20261018T122032419364"
MODELS = ("random_forest", "xgboost", "gru")
STOCKS = ("2303", "2330", "2379", "2454", "3034")
VARIANTS = ("CONTROL", "TECH_V2_A", "TECH_V2_B", "TECH_V2_C")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _entry_key(stock: str, fold: str, entry_time: Any) -> tuple[str, str, str]:
    ts = pd.Timestamp(entry_time)
    if ts.tzinfo is None:
        ts = ts.tz_localize(TAIPEI)
    else:
        ts = ts.tz_convert(TAIPEI)
    return str(stock), str(fold), ts.isoformat(timespec="microseconds")


def build_volume_context(connection: sqlite3.Connection, candidates: pd.DataFrame) -> dict[tuple[str, str, str], dict[str, Any]]:
    contexts: dict[tuple[str, str, str], dict[str, Any]] = {}
    cadence = int(technical_config_cadence())
    lower, upper = tv1._timestamp_bounds("2023-01-01", "2024-12-31")
    for row in candidates.to_dict("records"):
        stock, fold, entry = str(row["stock_code"]), str(row["fold_id"]), pd.Timestamp(row["earliest_entry_time"])
        entry_epoch = int(entry.timestamp())
        signal_epoch = entry_epoch - cadence
        if signal_epoch < lower or signal_epoch >= upper:
            raise HybridPOCError("V2-A signal bar escaped the 2023–2024 Training boundary")
        signal_row = connection.execute(
            "SELECT volume FROM market_kbars WHERE stock_code=? AND freq_minutes=5 AND kbar_timestamp=?",
            (stock, signal_epoch),
        ).fetchone()
        prior_rows = connection.execute(
            "SELECT volume FROM market_kbars WHERE stock_code=? AND freq_minutes=5 "
            "AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp DESC LIMIT 20",
            (stock, lower, signal_epoch),
        ).fetchall()
        volumes = [float(r[0]) for r in reversed(prior_rows)]
        key = _entry_key(stock, fold, row["earliest_entry_time"])
        contexts[key] = {
            "signal_volume": float(signal_row[0]) if signal_row else None,
            "prior_volumes": volumes,
            "prior_count": len(volumes),
            "prior_20_volume_mean": float(np.mean(volumes)) if len(volumes) == 20 else None,
            "signal_timestamp": signal_epoch,
            "entry_timestamp": entry_epoch,
            "signal_bar_present": signal_row is not None,
        }
    return contexts


def technical_config_cadence() -> int:
    return 300


def build_regime_context(connection: sqlite3.Connection, stock_codes: tuple[str, ...], candidate_dates: set[str]) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    lower, upper = tv1._timestamp_bounds("2023-01-01", "2024-12-31")
    placeholders = ",".join("?" for _ in stock_codes)
    rows = connection.execute(
        f"SELECT stock_code,kbar_timestamp,close FROM market_kbars "
        f"WHERE stock_code IN ({placeholders}) AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
        "ORDER BY stock_code,kbar_timestamp", (*stock_codes, lower, upper),
    ).fetchall()
    by_day: dict[str, dict[str, list[tuple[int, float]]]] = {}
    for stock, stamp, close in rows:
        dt = datetime.fromtimestamp(int(stamp), TAIPEI)
        if not (datetime.strptime("09:00", "%H:%M").time() <= dt.time() <= datetime.strptime("13:25", "%H:%M").time()):
            continue
        day = dt.date().isoformat()
        by_day.setdefault(day, {}).setdefault(str(stock), []).append((int(stamp), float(close)))
    eligible_days: list[str] = []
    daily_closes: dict[str, dict[str, float]] = {}
    for day, stocks in by_day.items():
        if set(stocks) != set(stock_codes) or any(len(stocks[s]) != 54 for s in stock_codes):
            continue
        eligible_days.append(day)
        daily_closes[day] = {s: stocks[s][-1][1] for s in stock_codes}
    eligible_days.sort()
    contexts: dict[tuple[str, str], dict[str, Any]] = {}
    context_missing = 0
    for target in stock_codes:
        peers = [s for s in stock_codes if s != target]
        proxy = {day: float(np.mean([daily_closes[day][peer] for peer in peers])) for day in eligible_days}
        for candidate_day in candidate_dates:
            prior_days = [day for day in eligible_days if day < candidate_day]
            last_20_days = prior_days[-20:]
            eligible = len(last_20_days) == 20
            values = [proxy[d] for d in last_20_days] if eligible else []
            latest = values[-1] if values else None
            sma = float(np.mean(values)) if values else None
            if not eligible:
                context_missing += 1
            contexts[(target, candidate_day)] = {
                "eligible": bool(eligible and latest is not None and sma is not None and latest > sma),
                "reason": "PASS_PRIOR_PROXY_ABOVE_SMA20" if eligible and latest is not None and sma is not None and latest > sma else ("PROXY_NOT_ABOVE_SMA20" if eligible else "INSUFFICIENT_PRIOR_COMMON_SESSIONS"),
                "target_stock_excluded": True,
                "latest_common_prior_date": prior_days[-1] if prior_days else None,
                "last_20_proxy_closes": values,
                "latest_proxy_close": latest,
                "proxy_sma20": sma,
                "history_count": len(values),
            }
    return contexts, {
        "daily_dates_in_period": int(len(by_day)),
        "complete_global_intersection_days": int(len(eligible_days)),
        "candidate_dates": int(len(candidate_dates)),
        "target_date_contexts": int(len(contexts)),
        "missing_20_session_contexts": int(context_missing),
        "source_rule": "equal-weight arithmetic mean of other four completed closes; common complete 54-bar days; strict prior date; no fill",
    }


def simulate_one(*, connection: sqlite3.Connection, config: dict[str, Any], family_name: str,
                 family: dict[str, Any], candidate: dict[str, Any], stock: str, fold: dict[str, str],
                 version: str, model: str, lookup: dict[tuple[str, str], dict[str, Any]],
                 schedule: set[str], volume_context: dict, regime_context: dict) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    full_frame, evaluation, app, warmup_count, evaluate_lower = tv1._load_fold_frame(connection, config, candidate, stock, fold)
    strategy = StrategyV2BatchStrategy(
        config=app, study_config=config, family_name=family_name, family=family,
        candidate=candidate, data_role="training", fold_id=fold["id"],
        is_backtest=True, is_simulation=True, local_only=True, ml_model=model,
        prediction_lookup=lookup, candidate_schedule=schedule, variant=version,
        volume_context=volume_context, regime_context=regime_context,
    )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = evaluate_lower
    stats = bt.MarketDataStats(tick_count=full_frame.height,
        first_timestamp=float(full_frame["kbar_timestamp"][0]),
        last_timestamp=float(full_frame["kbar_timestamp"][-1]))
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full_frame, stats)
    trades = bt._build_trade_rows(strategy, app)
    pnl = bt._build_pnl_rows(trades)
    bar_rows = bt.build_kbar_rows_from_frame(evaluation, app)
    days = sorted({str(row["datetime"])[:10] for row in bar_rows})
    metrics = bt.calculate_performance_metrics(pnl, app.initial_capital, trading_days=days)
    metrics.update(bt.calculate_mark_to_market_risk(trades, bar_rows, app.initial_capital))
    if strategy.local_position != 0:
        raise HybridPOCError(f"Open position remained after evaluation: {version}/{model}/{stock}/{fold['id']}")
    result = {
        "version": version, "model": model, "stock_code": str(stock), "fold_id": str(fold["id"]),
        "evaluate_start": fold["evaluate_start"], "evaluate_end": fold["evaluate_end"],
        "candidate_id": candidate["id"], "parameter_fingerprint": tv1.candidate_fingerprint(config, candidate),
        "warmup_bar_count": int(warmup_count), "total_return": float(metrics["total_return"]),
        "initial_capital": float(metrics["initial_capital"]), "final_equity": float(metrics["ending_capital"]),
        "net_pnl": float(metrics["net_pnl"]), "gross_pnl": float(metrics["gross_pnl"]),
        "win_rate": float(metrics["win_rate"]), "profit_factor": float(metrics["profit_factor"]),
        "max_drawdown": float(metrics["max_drawdown"]), "sharpe_ratio": float(metrics["sharpe_ratio"]),
        "trade_count": int(metrics["completed_trades"]), "transaction_cost": float(metrics["transaction_cost"]),
        "technical_entry_count": int(sum(r["action"] == "Buy" for r in trades)),
    }
    tagged = [{"version": version, "model": model, "stock_code": str(stock), "fold_id": fold["id"], **row} for row in trades]
    gate_rows = [{"version": version, "model": model, "stock_code": str(stock), "fold_id": fold["id"], **row} for row in strategy.hybrid_decisions]
    return result, tagged, gate_rows, list(strategy.risk_decisions), dict(strategy.filter_audit)


def summarize_rows(frame: pd.DataFrame) -> dict[str, Any]:
    pf = pd.to_numeric(frame["profit_factor"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return {
        "evaluation_count": int(len(frame)), "trade_count": int(frame["trade_count"].sum()),
        "average_return": float(frame["total_return"].mean()), "median_return": float(frame["total_return"].median()),
        "return_std": float(frame["total_return"].std(ddof=1)) if len(frame) > 1 else 0.0,
        "worst_mdd": float(frame["max_drawdown"].max()), "win_rate": float(frame["win_rate"].mean()),
        "profit_factor": float(pf.mean()) if len(pf) else 0.0,
        "sharpe": float(frame["sharpe_ratio"].mean()), "net_pnl": float(frame["net_pnl"].sum()),
        "gross_pnl": float(frame["gross_pnl"].sum()), "transaction_cost": float(frame["transaction_cost"].sum()),
        "final_equity_mean": float(frame["final_equity"].mean()),
    }


def build_summaries(results: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    overall = pd.DataFrame([{"version": v, "model": m, **summarize_rows(g)}
        for (v, m), g in results.groupby(["version", "model"], sort=False)])
    per_stock = pd.DataFrame([{"version": v, "model": m, "stock_code": s, **summarize_rows(g)}
        for (v, m, s), g in results.groupby(["version", "model", "stock_code"], sort=False)])
    per_fold = pd.DataFrame([{"version": v, "model": m, "fold_id": f, **summarize_rows(g)}
        for (v, m, f), g in results.groupby(["version", "model", "fold_id"], sort=False)])
    return overall, per_stock, per_fold


def create_comparison(overall: pd.DataFrame, per_stock: pd.DataFrame, per_fold: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    rows = []
    for model in MODELS:
        control = overall.loc[(overall.version == "CONTROL") & (overall.model == model)].iloc[0]
        for version in EXPERIMENTS:
            got = overall.loc[(overall.version == version) & (overall.model == model)].iloc[0]
            row = {"version": version, "model": model}
            for col in ("average_return", "median_return", "worst_mdd", "win_rate", "profit_factor", "sharpe", "trade_count", "net_pnl", "gross_pnl", "transaction_cost", "final_equity_mean"):
                row[f"{col}_control"] = float(control[col])
                row[f"{col}_variant"] = float(got[col])
                row[f"{col}_change"] = float(got[col] - control[col])
            rows.append(row)
    comparison = pd.DataFrame(rows)
    classes: dict[str, str] = {}
    for version in EXPERIMENTS[1:]:
        model_support = 0
        model_regress = 0
        target_metric = "mdd" if version == "TECH_V2_C" else "pf"
        for model in MODELS:
            row = comparison.loc[(comparison.version == version) & (comparison.model == model)].iloc[0]
            if version == "TECH_V2_C":
                supports = row["worst_mdd_change"] < 0 and row["profit_factor_change"] >= 0
                regresses = row["worst_mdd_change"] > 0 and row["profit_factor_change"] < 0
                cols = ("worst_mdd", "profit_factor")
            else:
                supports = row["profit_factor_change"] > 0 and row["worst_mdd_change"] <= 0
                regresses = row["profit_factor_change"] < 0 and row["worst_mdd_change"] > 0
                cols = ("profit_factor", "worst_mdd")
            model_support += int(supports)
            model_regress += int(regresses)
        stock_non_degraded = 0
        fold_non_degraded = 0
        for stock in STOCKS:
            c_rows = per_stock.loc[(per_stock.version == "CONTROL") & (per_stock.stock_code == stock)]
            v_rows = per_stock.loc[(per_stock.version == version) & (per_stock.stock_code == stock)]
            c_pf = float(pd.to_numeric(c_rows["profit_factor"], errors="coerce").mean())
            v_pf = float(pd.to_numeric(v_rows["profit_factor"], errors="coerce").mean())
            c_mdd = float(pd.to_numeric(c_rows["worst_mdd"], errors="coerce").max())
            v_mdd = float(pd.to_numeric(v_rows["worst_mdd"], errors="coerce").max())
            if version == "TECH_V2_C":
                okay = v_mdd <= c_mdd and v_pf >= c_pf
            else:
                okay = v_pf >= c_pf and v_mdd <= c_mdd
            stock_non_degraded += int(okay)
        for fold in ("WF1", "WF2", "WF3"):
            c_rows = per_fold.loc[(per_fold.version == "CONTROL") & (per_fold.fold_id == fold)]
            v_rows = per_fold.loc[(per_fold.version == version) & (per_fold.fold_id == fold)]
            c_pf = float(pd.to_numeric(c_rows["profit_factor"], errors="coerce").mean())
            v_pf = float(pd.to_numeric(v_rows["profit_factor"], errors="coerce").mean())
            c_mdd = float(pd.to_numeric(c_rows["worst_mdd"], errors="coerce").max())
            v_mdd = float(pd.to_numeric(v_rows["worst_mdd"], errors="coerce").max())
            if version == "TECH_V2_C":
                okay = v_mdd <= c_mdd and v_pf >= c_pf
            else:
                okay = v_pf >= c_pf and v_mdd <= c_mdd
            fold_non_degraded += int(okay)
        if model_support >= 2 and stock_non_degraded >= 3 and fold_non_degraded >= 2:
            classification = "SUPPORTED"
        elif model_regress >= 2 and (stock_non_degraded < 3 or fold_non_degraded < 2):
            classification = "NOT_SUPPORTED"
        else:
            classification = "MIXED"
        classes[version] = classification
    return comparison, classes


def validate_control_reproduction(results: pd.DataFrame, prior: pd.DataFrame) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    metric_cols = ("total_return", "max_drawdown", "win_rate", "profit_factor", "sharpe_ratio", "trade_count", "net_pnl", "transaction_cost", "gross_pnl", "final_equity")
    for model in MODELS:
        current = results.loc[(results.version == "CONTROL") & (results.model == model)].set_index(["stock_code", "fold_id"]).sort_index()
        old = prior.loc[prior.variant == f"hybrid_{model}_risk_v1"].set_index(["stock_code", "fold_id"]).sort_index()
        if len(current) != 15 or len(old) != 15 or not current.index.equals(old.index):
            raise HybridPOCError(f"Control matrix does not align with frozen Risk V1 results for {model}")
        differences = {}
        for metric in metric_cols:
            old_metric = "max_drawdown" if metric == "max_drawdown" else metric
            equal = all(math.isclose(float(current.loc[k, metric]), float(old.loc[k, old_metric]), rel_tol=1e-10, abs_tol=1e-7) for k in current.index)
            differences[metric] = equal
            if not equal:
                raise HybridPOCError(f"Control reproduction mismatch: {model}/{metric}")
        checks[model] = {"status": "PASS", "row_count": 15, "metrics_match": differences}
    return checks


def gate_parity(gates: pd.DataFrame) -> dict[str, Any]:
    output = {}
    # technical_signal_time is part of the index below, so compare only the
    # decision payload columns after aligning on the shared signal identity.
    cols = ["ml_prediction", "hybrid_action", "ml_signal_date", "prob_sell", "prob_hold", "prob_buy"]
    for model in MODELS:
        base = gates.loc[(gates.version == "CONTROL") & (gates.model == model)].set_index(["stock_code", "fold_id", "technical_signal_time"])
        output[model] = {}
        for version in EXPERIMENTS[1:]:
            variant = gates.loc[(gates.version == version) & (gates.model == model)].set_index(["stock_code", "fold_id", "technical_signal_time"])
            shared = base.index.intersection(variant.index)
            same = base.loc[shared, cols].reset_index(drop=True).equals(variant.loc[shared, cols].reset_index(drop=True))
            output[model][version] = {"shared_candidate_decisions": len(shared), "matches_on_shared_decisions": bool(same)}
            if not same:
                raise HybridPOCError(f"Hybrid gate changed for shared candidates: {model}/{version}")
    return output


def render_html(audit: dict[str, Any], overall: pd.DataFrame, comparison: pd.DataFrame,
                per_stock: pd.DataFrame, per_fold: pd.DataFrame, user_facing: bool) -> str:
    data = {"audit": audit, "overall": overall.to_dict("records"), "comparison": comparison.to_dict("records"),
            "per_stock": per_stock.to_dict("records"), "per_fold": per_fold.to_dict("records")}
    payload = json.dumps(json_safe(data), ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    title = "Strategy V2｜Technical 批次實驗分析" if user_facing else "Strategy V2 Technical Batch Research Report"
    intro = "四個版本共用相同股票、OOF、Walk-Forward、風控與成交時序；A/B/C 僅套用各自事前登記的一項改動。" if user_facing else "Training-only independent CONTROL/A/B/C runs with frozen execution and risk rules."
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;background:#091521;color:#e8f2fb;font:15px/1.6 system-ui,"Microsoft JhengHei",sans-serif}}main{{max-width:1250px;margin:auto;padding:28px 18px 50px}}header,.panel{{background:#102435;border:1px solid #29475e;border-radius:16px;padding:20px;margin:16px 0}}header{{background:#10354d;border-color:#2478a8}}h1,h2{{margin-top:0}}.muted{{color:#a8c0d1}}.pill{{display:inline-block;border-radius:99px;background:#163c54;color:#9bd5ff;padding:4px 11px;margin:2px;font-weight:700}}select{{background:#091a28;color:#fff;border:1px solid #45657c;padding:10px 14px;border-radius:9px;font-size:15px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}.metric{{background:#0a1b29;border:1px solid #29475e;padding:13px;border-radius:12px}}.metric b{{display:block;color:#a8c0d1;font-size:12px}}.metric span{{font-size:21px;font-weight:750}}.tablewrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid #29475e;padding:8px;text-align:right;white-space:nowrap}}td:first-child,th:first-child{{text-align:left}}th{{color:#a8c0d1}}.status{{font-size:20px;font-weight:800;color:#7cc6ff}}.warn{{background:#3d2a19;border:1px solid #845b28;padding:12px;border-radius:10px}}details{{margin-top:12px}}summary{{cursor:pointer;color:#85cbff}}footer{{color:#a8c0d1;font-size:13px;margin-top:24px}}@media(max-width:600px){{main{{padding:14px 10px}}header,.panel{{padding:15px}}}}</style></head><body><main><header><span class="pill">2023–2024 Training / Development</span><span class="pill">Long-only · Single-stock</span><h1>{title}</h1><p>{intro}</p><p>執行狀態：<strong id="runStatus"></strong>　分類：<strong id="overallStatus"></strong></p><label>切換模型　<select id="model"><option value="random_forest">Random Forest</option><option value="xgboost">XGBoost</option><option value="gru">GRU</option></select></label></header>
<section class="panel"><h2>四版本總覽</h2><div id="cards" class="cards"></div><div class="tablewrap"><table id="overview"></table></div><p class="muted">平均報酬採每個股票-Fold 的報酬平均；最大回撤取所有股票-Fold 中最差值。沒有以報酬單一指標選 Winner。</p></section>
<section class="panel"><h2>每檔股票</h2><div class="tablewrap"><table id="stocks"></table></div></section><section class="panel"><h2>每個 Walk-Forward Fold</h2><div class="tablewrap"><table id="folds"></table></div></section>
<section class="panel"><h2>研究解讀</h2><p id="classification"></p><p class="muted">SUPPORTED / MIXED / NOT_SUPPORTED 依預先固定的 Profit Factor、最差回撤及跨模型、股票、Fold 一致性規則分類。INVALID 代表資料或執行檢查失敗。</p><div class="warn">A/B/C 是彼此獨立的單一改動版本，沒有測試任何組合。結果來自已曝光的 2023–2024 Training/Development，不代表未來績效。</div></section>
<section class="panel"><details><summary>查看進階稽核資訊</summary><pre id="audit" style="white-space:pre-wrap;overflow:auto"></pre></details></section><footer>沒有重新訓練 ML，沿用 RF / XGBoost / GRU OOF。沒有使用 2025、2026 或 Final OOS；沒有參數搜尋。回測不代表未來績效。</footer></main>
<script>const DATA=__DATA__;const names={{CONTROL:"Control",TECH_V2_A:"Technical V2-A",TECH_V2_B:"Technical V2-B",TECH_V2_C:"Technical V2-C"}};const f=(x,p=2)=>x==null||!Number.isFinite(Number(x))?"—":(Number(x)*100).toFixed(p)+"%";const n=(x,d=2)=>x==null||!Number.isFinite(Number(x))?"—":Number(x).toLocaleString(undefined,{{maximumFractionDigits:d}});const money=x=>x==null||!Number.isFinite(Number(x))?"—":"NT$ "+Number(x).toLocaleString(undefined,{{maximumFractionDigits:0}});function table(el,rows,cols){{el.innerHTML="<thead><tr>"+cols.map(c=>"<th>"+c[0]+"</th>").join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+cols.map(c=>"<td>"+(c[1](r)??"—")+"</td>").join("")+"</tr>").join("")+"</tbody>"}}function draw(){{const m=document.querySelector('#model').value;const rows=DATA.overall.filter(r=>r.model===m);document.querySelector('#runStatus').textContent=DATA.audit.status;document.querySelector('#overallStatus').textContent="A: "+DATA.audit.classifications.TECH_V2_A+" / B: "+DATA.audit.classifications.TECH_V2_B+" / C: "+DATA.audit.classifications.TECH_V2_C;document.querySelector('#cards').innerHTML=rows.map(r=>`<div class="metric"><b>${{names[r.version]}}</b><span>${{f(r.average_return)}}</span><div class="muted">平均報酬</div></div>`).join('');table(document.querySelector('#overview'),rows,[["版本",r=>names[r.version]],["平均報酬",r=>f(r.average_return)],["最大回撤",r=>f(r.worst_mdd)],["勝率",r=>f(r.win_rate)],["Profit Factor",r=>n(r.profit_factor)],["Sharpe",r=>n(r.sharpe)],["交易次數",r=>n(r.trade_count,0)],["Net PnL",r=>money(r.net_pnl)],["交易成本",r=>money(r.transaction_cost)]]);const s=DATA.per_stock.filter(r=>r.model===m);table(document.querySelector('#stocks'),s,[["版本",r=>names[r.version]],["股票",r=>r.stock_code],["平均報酬",r=>f(r.average_return)],["最大回撤",r=>f(r.worst_mdd)],["勝率",r=>f(r.win_rate)],["PF",r=>n(r.profit_factor)],["Sharpe",r=>n(r.sharpe)],["交易次數",r=>n(r.trade_count,0)],["Net PnL",r=>money(r.net_pnl)],["成本",r=>money(r.transaction_cost)]]);const ff=DATA.per_fold.filter(r=>r.model===m);table(document.querySelector('#folds'),ff,[["版本",r=>names[r.version]],["Fold",r=>r.fold_id],["平均報酬",r=>f(r.average_return)],["最大回撤",r=>f(r.worst_mdd)],["勝率",r=>f(r.win_rate)],["PF",r=>n(r.profit_factor)],["Sharpe",r=>n(r.sharpe)],["交易次數",r=>n(r.trade_count,0)],["Net PnL",r=>money(r.net_pnl)],["成本",r=>money(r.transaction_cost)]]);document.querySelector('#classification').textContent=DATA.audit.interpretation;document.querySelector('#audit').textContent=JSON.stringify(DATA.audit,null,2)}}document.querySelector('#model').addEventListener('change',draw);draw();</script></body></html>'''.replace("__DATA__", payload)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", nargs="+", choices=VARIANTS, required=True,
                        help="Explicitly execute independent frozen versions; pass CONTROL TECH_V2_A TECH_V2_B TECH_V2_C")
    args = parser.parse_args()
    if tuple(args.execute) != VARIANTS:
        raise SystemExit("This preregistered batch requires exactly CONTROL TECH_V2_A TECH_V2_B TECH_V2_C, in that order")
    plan = build_plan(ROOT, args.execute)
    prereg_texts = {k: (ROOT / v["preregistration"]).read_text(encoding="utf-8") for k, v in zip(args.execute, plan)}
    for version, text in prereg_texts.items():
        if version != "CONTROL" and "PREREGISTERED" not in text:
            raise HybridPOCError(f"Missing frozen preregistration for {version}")

    risk_config = json.loads(RISK_CONFIG_PATH.read_text(encoding="utf-8"))
    validate_risk_config(risk_config)
    poc = read_json(POC_CONFIG_PATH)
    config_path = ROOT / poc["technical"]["config"]
    config = tv1.read_config(config_path)
    tv1.assert_execution_ready(config, "training")
    if tuple(poc["stock_universe"]) != STOCKS or tuple(config["walk_forward_folds"][i]["id"] for i in range(3)) != ("WF1", "WF2", "WF3"):
        raise HybridPOCError("Frozen universe/fold contract changed")
    preflight_path, preflight = latest_ready_preflight()
    if preflight.get("status") != "PREFLIGHT_READY" or preflight.get("forbidden_roles_queried"):
        raise HybridPOCError("Technical Training preflight is not ready / role-safe")

    source_run = ROOT / poc["technical"]["source_run"]
    source_paths = [source_run / n for n in ("technical_v1_audit.json", "technical_v1_results.json", "technical_v1_signals.csv")]
    poc_audit_path = ROOT / "exports/hybrid_poc_v1_20261013T041540804059/hybrid_poc_audit.json"
    poc_audit = read_json(poc_audit_path)
    source_hashes = {p.name: sha256_file(p) for p in source_paths}
    if source_hashes != poc_audit.get("technical_source_hashes"):
        raise HybridPOCError("Frozen Technical signal source hashes do not match the baseline POC")
    oof_path = ROOT / "exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv"
    common_audit_path = COMMON_RUN / "common_coverage_audit.json"
    candidates_path = COMMON_RUN / "common_coverage_candidates.csv"
    if not all(p.is_file() for p in [*source_paths, oof_path, common_audit_path, candidates_path, RISK_RUN / "risk_v1_results.csv"]):
        raise HybridPOCError("Required frozen Technical/OOF/Common-Coverage/Risk V1 artifacts are missing")
    common_audit = read_json(common_audit_path)
    if common_audit.get("status") != "PASS" or common_audit.get("training_only") is not True or any(common_audit.get(k) for k in ("controlled_validation_used","development_used","additional_holdout_used","final_oos_used","forbidden_roles_used")):
        raise HybridPOCError("Common-Coverage audit is not a clean Training-only source")
    candidates = pd.read_csv(candidates_path, dtype={"stock_code": str})
    validate_training_only_dates(candidates["signal_time"])
    if len(candidates) != int(common_audit["common_candidate_count"]):
        raise HybridPOCError("Common-Coverage candidate count mismatch")
    if candidates["stock_code"].astype(str).isin(STOCKS).all() is False:
        raise HybridPOCError("Common-Coverage contains an unexpected stock")
    if candidates.duplicated(["stock_code","fold_id","signal_time","earliest_entry_time"]).any():
        raise HybridPOCError("Duplicate Common-Coverage candidate identity")
    oof = pd.read_csv(oof_path, dtype={"stock_code": str})
    oof_validation = validate_oof_predictions(oof, expected_models=MODELS, expected_stocks=STOCKS,
        period_start="2023-01-01", period_end="2024-12-31")
    if oof_validation.get("forbidden_roles_used") or oof_validation.get("in_sample_predictions_used"):
        raise HybridPOCError("OOF source has forbidden provenance")
    lookups = {model: build_prediction_lookup(oof, model) for model in MODELS}
    schedules: dict[tuple[str,str], set[str]] = {}
    for (fold_id, stock), group in candidates.groupby(["fold_id","stock_code"]):
        schedules[(str(fold_id),str(stock))] = {_entry_key(str(stock), str(fold_id), v)[2] for v in group["earliest_entry_time"]}

    database_path = tv1.resolve_market_data_path(ROOT, config)
    database_hash_before = sha256_file(database_path)
    common_hashes_before = directory_hashes(COMMON_RUN)
    poc_hashes_before = directory_hashes(poc_audit_path.parent)
    risk_hashes_before = directory_hashes(RISK_RUN)
    source_hashes_before = {p.name: sha256_file(p) for p in source_paths}
    risk_reference = pd.read_csv(RISK_RUN / "risk_v1_results.csv", dtype={"stock_code": str})
    family_name, family, candidate = tv1.candidate_by_id(config, poc["technical"]["candidate_id"])
    if family_name != "breakout" or candidate["id"] != "BR_SENS_RANGE55_BB20_2":
        raise HybridPOCError("Declared V2 Control Candidate changed")

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    output = ROOT / "exports" / f"strategy_v2_technical_batch_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    results_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    filter_audit: dict[str, dict[str,int]] = {v: {"candidate_checks":0,"rejected":0,"missing_context":0} for v in VARIANTS}
    with tv1._readonly_connection(database_path) as connection:
        vol_context = build_volume_context(connection, candidates)
        regime_context, regime_audit = build_regime_context(connection, STOCKS, set(candidates["signal_time"].astype(str).str[:10]))
        for version in VARIANTS:
            for model in MODELS:
                for fold in config["walk_forward_folds"]:
                    for stock in STOCKS:
                        result, trade_rows, gates, risk_decisions, local_filter = simulate_one(
                            connection=connection, config=config, family_name=family_name, family=family,
                            candidate=candidate, stock=stock, fold=fold, version=version, model=model,
                            lookup=lookups[model], schedule=schedules.get((fold["id"],stock),set()),
                            volume_context=vol_context, regime_context=regime_context,
                        )
                        results_rows.append(result)
                        trades.extend(trade_rows)
                        gate_rows.extend(gates)
                        risk_rows.extend([{"version":version,"model":model,"stock_code":stock,"fold_id":fold["id"],**r} for r in risk_decisions])
                        for k,v in local_filter.items():
                            filter_audit[version][k] += int(v)

    results = pd.DataFrame(results_rows)
    expected = {(v,m,s,f["id"]) for v in VARIANTS for m in MODELS for s in STOCKS for f in config["walk_forward_folds"]}
    actual = set(map(tuple, results[["version","model","stock_code","fold_id"]].astype(str).itertuples(index=False,name=None)))
    if actual != expected or len(results) != len(expected):
        raise HybridPOCError(f"Incomplete batch matrix: missing={sorted(expected-actual)}")
    control_reproduction = validate_control_reproduction(results, risk_reference)
    gate_audit = gate_parity(pd.DataFrame(gate_rows))
    if sha256_file(database_path) != database_hash_before:
        raise HybridPOCError("Raw Technical database changed during read-only batch")
    if directory_hashes(COMMON_RUN) != common_hashes_before or directory_hashes(poc_audit_path.parent) != poc_hashes_before or directory_hashes(RISK_RUN) != risk_hashes_before:
        raise HybridPOCError("An immutable source export changed during the batch")
    if {p.name: sha256_file(p) for p in source_paths} != source_hashes_before:
        raise HybridPOCError("Immutable Technical source changed during batch")

    overall, per_stock, per_fold = build_summaries(results)
    comparison, classifications = create_comparison(overall, per_stock, per_fold)
    audit = {
        "status":"COMPLETED", "experiment_id":f"strategy-v2-technical-batch-{stamp}", "generated_at":datetime.now().astimezone().isoformat(timespec="seconds"),
        "classification":"BATCH_COMPLETED", "classifications":classifications,
        "classification_rule_source":"research/strategy_v2_technical_batch_execution.md",
        "execution_contract":"completed_kbar_signal_to_next_tradable_5min_bar_open",
        "data_role":"training_development_exposed", "period":{"start":"2023-01-01","end":"2024-12-31"},
        "stock_universe":list(STOCKS), "technical_candidate":"BR_SENS_RANGE55_BB20_2",
        "models":list(MODELS), "folds":[f["id"] for f in config["walk_forward_folds"]],
        "common_coverage_candidate_count":int(len(candidates)), "result_matrix_rows":int(len(results)),
        "risk_parameters":{"risk_per_trade":0.01,"max_position_fraction":0.40,"stop_loss_pct":0.025},
        "technical_config_sha256":sha256_file(config_path), "technical_source_sha256":sha256_file(ROOT/"trading_system/technical_v1.py"),
        "technical_database_sha256_before":database_hash_before,"technical_database_sha256_after":sha256_file(database_path),
        "oof_path":str(oof_path.relative_to(ROOT)),"oof_sha256":sha256_file(oof_path),
        "common_coverage_path":str(COMMON_RUN.relative_to(ROOT)),"common_coverage_audit":common_audit,
        "control_reproduction_against_risk_v1":control_reproduction,
        "hybrid_gate_parity_on_shared_candidate_decisions":gate_audit,
        "v2b_regime_context":regime_audit,"filter_audit":filter_audit,
        "variant_rules":{"CONTROL":"unchanged Technical V2 control","TECH_V2_A":"completed signal volume > mean of preceding 20 completed bars; signal bar excluded","TECH_V2_B":"target-excluded equal-weight peer close proxy above trailing 20 common-session SMA using latest prior session","TECH_V2_C":"replace breakout low-30 exit with completed close below EMA20; retain stop/min hold/other safety exits"},
        "independent_variants_only":True,"combination_variants_run":False,"parameter_search_performed":False,
        "ml_retrained":False,"oof_reused":True,"hybrid_gate_changed":False,"risk_parameters_changed":False,
        "technical_database_modified":False,"technical_source_modified":False,"2025_used":False,"2026_used":False,
        "final_oos_used":False,"short_implemented":False,"portfolio_enabled":False,
        "control_signal_parity":"PASS_SOURCE_CANDIDATE_IDENTITY_FROZEN","execution_contract":"NEXT_TRADABLE_BAR_OPEN",
        "no_future_bar_used_before_signal":True,"output_files":[],
    }
    for version in VARIANTS:
        results.loc[results.version == version].to_csv(output/f"technical_v2_{'control' if version=='CONTROL' else 'exp'+version[-1]}.csv",index=False,encoding="utf-8-sig")
    per_stock.to_csv(output/"technical_v2_per_stock.csv",index=False,encoding="utf-8-sig")
    per_fold.to_csv(output/"technical_v2_per_fold.csv",index=False,encoding="utf-8-sig")
    comparison.to_csv(output/"technical_v2_comparison.csv",index=False,encoding="utf-8-sig")
    results.to_csv(output/"technical_v2_results.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(trades).to_csv(output/"technical_v2_trades.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(gate_rows).to_csv(output/"technical_v2_gate_decisions.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(risk_rows).to_csv(output/"technical_v2_risk_decisions.csv",index=False,encoding="utf-8-sig")
    audit["output_files"] = sorted(p.name for p in output.iterdir()) + ["technical_v2_report.html","technical_v2_user_analysis.html","technical_v2_batch_audit.json"]
    write_json(output/"technical_v2_batch_audit.json",audit)
    (output/"technical_v2_report.html").write_text(render_html(audit,overall,comparison,per_stock,per_fold,False),encoding="utf-8")
    (output/"technical_v2_user_analysis.html").write_text(render_html(audit,overall,comparison,per_stock,per_fold,True),encoding="utf-8")
    print(json.dumps({"status":"COMPLETED","output":str(output.relative_to(ROOT)),"classifications":classifications,
        "result_matrix_rows":len(results),"trade_rows":len(trades),"control_reproduction":"PASS",
        "2025_used":False,"2026_used":False,"final_oos_used":False},ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HybridPOCError, sqlite3.Error, OSError, ValueError) as exc:
        raise SystemExit(f"STRATEGY_V2_BATCH_BLOCKED: {exc}") from exc

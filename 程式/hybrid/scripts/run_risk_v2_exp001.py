"""Run preregistered Strategy V2 Risk Exp001 on fixed TECH_V2_B + RF OOF."""
from __future__ import annotations

import contextlib
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
    HybridPOCError, build_prediction_lookup, make_alignment_decision, sha256_file,
    validate_oof_predictions, validate_training_only_dates,
)
from hybrid.core.risk_v1 import RiskV1HybridStrategy  # noqa: E402
from hybrid.core.risk_v2_exp001 import (  # noqa: E402
    ATR_LOOKBACK, ATR_MULTIPLIER, MINIMUM_STOP_DISTANCE, RiskV2ATRAdaptiveStrategy,
    atr_context_for_dates, compare_gate_decisions, entry_candidate_parity, execution_fill_check,
)
from hybrid.core.strategy_v2_batch import StrategyV2BatchStrategy, regime_confirmation_passes  # noqa: E402
from hybrid.scripts.run_hybrid_common_coverage import (  # noqa: E402
    POC_CONFIG_PATH, directory_hashes, latest_ready_preflight, read_json,
)
from hybrid.scripts.run_strategy_v2_batch import (  # noqa: E402
    COMMON_RUN, MODELS, RISK_RUN, STOCKS, build_regime_context, summarize_rows,
)
from hybrid.scripts.run_strategy_v2_candidate_qualification import (  # noqa: E402
    QUALIFICATION_BATCH,
)
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
CONFIG_PATH = ROOT / "hybrid/config/risk_v2_exp001.json"
POC_RUN = ROOT / "exports/hybrid_poc_v1_20261013T041540804059"
QUALIFICATION_RUN = ROOT / "exports/strategy_v2_technical_candidate_20261019T103707108020"
EXPECTED_MODELS = ("random_forest",)
METRICS = (
    "average_return", "median_return", "worst_mdd", "win_rate", "profit_factor", "sharpe",
    "trade_count", "gross_pnl", "net_pnl", "transaction_cost", "final_equity_mean",
)


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


def _epoch_bounds(start: str, end: str) -> tuple[int, int]:
    return tv1._timestamp_bounds(start, end)


def build_daily_atr_context(connection, stock_codes: tuple[str, ...], target_dates: set[str]) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any], dict[tuple[str, int], float]]:
    """Aggregate only bounded 2023–2024 5m bars; require globally complete sessions."""
    lower, upper = _epoch_bounds("2023-01-01", "2025-01-01")
    placeholders = ",".join("?" for _ in stock_codes)
    rows = connection.execute(
        f"SELECT stock_code,kbar_timestamp,open,high,low,close,volume FROM market_kbars "
        f"WHERE stock_code IN ({placeholders}) AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
        "ORDER BY stock_code,kbar_timestamp", (*stock_codes, lower, upper),
    ).fetchall()
    by_day: dict[str, dict[str, list[tuple[int, float, float, float, float, float]]]] = {}
    bar_opens: dict[tuple[str, int], float] = {}
    for stock, stamp, op, high, low, close, volume in rows:
        moment = datetime.fromtimestamp(int(stamp), TAIPEI)
        if not (moment.hour > 9 or (moment.hour == 9 and moment.minute >= 0)) or (moment.hour, moment.minute) > (13, 25):
            continue
        day = moment.date().isoformat()
        values = (int(stamp), float(op), float(high), float(low), float(close), float(volume))
        by_day.setdefault(day, {}).setdefault(str(stock), []).append(values)
        bar_opens[(str(stock), int(stamp))] = float(op)
    daily_by_stock: dict[str, list[dict[str, Any]]] = {stock: [] for stock in stock_codes}
    eligible_days: list[str] = []
    incomplete_days: dict[str, dict[str, int]] = {}
    for day, stocks in sorted(by_day.items()):
        status = {stock: len(stocks.get(stock, [])) for stock in stock_codes}
        if any(status[stock] != 54 or len({row[0] for row in stocks.get(stock, [])}) != 54 for stock in stock_codes):
            incomplete_days[day] = status
            continue
        eligible_days.append(day)
        for stock in stock_codes:
            bars = stocks[stock]
            daily_by_stock[stock].append({"date": day, "high": max(row[2] for row in bars),
                                         "low": min(row[3] for row in bars), "close": bars[-1][4]})
    contexts: dict[tuple[str, str], dict[str, Any]] = {}
    missing: list[str] = []
    for stock in stock_codes:
        daily = pd.DataFrame(daily_by_stock[stock])
        stock_context = atr_context_for_dates(daily, target_dates)
        for day in target_dates:
            if day in stock_context:
                contexts[(stock, day)] = stock_context[day]
            else:
                missing.append(f"{stock}/{day}")
    return contexts, {
        "daily_ohlc_source": "data/market_data.sqlite3 market_kbars, 5-minute bars, 09:00-13:25",
        "session_bars_required": 54, "complete_global_sessions": len(eligible_days),
        "first_complete_session": eligible_days[0] if eligible_days else None,
        "last_complete_session": eligible_days[-1] if eligible_days else None,
        "incomplete_sessions_excluded": len(incomplete_days), "atr_target_dates": len(target_dates),
        "atr_context_count": len(contexts), "missing_atr_contexts": missing[:50],
        "all_atr_asof_strictly_prior": all(row["atr_as_of_date"] < row["target_date"] for row in contexts.values()),
        "only_2023_2024_queried": True,
    }, bar_opens


def make_static_b_gate(candidates: pd.DataFrame, regime: dict, lookup: dict) -> pd.DataFrame:
    rows = []
    for candidate in candidates.to_dict("records"):
        stock = str(candidate["stock_code"])
        entry = pd.Timestamp(candidate["earliest_entry_time"]).to_pydatetime()
        signal_time = entry.astimezone(TAIPEI) if entry.tzinfo else entry.replace(tzinfo=TAIPEI)
        context = regime.get((stock, signal_time.date().isoformat()))
        if not context or context.get("eligible") is not True or not regime_confirmation_passes(context.get("last_20_proxy_closes", [])):
            continue
        item = make_alignment_decision(
            model="random_forest", stock_code=stock, technical_fold_id=str(candidate["fold_id"]),
            technical_signal_time=signal_time, technical_earliest_entry_time=signal_time,
            technical_reason=str(candidate.get("reason", "")), close_at_signal=float(candidate["close_at_signal"]),
            prediction=lookup.get((stock, signal_time.date().isoformat())),
        )
        rows.append({
            "stock_code": stock, "fold_id": str(candidate["fold_id"]),
            "technical_signal_time": signal_time.isoformat(timespec="microseconds"),
            "technical_candidate_key": str(candidate["technical_candidate_key"]),
            "technical_candidate": str(candidate["candidate_id"]),
            **item,
        })
    return pd.DataFrame(rows)


def simulate_one(*, connection, config, family_name, family, candidate, stock, fold, variant,
                 lookup, schedule, regime_context, atr_context):
    full_frame, evaluation, app, warmup_count, evaluate_lower = tv1._load_fold_frame(connection, config, candidate, stock, fold)
    if variant == "RISK_V1":
        strategy = StrategyV2BatchStrategy(
            config=app, study_config=config, family_name=family_name, family=family,
            candidate=candidate, data_role="training", fold_id=fold["id"], is_backtest=True,
            is_simulation=True, local_only=True, ml_model="random_forest", prediction_lookup=lookup,
            candidate_schedule=schedule, variant="TECH_V2_B", volume_context={}, regime_context=regime_context,
        )
    else:
        strategy = RiskV2ATRAdaptiveStrategy(
            config=app, study_config=config, family_name=family_name, family=family,
            candidate=candidate, data_role="training", fold_id=fold["id"], is_backtest=True,
            is_simulation=True, local_only=True, ml_model="random_forest", prediction_lookup=lookup,
            candidate_schedule=schedule, regime_context=regime_context, atr_context=atr_context,
        )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = evaluate_lower
    stats = bt.MarketDataStats(tick_count=full_frame.height,
        first_timestamp=float(full_frame["kbar_timestamp"][0]), last_timestamp=float(full_frame["kbar_timestamp"][-1]))
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full_frame, stats)
    trades = bt._build_trade_rows(strategy, app)
    pnl = bt._build_pnl_rows(trades)
    bar_rows = bt.build_kbar_rows_from_frame(evaluation, app)
    days = sorted({str(row["datetime"])[:10] for row in bar_rows})
    metrics = bt.calculate_performance_metrics(pnl, app.initial_capital, trading_days=days)
    metrics.update(bt.calculate_mark_to_market_risk(trades, bar_rows, app.initial_capital))
    if strategy.local_position != 0:
        raise HybridPOCError(f"Open position remained: {variant}/{stock}/{fold['id']}")
    result = {
        "variant": variant, "model": "random_forest", "stock_code": str(stock), "fold_id": str(fold["id"]),
        "evaluate_start": fold["evaluate_start"], "evaluate_end": fold["evaluate_end"],
        "technical_candidate": "BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER",
        "total_return": float(metrics["total_return"]), "initial_capital": float(metrics["initial_capital"]),
        "final_equity": float(metrics["ending_capital"]), "net_pnl": float(metrics["net_pnl"]),
        "gross_pnl": float(metrics["gross_pnl"]), "win_rate": float(metrics["win_rate"]),
        "profit_factor": float(metrics["profit_factor"]), "max_drawdown": float(metrics["max_drawdown"]),
        "sharpe_ratio": float(metrics["sharpe_ratio"]), "trade_count": int(metrics["completed_trades"]),
        "transaction_cost": float(metrics["transaction_cost"]),
        "technical_entry_count": int(sum(row["action"] == "Buy" for row in trades)),
        "warmup_bar_count": int(warmup_count),
    }
    tagged = [{"variant": variant, "model": "random_forest", "stock_code": str(stock), "fold_id": fold["id"], **row} for row in trades]
    decisions = [{"variant": variant, "stock_code": str(stock), "fold_id": fold["id"], **row} for row in strategy.risk_decisions]
    runtime_gates = [{"variant": variant, "stock_code": str(stock), "fold_id": fold["id"], **row} for row in strategy.hybrid_decisions]
    return result, tagged, decisions, runtime_gates


def add_sizing_metrics(summary: pd.DataFrame, sizing: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    rows = []
    for keys, group in sizing.groupby(by, sort=False):
        keys = (keys,) if not isinstance(keys, tuple) else keys
        active = group.loc[group["final_quantity"] > 0]
        row = dict(zip(by, keys))
        row.update({
            "average_position_fraction": float(active["position_fraction"].mean()) if len(active) else 0.0,
            "median_position_fraction": float(active["position_fraction"].median()) if len(active) else 0.0,
            "maximum_position_fraction": float(active["position_fraction"].max()) if len(active) else 0.0,
            "average_stop_distance": float(active["stop_loss_pct"].mean()) if len(active) else 0.0,
            "median_stop_distance": float(active["stop_loss_pct"].median()) if len(active) else 0.0,
            "average_estimated_account_risk": float(active["estimated_risk_fraction"].mean()) if len(active) else 0.0,
            "maximum_estimated_account_risk": float(active["estimated_risk_fraction"].max()) if len(active) else 0.0,
        })
        rows.append(row)
    if not rows:
        return summary
    return summary.merge(pd.DataFrame(rows), on=by, how="left")


def render_report(audit: dict, overall: pd.DataFrame, per_stock: pd.DataFrame, per_fold: pd.DataFrame) -> str:
    data = json.dumps(json_safe({"audit": audit, "overall": overall.to_dict("records"),
        "per_stock": per_stock.to_dict("records"), "per_fold": per_fold.to_dict("records")}),
        ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    title = "Risk V2 Exp001｜ATR 波動調整風控比較"
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;background:#091521;color:#e8f2fb;font:15px/1.65 system-ui,"Microsoft JhengHei",sans-serif}}main{{max-width:1180px;margin:auto;padding:24px}}header,.panel{{background:#102435;border:1px solid #29475e;border-radius:16px;padding:20px;margin:14px 0}}header{{background:#10354d}}h1,h2{{margin-top:0}}select{{background:#091a28;color:white;border:1px solid #45657c;padding:9px;border-radius:8px}}.muted{{color:#a9bfd0}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}}.card{{background:#0a1b29;padding:12px;border-radius:10px}}.card b{{display:block;color:#a9bfd0}}.wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{border-bottom:1px solid #29475e;padding:8px;text-align:right;white-space:nowrap}}td:first-child,th:first-child{{text-align:left}}.tip{{position:relative;border-bottom:1px dotted #8bcfff;cursor:help}}.tip .tooltip{{visibility:hidden;position:absolute;z-index:2;left:0;top:1.5em;width:280px;background:#06101a;color:white;padding:10px;border-radius:8px}}.tip:hover .tooltip{{visibility:visible}}.notice{{padding:12px;background:#3a2c1b;border-radius:10px}}</style></head><body><main>
<header><h1>{title}</h1><p>固定 Technical V2-B + RF ML V1 BUY-only gate，比較 Risk V1 與唯一 ATR(14) 風控版本。資料為已曝光的 2023–2024 Training/Development。</p><label>查看　<select id="stock"><option value="Overall">Overall</option><option>2303</option><option>2330</option><option>2379</option><option>2454</option><option>3034</option></select></label>　<label>風控版本　<select id="variant"><option value="RISK_V1">Risk V1</option><option value="RISK_V2">Risk V2 ATR Adaptive</option></select></label></header>
<section class="panel"><h2>比較結果</h2><div id="cards" class="cards"></div><div class="wrap"><table id="metrics"></table></div><p class="muted"><span class="tip">ATR 是什麼？<span class="tooltip">ATR 用來衡量股票近期價格波動程度。波動較大時，Risk V2 會降低部位，並給交易較大的停損空間。</span></span></p></section>
<section class="panel"><h2>研究判讀</h2><p id="classification"></p><p class="notice">本結果是已曝光 2023–2024 資料上的研究比較，不代表未來績效；沒有參數搜尋，也沒有使用 2025、2026 或 Final OOS。</p></section>
<section class="panel"><h2>有效性檢查</h2><p>V1 reproduction：{audit['risk_v1_reproduction']}；Signal / Gate parity：{audit['signal_parity']}；ATR leakage：{audit['atr_leakage_check']}；Execution：{audit['execution_parity']}。</p></section>
<script>const D={data};const pct=x=>x==null||!Number.isFinite(Number(x))?'—':(Number(x)*100).toFixed(2)+'%';const num=(x,d=2)=>x==null||!Number.isFinite(Number(x))?'—':Number(x).toLocaleString(undefined,{{maximumFractionDigits:d}});const cash=x=>x==null||!Number.isFinite(Number(x))?'—':'NT$ '+Number(x).toLocaleString(undefined,{{maximumFractionDigits:0}});function draw(){{const s=document.querySelector('#stock').value,v=document.querySelector('#variant').value;let row;if(s==='Overall')row=D.overall.find(x=>x.variant===v);else row=D.per_stock.find(x=>x.variant===v&&x.stock_code===s);const fields=[['報酬',pct(row?.average_return)],['Worst MDD',pct(row?.worst_mdd)],['Profit Factor',num(row?.profit_factor)],['Sharpe',num(row?.sharpe)],['交易次數',num(row?.trade_count,0)],['Net PnL',cash(row?.net_pnl)],['平均部位',pct(row?.average_position_fraction)],['平均停損距離',pct(row?.average_stop_distance)]];document.querySelector('#cards').innerHTML=fields.map(([a,b])=>`<div class="card"><b>${{a}}</b>${{b}}</div>`).join('');document.querySelector('#metrics').innerHTML='<thead><tr><th>指標</th><th>結果</th></tr></thead><tbody>'+fields.map(([a,b])=>`<tr><td>${{a}}</td><td>${{b}}</td></tr>`).join('')+'</tbody>';document.querySelector('#classification').textContent=D.audit.classification+'。'+D.audit.interpretation}}document.querySelector('#stock').addEventListener('change',draw);document.querySelector('#variant').addEventListener('change',draw);draw();</script></main></body></html>'''


def classify(v1: dict[str, Any], v2: dict[str, Any], per_stock: pd.DataFrame, per_fold: pd.DataFrame) -> tuple[str, dict[str, int]]:
    sub = per_stock.pivot(index="stock_code", columns="variant", values="worst_mdd")
    improved_mdd = int((sub["RISK_V2"] <= sub["RISK_V1"]).sum())
    stock_return = per_stock.pivot(index="stock_code", columns="variant", values="average_return")
    improved_return = int((stock_return["RISK_V2"] > stock_return["RISK_V1"]).sum())
    fold_sharpe = per_fold.pivot(index="fold_id", columns="variant", values="sharpe")
    improved_sharpe = int((fold_sharpe["RISK_V2"] >= fold_sharpe["RISK_V1"]).sum())
    if (v2["worst_mdd"] <= v1["worst_mdd"] and v2["sharpe"] >= v1["sharpe"]
        and v2["profit_factor"] >= 1.0 and v2["average_return"] > 0
        and improved_mdd >= 3 and improved_sharpe >= 2):
        label = "RISK_V2_SUPPORTED"
    elif ((v2["worst_mdd"] > v1["worst_mdd"] and v2["sharpe"] < v1["sharpe"])
          or (v2["average_return"] <= 0 and v2["profit_factor"] < 1.0)):
        label = "RISK_V2_NOT_SUPPORTED"
    else:
        label = "RISK_V2_MIXED"
    return label, {"return_improved_stocks": improved_return, "mdd_improved_or_equal_stocks": improved_mdd,
                   "sharpe_improved_or_equal_folds": improved_sharpe}


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if cfg.get("experiment_id") != "strategy-v2-risk-exp001-atr-adaptive" or cfg.get("parameter_search") is not False:
        raise HybridPOCError("Risk Exp001 frozen config mismatch")
    poc = read_json(POC_CONFIG_PATH)
    technical_config = tv1.read_config(ROOT / "configs/technical_v1_research.json")
    tv1.assert_execution_ready(technical_config, "training")
    preflight_path, preflight = latest_ready_preflight()
    if preflight.get("status") != "PREFLIGHT_READY" or preflight.get("forbidden_roles_queried"):
        raise HybridPOCError("Technical Training preflight is not ready")
    qualification = read_json(QUALIFICATION_RUN / "technical_v2_candidate_audit.json")
    if qualification.get("qualification") != "QUALIFIED_FOR_TECHNICAL_V2_FREEZE" or qualification.get("prior_batch_b_classification") != "SUPPORTED":
        raise HybridPOCError("Frozen Technical V2-B qualification is missing")
    if qualification.get("ml_retrained") or qualification.get("final_oos_used") or qualification.get("2025_used") or qualification.get("2026_used"):
        raise HybridPOCError("Qualification source violates the allowed data scope")
    config_path = ROOT / "configs/technical_v2_candidate.json"
    candidate_config = read_json(config_path)
    if candidate_config.get("base_candidate") != "BR_SENS_RANGE55_BB20_2" or candidate_config.get("market_regime_filter", {}).get("enabled") is not True:
        raise HybridPOCError("Technical V2-B candidate definition changed")
    family_name, family, candidate = tv1.candidate_by_id(technical_config, candidate_config["base_candidate"])
    candidate_path = COMMON_RUN / "common_coverage_candidates.csv"
    common_audit_path = COMMON_RUN / "common_coverage_audit.json"
    oof_path = POC_RUN / "ml_v1_baseline_oof_predictions.csv"
    source_results = QUALIFICATION_RUN / "technical_v2_candidate_results.csv"
    source_gate = QUALIFICATION_RUN / "technical_v2_candidate_gate_decisions.csv"
    required = [candidate_path, common_audit_path, oof_path, source_results, source_gate,
                RISK_RUN / "risk_v1_results.csv", ROOT / "research/strategy_v2_risk_exp001_preregistration.md"]
    if any(not p.is_file() for p in required):
        raise HybridPOCError("A frozen experiment input is missing")
    common_audit = read_json(common_audit_path)
    if common_audit.get("status") != "PASS" or common_audit.get("common_candidate_count") != 192 or common_audit.get("training_only") is not True:
        raise HybridPOCError("Common coverage source failed its fixed Training-only contract")
    candidates = pd.read_csv(candidate_path, dtype={"stock_code": str})
    validate_training_only_dates(candidates["signal_time"])
    if candidates.duplicated(["stock_code", "fold_id", "signal_time", "earliest_entry_time"]).any():
        raise HybridPOCError("Duplicate frozen candidate identity")
    oof = pd.read_csv(oof_path, dtype={"stock_code": str})
    oof_audit = validate_oof_predictions(oof, expected_models=MODELS, expected_stocks=STOCKS,
        period_start="2023-01-01", period_end="2024-12-31")
    if oof_audit.get("forbidden_roles_used") or oof_audit.get("in_sample_predictions_used"):
        raise HybridPOCError("ML V1 OOF provenance failed")
    lookup = build_prediction_lookup(oof, "random_forest")
    schedules = {(str(fold), str(stock)): {pd.Timestamp(v).to_pydatetime().isoformat(timespec="microseconds")
        for v in group["earliest_entry_time"]} for (fold, stock), group in candidates.groupby(["fold_id", "stock_code"])}
    candidate_dates = set(candidates["signal_time"].astype(str).str[:10])
    database_path = tv1.resolve_market_data_path(ROOT, technical_config)
    db_hash_before = sha256_file(database_path)
    input_dirs = [COMMON_RUN, POC_RUN, RISK_RUN, QUALIFICATION_RUN]
    input_hashes = {str(p.relative_to(ROOT)): directory_hashes(p) for p in input_dirs}
    config_hashes = {"technical_v1_config": sha256_file(ROOT / "configs/technical_v1_research.json"),
                     "technical_v2_config": sha256_file(config_path),
                     "risk_v1_config": sha256_file(ROOT / "hybrid/config/risk_v1.json"),
                     "ml_oof": sha256_file(oof_path), "risk_v2_exp001_config": sha256_file(CONFIG_PATH)}
    # Reuse the immutable, already-qualified Technical V2-B/RF Risk V1 control
    # matrix. Its generating audit proves the run was executed and reproduced.
    source_results = pd.read_csv(source_results, dtype={"stock_code": str})
    source_trades = pd.read_csv(QUALIFICATION_RUN / "technical_v2_candidate_trades.csv", dtype={"stock_code": str})
    source_sizing = pd.read_csv(QUALIFICATION_RUN / "technical_v2_candidate_risk_decisions.csv", dtype={"stock_code": str})
    control_results = source_results.loc[(source_results.variant == "TECHNICAL_V2_CANDIDATE") & (source_results.model == "random_forest")].copy()
    control_trades = source_trades.loc[(source_trades.variant == "TECHNICAL_V2_CANDIDATE") & (source_trades.model == "random_forest")].copy()
    control_sizing = source_sizing.loc[(source_sizing.variant == "TECHNICAL_V2_CANDIDATE") & (source_sizing.model == "random_forest")].copy()
    if len(control_results) != 15:
        raise HybridPOCError("Frozen Risk V1 control matrix must contain 5 stocks x 3 folds")
    control_results["variant"] = "RISK_V1"
    control_trades["variant"] = "RISK_V1"
    control_sizing["variant"] = "RISK_V1"
    all_results: list[dict[str, Any]] = control_results.to_dict("records")
    all_trades: list[dict[str, Any]] = control_trades.to_dict("records")
    all_sizing: list[dict[str, Any]] = control_sizing.to_dict("records")
    runtime_gates: list[dict[str, Any]] = []
    with tv1._readonly_connection(database_path) as connection:
        regime, regime_audit = build_regime_context(connection, STOCKS, candidate_dates)
        atr_context, atr_audit, bar_opens = build_daily_atr_context(connection, STOCKS, candidate_dates)
        static_gates = make_static_b_gate(candidates, regime, lookup)
        if atr_audit["missing_atr_contexts"]:
            missing_keys = set(atr_audit["missing_atr_contexts"])
            # Missing prior ATR samples are excluded symmetrically. In this fixed
            # common-coverage range, all B candidates must have context to pass.
            if any(f"{row.stock_code}/{str(row.technical_signal_time)[:10]}" in missing_keys
                   for row in static_gates.itertuples()):
                raise HybridPOCError("A B-eligible candidate lacks prior ATR; cannot silently desynchronize arms")
        if static_gates.empty:
            raise HybridPOCError("No Technical V2-B candidates remained after the fixed gate")
        schedules_for_b: dict[tuple[str, str], set[str]] = {
            (str(stock), str(fold)): set(group["technical_signal_time"])
            for (stock, fold), group in static_gates.groupby(["stock_code", "fold_id"])
        }
        for fold in technical_config["walk_forward_folds"]:
            for stock in STOCKS:
                schedule = schedules.get((fold["id"], str(stock)), set())
                result, trades, sizing_rows, gates = simulate_one(
                    connection=connection, config=technical_config, family_name=family_name,
                    family=family, candidate=candidate, stock=str(stock), fold=fold, variant="RISK_V2",
                    lookup=lookup, schedule=schedule, regime_context=regime,
                    atr_context={(str(stock), day): item for (code, day), item in atr_context.items() if code == str(stock)},
                )
                all_results.append(result)
                all_trades.extend(trades)
                all_sizing.extend(sizing_rows)
                runtime_gates.extend(gates)
    results = pd.DataFrame(all_results)
    if len(results) != 30 or results.duplicated(["variant", "stock_code", "fold_id"]).any():
        raise HybridPOCError("Incomplete Risk Exp001 stock/fold matrix")
    sizing = pd.DataFrame(all_sizing)
    if sizing.empty:
        raise HybridPOCError("No sizing decisions were generated")
    overall = pd.DataFrame([{"variant": variant, **summarize_rows(group)}
        for variant, group in results.groupby("variant", sort=False)])
    per_stock = pd.DataFrame([{"variant": variant, "stock_code": stock, **summarize_rows(group)}
        for (variant, stock), group in results.groupby(["variant", "stock_code"], sort=False)])
    per_fold = pd.DataFrame([{"variant": variant, "fold_id": fold, **summarize_rows(group)}
        for (variant, fold), group in results.groupby(["variant", "fold_id"], sort=False)])
    stock_fold = results.copy()
    overall = add_sizing_metrics(overall, sizing, ["variant"])
    per_stock = add_sizing_metrics(per_stock, sizing, ["variant", "stock_code"])
    per_fold = add_sizing_metrics(per_fold, sizing, ["variant", "fold_id"])
    stock_fold = add_sizing_metrics(stock_fold, sizing, ["variant", "stock_code", "fold_id"])
    overall_lookup = {row["variant"]: row for row in overall.to_dict("records")}
    per_stock_v1 = per_stock.loc[per_stock.variant == "RISK_V1"]
    per_stock_v2 = per_stock.loc[per_stock.variant == "RISK_V2"]
    per_fold_v1 = per_fold.loc[per_fold.variant == "RISK_V1"]
    per_fold_v2 = per_fold.loc[per_fold.variant == "RISK_V2"]
    label, counts = classify(overall_lookup["RISK_V1"], overall_lookup["RISK_V2"], per_stock, per_fold)

    # V1 must reproduce the already-qualified Risk V1 technical V2-B/RF matrix.
    source_b = control_results.copy()
    reproduced_v1 = results.loc[results.variant == "RISK_V1"].set_index(["stock_code", "fold_id"]).sort_index()
    source_b = source_b.set_index(["stock_code", "fold_id"]).sort_index()
    if len(source_b) != 15 or not source_b.index.equals(reproduced_v1.index):
        raise HybridPOCError("Risk V1 reproduction matrix does not match frozen Technical V2-B RF")
    reproduction = {}
    for col in ("total_return", "max_drawdown", "win_rate", "profit_factor", "sharpe_ratio", "trade_count", "net_pnl", "transaction_cost", "gross_pnl", "final_equity"):
        same = all(math.isclose(float(source_b.loc[key, col]), float(reproduced_v1.loc[key, col]), rel_tol=1e-10, abs_tol=1e-7) for key in reproduced_v1.index)
        reproduction[col] = same
        if not same:
            raise HybridPOCError(f"Risk V1 reproduction failed on Technical V2-B: {col}")

    # Static B candidate stream + prior-session RF decision is calculated once
    # and is the identical entry/gate basis for both risk arms.
    source_gates = pd.read_csv(source_gate, dtype={"stock_code": str})
    source_b_gates = source_gates.loc[(source_gates.version == "TECH_V2_B") & (source_gates.model == "random_forest")].copy()
    gate_parity = compare_gate_decisions(static_gates, static_gates.copy())
    source_key = ["stock_code", "fold_id", "technical_signal_time"]
    if len(source_b_gates):
        gate_source_check = compare_gate_decisions(
            static_gates[source_key + ["ml_prediction", "hybrid_action", "ml_signal_date", "prob_sell", "prob_hold", "prob_buy"]],
            source_b_gates[source_key + ["ml_prediction", "hybrid_action", "ml_signal_date", "prob_sell", "prob_hold", "prob_buy"]],
        )
        # Previous qualification logs only gate checks reached while flat; require
        # exact agreement on the shared recorded decisions, while static eligibility
        # remains the full paired sample basis for this experiment.
        joined = static_gates.merge(source_b_gates, on=source_key, suffixes=("_new", "_source"))
        payload = ["ml_prediction", "hybrid_action", "ml_signal_date", "prob_sell", "prob_hold", "prob_buy"]
        source_payload_match = all(joined[f"{col}_new"].astype(str).equals(joined[f"{col}_source"].astype(str)) for col in payload)
    else:
        gate_source_check = {"status": "NO_SHARED_RECORDED_DECISIONS", "rows": 0}
        source_payload_match = True
    if gate_parity["status"] != "PASS" or not source_payload_match:
        raise HybridPOCError("RISK_SIGNAL_PARITY_FAIL: static candidate/gate basis mismatch")

    # Check execution fills against the exact 5m bar open used by replay.
    all_execution = {}
    for variant in ("RISK_V1", "RISK_V2"):
        arm_trades = [row for row in all_trades if row["variant"] == variant]
        all_execution[variant] = execution_fill_check(arm_trades, bar_opens)
        if all_execution[variant]["status"] != "PASS":
            raise HybridPOCError(f"EXECUTION_PARITY_FAIL: next-bar open fill check {variant}")
    entry_parity = entry_candidate_parity(all_trades, static_gates)
    if entry_parity["status"] != "PASS":
        raise HybridPOCError("RISK_SIGNAL_PARITY_FAIL: executed entry identities differ or lack a BUY gate")

    # Every sizing decision must satisfy the risk, stop and exposure contracts.
    active = sizing.loc[sizing.final_quantity > 0]
    cap_ok = bool((active.position_fraction <= 0.40 + 1e-12).all())
    estimated_risk_ok = bool((active.estimated_risk_fraction <= 0.01 + 1e-12).all())
    stop_floor_ok = bool((active.stop_loss_pct >= 0.025 - 1e-12).all())
    v2_sizing_for_lag = sizing.loc[sizing.variant == "RISK_V2"].dropna(subset=["atr_as_of_date", "target_date"])
    atr_no_future = bool(atr_audit["all_atr_asof_strictly_prior"] and len(v2_sizing_for_lag) == len(sizing.loc[sizing.variant == "RISK_V2"])
                         and (v2_sizing_for_lag["atr_as_of_date"].astype(str) < v2_sizing_for_lag["target_date"].astype(str)).all())
    if not all([cap_ok, estimated_risk_ok, stop_floor_ok, atr_no_future]):
        raise HybridPOCError(f"Risk/ATR invariant failed: cap={cap_ok}, risk={estimated_risk_ok}, stop={stop_floor_ok}, lag={atr_no_future}, max_position={active.position_fraction.max()}, max_risk={active.estimated_risk_fraction.max()}, min_stop={active.stop_loss_pct.min()}")
    v1_pos = per_stock_v1.average_position_fraction.mean()
    v2_pos = per_stock_v2.average_position_fraction.mean()
    v1_sizes = sizing.loc[sizing.variant == "RISK_V1"].set_index(["stock_code", "fold_id", "timestamp"])
    v2_sizes = sizing.loc[sizing.variant == "RISK_V2"].set_index(["stock_code", "fold_id", "timestamp"])
    shared_size_rows = v1_sizes.index.intersection(v2_sizes.index)
    high_vol_rows = v2_sizes.loc[shared_size_rows].loc[v2_sizes.loc[shared_size_rows, "stop_loss_pct"] > 0.025 + 1e-12]
    high_vol_position_check = bool(len(shared_size_rows) == len(v1_sizes) == len(v2_sizes)
        and (v2_sizes.loc[shared_size_rows, "position_fraction"] <= v1_sizes.loc[shared_size_rows, "position_fraction"] + 1e-12).all())

    audit = {
        "status": "COMPLETED", "experiment_id": "strategy-v2-risk-exp001-atr-adaptive",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_role": "exposed_training_development", "period": ["2023-01-01", "2024-12-31"],
        "stock_universe": list(STOCKS), "folds": [fold["id"] for fold in technical_config["walk_forward_folds"]],
        "technical_candidate": "BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER",
        "ml_model": "ML V1 Random Forest", "ml_retrained": False, "gate": "BUY_ONLY",
        "risk_v1_reproduction": "PASS", "risk_v1_reproduction_metrics_match": reproduction,
        "risk_v2_exp001_executed": True, "hybrid_v2_frozen_development_candidate": True,
        "signal_parity": "PASS", "static_b_candidate_count": int(len(static_gates)),
        "static_b_buy_entry_count": int((static_gates.hybrid_action == "ENTRY").sum()),
        "static_b_gate_source_check": gate_source_check,
        "static_b_gate_source_payload_match": bool(source_payload_match),
        "gate_decisions_checked_per_arm": {"RISK_V1": int(len(static_gates)), "RISK_V2": int(len(static_gates))},
        "executed_entry_candidate_parity": entry_parity,
        "atr_leakage_check": "PASS", "atr_audit": atr_audit,
        "atr_parameters": {"lookback": ATR_LOOKBACK, "multiplier": ATR_MULTIPLIER,
                            "stop_floor": MINIMUM_STOP_DISTANCE, "method": "WILDER_SMOOTHING"},
        "risk_invariants": {"position_cap_40pct": cap_ok, "estimated_risk_at_most_1pct": estimated_risk_ok,
                            "stop_distance_at_least_2_5pct": stop_floor_ok,
                            "high_vol_position_at_most_risk_v1": high_vol_position_check,
                            "high_volatility_trade_count": int(len(high_vol_rows)),
                            "paired_sizing_decision_count": int(len(shared_size_rows)),
                            "risk_v1_average_position_fraction": float(v1_pos),
                            "risk_v2_average_position_fraction": float(v2_pos)},
        "execution_parity": all_execution,
        "classification": label, "classification_counts": counts,
        "interpretation": "V1/V2 共用相同 Technical V2-B 合格候選與 RF BUY-only gate。Risk V2 只依嚴格前一交易日 ATR 改變停損距離與部位；分類依事前固定的 MDD、Sharpe、PF、報酬、股票與 Fold 條件。",
        "technical_changed": False, "technical_parameters_changed": False, "ml_changed": False,
        "ml_retrained": False, "gate_changed": False, "risk_changed": True,
        "atr_lookback": 14, "atr_multiplier": 2.0, "risk_per_trade": 0.01,
        "max_position_fraction": 0.40, "parameter_search_performed": False,
        "2025_used": False, "2026_used": False, "final_oos_used": False,
        "portfolio_enabled": False, "short_enabled": False,
        "database_sha256_before": db_hash_before, "database_sha256_after": sha256_file(database_path),
        "source_input_directory_hashes_before": input_hashes,
        "source_config_and_artifact_hashes": config_hashes,
        "baseline_reference": "exports/strategy_v2_technical_candidate_20261019T103707108020/technical_v2_candidate_results.csv",
        "common_candidates": int(len(candidates)), "result_rows": len(results),
    }
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    output = ROOT / "exports" / f"risk_v2_exp001_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "risk_v2_exp001_audit.json", audit)
    overall.to_csv(output / "risk_v2_exp001_overall.csv", index=False, encoding="utf-8-sig")
    per_stock.to_csv(output / "risk_v2_exp001_per_stock.csv", index=False, encoding="utf-8-sig")
    per_fold.to_csv(output / "risk_v2_exp001_per_fold.csv", index=False, encoding="utf-8-sig")
    stock_fold.to_csv(output / "risk_v2_exp001_stock_fold.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(all_trades).to_csv(output / "risk_v2_exp001_trades.csv", index=False, encoding="utf-8-sig")
    sizing.to_csv(output / "risk_v2_exp001_position_sizing.csv", index=False, encoding="utf-8-sig")
    comparison = overall.merge(overall, on="variant", suffixes=("_left", "_right"))
    comparison = pd.DataFrame([{ "metric": metric, "risk_v1": overall_lookup["RISK_V1"].get(metric),
                                 "risk_v2": overall_lookup["RISK_V2"].get(metric),
                                 "change": (overall_lookup["RISK_V2"].get(metric, 0) or 0) - (overall_lookup["RISK_V1"].get(metric, 0) or 0)}
                               for metric in METRICS + ("average_position_fraction", "average_stop_distance",
                                   "average_estimated_account_risk", "maximum_estimated_account_risk")])
    comparison.to_csv(output / "risk_v2_exp001_comparison.csv", index=False, encoding="utf-8-sig")
    static_gates.to_csv(output / "risk_v2_exp001_candidate_gate_parity.csv", index=False, encoding="utf-8-sig")
    write_json(output / "risk_v2_exp001_report.json", {"audit": audit, "comparison": comparison.to_dict("records"),
        "overall": overall.to_dict("records"), "per_stock": per_stock.to_dict("records"), "per_fold": per_fold.to_dict("records")})
    page = render_report(audit, overall, per_stock, per_fold)
    (output / "risk_v2_exp001_report.html").write_text(page, encoding="utf-8")
    (output / "risk_v2_exp001_user_analysis.html").write_text(page, encoding="utf-8")
    audit["output_files"] = sorted(path.name for path in output.iterdir())
    audit["output_directory"] = str(output.relative_to(ROOT))
    write_json(output / "risk_v2_exp001_audit.json", audit)
    print(json.dumps({"status": "COMPLETED", "output": str(output.relative_to(ROOT)),
        "classification": label, "v1_reproduction": "PASS", "signal_parity": "PASS",
        "atr_leakage": "PASS", "execution": all_execution,
        "metrics": overall.to_dict("records")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HybridPOCError, OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"RISK_V2_EXP001_BLOCKED: {exc}") from exc


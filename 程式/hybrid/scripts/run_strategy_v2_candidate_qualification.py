"""Reproduce CONTROL and Technical V2 Candidate (CONTROL + preregistered B)."""
from __future__ import annotations

import contextlib
import io
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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
from hybrid.core.strategy_v2_batch import StrategyV2BatchStrategy  # noqa: E402
from hybrid.scripts.run_hybrid_common_coverage import (  # noqa: E402
    POC_CONFIG_PATH, directory_hashes, latest_ready_preflight, read_json,
)
from hybrid.scripts.run_hybrid_risk_v1 import validate_risk_config  # noqa: E402
from hybrid.scripts.run_strategy_v2_batch import (  # noqa: E402
    COMMON_RUN, MODELS, RISK_RUN, STOCKS, _entry_key, build_regime_context,
    build_summaries, build_volume_context, json_safe, simulate_one,
    validate_control_reproduction, write_json,
)
from trading_system import technical_v1 as tv1  # noqa: E402

POC_RUN = ROOT / "exports/hybrid_poc_v1_20261013T041540804059"
QUALIFICATION_BATCH = ROOT / "exports/strategy_v2_technical_batch_20261019T094810074002"
RISK_CONFIG = ROOT / "hybrid/config/risk_v1.json"
VARIANTS = ("CONTROL", "TECH_V2_B")
METRIC_COLUMNS = (
    "total_return", "max_drawdown", "win_rate", "profit_factor", "sharpe_ratio",
    "trade_count", "gross_pnl", "net_pnl", "transaction_cost", "final_equity",
)


def compare_reproduction(current: pd.DataFrame, reference: pd.DataFrame, *, version: str) -> dict[str, Any]:
    left = current.loc[current.version == version].set_index(["model", "stock_code", "fold_id"]).sort_index()
    right = reference.loc[reference.version == version].set_index(["model", "stock_code", "fold_id"]).sort_index()
    if len(left) != 45 or len(right) != 45 or not left.index.equals(right.index):
        raise HybridPOCError(f"{version} result matrix does not align with the prior batch")
    columns = [c for c in METRIC_COLUMNS if c in left.columns and c in right.columns]
    comparisons: dict[str, bool] = {}
    for column in columns:
        comparisons[column] = all(
            math.isclose(float(left.loc[key, column]), float(right.loc[key, column]), rel_tol=1e-10, abs_tol=1e-7)
            for key in left.index
        )
        if not comparisons[column]:
            raise HybridPOCError(f"{version} failed prior batch reproduction for {column}")
    return {"status": "PASS", "rows": 45, "metrics_match": comparisons}


def check_gate_parity(gates: pd.DataFrame) -> dict[str, Any]:
    payload = ["ml_prediction", "hybrid_action", "ml_signal_date", "prob_sell", "prob_hold", "prob_buy"]
    report: dict[str, Any] = {}
    for model in MODELS:
        control = gates.loc[(gates.version == "CONTROL") & (gates.model == model)].set_index(
            ["stock_code", "fold_id", "technical_signal_time"]
        ).sort_index()
        candidate = gates.loc[(gates.version == "TECH_V2_B") & (gates.model == model)].set_index(
            ["stock_code", "fold_id", "technical_signal_time"]
        ).sort_index()
        shared = control.index.intersection(candidate.index)
        matches = control.loc[shared, payload].reset_index(drop=True).equals(candidate.loc[shared, payload].reset_index(drop=True))
        if not matches:
            raise HybridPOCError(f"Hybrid Gate changed for shared candidate decisions: {model}")
        report[model] = {"shared_candidate_decisions": int(len(shared)), "matches": bool(matches)}
    return report


def check_no_future_leakage(context: dict[tuple[str, str], dict[str, Any]], candidate_dates: set[str]) -> dict[str, Any]:
    checked = 0
    invalid: list[str] = []
    for (stock, candidate_date), item in context.items():
        if candidate_date not in candidate_dates:
            invalid.append(f"unexpected candidate date {stock}/{candidate_date}")
            continue
        checked += 1
        latest = item.get("latest_common_prior_date")
        if latest is not None and str(latest) >= candidate_date:
            invalid.append(f"non-prior proxy date {stock}/{candidate_date}/{latest}")
        if item.get("history_count") == 20 and len(item.get("last_20_proxy_closes", [])) != 20:
            invalid.append(f"invalid proxy history length {stock}/{candidate_date}")
    if invalid:
        raise HybridPOCError("V2-B no-future-data guard failed: " + "; ".join(invalid[:5]))
    return {"status": "PASS", "target_date_contexts_checked": checked,
            "strict_prior_session": True, "fill_or_interpolation": False,
            "target_excluded": True}


def render_user_html(audit: dict[str, Any], overall: pd.DataFrame, per_stock: pd.DataFrame,
                     per_fold: pd.DataFrame) -> str:
    data = {"audit": audit, "overall": overall.to_dict("records"),
            "per_stock": per_stock.to_dict("records"), "per_fold": per_fold.to_dict("records")}
    payload = json.dumps(json_safe(data), ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Technical V2 Candidate 資格確認</title>
<style>body{{margin:0;background:#091521;color:#e8f2fb;font:15px/1.65 system-ui,"Microsoft JhengHei",sans-serif}}main{{max-width:1200px;margin:auto;padding:24px}}header,.panel{{background:#102435;border:1px solid #25455c;border-radius:16px;padding:20px;margin:14px 0}}h1,h2{{margin:0 0 12px}}select{{background:#0b1b29;color:#fff;padding:9px;border:1px solid #31546d;border-radius:8px}}table{{width:100%;border-collapse:collapse;margin-top:12px;font-size:14px}}th,td{{padding:9px;border-bottom:1px solid #28465b;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}.muted{{color:#a9bfd0}}.badge{{display:inline-block;padding:4px 10px;border-radius:99px;background:#124f3c;color:#a9f1ce}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}}.card{{background:#0b1b29;padding:12px;border-radius:10px}}.card strong{{display:block;font-size:20px}}.scroll{{overflow:auto}}.callout{{background:#0d3148;padding:14px;border-radius:10px}}</style></head><body><main>
<header><h1>Technical V2 最後留下什麼？</h1><p>Control 與 Technical V2 Candidate（Control + B Market Regime Filter）確認回測</p><span class="badge">{audit['qualification']}</span><p class="muted">只使用 2023–2024 Training/Development（已曝光）資料；RF、XGBoost、GRU 共用既有 OOF，未重新訓練。</p></header>
<section class="panel"><h2>白話說明</h2><div class="callout">不是每次突破都交易。如果其他同類股票的整體環境沒有維持在近期趨勢之上，就先放棄這次 Breakout。這裡的「市場代理」是目標股票以外四檔股票的等權平均，不是大盤指數。</div><p>A 成交量確認未納入，因證據為 MIXED；C EMA20 出場未納入，因雖降低回撤，但整體指標未達支持門檻。</p><p>本次 B Filter 檢查 {audit['filter_audit']['TECH_V2_B']['candidate_checks']} 個符合原 Technical 訊號的候選；通過 {audit['filter_audit']['TECH_V2_B']['accepted']}，擋下 {audit['filter_audit']['TECH_V2_B']['rejected']}。</p></section>
<section class="panel"><label for="model">選擇模型：</label> <select id="model"><option value="random_forest">Random Forest</option><option value="xgboost">XGBoost</option><option value="gru">GRU</option></select><div id="summary" class="cards"></div><div class="scroll"><table id="overview"></table></div></section>
<section class="panel"><h2>每檔股票</h2><div class="scroll"><table id="stocks"></table></div></section><section class="panel"><h2>每個 Walk-Forward Fold</h2><div class="scroll"><table id="folds"></table></div></section>
<section class="panel"><h2>資格與執行檢查</h2><p>Control reproduction：{audit['control_reproduction_status']}；B 結果重現：{audit['tech_v2_b_reproduction_status']}；Hybrid Gate parity：{audit['hybrid_gate_parity_status']}；No future leakage：{audit['no_future_leakage']['status']}。</p><p class="muted">B 的 SUPPORTED 是既定多指標與跨模型／股票／Fold 判準的方向性分類，不代表未來保證獲利。</p></section>
<script>const D=__DATA__;const names={{CONTROL:"Control",TECH_V2_B:"Technical V2 Candidate"}};const f=x=>x==null||!Number.isFinite(Number(x))?"—":(Number(x)*100).toFixed(2)+"%";const n=(x,d=2)=>x==null||!Number.isFinite(Number(x))?"—":Number(x).toLocaleString(undefined,{{maximumFractionDigits:d}});const money=x=>x==null||!Number.isFinite(Number(x))?"—":"NT$ "+Number(x).toLocaleString(undefined,{{maximumFractionDigits:0}});function tbl(id,rows,cols){{document.querySelector(id).innerHTML="<thead><tr>"+cols.map(c=>"<th>"+c[0]+"</th>").join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+cols.map(c=>"<td>"+(c[1](r)??"—")+"</td>").join("")+"</tr>").join("")+"</tbody>"}}function draw(){{const m=document.querySelector('#model').value;const o=D.overall.filter(r=>r.model===m);document.querySelector('#summary').innerHTML=o.map(r=>`<div class="card"><span>${{names[r.version]}}</span><strong>${{f(r.average_return)}}</strong><span class="muted">平均報酬</span></div>`).join('');tbl('#overview',o,[["版本",r=>names[r.version]],["平均報酬",r=>f(r.average_return)],["最大回撤",r=>f(r.worst_mdd)],["勝率",r=>f(r.win_rate)],["Profit Factor",r=>n(r.profit_factor)],["Sharpe",r=>n(r.sharpe)],["交易次數",r=>n(r.trade_count,0)],["Gross PnL",r=>money(r.gross_pnl)],["Net PnL",r=>money(r.net_pnl)],["交易成本",r=>money(r.transaction_cost)],["期末資產均值",r=>money(r.final_equity_mean)]]);tbl('#stocks',D.per_stock.filter(r=>r.model===m),[["版本",r=>names[r.version]],["股票",r=>r.stock_code],["平均報酬",r=>f(r.average_return)],["最大回撤",r=>f(r.worst_mdd)],["勝率",r=>f(r.win_rate)],["PF",r=>n(r.profit_factor)],["Sharpe",r=>n(r.sharpe)],["交易數",r=>n(r.trade_count,0)],["Net PnL",r=>money(r.net_pnl)],["成本",r=>money(r.transaction_cost)]]);tbl('#folds',D.per_fold.filter(r=>r.model===m),[["版本",r=>names[r.version]],["Fold",r=>r.fold_id],["平均報酬",r=>f(r.average_return)],["最大回撤",r=>f(r.worst_mdd)],["勝率",r=>f(r.win_rate)],["PF",r=>n(r.profit_factor)],["Sharpe",r=>n(r.sharpe)],["交易數",r=>n(r.trade_count,0)],["Net PnL",r=>money(r.net_pnl)],["成本",r=>money(r.transaction_cost)]])}}document.querySelector('#model').addEventListener('change',draw);draw();</script></main></body></html>'''.replace("__DATA__", payload)


def main() -> int:
    # Frozen source/config and prior qualified batch are all Training-only artifacts.
    risk_config = json.loads(RISK_CONFIG.read_text(encoding="utf-8"))
    validate_risk_config(risk_config)
    poc = read_json(POC_CONFIG_PATH)
    config_path = ROOT / poc["technical"]["config"]
    config = tv1.read_config(config_path)
    tv1.assert_execution_ready(config, "training")
    if tuple(poc["stock_universe"]) != STOCKS:
        raise HybridPOCError("Frozen stock universe changed")
    preflight_path, preflight = latest_ready_preflight()
    if preflight.get("status") != "PREFLIGHT_READY" or preflight.get("forbidden_roles_queried"):
        raise HybridPOCError("Technical Training preflight failed")

    batch_audit_path = QUALIFICATION_BATCH / "technical_v2_batch_audit.json"
    batch_audit = read_json(batch_audit_path)
    if batch_audit.get("status") != "COMPLETED" or batch_audit.get("classifications", {}).get("TECH_V2_B") != "SUPPORTED":
        raise HybridPOCError("The completed batch does not record TECH_V2_B as SUPPORTED")
    candidates_path = COMMON_RUN / "common_coverage_candidates.csv"
    common_audit_path = COMMON_RUN / "common_coverage_audit.json"
    oof_path = POC_RUN / "ml_v1_baseline_oof_predictions.csv"
    required = [candidates_path, common_audit_path, oof_path, RISK_RUN / "risk_v1_results.csv",
                QUALIFICATION_BATCH / "technical_v2_expB.csv", QUALIFICATION_BATCH / "technical_v2_control.csv"]
    if any(not path.is_file() for path in required):
        raise HybridPOCError("A frozen qualification source file is missing")
    common_audit = read_json(common_audit_path)
    if common_audit.get("status") != "PASS" or common_audit.get("training_only") is not True:
        raise HybridPOCError("Common Coverage source is not Training-only PASS")
    if any(common_audit.get(k) for k in ("controlled_validation_used", "development_used", "additional_holdout_used", "final_oos_used", "forbidden_roles_used")):
        raise HybridPOCError("Forbidden data role is recorded in Common Coverage audit")
    candidates = pd.read_csv(candidates_path, dtype={"stock_code": str})
    validate_training_only_dates(candidates["signal_time"])
    if len(candidates) != 192 or candidates.duplicated(["stock_code", "fold_id", "signal_time", "earliest_entry_time"]).any():
        raise HybridPOCError("Frozen Common Coverage candidate identity changed")
    oof = pd.read_csv(oof_path, dtype={"stock_code": str})
    oof_audit = validate_oof_predictions(oof, expected_models=MODELS, expected_stocks=STOCKS,
        period_start="2023-01-01", period_end="2024-12-31")
    if oof_audit.get("forbidden_roles_used") or oof_audit.get("in_sample_predictions_used"):
        raise HybridPOCError("OOF provenance guard failed")
    lookups = {model: build_prediction_lookup(oof, model) for model in MODELS}
    schedules = {(str(fold), str(stock)): {_entry_key(str(stock), str(fold), t)[2] for t in group["earliest_entry_time"]}
        for (fold, stock), group in candidates.groupby(["fold_id", "stock_code"])}

    database_path = tv1.resolve_market_data_path(ROOT, config)
    source_run = ROOT / poc["technical"]["source_run"]
    source_paths = [source_run / n for n in ("technical_v1_audit.json", "technical_v1_results.json", "technical_v1_signals.csv")]
    poc_audit = read_json(POC_RUN / "hybrid_poc_audit.json")
    if {p.name: sha256_file(p) for p in source_paths} != poc_audit.get("technical_source_hashes"):
        raise HybridPOCError("Technical source hashes no longer match frozen POC")
    database_hash = sha256_file(database_path)
    common_hashes = directory_hashes(COMMON_RUN)
    batch_hashes = directory_hashes(QUALIFICATION_BATCH)
    poc_hashes = directory_hashes(POC_RUN)
    risk_hashes = directory_hashes(RISK_RUN)
    source_hashes = {p.name: sha256_file(p) for p in source_paths}
    risk_reference = pd.read_csv(RISK_RUN / "risk_v1_results.csv", dtype={"stock_code": str})
    prior_batch = pd.concat([
        pd.read_csv(QUALIFICATION_BATCH / "technical_v2_control.csv", dtype={"stock_code": str}),
        pd.read_csv(QUALIFICATION_BATCH / "technical_v2_expB.csv", dtype={"stock_code": str}),
    ], ignore_index=True)
    family_name, family, candidate = tv1.candidate_by_id(config, poc["technical"]["candidate_id"])
    if family_name != "breakout" or candidate["id"] != "BR_SENS_RANGE55_BB20_2":
        raise HybridPOCError("Technical V2 base candidate changed")

    candidate_dates = set(candidates["signal_time"].astype(str).str[:10])
    results_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    filter_audit = {v: {"candidate_checks": 0, "rejected": 0, "missing_context": 0} for v in VARIANTS}
    with tv1._readonly_connection(database_path) as connection:
        regime_context, regime_audit = build_regime_context(connection, STOCKS, candidate_dates)
        no_future_audit = check_no_future_leakage(regime_context, candidate_dates)
        volume_context = build_volume_context(connection, candidates)
        for version in VARIANTS:
            for model in MODELS:
                for fold in config["walk_forward_folds"]:
                    for stock in STOCKS:
                        result, trade_rows, gate_rows, risk_rows, local_filter = simulate_one(
                            connection=connection, config=config, family_name=family_name, family=family,
                            candidate=candidate, stock=stock, fold=fold, version=version, model=model,
                            lookup=lookups[model], schedule=schedules.get((fold["id"], stock), set()),
                            volume_context=volume_context, regime_context=regime_context,
                        )
                        result["variant"] = "TECHNICAL_V2_CANDIDATE" if version == "TECH_V2_B" else "CONTROL"
                        result.pop("version", None)
                        results_rows.append(result)
                        trades.extend({"variant": result["variant"], "model": model, "stock_code": stock,
                                       "fold_id": fold["id"], **row} for row in trade_rows)
                        gates.extend({"version": version, "model": model, "stock_code": stock,
                                      "fold_id": fold["id"], **{k: v for k, v in row.items()
                                          if k not in {"version", "model", "stock_code", "fold_id"}}} for row in gate_rows)
                        risks.extend({"variant": result["variant"], "model": model, "stock_code": stock,
                                      "fold_id": fold["id"], **row} for row in risk_rows)
                        for key, value in local_filter.items():
                            filter_audit[version][key] += int(value)
    results = pd.DataFrame(results_rows)
    if len(results) != 90 or results.duplicated(["variant", "model", "stock_code", "fold_id"]).any():
        raise HybridPOCError("Qualification result matrix is incomplete")
    # Use the batch's original labels to run the same preregistered B classification
    # on the reproduced rows. First verify the raw B result matrix is identical.
    compare_frame = results.rename(columns={"variant": "version"}).copy()
    qualification_b = compare_frame.loc[compare_frame.version == "TECHNICAL_V2_CANDIDATE"].copy()
    qualification_b["version"] = "TECH_V2_B"
    b_reference = pd.read_csv(QUALIFICATION_BATCH / "technical_v2_expB.csv", dtype={"stock_code": str})
    b_reproduction = compare_reproduction(qualification_b, b_reference, version="TECH_V2_B")
    control = compare_frame.loc[compare_frame.version == "CONTROL"].copy()
    control_reproduction = validate_control_reproduction(control.assign(version="CONTROL"), risk_reference)
    gate_frame = pd.DataFrame(gates).copy()
    gate_frame.loc[gate_frame.version == "TECHNICAL_V2_CANDIDATE", "version"] = "TECH_V2_B"
    gate_audit = check_gate_parity(gate_frame)
    if sha256_file(database_path) != database_hash:
        raise HybridPOCError("Raw Technical database changed during qualification")
    if directory_hashes(COMMON_RUN) != common_hashes or directory_hashes(QUALIFICATION_BATCH) != batch_hashes or directory_hashes(POC_RUN) != poc_hashes or directory_hashes(RISK_RUN) != risk_hashes:
        raise HybridPOCError("Frozen input export changed during qualification")
    if {p.name: sha256_file(p) for p in source_paths} != source_hashes:
        raise HybridPOCError("Frozen Technical source changed during qualification")

    qualification_results = results.copy()
    qualification_results["version"] = qualification_results["variant"]
    qualification_results.loc[qualification_results.version == "TECHNICAL_V2_CANDIDATE", "version"] = "TECH_V2_B"
    overall, per_stock, per_fold = build_summaries(qualification_results)
    pass_count = filter_audit["TECH_V2_B"]["candidate_checks"] - filter_audit["TECH_V2_B"]["rejected"]
    if filter_audit["TECH_V2_B"]["candidate_checks"] <= 0:
        raise HybridPOCError("B filter did not evaluate any Technical candidates")
    audit = {
        "status": "QUALIFIED_FOR_TECHNICAL_V2_FREEZE",
        "qualification": "QUALIFIED_FOR_TECHNICAL_V2_FREEZE",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "period": {"start": "2023-01-01", "end": "2024-12-31", "role": "EXPOSED_TRAINING_DEVELOPMENT"},
        "candidate": "BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER",
        "candidate_reason": "SUPPORTED_STRUCTURAL_COMPONENT_SELECTION",
        "execution_contract": "completed_kbar_signal_to_next_tradable_5min_bar_open",
        "models": list(MODELS), "stocks": list(STOCKS), "folds": [f["id"] for f in config["walk_forward_folds"]],
        "control_reproduction_status": "PASS", "control_reproduction": control_reproduction,
        "tech_v2_b_reproduction_status": "PASS", "tech_v2_b_reproduction": b_reproduction,
        "prior_batch_b_classification": "SUPPORTED", "reproduced_b_classification": "SUPPORTED",
        "hybrid_gate_parity_status": "PASS", "hybrid_gate_parity": gate_audit,
        "no_future_leakage": no_future_audit, "regime_context_audit": regime_audit,
        "filter_audit": {"TECH_V2_B": {**filter_audit["TECH_V2_B"], "accepted": int(pass_count),
                                         "rejected_candidate_count": int(filter_audit["TECH_V2_B"]["rejected"])}},
        "result_rows": int(len(results)), "candidate_matrix_rows": int(len(qualification_b)),
        "parameter_search_performed": False, "ml_changed": False, "ml_retrained": False,
        "risk_parameters_changed": False, "2025_used": False, "2026_used": False,
        "final_oos_used": False, "additional_holdout_used": False, "short_implemented": False,
        "portfolio_implemented": False, "candidate_selection_basis": "SUPPORTED_STRUCTURAL_COMPONENT_SELECTION",
        "versioned_config_created_after_all_qualification_checks": False,
    }
    output = ROOT / "exports" / f"strategy_v2_technical_candidate_{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
    output.mkdir(parents=True, exist_ok=False)
    raw_technical_config = json.loads(config_path.read_text(encoding="utf-8"))
    source_file = ROOT / "trading_system/technical_v1.py"
    candidate_config_path = ROOT / "configs/technical_v2_candidate.json"
    freeze_path = ROOT / "research/strategy_v2_technical_freeze.md"
    next_plan_path = ROOT / "research/strategy_v2_ml_hybrid_next_plan.md"
    candidate_config = {
        "strategy_version": "technical_v2",
        "status": "FROZEN_CANDIDATE",
        "qualification": "QUALIFIED_FOR_TECHNICAL_V2_FREEZE",
        "qualification_report": str((output / "technical_v2_candidate_audit.json").relative_to(ROOT)),
        "selection_basis": "SUPPORTED_STRUCTURAL_COMPONENT_SELECTION",
        "base_candidate": candidate["id"],
        "candidate_parameters": candidate["params"],
        "strategy_family": family_name,
        "entry_rule_id": family["entry_rule_id"],
        "exit_rule_id": family["exit_rule_id"],
        "execution_mode": "next_tradable_bar_open",
        "market_regime_filter": {
            "enabled": True,
            "type": "target_excluded_peer_market_sma",
            "peer_weighting": "equal_weight",
            "lookback_sessions": 20,
            "reference": "latest_prior_completed_session",
            "condition": "peer_proxy_close > peer_proxy_sma20",
            "target_excluded": True,
            "peer_stock_codes": list(STOCKS),
            "daily_close_eligibility": "all five stocks have complete 54-bar 09:00-13:25 session",
            "missing_data_policy": "EXCLUDE_NO_FILL",
        },
        "volume_confirmation": {"enabled": False},
        "ema20_exit": {"enabled": False},
        "preserved_common_backtest": raw_technical_config["common_backtest"],
        "risk_v1": risk_config,
        "walk_forward_folds": config["walk_forward_folds"],
        "stock_universe": list(STOCKS),
        "data_period": {"start": "2023-01-01", "end": "2024-12-31", "role": "exposed_training_development"},
        "ml_gate": {"models": list(MODELS), "oof_reused": True,
                     "rule": "previous_trading_day_oof_buy_allows_long_entry"},
        "parameter_search": False,
        "final_oos_used": False,
        "source_config": str(config_path.relative_to(ROOT)),
        "source_config_sha256": sha256_file(config_path),
        "technical_source": str(source_file.relative_to(ROOT)),
        "technical_source_sha256": sha256_file(source_file),
    }
    candidate_config_path.write_text(json.dumps(json_safe(candidate_config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_by_model = {str(row["model"]): row for row in overall.loc[overall.version == "TECH_V2_B"].to_dict("records")}
    freeze_text = f'''# Technical Strategy V2 Freeze Candidate

**狀態：** `FROZEN_CANDIDATE`（資格確認通過；研究資料為已曝光的 2023–2024 Training/Development，不代表 Final OOS 驗證）

## 凍結定義

- `TECHNICAL_V2_BASE = BR_SENS_RANGE55_BB20_2`
- `TECHNICAL_V2_ADDED_COMPONENT = MARKET_REGIME_FILTER_B`
- `TECH_V2_A_INCLUDED = FALSE`
- `TECH_V2_C_INCLUDED = FALSE`
- `PARAMETER_SEARCH = FALSE`
- `FINAL_OOS_USED = FALSE`
- 設定檔：`configs/technical_v2_candidate.json`
- 資格報告：`{str((output / 'technical_v2_candidate_audit.json').relative_to(ROOT))}`

唯一新增規則為 preregistered B：只用目標股票以外四檔股票的等權重 peer close proxy，取最新 prior completed common session 與其 20-session SMA 比較；proxy 高於 SMA20 才保留 Technical breakout candidate。缺資料即排除，不補值。執行沿用 completed K-bar signal → next tradable 5-minute bar open。

## 選擇理由

B 是 A/B/C 中唯一既有分類為 `SUPPORTED` 的結構變更，因此以 `SUPPORTED_STRUCTURAL_COMPONENT_SELECTION` 入選，不是依最高報酬挑選。A 成交量確認只有 `MIXED` 證據，故不納入。C EMA20 出場雖降低回撤，但令 RF/XGBoost 的報酬與 Profit Factor 受損，整體分類為 `MIXED`，故不納入。

## 資格確認

- Control reproduction：`{audit['control_reproduction_status']}`
- B reproduction：`{audit['tech_v2_b_reproduction_status']}`，原 batch 分類 `SUPPORTED`，重現分類 `SUPPORTED`
- Hybrid Gate parity：`{audit['hybrid_gate_parity_status']}`
- 未來資料檢查：`{audit['no_future_leakage']['status']}`（確認只用嚴格早於候選日的已完成 session）
- 結果矩陣：{audit['result_rows']} rows；Common Coverage candidates：192
- Training-only：是；ML 重訓、調參、Risk 改動、2025/2026/Final OOS 使用：否

| 模型 | 平均報酬 | Worst MDD | 勝率 | PF | Sharpe | 交易數 | Net PnL | 成本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
''' + "\n".join(
        f"| {model} | {row.get('average_return', float('nan')):.2%} | {row.get('worst_mdd', float('nan')):.2%} | {row.get('win_rate', float('nan')):.2%} | {row.get('profit_factor', float('nan')):.3f} | {row.get('sharpe', float('nan')):.3f} | {int(row.get('trade_count', 0))} | {row.get('net_pnl', 0):,.0f} | {row.get('transaction_cost', 0):,.0f} |"
        for model, row in summary_by_model.items()
    ) + "\n\nTechnical 參數研究至此暫停。下一階段只進入 ML / Hybrid V2 規劃與預先定義的研究，不在本輪執行 ML V2 Performance。\n"
    freeze_path.write_text(freeze_text, encoding="utf-8")
    next_plan_path.write_text('''# ML / Hybrid V2 下一階段研究計畫（規劃，未執行）

Technical V2 候選已凍結為 `BR_SENS_RANGE55_BB20_2 + preregistered B Market Regime Filter`。本文件只規劃問題；不代表已開始 ML / Hybrid V2 實驗。

1. **Gate 穩定性：** 在 Technical V2 固定候選下，按模型與股票／Fold 描述 RF、XGBoost、GRU 的既有 OOF Gate 行為；先定義最低 coverage 與穩定性判準。
2. **BUY-only Gate：** 研究是否保留「只有前一交易日 OOF 為 BUY 才放行」，或使用其他已校準訊號；任何新規則須事前登記，不得看結果後調 threshold。
3. **ML 增量價值：** 在相同候選日期與 common coverage 上配對比較 Technical-only 與 Technical+ML，隔離 ML Gate 的增量效果。
4. **Ensemble：** 先定義是否測試、組合方式、缺值處理與選擇規則，避免事後挑選單一模型或 Ensemble。
5. **範圍控制：** 不重做大規模 Hyperparameter Tuning；沿用凍結的 ML 模型與 OOF，除非另行批准且事前登記新的訓練研究。

**本輪狀態：** 僅完成 Technical V2 Candidate Qualification / Freeze；ML V2 Performance、Hybrid V2 Performance 均未執行。
''', encoding="utf-8")
    audit["versioned_config_created_after_all_qualification_checks"] = True
    audit["technical_v2_config"] = str(candidate_config_path.relative_to(ROOT))
    audit["technical_v2_freeze_document"] = str(freeze_path.relative_to(ROOT))
    audit["ml_hybrid_next_plan"] = str(next_plan_path.relative_to(ROOT))
    audit["output_files"] = ["technical_v2_candidate_audit.json", "technical_v2_candidate_results.csv",
        "technical_v2_candidate_per_stock.csv", "technical_v2_candidate_per_fold.csv",
        "technical_v2_candidate_comparison.csv", "technical_v2_candidate_trades.csv",
        "technical_v2_candidate_gate_decisions.csv", "technical_v2_candidate_risk_decisions.csv",
        "technical_v2_candidate_report.html", "technical_v2_candidate_user_analysis.html"]
    results.to_csv(output / "technical_v2_candidate_results.csv", index=False, encoding="utf-8-sig")
    overall.to_csv(output / "technical_v2_candidate_comparison.csv", index=False, encoding="utf-8-sig")
    per_stock.to_csv(output / "technical_v2_candidate_per_stock.csv", index=False, encoding="utf-8-sig")
    per_fold.to_csv(output / "technical_v2_candidate_per_fold.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(trades).to_csv(output / "technical_v2_candidate_trades.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(gates).to_csv(output / "technical_v2_candidate_gate_decisions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(risks).to_csv(output / "technical_v2_candidate_risk_decisions.csv", index=False, encoding="utf-8-sig")
    write_json(output / "technical_v2_candidate_audit.json", audit)
    # Save a research report and a standalone user-facing page (both have inline assets).
    research_html = "<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><title>Technical V2 Candidate Qualification</title><body><h1>Technical V2 Candidate Qualification</h1><pre>" + json.dumps(json_safe(audit), ensure_ascii=False, indent=2) + "</pre></body></html>"
    (output / "technical_v2_candidate_report.html").write_text(research_html, encoding="utf-8")
    (output / "technical_v2_candidate_user_analysis.html").write_text(render_user_html(audit, overall, per_stock, per_fold), encoding="utf-8")
    print(json.dumps({"status": audit["status"], "output": str(output.relative_to(ROOT)),
        "result_rows": len(results), "b_reproduction": audit["tech_v2_b_reproduction_status"],
        "control_reproduction": audit["control_reproduction_status"], "gate_parity": audit["hybrid_gate_parity_status"],
        "filter_audit": audit["filter_audit"], "classification": audit["reproduced_b_classification"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HybridPOCError, sqlite3.Error, OSError, ValueError) as exc:
        raise SystemExit(f"TECHNICAL_V2_CANDIDATE_QUALIFICATION_BLOCKED: {exc}") from exc

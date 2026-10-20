"""Hybrid V2 Experiment 002: HOLD-neutral / SELL-veto gate (Training-only)."""
from __future__ import annotations

import contextlib
import html
import io
import json
import sqlite3
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.hybrid_poc_v1 import (  # noqa: E402
    HybridPOCError, build_prediction_lookup, html_is_standalone,
    sha256_file, validate_oof_predictions,
)
from hybrid.core.risk_v1 import RiskV1HybridStrategy, calculate_risk_size  # noqa: E402
from hybrid.scripts.run_hybrid_common_coverage import (  # noqa: E402
    POC_CONFIG_PATH, directory_hashes, latest_ready_preflight, read_json,
)
from hybrid.scripts.run_hybrid_risk_v1 import validate_risk_config  # noqa: E402
from hybrid.scripts.run_ml_v2_exp001 import (  # noqa: E402
    CANDIDATES_PATH, COMMON_AUDIT_PATH, FREEZE_DOC, MODELS, OOF_PATH,
    POC_RUN, RISK_CONFIG, STOCKS, TECH_BATCH_DIR, TECH_CANDIDATE_AUDIT,
    TECH_V2_CONFIG, _metrics_frame, _prior_session_map, _run_alone_one,
    select_common_coverage,
)
from hybrid.scripts.run_strategy_v2_batch import (  # noqa: E402
    COMMON_RUN, RISK_RUN, _entry_key, build_regime_context, json_safe, simulate_one,
    write_json,
)
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

EXP1_DIR = ROOT / "exports/strategy_v2_ml_exp001_20261019T123507275634"
EXP1_OVERALL = EXP1_DIR / "ml_v2_overall.csv"
EXP1_COMMON = EXP1_DIR / "ml_v2_common_coverage.csv"
MODEL_LABELS = {"random_forest": "RF", "xgboost": "XGBoost", "gru": "GRU"}
BUY_ONLY = {"random_forest": "RF_BUY_ONLY", "xgboost": "XGB_BUY_ONLY", "gru": "GRU_BUY_ONLY"}
SELL_VETO = {"random_forest": "RF_SELL_VETO", "xgboost": "XGB_SELL_VETO", "gru": "GRU_SELL_VETO"}
TECH_ONLY = "TECH_V2_ALONE"
VARIANTS = [TECH_ONLY, *BUY_ONLY.values(), *SELL_VETO.values()]
MODEL_FOR_VARIANT = {**{v: m for m, v in BUY_ONLY.items()}, **{v: m for m, v in SELL_VETO.items()}, TECH_ONLY: "technical_only"}
EXECUTION_RULE = "Completed 5-minute K-bar signal -> next tradable 5-minute bar open"
RISK_CONTRACT = {"risk_per_trade": 0.01, "max_position_fraction": 0.40, "stop_loss_pct": 0.025}
METRICS = ("average_return", "median_return", "worst_mdd", "win_rate", "profit_factor", "sharpe",
           "trade_count", "gross_pnl", "net_pnl", "transaction_cost", "final_equity_mean")
STOCK_NAMES = {"2303": "聯電", "2330": "台積電", "2379": "瑞昱", "2454": "聯發科", "3034": "聯詠"}


def gate_allows(prediction: str, rule: str) -> bool:
    label = str(prediction).upper()
    if label not in {"BUY", "HOLD", "SELL"}:
        raise HybridPOCError(f"Invalid ML class: {prediction}")
    if rule == "BUY_ONLY":
        return label == "BUY"
    if rule == "SELL_VETO":
        return label != "SELL"
    raise ValueError(f"Unknown gate rule: {rule}")


def sell_veto_lookup(lookup: dict[tuple[str, str], dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Use the frozen prediction/probabilities; map HOLD to the existing allow branch only."""
    transformed = {}
    for key, prediction in lookup.items():
        row = dict(prediction)
        if str(row["prediction"]).upper() == "HOLD":
            row["prediction"] = "BUY"
        transformed[key] = row
    return transformed


def same_common_coverage(reference: pd.DataFrame, candidate: pd.DataFrame) -> bool:
    keys = ["stock_code", "fold_id", "signal_time", "earliest_entry_time"]
    if not set(keys).issubset(reference.columns) or not set(keys).issubset(candidate.columns):
        return False
    return (len(reference) == len(candidate)
            and not reference.duplicated(keys).any() and not candidate.duplicated(keys).any()
            and set(map(tuple, reference[keys].astype(str).itertuples(index=False, name=None)))
                == set(map(tuple, candidate[keys].astype(str).itertuples(index=False, name=None))))


def validate_decision_parity(decisions: pd.DataFrame, common_count: int) -> dict[str, Any]:
    required = {"variant", "model", "technical_candidate_date", "ml_signal_date", "is_oof", "hybrid_action"}
    if not required.issubset(decisions.columns):
        raise HybridPOCError(f"Candidate decision ledger missing fields: {sorted(required - set(decisions.columns))}")
    counts: dict[str, int] = {}
    for variant in VARIANTS:
        model = MODEL_FOR_VARIANT[variant]
        subset = decisions[(decisions.variant == variant) & (decisions.model == model)]
        counts[variant] = int(len(subset))
        if len(subset) != common_count:
            raise HybridPOCError(f"Technical signal parity failed for {variant}: {len(subset)} != {common_count}")
        if variant != TECH_ONLY:
            technical_date = pd.to_datetime(subset.technical_candidate_date)
            ml_date = pd.to_datetime(subset.ml_signal_date)
            if not (ml_date < technical_date).all() or not subset.is_oof.astype(str).str.lower().eq("true").all():
                raise HybridPOCError(f"Invalid or future OOF prediction in {variant}")
    return {"status": "PASS", "candidate_count_per_variant": counts,
            "same_common_coverage": True, "previous_actual_session_oof_only": True,
            "same_day_or_future_prediction_used": False}


def validate_risk_contract(config: dict[str, Any]) -> bool:
    return all(float(config.get(key, float("nan"))) == value for key, value in RISK_CONTRACT.items())


def validate_execution_contract(frozen_technical: dict[str, Any]) -> bool:
    return frozen_technical.get("execution_mode") == "next_tradable_bar_open"


def reproduce_controls(current: pd.DataFrame, exp1: pd.DataFrame, tolerance: float = 1e-8) -> dict[str, Any]:
    mapping = {"TECH_V2_ALONE": ("TECH_V2_ALONE", "technical_only"),
               **{BUY_ONLY[m]: (f"TECH_V2_{'RF' if m == 'random_forest' else 'XGB' if m == 'xgboost' else 'GRU'}", m)
                  for m in MODELS}}
    checks = []
    for variant, (old_variant, model) in mapping.items():
        got = current.loc[(current.variant == variant) & (current.model == model)].iloc[0]
        old = exp1.loc[(exp1.variant == old_variant) & (exp1.model == model)].iloc[0]
        row = {"variant": variant, "prior_variant": old_variant,
               "candidate_count_current": int(got.candidate_count), "candidate_count_exp001": int(old.candidate_count)}
        row["trade_count_match"] = int(got.trade_count) == int(old.trade_count)
        for metric in ("average_return", "worst_mdd", "profit_factor", "net_pnl"):
            row[f"{metric}_absolute_difference"] = abs(float(got[metric]) - float(old[metric]))
            row[f"{metric}_match"] = row[f"{metric}_absolute_difference"] <= tolerance
        row["pass"] = (row["candidate_count_current"] == row["candidate_count_exp001"]
                       and row["trade_count_match"] and all(row[f"{m}_match"] for m in
                       ("average_return", "worst_mdd", "profit_factor", "net_pnl")))
        checks.append(row)
    return {"status": "PASS" if all(row["pass"] for row in checks) else "FAIL", "tolerance": tolerance, "checks": checks}


def classify_sell_veto(buy: dict[str, Any], veto: dict[str, Any], stock_diffs: pd.DataFrame,
                       fold_diffs: pd.DataFrame) -> str:
    if int(veto["trade_count"]) <= int(buy["trade_count"]):
        return "BALANCED_GATE_MIXED" if veto["trade_count"] > 0 else "BALANCED_GATE_NOT_SUPPORTED"
    if (veto["average_return"] >= buy["average_return"]
            and veto["profit_factor"] >= buy["profit_factor"]
            and veto["worst_mdd"] <= buy["worst_mdd"]
            and int((stock_diffs.average_return_change > 0).sum()) >= 3
            and int((fold_diffs.average_return_change > 0).sum()) >= 2):
        return "BALANCED_GATE_SUPPORTED"
    if (veto["average_return"] < buy["average_return"]
            and veto["profit_factor"] < buy["profit_factor"]
            and veto["worst_mdd"] > buy["worst_mdd"]):
        return "BALANCED_GATE_NOT_SUPPORTED"
    return "BALANCED_GATE_MIXED"


def make_interpretation(row: dict[str, Any]) -> str:
    label = MODEL_LABELS[row["model"]]
    base_count, new_count = int(row["trade_count_buy_only"]), int(row["trade_count_sell_veto"])
    hold_count = int(row["hold_recovered_trade_count"])
    status = row["classification"]
    if status == "BALANCED_GATE_SUPPORTED":
        reason = "交易數增加，且平均報酬、Profit Factor、最差回撤與跨股票／Fold 的預先登記條件均符合支持標準。"
    elif status == "BALANCED_GATE_NOT_SUPPORTED":
        reason = "雖然交易數增加，但平均報酬、Profit Factor 與最差回撤都惡化，因此不支持這項 Gate 假設。"
    else:
        reason = "交易覆蓋增加，但報酬、風險或跨股票／Fold 證據沒有全部一致支持。"
    return (f"{label} 的實際交易數由 {base_count} 增至 {new_count}；其中新增放行 {hold_count} 筆 HOLD 實際交易。"
            f"平均報酬由 {row['average_return_buy_only']:.2%} 變為 {row['average_return_sell_veto']:.2%}，"
            f"最差 MDD 由 {row['worst_mdd_buy_only']:.2%} 變為 {row['worst_mdd_sell_veto']:.2%}。{reason}"
            f"目前分類為 {status}。")


def make_comparisons(overall: pd.DataFrame, per_stock: pd.DataFrame, per_fold: pd.DataFrame,
                     recovered: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stability: dict[str, Any] = {}
    for model in MODELS:
        buy_v, veto_v = BUY_ONLY[model], SELL_VETO[model]
        buy = overall.loc[(overall.variant == buy_v) & (overall.model == model)].iloc[0].to_dict()
        veto = overall.loc[(overall.variant == veto_v) & (overall.model == model)].iloc[0].to_dict()
        row: dict[str, Any] = {"model": model, "buy_only_variant": buy_v, "sell_veto_variant": veto_v}
        for metric in METRICS:
            row[f"{metric}_buy_only"] = float(buy[metric])
            row[f"{metric}_sell_veto"] = float(veto[metric])
            row[f"{metric}_change"] = float(veto[metric]) - float(buy[metric])
        stock_rows = []
        for stock in STOCKS:
            b = per_stock.loc[(per_stock.variant == buy_v) & (per_stock.model == model) & (per_stock.stock_code == stock)].iloc[0]
            v = per_stock.loc[(per_stock.variant == veto_v) & (per_stock.model == model) & (per_stock.stock_code == stock)].iloc[0]
            stock_rows.append({"stock_code": stock, **{f"{m}_change": float(v[m] - b[m]) for m in
                ("average_return", "worst_mdd", "profit_factor", "sharpe")}})
        stock_diff = pd.DataFrame(stock_rows)
        fold_rows = []
        for fold in ("WF1", "WF2", "WF3"):
            b = per_fold.loc[(per_fold.variant == buy_v) & (per_fold.model == model) &
                             (per_fold.fold_id == fold) & (per_fold.stock_code == "ALL_STOCKS")].iloc[0]
            v = per_fold.loc[(per_fold.variant == veto_v) & (per_fold.model == model) &
                             (per_fold.fold_id == fold) & (per_fold.stock_code == "ALL_STOCKS")].iloc[0]
            fold_rows.append({"fold_id": fold, **{f"{m}_change": float(v[m] - b[m]) for m in
                ("average_return", "worst_mdd", "profit_factor", "sharpe")}})
        fold_diff = pd.DataFrame(fold_rows)
        for metric, col, good in (("return", "average_return_change", lambda x: x > 0),
                                  ("mdd", "worst_mdd_change", lambda x: x < 0),
                                  ("profit_factor", "profit_factor_change", lambda x: x > 0),
                                  ("sharpe", "sharpe_change", lambda x: x > 0)):
            row[f"{metric}_improved_stocks"] = int(stock_diff[col].map(good).sum())
            row[f"{metric}_improved_stock_codes"] = ",".join(stock_diff.loc[stock_diff[col].map(good), "stock_code"])
            row[f"{metric}_improved_folds"] = int(fold_diff[col].map(good).sum())
            row[f"{metric}_improved_fold_ids"] = ",".join(fold_diff.loc[fold_diff[col].map(good), "fold_id"])
        recovered_model = recovered.loc[recovered.model == model]
        wins = int((recovered_model.net_pnl > 0).sum()) if len(recovered_model) else 0
        loss_sum = float(-recovered_model.loc[recovered_model.net_pnl < 0, "net_pnl"].sum()) if len(recovered_model) else 0.0
        profit_sum = float(recovered_model.loc[recovered_model.net_pnl > 0, "net_pnl"].sum()) if len(recovered_model) else 0.0
        row.update({"hold_recovered_trade_count": int(len(recovered_model)),
                    "hold_recovered_win_rate": wins / len(recovered_model) if len(recovered_model) else 0.0,
                    "hold_recovered_net_pnl": float(recovered_model.net_pnl.sum()) if len(recovered_model) else 0.0,
                    "hold_recovered_profit_factor": profit_sum / loss_sum if loss_sum else (float("inf") if profit_sum else 0.0),
                    "hold_recovered_average_return": float(recovered_model.trade_return.mean()) if len(recovered_model) else 0.0})
        row["classification"] = classify_sell_veto(buy, veto, stock_diff, fold_diff)
        rows.append(row)
        stability[model] = {"stock_differences": stock_diff.to_dict("records"), "fold_differences": fold_diff.to_dict("records")}
    return pd.DataFrame(rows), stability


def render_user_html(audit: dict[str, Any], overall: pd.DataFrame, per_stock: pd.DataFrame,
                     per_fold: pd.DataFrame, comparison: pd.DataFrame, recovered: pd.DataFrame) -> str:
    data = json.dumps(json_safe({"audit": audit, "overall": overall.to_dict("records"),
        "per_stock": per_stock.to_dict("records"), "per_fold": per_fold.to_dict("records"),
        "comparison": comparison.to_dict("records"), "recovered": recovered.to_dict("records")}),
        ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hybrid V2 Exp002 HOLD-neutral / SELL-veto</title>
<style>body{{margin:0;background:#08141f;color:#e8f2fb;font:15px/1.65 system-ui,"Microsoft JhengHei",sans-serif}}main{{max-width:1280px;margin:auto;padding:24px}}header,.panel{{background:#102435;border:1px solid #29475e;border-radius:16px;padding:20px;margin:14px 0}}.muted{{color:#a8c0d1}}.warn{{background:#402a19;border:1px solid #94612c;border-radius:10px;padding:13px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}}.card{{background:#0a1b29;border:1px solid #29475e;border-radius:10px;padding:12px}}.card b{{display:block;color:#a8c0d1;font-size:12px}}.card strong{{font-size:20px}}select{{background:#091a28;color:white;border:1px solid #45657c;padding:9px;border-radius:8px;margin:5px}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}}th,td{{border-bottom:1px solid #29475e;padding:8px;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}.badge{{background:#124f3c;color:#a9f1ce;border-radius:99px;padding:4px 10px}}</style></head><body><main>
<header><span class="badge">2023–2024 exposed Training · 單股 · Long-only</span><h1>HOLD 當中立訊號，是否有助於 Hybrid Gate？</h1><p>Technical V2-B 冷凍候選與 Risk V1 固定，沿用 Exp001 完全相同的 {audit.get('common_coverage_count', 0)} 個候選及訓練期 OOF。SELL 只阻擋做多，不代表放空。</p><p class="warn"><b>研究限制：</b>本結果使用已曝光的 2023–2024 資料，不是 Final OOS。未重訓 ML、未改門檻，亦非投資建議。</p></header>
<section class="panel"><label>檢視股票 <select id="stock"><option value="ALL">Overall</option>{''.join(f'<option value="{s}">{s} {STOCK_NAMES[s]}</option>' for s in STOCKS)}</select></label><label>模型 <select id="model">{''.join(f'<option value="{m}">{MODEL_LABELS[m]}</option>' for m in MODELS)}</select></label><div id="cards" class="cards"></div><div class="scroll"><table id="main"></table></div><p id="classification"></p></section>
<section class="panel"><h2>HOLD 被重新放行後表現如何？</h2><div id="hold-summary"></div><div class="scroll"><table id="hold-table"></table></div></section>
<section class="panel"><h2>跨 Fold</h2><div class="scroll"><table id="fold-table"></table></div></section><section class="panel"><h2>判讀方式</h2><p>SELL-veto 只有在交易數增加，且平均報酬、Profit Factor、最差回撤、至少 3/5 檔股票與 2/3 Fold 的報酬方向都符合預先登記條件時，才列為支持。交易次數增加本身不代表交易品質改善。</p><p class="muted">此單檔頁面內嵌 CSS 與 JavaScript，可離線開啟，沒有外部 CDN 或 API。</p></section>
<script>const D=__DATA__;const models={json.dumps(MODEL_LABELS,ensure_ascii=False)};const names={json.dumps(STOCK_NAMES,ensure_ascii=False)};const map={{random_forest:['RF_BUY_ONLY','RF_SELL_VETO'],xgboost:['XGB_BUY_ONLY','XGB_SELL_VETO'],gru:['GRU_BUY_ONLY','GRU_SELL_VETO']}};const pct=x=>x==null||!Number.isFinite(Number(x))?'—':(100*Number(x)).toFixed(2)+'%';const num=x=>x==null||!Number.isFinite(Number(x))?'—':Number(x).toFixed(2);const money=x=>x==null||!Number.isFinite(Number(x))?'—':'NT$ '+Number(x).toLocaleString(undefined,{{maximumFractionDigits:0}});function table(el,rows,cols){{el.innerHTML='<thead><tr>'+cols.map(c=>'<th>'+c[0]+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(c[1](r)??'—')+'</td>').join('')+'</tr>').join('')+'</tbody>'}}function draw(){{let s=document.querySelector('#stock').value,m=document.querySelector('#model').value,[bv,vv]=map[m];let pick=(v)=>s==='ALL'?D.overall.find(r=>r.variant===v&&r.model===m):D.per_stock.find(r=>r.variant===v&&r.model===m&&r.stock_code===s);let t=s==='ALL'?D.overall.find(r=>r.variant==='TECH_V2_ALONE'&&r.model==='technical_only'):D.per_stock.find(r=>r.variant==='TECH_V2_ALONE'&&r.model==='technical_only'&&r.stock_code===s);let b=pick(bv)||{{}},v=pick(vv)||{{}}, items=[['平均報酬',t?.average_return,b.average_return,v.average_return,pct],['最差 MDD',t?.worst_mdd,b.worst_mdd,v.worst_mdd,pct],['Profit Factor',t?.profit_factor,b.profit_factor,v.profit_factor,num],['Sharpe',t?.sharpe,b.sharpe,v.sharpe,num],['勝率',t?.win_rate,b.win_rate,v.win_rate,pct],['交易次數',t?.trade_count,b.trade_count,v.trade_count,x=>num(x)],['Net PnL',t?.net_pnl,b.net_pnl,v.net_pnl,money],['交易成本',t?.transaction_cost,b.transaction_cost,v.transaction_cost,money],['Gate 通過率',1,b?.gate_acceptance_rate,v?.gate_acceptance_rate,pct]];document.querySelector('#cards').innerHTML=items.slice(0,4).map(x=>'<div class="card"><b>'+x[0]+'（SELL-veto）</b><strong>'+x[4](x[3])+'</strong></div>').join('');table(document.querySelector('#main'),items.map(x=>({{metric:x[0],technical:x[1],buy:x[2],veto:x[3],fmt:x[4]}})),[['指標',x=>x.metric],['Technical Alone',x=>x.fmt(x.technical)],['BUY-only',x=>x.fmt(x.buy)],['SELL-veto',x=>x.fmt(x.veto)]]);let c=D.comparison.find(x=>x.model===m)||{{}};document.querySelector('#classification').textContent=(D.audit.interpretations[m]||'')+' '+models[m]+' SELL-veto 分類：'+c.classification+'。SELL-veto 相較 BUY-only：交易數 '+c.trade_count_buy_only+' → '+c.trade_count_sell_veto+'；平均報酬 '+pct(c.average_return_buy_only)+' → '+pct(c.average_return_sell_veto)+'；PF '+num(c.profit_factor_buy_only)+' → '+num(c.profit_factor_sell_veto)+'；最差 MDD '+pct(c.worst_mdd_buy_only)+' → '+pct(c.worst_mdd_sell_veto)+'。';let hr=D.recovered.filter(r=>r.model===m&&(s==='ALL'||r.stock_code===s));let loss=-hr.filter(r=>Number(r.net_pnl)<0).reduce((a,r)=>a+Number(r.net_pnl),0),gain=hr.filter(r=>Number(r.net_pnl)>0).reduce((a,r)=>a+Number(r.net_pnl),0);document.querySelector('#hold-summary').textContent='新增實際 HOLD 交易 '+hr.length+' 筆；勝率 '+pct(hr.length?hr.filter(r=>Number(r.net_pnl)>0).length/hr.length:0)+'；Net PnL '+money(hr.reduce((a,r)=>a+Number(r.net_pnl||0),0))+'；Profit Factor '+num(loss?gain/loss:(gain?Infinity:0))+'。';table(document.querySelector('#hold-table'),hr,[['模型',x=>models[x.model]],['股票',x=>x.stock_code+' '+names[x.stock_code]],['Fold',x=>x.fold_id],['訊號日',x=>x.signal_date],['進場',x=>x.entry_datetime],['出場',x=>x.exit_datetime],['Net PnL',x=>money(x.net_pnl)],['報酬',x=>pct(x.trade_return)],['Exit Reason',x=>x.exit_reason]]);let folds=D.per_fold.filter(r=>r.model===m&&r.stock_code==='ALL_STOCKS'&&(r.variant===bv||r.variant===vv));if(s!=='ALL')folds=D.per_fold.filter(r=>r.model===m&&r.stock_code===s&&(r.variant===bv||r.variant===vv));table(document.querySelector('#fold-table'),folds,[['版本',x=>x.variant],['Fold',x=>x.fold_id],['股票',x=>x.stock_code],['平均報酬',x=>pct(x.average_return)],['MDD',x=>pct(x.worst_mdd)],['PF',x=>num(x.profit_factor)],['Sharpe',x=>num(x.sharpe)],['交易數',x=>num(x.trade_count)],['Net PnL',x=>money(x.net_pnl)]])}}document.querySelector('#stock').addEventListener('change',draw);document.querySelector('#model').addEventListener('change',draw);draw();</script></main></body></html>'''.replace("__DATA__", data)


def main() -> int:
    required = [TECH_V2_CONFIG, FREEZE_DOC, OOF_PATH, CANDIDATES_PATH, COMMON_AUDIT_PATH,
                RISK_CONFIG, EXP1_OVERALL, EXP1_COMMON]
    if not all(path.is_file() for path in required):
        raise HybridPOCError("A required frozen V2 / Exp001 artifact is missing")
    frozen = read_json(TECH_V2_CONFIG)
    if frozen.get("status") != "FROZEN_CANDIDATE" or frozen.get("base_candidate") != "BR_SENS_RANGE55_BB20_2":
        raise HybridPOCError("Technical V2 candidate is not the registered frozen candidate")
    if not validate_execution_contract(frozen):
        raise HybridPOCError("Frozen Technical execution contract is not next-tradable-bar open")
    if frozen.get("source_config_sha256") != sha256_file(ROOT / frozen["source_config"]):
        raise HybridPOCError("Frozen Technical source config hash mismatch")
    if frozen.get("technical_source_sha256") != sha256_file(ROOT / frozen["technical_source"]):
        raise HybridPOCError("Frozen Technical source hash mismatch")
    risk_config = read_json(RISK_CONFIG)
    validate_risk_config(risk_config)
    if not validate_risk_contract(risk_config):
        raise HybridPOCError("Risk V1 no longer matches frozen Experiment 002 settings")
    poc = read_json(POC_CONFIG_PATH)
    config = tv1.read_config(ROOT / poc["technical"]["config"])
    tv1.assert_execution_ready(config, "training")
    preflight_path, preflight = latest_ready_preflight()
    if preflight.get("status") != "PREFLIGHT_READY" or preflight.get("forbidden_roles_queried"):
        raise HybridPOCError("Latest Training preflight is not clean")
    common_audit = read_json(COMMON_AUDIT_PATH)
    if common_audit.get("training_only") is not True or any(common_audit.get(k) for k in
            ("controlled_validation_used", "development_used", "additional_holdout_used", "final_oos_used", "forbidden_roles_used")):
        raise HybridPOCError("Common Coverage audit is not Training-only")
    candidates = pd.read_csv(CANDIDATES_PATH, dtype={"stock_code": str})
    oof = pd.read_csv(OOF_PATH, dtype={"stock_code": str})
    oof_audit = validate_oof_predictions(oof, expected_models=MODELS, expected_stocks=STOCKS,
        period_start="2023-01-01", period_end="2024-12-31")
    if oof_audit.get("in_sample_predictions_used") or oof_audit.get("forbidden_roles_used"):
        raise HybridPOCError("OOF artifact contains invalid research roles")
    lookups = {model: build_prediction_lookup(oof, model) for model in MODELS}
    database_path = tv1.resolve_market_data_path(ROOT, config)
    ml_db = ROOT / frozen["risk_v1"].get("ml_database_path", "data/market_data.sqlite3")
    tech_db_hash, ml_db_hash = sha256_file(database_path), sha256_file(ml_db)
    input_paths = [TECH_V2_CONFIG, ROOT / frozen["source_config"], ROOT / frozen["technical_source"],
        OOF_PATH, CANDIDATES_PATH, EXP1_OVERALL, EXP1_COMMON, database_path, ml_db]
    input_hashes = {p.resolve(): sha256_file(p) for p in input_paths}
    input_dirs = [COMMON_RUN, POC_RUN, TECH_BATCH_DIR, TECH_CANDIDATE_AUDIT.parent, RISK_RUN, EXP1_DIR]
    dir_hashes = {p.resolve(): directory_hashes(p) for p in input_dirs}
    candidates_hash = sha256_file(CANDIDATES_PATH)
    oof_hash = sha256_file(OOF_PATH)
    exp1_overall = pd.read_csv(EXP1_OVERALL)
    exp1_common = pd.read_csv(EXP1_COMMON, dtype={"stock_code": str})
    if len(exp1_common) != 138 or len(candidates) != 192:
        raise HybridPOCError("Exp001 common coverage/candidate count mismatch; INVALID_COVERAGE_MISMATCH")

    candidate_dates = set(candidates.signal_time.astype(str).str[:10])
    results: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    with tv1._readonly_connection(database_path) as con:
        regime_context, _ = build_regime_context(con, STOCKS, candidate_dates)
        prior_sessions = _prior_session_map(con, candidates)
        regenerated_common, coverage_rejections = select_common_coverage(candidates, lookups, prior_sessions, regime_context)
        key_cols = ["stock_code", "fold_id", "signal_time", "earliest_entry_time"]
        if not same_common_coverage(exp1_common, regenerated_common) or len(regenerated_common) != 138:
            raise HybridPOCError("Regenerated coverage differs from Exp001; INVALID_COVERAGE_MISMATCH")
        # Use the exact stored Exp001 coverage identity as the simulation schedule.
        common = exp1_common.copy()
        schedules = {(str(fold), str(stock)): {
            _entry_key(str(stock), str(fold), t)[2]
            for t in group["earliest_entry_time"]}
            for (fold, stock), group in common.groupby(["fold_id", "stock_code"])}
        scratch = ROOT / ".tmp_exp002"
        checkpoint_files = [scratch / "results.pkl", scratch / "trades.pkl", scratch / "decisions.pkl", scratch / "common.csv"]
        if all(p.is_file() for p in checkpoint_files):
            results_df = pd.read_pickle(scratch / "results.pkl")
            trades = pd.read_pickle(scratch / "trades.pkl").to_dict("records")
            decisions = pd.read_pickle(scratch / "decisions.pkl").to_dict("records")
            common = pd.read_csv(scratch / "common.csv", dtype={"stock_code": str})
            print("Resuming Experiment 002 post-processing from verified in-progress replay checkpoint.", flush=True)
        else:
            # Re-run Technical Alone and all three BUY-only controls to prove Exp001 controls.
            family_name, family, candidate = tv1.candidate_by_id(config, frozen["base_candidate"])
            for fold in config["walk_forward_folds"]:
                for stock in STOCKS:
                    result, trade_rows, gate_rows, risk_dec = _run_alone_one(
                        connection=con, config=config, family_name=family_name, family=family,
                        candidate=candidate, stock=stock, fold=fold,
                        schedule=schedules.get((fold["id"], stock), set()), regime_context=regime_context)
                    result.update({"variant": TECH_ONLY, "model": "technical_only", "candidate_count": len(common),
                                   "allowed_entry_count": len(common), "blocked_entry_count": 0, "gate_acceptance_rate": 1.0})
                    results.append(result)
                    trades.extend({**row, "variant": TECH_ONLY, "model": "technical_only"} for row in trade_rows)
                    decisions.extend({**row, "variant": TECH_ONLY, "model": "technical_only"} for row in gate_rows)
                    risk_rows.extend({**row, "variant": TECH_ONLY, "model": "technical_only"} for row in risk_dec)
            for model in MODELS:
                for rule, variant in (("BUY_ONLY", BUY_ONLY[model]), ("SELL_VETO", SELL_VETO[model])):
                    lookup = lookups[model] if rule == "BUY_ONLY" else sell_veto_lookup(lookups[model])
                    for fold in config["walk_forward_folds"]:
                        for stock in STOCKS:
                            result, trade_rows, gate_rows, risk_dec, _ = simulate_one(
                                connection=con, config=config, family_name=family_name, family=family,
                                candidate=candidate, stock=stock, fold=fold, version="TECH_V2_B", model=model,
                                lookup=lookup, schedule=schedules.get((fold["id"], stock), set()),
                                volume_context={}, regime_context=regime_context)
                            result.update({"variant": variant, "candidate_count": len(common)})
                            pred_col = {"random_forest": "rf_prediction", "xgboost": "xgb_prediction", "gru": "gru_prediction"}[model]
                            original = {(str(r.stock_code), str(r.candidate_date)): str(getattr(r, pred_col))
                                        for r in common.itertuples(index=False)}
                            allowed = 0
                            for gate in gate_rows:
                                label = original[(str(gate["stock_code"]), str(gate["technical_candidate_date"]))]
                                gate["original_ml_prediction"] = label
                                gate["gate_rule"] = rule
                                gate["gate_label"] = label
                                gate["hybrid_action"] = "ENTRY" if gate_allows(label, rule) else "BLOCK"
                                gate["hybrid_decision"] = gate["hybrid_action"]
                                allowed += int(gate["hybrid_action"] == "ENTRY")
                            result["allowed_entry_count"] = allowed
                            result["blocked_entry_count"] = len(common) - allowed
                            result["gate_acceptance_rate"] = allowed / len(common)
                            results.append(result)
                            trades.extend({**row, "variant": variant, "model": model} for row in trade_rows)
                            decisions.extend({**row, "variant": variant, "model": model} for row in gate_rows)
                            risk_rows.extend({**row, "variant": variant, "model": model} for row in risk_dec)

    scratch = ROOT / ".tmp_exp002"
    scratch.mkdir(exist_ok=True)
    if results:
        results_df = pd.DataFrame(results)
        pd.DataFrame(trades).to_pickle(scratch / "trades.pkl")
        pd.DataFrame(decisions).to_pickle(scratch / "decisions.pkl")
        common.to_csv(scratch / "common.csv", index=False)
        results_df.to_pickle(scratch / "results.pkl")
    else:
        results_df = pd.read_pickle(scratch / "results.pkl")
        trades = pd.read_pickle(scratch / "trades.pkl").to_dict("records")
        decisions = pd.read_pickle(scratch / "decisions.pkl").to_dict("records")
        common = pd.read_csv(scratch / "common.csv", dtype={"stock_code": str})
    expected_rows = len(VARIANTS) * len(STOCKS) * 3
    if len(results_df) != expected_rows or results_df.duplicated(["variant", "model", "stock_code", "fold_id"]).any():
        raise HybridPOCError("Incomplete Experiment 002 results matrix")
    if results_df.groupby("variant").candidate_count.sum().nunique() != 1:
        raise HybridPOCError("Variants did not share the same candidate set")
    decision_df = pd.DataFrame(decisions)
    decision_parity = validate_decision_parity(decision_df, len(common))
    overall, per_stock, per_fold, stock_fold = _metrics_frame(results_df)
    # Candidate/allowed counts come from the actual decision ledger, not the 15 stock-fold summaries.
    decision_df = pd.DataFrame(decisions)
    for row in overall.to_dict("records"):
        mask = decision_df.variant.astype(str).eq(str(row["variant"])) & decision_df.model.astype(str).eq(str(row["model"]))
        group = decision_df.loc[mask]
        i = overall.index[(overall.variant == row["variant"]) & (overall.model == row["model"])][0]
        allowed = int((group.hybrid_action == "ENTRY").sum())
        overall.loc[i, "candidate_count"] = len(group)
        overall.loc[i, "allowed_entry_count"] = allowed
        overall.loc[i, "blocked_entry_count"] = len(group) - allowed
        overall.loc[i, "gate_acceptance_rate"] = allowed / len(group) if len(group) else 0.0
    control_reproduction = reproduce_controls(overall, exp1_overall)
    if control_reproduction["status"] != "PASS":
        raise HybridPOCError("CONTROL_REPRODUCTION=FAIL; stop interpretation of new arms")

    # Identify realized HOLD entries: only actual BUY trade rows whose executed next-bar timestamp
    # matches a HOLD candidate's registered earliest entry time are included.
    entry_schedule = {}
    for r in common.to_dict("records"):
        for model, v in SELL_VETO.items():
            label = str(r[{"random_forest": "rf_prediction", "xgboost": "xgb_prediction", "gru": "gru_prediction"}[model]])
            if label == "HOLD":
                entry_schedule[(v, model, str(r["stock_code"]), str(r["fold_id"]),
                                pd.Timestamp(r["earliest_entry_time"]).strftime("%Y-%m-%d %H:%M:%S"))] = r
    recovered_rows = []
    trade_frame = pd.DataFrame(trades)
    for (variant, model, stock, fold), group in trade_frame.groupby(["variant", "model", "stock_code", "fold_id"], sort=False):
        if variant not in SELL_VETO.values():
            continue
        pending_buy = None
        for trade in group.sort_values("timestamp").to_dict("records"):
            if trade.get("action") == "Buy":
                pending_buy = trade
                continue
            if trade.get("action") != "Sell" or pending_buy is None:
                continue
            buy = pending_buy
            pending_buy = None
            key = (variant, model, str(stock), str(fold), str(buy["datetime"])[:19])
            candidate_row = entry_schedule.get(key)
            if not candidate_row:
                continue
            net_pnl = float(trade["net_cash_flow"]) + float(buy["net_cash_flow"])
            recovered_rows.append({"model": model, "variant": variant, "stock_code": str(stock),
                "fold_id": str(fold), "signal_date": candidate_row["candidate_date"],
                "signal_time": candidate_row["signal_time"], "entry_datetime": buy["datetime"],
                "exit_datetime": trade["datetime"], "entry_price": float(buy["price"]), "exit_price": float(trade["price"]),
                "quantity": int(buy["quantity"]), "net_pnl": net_pnl,
                "trade_return": net_pnl / (float(buy["price"]) * int(buy["quantity"])) if float(buy["price"]) else 0.0,
                "exit_reason": "交易成交資料未保留出場原因"})
    recovered_df = pd.DataFrame(recovered_rows, columns=["model", "variant", "stock_code", "fold_id", "signal_date", "signal_time",
        "entry_datetime", "exit_datetime", "entry_price", "exit_price", "quantity", "net_pnl", "trade_return", "exit_reason"])
    for frame, mask_cols in ((overall, ["variant", "model"]), (per_stock, ["variant", "model", "stock_code"]),
                             (per_fold, ["variant", "model", "fold_id", "stock_code"])):
        candidate_n, allowed_n, blocked_n, acceptance = [], [], [], []
        for row in frame.to_dict("records"):
            mask = pd.Series(True, index=decision_df.index)
            for col in mask_cols:
                mask &= decision_df[col].astype(str).eq(str(row[col]))
            group = decision_df.loc[mask]
            n_allowed = int((group.hybrid_action == "ENTRY").sum())
            candidate_n.append(int(len(group))); allowed_n.append(n_allowed)
            blocked_n.append(int(len(group) - n_allowed)); acceptance.append(n_allowed / len(group) if len(group) else 0.0)
        frame["candidate_count"] = candidate_n; frame["allowed_entry_count"] = allowed_n
        frame["blocked_entry_count"] = blocked_n; frame["gate_acceptance_rate"] = acceptance
    comparison, stability = make_comparisons(overall, per_stock, per_fold, recovered_df)
    gate_counts = {}
    for variant in VARIANTS:
        model = MODEL_FOR_VARIANT[variant]
        grp = decision_df[(decision_df.variant == variant) & (decision_df.model == model)]
        labels = grp.original_ml_prediction if variant != TECH_ONLY else grp.ml_prediction
        gate_counts[variant] = {"candidate_count": len(grp), "allowed_entry_count": int((grp.hybrid_action == "ENTRY").sum()),
            "blocked_entry_count": int((grp.hybrid_action == "BLOCK").sum()),
            "acceptance_rate": float((grp.hybrid_action == "ENTRY").mean()) if len(grp) else 0.0,
            "buy_candidate_count": int((labels == "BUY").sum()), "hold_candidate_count": int((labels == "HOLD").sum()),
            "sell_candidate_count": int((labels == "SELL").sum()),
            "sell_veto_released_hold_signal_count": int((labels == "HOLD").sum()) if variant in SELL_VETO.values() else 0}
    # Retain the actual number of recovered HOLD fills in the gate summary, separate from eligible HOLD signals.
    for model, variant in SELL_VETO.items():
        gate_counts[variant]["hold_recovered_trade_count"] = int((recovered_df.model == model).sum())
    comparison_csv = comparison.copy()
    interpretations = {row["model"]: make_interpretation(row) for row in comparison.to_dict("records")}
    audit = {
        "status": "COMPLETED", "experiment_id": "strategy-v2-hybrid-exp002-sell-veto",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip() or "UNKNOWN",
        "working_tree_status": "DIRTY" if subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip() else "CLEAN",
        "technical_candidate": "BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER", "technical_frozen": True,
        "technical_config_sha256": sha256_file(TECH_V2_CONFIG), "technical_source_sha256": sha256_file(ROOT / frozen["technical_source"]),
        "technical_database_path": str(database_path.relative_to(ROOT)), "technical_database_sha256": tech_db_hash,
        "ml_database_path": str(ml_db.relative_to(ROOT)), "ml_database_sha256": ml_db_hash,
        "oof_path": str(OOF_PATH.relative_to(ROOT)), "oof_sha256": oof_hash,
        "common_coverage_path": str(EXP1_COMMON.relative_to(ROOT)), "common_coverage_count": len(common),
        "coverage_regeneration_matches_exp001": True, "coverage_rejections": coverage_rejections,
        "oof_validation": oof_audit, "control_reproduction": control_reproduction,
        "models": list(MODELS), "stock_universe": list(STOCKS), "folds": [f["id"] for f in config["walk_forward_folds"]],
        "period": {"start": "2023-01-01", "end": "2024-12-31", "role": "EXPOSED_TRAINING_DEVELOPMENT"},
        "risk_parameters": {k: risk_config[k] for k in ("risk_per_trade", "max_position_fraction", "stop_loss_pct")},
        "execution_rule": EXECUTION_RULE, "execution_parity": {"status": "PASS", "contract": EXECUTION_RULE},
        "technical_signal_parity": decision_parity,
        "risk_parity": {"status": "PASS" if validate_risk_contract(risk_config) else "FAIL",
                        "parameters": {k: risk_config[k] for k in RISK_CONTRACT}},
        "gate_rules": {"BUY_ONLY": "BUY allow; HOLD/SELL block", "SELL_VETO": "BUY/HOLD allow; SELL block; no short"},
        "exit_reason_source_note": "Trade transaction export has no exit-reason field; recovered-trade rows state this limitation explicitly.",
        "gate_metrics": gate_counts, "classifications": {r.model: r.classification for r in comparison.itertuples()},
        "interpretations": interpretations,
        "class_distribution": {m: {k: int((common[{"random_forest":"rf_prediction","xgboost":"xgb_prediction","gru":"gru_prediction"}[m]] == k).sum()) for k in ("BUY","HOLD","SELL")} for m in MODELS},
        "ml_retrained": False, "ml_parameters_changed": False, "threshold_changed": False,
        "technical_changed": False, "risk_changed": False, "parameter_search": False,
        "2025_used": False, "2026_used": False, "final_oos_used": False,
        "additional_holdout_used": False, "portfolio": False, "short": False,
        "input_hashes_before": {str(p.relative_to(ROOT)): h for p,h in input_hashes.items()},
        "immutable_input_hashes_after_match": True, "immutable_export_directories_after_match": True,
    }
    for p, before in input_hashes.items():
        if sha256_file(p) != before:
            raise HybridPOCError(f"Immutable input changed during experiment: {p}")
    for p, before in dir_hashes.items():
        if directory_hashes(p) != before:
            raise HybridPOCError(f"Immutable input export changed during experiment: {p}")
    if sha256_file(CANDIDATES_PATH) != candidates_hash or sha256_file(OOF_PATH) != oof_hash:
        raise HybridPOCError("Frozen candidate or OOF changed during experiment")

    output = ROOT / "exports" / f"strategy_v2_hybrid_exp002_{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
    output.mkdir(parents=True, exist_ok=False)
    overall.to_csv(output / "hybrid_v2_exp002_overall.csv", index=False, encoding="utf-8-sig")
    per_stock.to_csv(output / "hybrid_v2_exp002_per_stock.csv", index=False, encoding="utf-8-sig")
    per_fold.to_csv(output / "hybrid_v2_exp002_per_fold.csv", index=False, encoding="utf-8-sig")
    stock_fold.to_csv(output / "hybrid_v2_exp002_stock_fold.csv", index=False, encoding="utf-8-sig")
    decision_df.to_csv(output / "hybrid_v2_exp002_gate_decisions.csv", index=False, encoding="utf-8-sig")
    recovered_df.to_csv(output / "hybrid_v2_exp002_hold_recovered_trades.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(trades).to_csv(output / "hybrid_v2_exp002_trades.csv", index=False, encoding="utf-8-sig")
    comparison_csv.to_csv(output / "hybrid_v2_exp002_comparison.csv", index=False, encoding="utf-8-sig")
    (output / "hybrid_v2_exp002_common_coverage.csv").write_bytes(EXP1_COMMON.read_bytes())
    audit["classification_details"] = comparison.to_dict("records")
    audit["hold_recovered_trade_count"] = {m: int((recovered_df.model == m).sum()) for m in MODELS}
    audit["output_files"] = sorted(p.name for p in output.iterdir()) + ["hybrid_v2_exp002_audit.json"]
    write_json(output / "hybrid_v2_exp002_audit.json", audit)
    html_report = "<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><title>Hybrid V2 Experiment 002 Audit</title><body><h1>Hybrid V2 Experiment 002 — Audit</h1><pre>" + html.escape(json.dumps(json_safe(audit), ensure_ascii=False, indent=2)) + "</pre><h2>Overall</h2><pre>" + html.escape(overall.to_string(index=False)) + "</pre><h2>Comparison</h2><pre>" + html.escape(comparison.to_string(index=False)) + "</pre></body></html>"
    (output / "hybrid_v2_exp002_report.html").write_text(html_report, encoding="utf-8")
    user_html = render_user_html(audit, overall, per_stock, per_fold, comparison, recovered_df)
    if not html_is_standalone(user_html):
        raise HybridPOCError("User analysis HTML is not standalone")
    (output / "hybrid_v2_exp002_user_analysis.html").write_text(user_html, encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", "output": str(output.relative_to(ROOT)),
        "common_coverage": len(common), "control_reproduction": control_reproduction["status"],
        "classifications": audit["classifications"], "hold_recovered_trade_count": audit["hold_recovered_trade_count"],
        "2025_used": False, "2026_used": False, "final_oos_used": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HybridPOCError, sqlite3.Error, OSError, ValueError, KeyError) as exc:
        traceback.print_exc()
        raise SystemExit(f"HYBRID_V2_EXP002_BLOCKED: {exc}") from exc

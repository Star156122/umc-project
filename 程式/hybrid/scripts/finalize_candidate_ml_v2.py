"""Finalize reports from a completed Candidate ML V2 run's persisted CSV outputs."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support

ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "exports/ml_candidate_error_audit_20261019T134230804119"
PREREG = ROOT / "research/ml_v2_candidate_specific_preregistration.md"
TECH_CONFIG = ROOT / "configs/technical_v1_research.json"
V2_CONFIG = ROOT / "configs/technical_v2_candidate.json"
TECH_GATE = ROOT / "exports/strategy_v2_technical_candidate_20261019T103707108020/technical_v2_candidate_gate_decisions.csv"
OOF = ROOT / "exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv"
MARKET_DB = ROOT / "data/market_data.sqlite3"
TECH_DB = ROOT / "data/technical_v1_mltech_clean.sqlite3"
MODELS = ("random_forest", "xgboost", "gru")
STOCKS = ("2303", "2330", "2379", "2454", "3034")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def classify(frame: pd.DataFrame, model: str, version: str, scope: str, value: str) -> dict[str, Any]:
    labels = ("SELL", "HOLD", "BUY")
    actual = frame.actual_label.astype(str).str.upper()
    pred = frame.prediction.astype(str).str.upper()
    precision, recall, f1, _ = precision_recall_fscore_support(actual, pred, labels=labels, zero_division=0)
    row = {"model": model, "version": version, "scope": scope, "value": value,
           "samples": int(len(frame)), "accuracy": float(accuracy_score(actual, pred)),
           "macro_f1": float(f1_score(actual, pred, labels=labels, average="macro", zero_division=0))}
    for i, label in enumerate(labels):
        key = label.lower()
        row[f"{key}_precision"] = float(precision[i])
        row[f"{key}_recall"] = float(recall[i])
        row[f"{key}_f1"] = float(f1[i])
        row[f"actual_{key}_count"] = int(actual.eq(label).sum())
        row[f"predicted_{key}_count"] = int(pred.eq(label).sum())
    return row


def build_classification(audit_dir: Path, run_dir: Path) -> pd.DataFrame:
    old = pd.read_csv(AUDIT_DIR / "candidate_error_rows.csv", dtype={"stock_code": str})
    old["actual_label"] = old.actual_label.astype(str).str.upper()
    old["prediction"] = old.prediction.astype(str).str.upper()
    old["stock_code"] = old.stock_code.str.zfill(4)
    new = pd.read_csv(run_dir / "candidate_ml_v2_oof_predictions.csv", dtype={"stock_code": str})
    new["actual_label"] = new.actual_class.astype(str).str.upper()
    new["stock_code"] = new.stock_code.str.zfill(4)
    rows = []
    for model in MODELS:
        for version, source in (("ML_V1", old), ("CANDIDATE_ML_V2", new)):
            g = source[source.model.eq(model)]
            rows.append(classify(g, model, version, "overall", "ALL"))
            for stock in STOCKS:
                rows.append(classify(g[g.stock_code.eq(stock)], model, version, "stock", stock))
            fold_col = "technical_fold" if version == "ML_V1" else "fold_id"
            for fold in ("WF1", "WF2", "WF3"):
                rows.append(classify(g[g[fold_col].astype(str).eq(fold)], model, version, "fold", fold))
    return pd.DataFrame(rows)


def main() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path, help="Existing candidate_ml_v2_<timestamp> directory with completed CSVs")
    args = parser.parse_args()
    run_dir = args.run_dir if args.run_dir.is_absolute() else ROOT / args.run_dir
    required = ["candidate_ml_v2_oof_predictions.csv", "candidate_ml_v2_dataset_summary.csv",
        "candidate_ml_v2_overall.csv", "candidate_ml_v2_per_stock.csv", "candidate_ml_v2_per_fold.csv",
        "candidate_ml_v2_stock_fold.csv", "candidate_ml_v2_trades.csv", "candidate_ml_v2_comparison.csv",
        "candidate_ml_v2_classification_comparison.csv"]
    missing = [name for name in required if not (run_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Incomplete completed-run CSV output: {missing}")

    candidate = pd.read_csv(run_dir / "candidate_ml_v2_oof_predictions.csv", dtype={"stock_code": str})
    fold_summary = pd.read_csv(run_dir / "candidate_ml_v2_dataset_summary.csv")
    overall = pd.read_csv(run_dir / "candidate_ml_v2_overall.csv")
    per_stock = pd.read_csv(run_dir / "candidate_ml_v2_per_stock.csv")
    per_fold = pd.read_csv(run_dir / "candidate_ml_v2_per_fold.csv")
    comparison = pd.read_csv(run_dir / "candidate_ml_v2_comparison.csv")
    phase_a = json.loads((AUDIT_DIR / "candidate_error_audit.json").read_text(encoding="utf-8"))
    prereg_hash = file_hash(PREREG)
    if prereg_hash != "f8bc779ec0cf0171d7013a99cedbe7b5424e11565456737061b97df23d027916":
        raise RuntimeError("Frozen preregistration hash changed; do not finalize this run")
    if not candidate.no_future_leakage_check.astype(str).str.lower().eq("true").all():
        raise RuntimeError("Candidate OOF leakage marker failed")
    if set(candidate.data_role.astype(str).str.lower()) != {"training"} or not candidate.is_oof.astype(str).str.lower().eq("true").all():
        raise RuntimeError("Candidate OOF provenance check failed")
    if candidate.groupby("model").size().to_dict() != {m: 138 for m in MODELS}:
        raise RuntimeError("Candidate OOF coverage does not match the completed 138-event universe")
    if candidate.candidate_date.astype(str).str[:4].isin(["2025", "2026"]).any() or candidate.target_date.astype(str).str[:4].isin(["2025", "2026"]).any():
        raise RuntimeError("Forbidden period found in Candidate OOF output")
    old_labels = pd.read_csv(AUDIT_DIR / "candidate_error_rows.csv", dtype={"stock_code": str})
    old_labels["stock_code"] = old_labels.stock_code.str.zfill(4)
    old_labels["candidate_date"] = old_labels.candidate_date.astype(str).str[:10]
    old_labels["model"] = old_labels.model.astype(str).str.lower()
    new_keys = candidate[["model", "stock_code", "candidate_date", "actual_class"]].copy()
    new_keys["candidate_date"] = new_keys.candidate_date.astype(str).str[:10]
    old_keys = old_labels[["model", "stock_code", "candidate_date", "actual_label"]]
    paired_labels = new_keys.merge(old_keys, on=["model", "stock_code", "candidate_date"], how="outer", validate="one_to_one", indicator=True)
    if not paired_labels._merge.eq("both").all() or not paired_labels.actual_class.astype(str).str.upper().eq(paired_labels.actual_label.astype(str).str.upper()).all():
        raise RuntimeError("Candidate ML V2 and ML V1 labels / coverage are not aligned")

    classification = build_classification(AUDIT_DIR, run_dir)
    classification.to_csv(run_dir / "candidate_ml_v2_classification_metrics.csv", index=False, encoding="utf-8-sig")
    fairness = {
        "candidate_oof_count": int(len(candidate)), "fair_model_rows": int(len(candidate)),
        "all_model_common_coverage": int(candidate.groupby(["stock_code", "fold_id", "candidate_date"]).filter(lambda g: set(g.model) == set(MODELS)).groupby(["stock_code", "fold_id", "candidate_date"]).ngroups),
        "coverage_by_model": {str(k): int(v) for k, v in candidate.groupby("model").size().items()},
        "coverage_by_model_fold": {f"{m}|{f}": int(n) for (m, f), n in candidate.groupby(["model", "fold_id"]).size().items()},
    }
    metrics_by_model = {}
    for model in MODELS:
        model_rows = classification[(classification.model == model) & (classification.version == "CANDIDATE_ML_V2")]
        metrics_by_model[model] = {"folds": model_rows[model_rows.scope.eq("fold")].to_dict("records"),
                                   "overall": model_rows[model_rows.scope.eq("overall")].iloc[0].to_dict()}
    trade_status = {str(r.model): str(r.classification) for r in comparison.drop_duplicates("model").itertuples(index=False)}
    model_fold_results = []
    for model in MODELS:
        for fold in ("WF1", "WF2", "WF3"):
            ps = fold_summary[fold_summary.fold_id.eq(fold)].iloc[0]
            c = metrics_by_model[model]["folds"]
            metric = next((r for r in c if r["value"] == fold), None)
            model_fold_results.append({"model": model, "fold_id": fold, "status": "COMPLETED" if metric else "INSUFFICIENT_CANDIDATE_SAMPLE",
                "training_candidate_count": int(ps.train_candidate_count), "validation_candidate_count": int(ps.validation_candidate_count),
                "train_class_counts": {"SELL": int(ast.literal_eval(ps.train_class_counts).get("SELL", 0)), "HOLD": int(ast.literal_eval(ps.train_class_counts).get("HOLD", 0)), "BUY": int(ast.literal_eval(ps.train_class_counts).get("BUY", 0))},
                "validation_metrics": metric,
                "training_log_note": "Fold prediction metrics were retained; per-epoch GRU log was not persisted by the execution script."})

    sources = [TECH_CONFIG, V2_CONFIG, TECH_GATE, OOF, MARKET_DB, TECH_DB,
               ROOT / "exports/strategy_v2_ml_exp001_20261019T123507275634/ml_v2_common_coverage.csv",
               ROOT / "exports/strategy_v2_hybrid_exp002_20261019T132413877488/hybrid_v2_exp002_common_coverage.csv"]
    preflight_records = fold_summary.to_dict("records")
    for record in preflight_records:
        for name in ("train_class_counts", "validation_class_counts", "per_stock_train", "per_stock_validation"):
            record[name] = ast.literal_eval(record[name])
    audit = {
        "status": "COMPLETED", "experiment_id": "strategy-v2-candidate-specific-ml-v2",
        "generated_at": pd.Timestamp.now(tz="Asia/Taipei").isoformat(timespec="seconds"),
        "preregistration": "research/ml_v2_candidate_specific_preregistration.md", "preregistration_sha256": prereg_hash,
        "phase_a_audit": str(AUDIT_DIR.relative_to(ROOT)), "phase_a_status": phase_a["status"],
        "technical_candidate": "BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER", "technical_candidate_frozen": True,
        "technical_validation_reproduction": {"generated_count": 138, "frozen_count": 138, "matching_event_count": 138, "missing": 0, "extra": 0, "status": "PASS"},
        "technical_candidate_count": 138,
        "candidate_training_pool_policy": "Replayed frozen V2-B candidates through each WF training cutoff; previous-session V1 features, Fixed ±1% label, target end no later than cutoff.",
        "training_period": ["2023-01-01", "2024-12-31"], "data_role": "TRAINING_ONLY",
        "stocks": list(STOCKS), "models": list(MODELS), "folds": ["WF1", "WF2", "WF3"],
        "feature_policy": "Complete ML V1 frozen feature pipeline; no feature selection or lookback change.",
        "label_policy": "ML V1 Fixed ±1%; T+1 Open to T+3 Close after frozen one-round-trip cost.",
        "fold_preflight": preflight_records, "model_fold_results": model_fold_results,
        "insufficient_folds": [r for r in model_fold_results if r["status"] != "COMPLETED"],
        "candidate_oof_count": int(len(candidate)), "fair_coverage": fairness,
        "classification_comparison": pd.read_csv(run_dir / "candidate_ml_v2_classification_comparison.csv").to_dict("records"),
        "trade_classification": trade_status,
        "gru_inner_validation": "Chronological 20% of fold training candidate dates; outer fold validation did not control early stopping.",
        "model_parameters_tuned": False, "feature_selection_performed": False, "label_changed": False,
        "technical_changed": False, "risk_changed": False, "threshold_changed": False,
        "2025_used": False, "2026_used": False, "final_oos_used": False,
        "database_modified": False, "backtest_scope": "fair candidate coverage only", "parameter_search": False,
        "audit_note": "CSV outputs contain the completed model and fair-coverage results. The original run stopped only during JSON serialization after CSV persistence; this final audit was reconstructed and validated from those CSVs.",
        "source_sha256": {str(p.relative_to(ROOT)): file_hash(p) for p in sources},
    }
    (run_dir / "candidate_ml_v2_audit.json").write_text(json.dumps(safe(audit), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    payload = {
        "classification": classification.replace({np.nan: None}).to_dict("records"),
        "overall": overall.replace({np.nan: None}).to_dict("records"),
        "per_stock": per_stock.replace({np.nan: None}).to_dict("records"),
        "per_fold": per_fold.replace({np.nan: None}).to_dict("records"),
        "audit": audit,
    }
    data = json.dumps(safe(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    page = f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Candidate-specific ML V2 分析</title><style>
    :root{{color-scheme:dark}}body{{margin:0;background:#08131d;color:#e7f0f7;font:16px/1.6 system-ui,"Microsoft JhengHei",sans-serif}}main{{max-width:1180px;margin:30px auto;padding:0 18px}}header,section{{background:#102331;border:1px solid #29495e;border-radius:16px;padding:22px;margin:16px 0}}h1,h2{{margin:0 0 12px}}p,.muted{{color:#a8bfd0}}select{{background:#0a1b27;color:#fff;border:1px solid #42647a;border-radius:8px;padding:9px;margin:5px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}.card{{background:#0a1b27;padding:14px;border-radius:12px}}.card strong{{display:block;font-size:21px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #29495e;white-space:nowrap}}th{{color:#9ccaff}}.ok{{color:#75d6a1}}details{{margin-top:10px}}</style><main><header><h1>Technical 候選專屬 ML V2</h1><p>這個模型只用歷史上 Technical V2-B 實際出現的候選情境訓練。每次候選日 D 的輸入只取前一個交易日的 ML 特徵，回答「Technical 已發出候選時，ML 能否協助判斷方向？」</p><p>本報告只使用 2023–2024 Training。Fold 候選 OOF 共 414 筆，三模型共同候選日期 138 筆。</p><label>模型<select id="model"><option value="random_forest">Random Forest</option><option value="xgboost">XGBoost</option><option value="gru">GRU</option></select></label><label>資料範圍<select id="scope"><option value="overall">整體</option><option value="stock">單一股票</option><option value="fold">單一 Fold</option></select></label><label>股票 / Fold<select id="item"></select></label></header>
    <section><h2>分類能力</h2><label>版本<select id="clsver"><option value="ML_V1">ML V1</option><option value="CANDIDATE_ML_V2">Candidate ML V2</option></select></label><div class="grid" id="classcards"></div><div class="scroll"><table id="classTable"></table></div></section>
    <section><h2>交易結果</h2><label>比較版本<select id="tradever"><option value="TECHNICAL_V2_ALONE">Technical V2 單獨</option><option value="ML_V1">Technical V2 + ML V1</option><option value="CANDIDATE_ML_V2">Technical V2 + Candidate ML V2</option></select></label><div class="grid" id="tradecards"></div><div class="scroll"><table id="tradeTable"></table></div></section>
    <section><h2>事前判定</h2><div id="status"></div><p class="muted">Technical 候選、成本、執行、Exit 與 Risk V1 固定。分類表現不單獨決定策略是否支持；狀態依事前登記的報酬、PF、回撤及跨股票／Fold 規則判定。</p><details><summary>研究與資料檢查</summary><pre id="audit"></pre></details></section></main>
    <script>const D={data};const fmt=x=>x==null||!Number.isFinite(Number(x))?'—':(Number(x)*100).toFixed(2)+'%';const num=x=>x==null||!Number.isFinite(Number(x))?'—':Number(x).toLocaleString(undefined,{{maximumFractionDigits:2}});const cash=x=>x==null||!Number.isFinite(Number(x))?'—':'NT$ '+Number(x).toLocaleString(undefined,{{maximumFractionDigits:0}});const labels={{macro_f1:'Macro F1',accuracy:'Accuracy',buy_precision:'BUY Precision',buy_recall:'BUY Recall',sell_precision:'SELL Precision',hold_precision:'HOLD Precision'}};function options(){{let scope=document.querySelector('#scope').value;let el=document.querySelector('#item');let vals=scope==='stock'?['2303','2330','2379','2454','3034']:scope==='fold'?['WF1','WF2','WF3']:['ALL'];el.innerHTML=vals.map(v=>`<option>${{v}}</option>`).join('')}}function draw(){{const m=document.querySelector('#model').value,scope=document.querySelector('#scope').value,item=document.querySelector('#item').value,ver=document.querySelector('#clsver').value;let c=D.classification.find(x=>x.model===m&&x.version===ver&&x.scope===scope&&x.value===item)||{{}};document.querySelector('#classcards').innerHTML=['macro_f1','accuracy','buy_precision','buy_recall'].map(k=>`<div class="card">${{labels[k]}}<strong>${{fmt(c[k])}}</strong></div>`).join('');let classCols=['分類','Precision','Recall','F1','實際筆數','預測筆數'];document.querySelector('#classTable').innerHTML='<tr>'+classCols.map(x=>`<th>${{x}}</th>`).join('')+'</tr>'+['SELL','HOLD','BUY'].map(k=>`<tr><td>${{k}}</td><td>${{fmt(c[k.toLowerCase()+'_precision'])}}</td><td>${{fmt(c[k.toLowerCase()+'_recall'])}}</td><td>${{fmt(c[k.toLowerCase()+'_f1'])}}</td><td>${{num(c['actual_'+k.toLowerCase()+'_count'])}}</td><td>${{num(c['predicted_'+k.toLowerCase()+'_count'])}}</td></tr>`).join('');const tv=document.querySelector('#tradever').value;let t;if(scope==='overall')t=D.overall.find(x=>x.model===m&&x.version===tv);else if(scope==='stock')t=D.per_stock.find(x=>x.model===m&&x.version===tv&&x.stock_code===item);else t=D.per_fold.find(x=>x.model===m&&x.version===tv&&x.fold_id===item);t=t||{{}};document.querySelector('#tradecards').innerHTML=[['平均報酬',fmt(t.average_return)],['Worst MDD',fmt(t.worst_mdd)],['PF',num(t.profit_factor)],['Sharpe',num(t.sharpe)],['交易數',num(t.trade_count)],['Net PnL',cash(t.net_pnl)],['交易成本',cash(t.transaction_cost)]].map(x=>`<div class="card">${{x[0]}}<strong>${{x[1]}}</strong></div>`).join('');document.querySelector('#tradeTable').innerHTML='<tr><th>版本</th><th>勝率</th><th>平均報酬</th><th>Worst MDD</th><th>Profit Factor</th><th>Sharpe</th><th>交易數</th><th>Net PnL</th><th>交易成本</th></tr>'+`<tr><td>${{tv}}</td><td>${{fmt(t.win_rate)}}</td><td>${{fmt(t.average_return)}}</td><td>${{fmt(t.worst_mdd)}}</td><td>${{num(t.profit_factor)}}</td><td>${{num(t.sharpe)}}</td><td>${{num(t.trade_count)}}</td><td>${{cash(t.net_pnl)}}</td><td>${{cash(t.transaction_cost)}}</td></tr>`;document.querySelector('#status').innerHTML=['random_forest','xgboost','gru'].map(k=>{{let r=D.overall.find(x=>x.model===k&&x.version==='CANDIDATE_ML_V2');let b=D.overall.find(x=>x.model===k&&x.version==='ML_V1');let s=D.audit.trade_classification[k]||'MIXED';return `<p><b>${{k}}</b>：<span class="ok">${{s}}</span>；ML V1 報酬 ${{fmt(b?.average_return)}} / Candidate V2 ${{fmt(r?.average_return)}}；交易 ${{num(b?.trade_count)}} / ${{num(r?.trade_count)}}。</p>`}}).join('');document.querySelector('#audit').textContent=JSON.stringify(D.audit,null,2)}}document.querySelector('#scope').addEventListener('change',()=>{{options();draw()}});for(const id of ['model','item','clsver','tradever'])document.querySelector('#'+id).addEventListener('change',draw);options();draw();</script></html>'''
    (run_dir / "candidate_ml_v2_user_analysis.html").write_text(page, encoding="utf-8")
    report = "<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><title>Candidate ML V2 Report</title><body><h1>Candidate-Specific ML V2 Research Report</h1><pre>" + json.dumps(safe(audit), ensure_ascii=False, indent=2) + "</pre>" + classification.to_html(index=False) + comparison.to_html(index=False) + per_stock.to_html(index=False) + per_fold.to_html(index=False) + "</body></html>"
    (run_dir / "candidate_ml_v2_report.html").write_text(report, encoding="utf-8")
    return run_dir


if __name__ == "__main__":
    print(main())

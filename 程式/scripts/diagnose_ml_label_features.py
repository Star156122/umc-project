"""Training-only label and feature structure diagnosis for the fixed V3 design.

This script never reads Controlled Validation, Development, Holdout, or Final OOS.
It does not modify labels, features, model parameters, trading rules, or Candidate files.
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.models import fit_multiclass_model
from ml.trading_pipeline import load_trading_plan, prepare_trading_data
from ml.walk_forward import build_full_training_data, build_walk_forward_fold, load_walk_forward_protocol

PLAN_PATH = ROOT / "configs/ml_trading_v3_20261004.json"
PROTOCOL_PATH = ROOT / "configs/ml_training_protocol_v3.json"
DATABASE = ROOT / "data/market_data.sqlite3"
TRAINING_DIAGNOSIS = ROOT / "exports/ml_v3_candidate_20261005/training_diagnosis.json"
PARAM_COMPARE = ROOT / "exports/ml_training_param_compare_20261005/param_compare_results.json"
OUT = ROOT / "exports/ml_label_feature_diagnosis_20261005"
LABEL_NAMES = {0: "SELL", 1: "HOLD", 2: "BUY"}
THRESHOLDS = (0.005, 0.0075, 0.01, 0.0125, 0.015)
GROUPS = {"科技組": ["2303", "2330"], "金融組": ["2881", "2882"], "其他參考": ["2002", "2412"]}
FLEXIBLE_RF = {"n_estimators": 200, "max_depth": 10, "min_samples_leaf": 5,
               "class_weight": "balanced", "random_state": 42}
FEATURE_CATEGORIES = {
    "open_vs_prev_close": "Price / Return", "high_vs_prev_close": "Price / Return",
    "low_vs_prev_close": "Price / Return", "return_1": "Price / Return",
    "return_3": "Price / Return", "return_6": "Price / Return", "return_12": "Price / Return",
    "volume_ratio_5": "Volume", "volume_ratio_20": "Volume", "volume_zscore_20": "Volume",
    "volatility_12": "Volatility", "volatility_24": "Volatility",
    "trend_slope_12": "Trend", "trend_slope_24": "Trend",
    "close_vs_ma5": "MA", "close_vs_ma20": "MA", "price_zscore_20": "MA",
    "rsi14_scaled": "RSI", "macd_vs_close": "MACD", "macd_signal_vs_close": "MACD",
    "macd_hist_vs_close": "MACD", "range_vs_close": "Candle / Range",
    "body_return": "Candle / Range", "close_location": "Candle / Range",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def return_stats(values: pd.Series | np.ndarray) -> dict[str, Any]:
    a = np.asarray(values, dtype=float)
    q = np.quantile(a, [.05, .25, .5, .75, .95])
    return {"samples": int(len(a)), "mean": float(a.mean()), "median": float(np.median(a)),
            "standard_deviation": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
            "min": float(a.min()), "max": float(a.max()),
            "quantiles": {"5%": float(q[0]), "25%": float(q[1]), "50%": float(q[2]),
                          "75%": float(q[3]), "95%": float(q[4])}}


def label_distribution(values: pd.Series | np.ndarray, threshold: float = .01) -> dict[str, Any]:
    a = np.asarray(values, dtype=float)
    labels = np.where(a <= -threshold, 0, np.where(a >= threshold, 2, 1))
    result: dict[str, Any] = {"threshold": threshold, "samples": int(len(a)), "classes": {}}
    for label, name in LABEL_NAMES.items():
        count = int((labels == label).sum())
        result["classes"][name] = {"count": count, "proportion": float(count / len(labels))}
    p = {name: result["classes"][name]["proportion"] for name in LABEL_NAMES.values()}
    result["flags"] = (["HOLD > 70%"] if p["HOLD"] > .7 else []) + \
                      (["SELL < 10%"] if p["SELL"] < .1 else []) + \
                      (["BUY < 10%"] if p["BUY"] < .1 else [])
    result["severe_class_imbalance"] = bool(result["flags"])
    return result


def metric_delta(new: dict[str, Any], old: dict[str, Any]) -> dict[str, float]:
    keys = ("mean", "standard_deviation", "worst")
    return {key: float(new[key]) - float(old[key]) for key in keys}


def feature_diagnosis(raw, plan: dict[str, Any]) -> dict[str, Any]:
    full = build_full_training_data(raw)
    model, seconds, _, _ = fit_multiclass_model("random_forest", full, FLEXIBLE_RF)
    matrix = np.asarray(model.feature_importances_).reshape(plan["sequence_bars"], len(plan["features"]))
    importance = matrix.sum(axis=0)
    ranked = sorted(({"rank": 0, "feature": feature, "category": FEATURE_CATEGORIES[feature],
                      "importance": float(importance[i])} for i, feature in enumerate(plan["features"])),
                    key=lambda item: item["importance"], reverse=True)
    for rank, item in enumerate(ranked, 1): item["rank"] = rank
    categories: dict[str, float] = {}
    for item in ranked: categories[item["category"]] = categories.get(item["category"], 0.0) + item["importance"]
    categories = dict(sorted(categories.items(), key=lambda item: item[1], reverse=True))
    top_names = [item["feature"] for item in ranked[:10]]
    last_bar = raw.X["train"][:, -1, :]
    by_label: dict[str, Any] = {}
    limited: list[str] = []
    for feature in top_names:
        index = plan["features"].index(feature)
        values = last_bar[:, index]
        item: dict[str, Any] = {"feature": feature, "measurement": "last completed 5-minute bar before signal",
                                "classes": {}}
        means, pooled_stds = [], []
        for label, name in LABEL_NAMES.items():
            subset = values[raw.y["train"] == label]
            mean = float(np.mean(subset)); std = float(np.std(subset, ddof=1)) if len(subset) > 1 else 0.0
            item["classes"][name] = {"samples": int(len(subset)), "mean": mean, "standard_deviation": std}
            means.append(mean); pooled_stds.append(std)
        pooled = float(np.mean(pooled_stds))
        separation = float((max(means) - min(means)) / pooled) if pooled > 0 else None
        item["mean_range_over_average_class_std"] = separation
        item["limited_discrimination_flag"] = separation is None or separation < .25
        if item["limited_discrimination_flag"]: limited.append(feature)
        by_label[feature] = item
    return {"model": "random_forest flexible", "parameters": FLEXIBLE_RF,
            "fit_scope": "full Training 2023-01-01..2024-12-31 only", "fit_seconds": seconds,
            "sequence_aggregation": "importance summed across all 24 sequence positions per feature",
            "all_features": ranked, "top_10": ranked[:10], "bottom_10": ranked[-10:],
            "category_importance": categories, "top_10_feature_vs_label": by_label,
            "limited_discrimination_features": limited,
            "limited_discrimination_message": "目前 Feature 對此 Label 的辨識力可能有限" if limited else None}


def existing_model_comparison(training_diag: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    baseline = next(x for x in params["results"] if x["model"] == "random_forest" and x["config"] == "baseline")
    flexible = next(x for x in params["results"] if x["model"] == "random_forest" and x["config"] == "flexible")
    b, f = baseline["summary"], flexible["summary"]
    improvement = {"macro_f1": metric_delta(f["macro_f1"], b["macro_f1"]),
                   "macro_auc": metric_delta(f["macro_roc_auc_ovr"], b["macro_roc_auc_ovr"])}
    best = {}
    for name, item in params["best_per_model"].items():
        best[name] = {"config": item["config"], "params": item["params"],
                      "macro_f1": item["summary"]["macro_f1"],
                      "macro_auc": item["summary"]["macro_roc_auc_ovr"],
                      "individual_auc": item["summary"]["individual_roc_auc_ovr"]}
    return {"rf_baseline_to_flexible_delta": improvement, "best_training_only_per_model": best,
            "stock_and_group_diagnosis": {"stocks": training_diag.get("stocks", {}),
                                          "groups": training_diag.get("groups", {}),
                                          "flags": training_diag.get("diagnostic_flags", [])},
            "interpretation": [
                "Flexible RF 的平均 Macro F1 與平均 Macro AUC 只小幅增加，最差 Fold F1 改善，但最差 Fold AUC 下降。",
                "RF、XGBoost、GRU 的 Macro F1 都偏低，Macro AUC 僅略高於 0.5，並非單一模型參數造成。",
                "2412 的 HOLD 集中與股票間分布差異顯示，問題較像 Label 固定門檻、現有 Feature 辨識力及股票差異的共同結果。",
            ]}


def fmt_pct(value: float) -> str: return f"{value * 100:.2f}%"
def fmt_num(value: Any, digits: int = 4) -> str: return "—" if value is None else f"{float(value):.{digits}f}"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return "<table><thead><tr>" + "".join(f"<th>{html.escape(str(x))}</th>" for x in headers) + \
           "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{html.escape(str(x))}</td>" for x in row) + "</tr>" for row in rows) + "</tbody></table>"


def render(report: dict[str, Any]) -> str:
    stocks = report["label_diagnosis"]["by_stock"]
    return_rows, label_rows, sensitivity_rows, fold_rows = [], [], [], []
    for code, item in stocks.items():
        s, d = item["return_distribution"], item["label_distribution"]
        return_rows.append([code, s["samples"], fmt_pct(s["mean"]), fmt_pct(s["median"]), fmt_pct(s["standard_deviation"]), fmt_pct(s["min"]), fmt_pct(s["max"]), fmt_pct(s["quantiles"]["5%"]), fmt_pct(s["quantiles"]["95%"])])
        label_rows.append([code] + [f"{d['classes'][name]['count']} ({fmt_pct(d['classes'][name]['proportion'])})" for name in ("SELL", "HOLD", "BUY")] + [", ".join(d["flags"]) or "—"])
        for threshold, dist in item["threshold_sensitivity"].items():
            sensitivity_rows.append([code, threshold] + [fmt_pct(dist["classes"][name]["proportion"]) for name in ("SELL", "HOLD", "BUY")])
    for row in report["label_diagnosis"]["fold_stock_distribution"]:
        fold_rows.append([row["fold"], row["stock_code"]] + [fmt_pct(row["classes"][name]["proportion"]) for name in ("SELL", "HOLD", "BUY")] + [", ".join(row["flags"]) or "—"])
    group_rows = []
    for name, item in report["industry_comparison"].items():
        d = item["label_distribution"]
        group_rows.append([name, ", ".join(item["stocks"]), fmt_pct(item["return_distribution"]["standard_deviation"])] + [fmt_pct(d["classes"][x]["proportion"]) for x in ("SELL", "HOLD", "BUY")] + [", ".join(d["flags"]) or "—"])
    feature = report["feature_diagnosis"]
    importance_rows = [[x["rank"], x["feature"], x["category"], fmt_num(x["importance"], 6)] for x in feature["all_features"]]
    category_rows = [[k, fmt_num(v, 6), fmt_pct(v)] for k, v in feature["category_importance"].items()]
    feature_label_rows = []
    for name, item in feature["top_10_feature_vs_label"].items():
        feature_label_rows.append([name] + [f"{fmt_num(item['classes'][x]['mean'], 6)} ± {fmt_num(item['classes'][x]['standard_deviation'], 6)}" for x in ("SELL", "HOLD", "BUY")] + [fmt_num(item["mean_range_over_average_class_std"], 3), "是" if item["limited_discrimination_flag"] else "否"])
    models = report["existing_model_comparison"]["best_training_only_per_model"]
    model_rows = [[name, item["config"], fmt_num(item["macro_f1"]["mean"]), fmt_num(item["macro_f1"]["standard_deviation"]), fmt_num(item["macro_f1"]["worst"]), fmt_num(item["macro_auc"]["mean"]), fmt_num(item["macro_auc"]["worst"])] for name, item in models.items()]
    flags = report["diagnostic_findings"]
    return f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>ML Label 與 Feature 結構診斷</title><style>
body{{font-family:system-ui,'Noto Sans TC',sans-serif;max-width:1400px;margin:28px auto;padding:0 18px;color:#182536;background:#f6f8fb}}h1,h2{{color:#0b4f78}}section,.card{{background:white;border:1px solid #dbe4ec;border-radius:12px;padding:18px;margin:16px 0}}table{{border-collapse:collapse;width:100%;font-size:14px;margin:10px 0}}th,td{{border:1px solid #d7e0e8;padding:7px;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#eaf3f8}}.warn{{background:#fff2d7}}code{{background:#eef2f5;padding:2px 5px}}ul{{line-height:1.7}}</style></head><body>
<h1>ML V3 Training-only：Label 與 Feature 結構診斷</h1>
<section><h2>1. Executive Summary</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in report['executive_summary'])}</ul><p><strong>資料範圍：</strong>2023-01-01～2024-12-31 Training only；不是 V4，也不是正式 Validation。</p></section>
<section><h2>2. Label Distribution</h2>{table(['股票','SELL','HOLD','BUY','警示'],label_rows)}<h3>各 Fold Validation 區段</h3>{table(['Fold','股票','SELL','HOLD','BUY','警示'],fold_rows)}</section>
<section><h2>3. Return Distribution</h2>{table(['股票','樣本','Mean','Median','Std','Min','Max','5%','95%'],return_rows)}</section>
<section><h2>4. Threshold Sensitivity</h2><p>僅重新計算分布，沒有修改 Label。</p>{table(['股票','假設門檻','SELL','HOLD','BUY'],sensitivity_rows)}</section>
<section><h2>5. Industry Comparison</h2>{table(['群組','股票','Return Std','SELL','HOLD','BUY','警示'],group_rows)}</section>
<section><h2>6. Feature Importance</h2><p>Flexible RF；24 個時間位置的 importance 按 Feature 加總。</p>{table(['排名','Feature','類別','Importance'],importance_rows)}<h3>Feature 類別總 Importance</h3>{table(['類別','Importance','比例'],category_rows)}</section>
<section><h2>7. Feature vs Label</h2><p>採每筆樣本訊號當下最後一根 5 分 K 的原始值。分離度為三類平均值範圍 ÷ 平均類內標準差；低於 0.25 標為辨識力可能有限。</p>{table(['Feature','SELL mean ± std','HOLD mean ± std','BUY mean ± std','分離度','可能有限'],feature_label_rows)}</section>
<section><h2>8. Existing Model Comparison</h2>{table(['模型','設定','F1 mean','F1 std','F1 worst','AUC mean','AUC worst'],model_rows)}<ul>{''.join(f'<li>{html.escape(x)}</li>' for x in report['existing_model_comparison']['interpretation'])}</ul></section>
<section class='warn'><h2>9. Diagnostic Findings</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in flags)}</ul></section>
<section><h2>10. Possible Next Directions</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in report['possible_next_directions'])}</ul><p>以上只是假設與建議，本次沒有自動執行。</p></section>
<section><h2>執行限制確認</h2><pre>{html.escape(json.dumps(report['execution_confirmation'], ensure_ascii=False, indent=2))}</pre></section>
</body></html>"""


def main() -> int:
    plan = load_trading_plan(PLAN_PATH)
    protocol = load_walk_forward_protocol(PROTOCOL_PATH)
    training_diag, param_compare = read_json(TRAINING_DIAGNOSIS), read_json(PARAM_COMPARE)
    raw, _ = prepare_trading_data(DATABASE, plan, roles=("train",), apply_scaling=False,
                                  workflow_stage="label_feature_diagnosis_training_only")
    meta = raw.metadata["train"].reset_index(drop=True)
    by_stock: dict[str, Any] = {}
    for code in sorted(plan["stock_codes"]):
        values = meta.loc[meta["stock_code"] == code, "future_net_return"]
        by_stock[code] = {"return_distribution": return_stats(values),
                          "label_distribution": label_distribution(values),
                          "threshold_sensitivity": {f"±{t*100:.2f}%": label_distribution(values, t) for t in THRESHOLDS}}
    fold_stock = []
    for spec in protocol["walk_forward"]["folds"]:
        fold = build_walk_forward_fold(raw, spec)
        fm = fold.metadata["validation"]
        for code in sorted(plan["stock_codes"]):
            values = fm.loc[fm["stock_code"] == code, "future_net_return"]
            if len(values): fold_stock.append({"fold": int(spec["fold"]), "stock_code": code, **label_distribution(values)})
    industries = {}
    for name, codes in GROUPS.items():
        values = meta.loc[meta["stock_code"].isin(codes), "future_net_return"]
        industries[name] = {"stocks": codes, "return_distribution": return_stats(values),
                            "label_distribution": label_distribution(values),
                            "threshold_sensitivity": {f"±{t*100:.2f}%": label_distribution(values, t) for t in THRESHOLDS}}
    features = feature_diagnosis(raw, plan)
    existing = existing_model_comparison(training_diag, param_compare)
    flagged_stocks = [code for code, x in by_stock.items() if x["label_distribution"]["flags"]]
    flagged_folds = [f"Fold {x['fold']} / {x['stock_code']}: {', '.join(x['flags'])}" for x in fold_stock if x["flags"]]
    findings = [
        f"±1% 下出現警示的股票：{', '.join(flagged_stocks) if flagged_stocks else '無'}。",
        *flagged_folds,
        *existing["interpretation"],
    ]
    if features["limited_discrimination_features"]:
        findings.append("Top 10 中被標記分布高度重疊的 Features：" + ", ".join(features["limited_discrimination_features"]) + "；目前 Feature 對此 Label 的辨識力可能有限。")
    report = {
        "report_name": "ML V3 Training-only Label and Feature Structure Diagnosis",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": {"period": ["2023-01-01", "2024-12-31"], "roles_loaded": raw.audit["loaded_roles"],
                  "workflow_stage": raw.audit["workflow_stage"], "label_definition_unchanged": plan["target"]},
        "executive_summary": [
            "Flexible Random Forest 的調參改善很小，沒有理由繼續擴大參數搜尋。",
            "三模型整體 Macro F1 偏低、Macro AUC 僅略高於 0.5，問題不只來自模型參數。",
            "固定 ±1% 門檻對不同股票與產業產生不同類別比例，2412 特別容易集中於 HOLD。",
            "Feature Importance 有集中項目，但多個 Top Feature 在三類間仍高度重疊。",
        ],
        "label_diagnosis": {"definition": {"SELL": "future_net_return <= -1%", "HOLD": "-1% < future_net_return < +1%", "BUY": "future_net_return >= +1%", "holding_sessions": 3},
                             "by_stock": by_stock, "fold_stock_distribution": fold_stock},
        "industry_comparison": industries, "feature_diagnosis": features,
        "existing_model_comparison": existing, "diagnostic_findings": findings,
        "possible_next_directions": ["保留目前 Label 作為基準。", "考慮 volatility-adjusted Label，但需另立後續版本評估。",
                                     "考慮產業限定模型，先以現有數據確認跨股票穩定性。", "考慮 Feature 改良，但先保留本次 V3 結果作比較基準。"],
        "execution_confirmation": {"controlled_validation_used": False, "development_used": False,
            "forbidden_roles_used": False, "label_changed": False, "features_changed": False,
            "model_parameters_changed": False, "v4_created": False, "formal_validation_run": False,
            "candidate_overwritten": False, "return_used_for_model_selection": False},
        "sources": {"training_diagnosis": str(TRAINING_DIAGNOSIS), "parameter_comparison": str(PARAM_COMPARE),
                    "plan": str(PLAN_PATH), "protocol": str(PROTOCOL_PATH)},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    json_path, html_path = OUT / "label_feature_diagnosis.json", OUT / "label_feature_diagnosis.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    html_path.write_text(render(report), encoding="utf-8")
    print(json.dumps(report["execution_confirmation"], ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

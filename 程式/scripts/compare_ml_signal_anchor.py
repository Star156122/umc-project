"""Training-only controlled comparison of 13:30 versus 13:25 signal anchors."""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.data_pipeline import PreparedData
from ml.models import run_multiclass_model
from ml.trading_pipeline import (_daily_market, _intraday_features, _read_split,
                                 load_trading_plan, prepare_trading_data)
from ml.walk_forward import aggregate_fold_metrics, build_walk_forward_fold, load_walk_forward_protocol

PLAN_PATH = ROOT / "configs/ml_trading_v3_20261004.json"
PROTOCOL_PATH = ROOT / "configs/ml_training_protocol_v3.json"
DATABASE = ROOT / "data/market_data.sqlite3"
OUT = ROOT / "exports/ml_signal_anchor_compare_20261005"
TAIPEI = ZoneInfo("Asia/Taipei")
LABELS = ("SELL", "HOLD", "BUY")


def experimental_samples(featured_1325: pd.DataFrame, full_daily: pd.DataFrame,
                         plan: dict[str, Any]) -> PreparedData:
    """Build 13:25 inputs while retaining the original full-session target close."""
    names = plan["features"]
    seq_len = int(plan["sequence_bars"])
    holding = int(plan["target"]["holding_sessions"])
    buy_threshold = float(plan["target"]["buy_net_return_threshold"])
    sell_threshold = float(plan["target"]["sell_net_return_threshold"])
    fee = float(plan["trading"]["commission_rate"])
    tax = float(plan["trading"]["transaction_tax_rate"])
    samples, labels, metadata = [], [], []
    for code, days0 in full_daily.groupby("stock_code", sort=False):
        days = days0.sort_values("trade_date").reset_index(drop=True)
        stock_features = featured_1325.loc[featured_1325["stock_code"] == code]
        bars_by_day = {day: g.sort_values("kbar_timestamp") for day, g in stock_features.groupby("trade_date")}
        for i in range(0, len(days) - holding):
            day = days.loc[i, "trade_date"]
            bars = bars_by_day.get(day)
            if bars is None or len(bars) < seq_len:
                continue
            window = bars[names].tail(seq_len).to_numpy(dtype=np.float64)
            if not np.isfinite(window).all():
                continue
            entry = float(days.loc[i + 1, "open"])
            target_close = float(days.loc[i + holding, "close"])
            gross_return = target_close / entry - 1
            net_return = target_close * (1 - fee - tax) / (entry * (1 + fee)) - 1
            samples.append(window.astype(np.float32))
            labels.append(2 if net_return >= buy_threshold else (0 if net_return <= sell_threshold else 1))
            metadata.append({"stock_code": str(code), "signal_date": str(day),
                "signal_time": bars.iloc[-1]["datetime"].isoformat(),
                "entry_date": str(days.loc[i + 1, "trade_date"]), "entry_open": entry,
                "target_date": str(days.loc[i + holding, "trade_date"]), "target_close": target_close,
                "future_gross_return": gross_return, "future_net_return": net_return})
    X = np.asarray(samples, dtype=np.float32)
    y = np.asarray(labels, dtype=np.int64)
    meta = pd.DataFrame(metadata)
    return PreparedData(X={"train": X}, y={"train": y}, metadata={"train": meta},
        feature_names=list(names), scaler_mean=np.zeros(len(names)), scaler_scale=np.ones(len(names)),
        audit={"loaded_roles": ["train"], "scaler_fit_on": None,
               "workflow_stage": "signal_anchor_1325_training_only",
               "anchor": "13:25", "target_close_source": "original full session including 13:30"})


def assert_identical_labels(baseline: PreparedData, experimental: PreparedData) -> dict[str, Any]:
    keys = ["stock_code", "signal_date", "entry_date", "target_date"]
    left = baseline.metadata["train"].reset_index(drop=True)
    right = experimental.metadata["train"].reset_index(drop=True)
    if len(left) != len(right):
        raise RuntimeError(f"Baseline/Experimental 樣本數不同：{len(left)} != {len(right)}")
    identity = bool(left[keys].equals(right[keys]))
    label_identity = bool(np.array_equal(baseline.y["train"], experimental.y["train"]))
    return_error = float(np.max(np.abs(left["future_net_return"].to_numpy() - right["future_net_return"].to_numpy())))
    if not identity or not label_identity or return_error > 1e-12:
        raise RuntimeError("13:25 實驗改動了樣本或 Label，拒絕執行。")
    return {"sample_count": int(len(left)), "sample_keys_identical": identity,
            "labels_identical": label_identity, "future_net_return_max_abs_error": return_error}


def align_common_samples(baseline: PreparedData, experimental: PreparedData) -> tuple[PreparedData, PreparedData, dict[str, Any]]:
    keys = ["stock_code", "signal_date", "entry_date", "target_date"]
    def tuples(data):
        return [tuple(row) for row in data.metadata["train"][keys].itertuples(index=False, name=None)]
    baseline_keys, experimental_keys = tuples(baseline), tuples(experimental)
    exp_lookup = {key: index for index, key in enumerate(experimental_keys)}
    base_indices = [i for i, key in enumerate(baseline_keys) if key in exp_lookup]
    exp_indices = [exp_lookup[baseline_keys[i]] for i in base_indices]
    if not base_indices:
        raise RuntimeError("Baseline 與 Experimental 沒有共同樣本。")
    def subset(data, indices):
        return PreparedData(X={"train": data.X["train"][indices]}, y={"train": data.y["train"][indices]},
            metadata={"train": data.metadata["train"].iloc[indices].reset_index(drop=True)},
            feature_names=list(data.feature_names), scaler_mean=data.scaler_mean, scaler_scale=data.scaler_scale,
            audit={**data.audit, "common_sample_alignment": True})
    audit = {"baseline_original_samples": len(baseline_keys), "experimental_original_samples": len(experimental_keys),
             "common_samples": len(base_indices), "baseline_only_removed": len(baseline_keys) - len(base_indices),
             "experimental_only_removed": len(experimental_keys) - len(exp_indices),
             "reason": "Both anchors use the identical common stock/date sample set; days lacking 24 bars by 13:25 are removed from both."}
    return subset(baseline, base_indices), subset(experimental, exp_indices), audit


def aggregate_predictions(folds: list[dict[str, Any]]) -> dict[str, Any]:
    actual = {str(i): 0 for i in range(3)}
    predicted = {str(i): 0 for i in range(3)}
    for fold in folds:
        for key in actual:
            actual[key] += int(fold["actual_class_counts"][key])
            predicted[key] += int(fold["predicted_class_counts"][key])
    total = sum(predicted.values())
    return {"actual_counts": {LABELS[int(k)]: v for k, v in actual.items()},
            "predicted_counts": {LABELS[int(k)]: v for k, v in predicted.items()},
            "predicted_proportions": {LABELS[int(k)]: float(v / total) for k, v in predicted.items()}}


def per_stock_summary(folds: list[dict[str, Any]]) -> dict[str, Any]:
    codes = sorted({code for fold in folds for code in fold["per_stock"]})
    output = {}
    for code in codes:
        f1 = np.asarray([fold["per_stock"][code]["macro_f1"] for fold in folds if code in fold["per_stock"]], dtype=float)
        auc = np.asarray([fold["per_stock"][code]["macro_roc_auc_ovr"] for fold in folds
                          if code in fold["per_stock"] and fold["per_stock"][code]["macro_roc_auc_ovr"] is not None], dtype=float)
        output[code] = {"completed_folds": int(len(f1)),
            "macro_f1": {"mean": float(f1.mean()), "standard_deviation": float(f1.std(ddof=1)), "worst": float(f1.min())},
            "macro_auc": {"mean": float(auc.mean()) if len(auc) else None,
                          "standard_deviation": float(auc.std(ddof=1)) if len(auc) > 1 else 0.0 if len(auc) else None,
                          "worst": float(auc.min()) if len(auc) else None, "available_folds": int(len(auc))}}
    return output


def model_view(summary: dict[str, Any]) -> dict[str, Any]:
    folds = summary["folds"]
    return {"completed_folds": summary["completed_folds"], "folds": folds,
            "macro_f1": summary["macro_f1"], "macro_auc": summary["macro_roc_auc_ovr"],
            "individual_auc": summary["individual_roc_auc_ovr"],
            "class_prediction_distribution": aggregate_predictions(folds),
            "per_stock": per_stock_summary(folds)}


def delta_metric(experimental: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    return {"mean": experimental["mean"] - baseline["mean"],
            "standard_deviation": experimental["standard_deviation"] - baseline["standard_deviation"],
            "worst": experimental["worst"] - baseline["worst"]}


def compare_models(baseline: dict[str, Any], experimental: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for name in baseline:
        b, e = baseline[name], experimental[name]
        stocks = {}
        for code in b["per_stock"]:
            stocks[code] = {"macro_f1": delta_metric(e["per_stock"][code]["macro_f1"], b["per_stock"][code]["macro_f1"]),
                            "macro_auc": delta_metric(e["per_stock"][code]["macro_auc"], b["per_stock"][code]["macro_auc"])}
        output[name] = {"macro_f1": delta_metric(e["macro_f1"], b["macro_f1"]),
                        "macro_auc": delta_metric(e["macro_auc"], b["macro_auc"]),
                        "individual_auc_mean": {label: e["individual_auc"][label]["mean"] - b["individual_auc"][label]["mean"] for label in LABELS},
                        "predicted_proportion_delta": {label: e["class_prediction_distribution"]["predicted_proportions"][label] - b["class_prediction_distribution"]["predicted_proportions"][label] for label in LABELS},
                        "per_stock": stocks}
    return output


def interpretation(comparison: dict[str, Any], range_check: dict[str, Any]) -> dict[str, Any]:
    model_directions = {}
    for name, item in comparison.items():
        favorable = {"f1_mean": item["macro_f1"]["mean"] > 0, "f1_worst": item["macro_f1"]["worst"] > 0,
                     "f1_std": item["macro_f1"]["standard_deviation"] < 0,
                     "auc_mean": item["macro_auc"]["mean"] > 0, "auc_worst": item["macro_auc"]["worst"] > 0,
                     "auc_std": item["macro_auc"]["standard_deviation"] < 0}
        stock_f1 = [x["macro_f1"]["mean"] > 0 for x in item["per_stock"].values()]
        stock_auc = [x["macro_auc"]["mean"] > 0 for x in item["per_stock"].values()]
        model_directions[name] = {"favorable_components": favorable,
            "favorable_component_count": int(sum(favorable.values())), "component_count": len(favorable),
            "stocks_with_higher_mean_f1": int(sum(stock_f1)), "stocks_total": len(stock_f1),
            "stocks_with_higher_mean_auc": int(sum(stock_auc))}
    all_consistent = all(x["favorable_component_count"] >= 4 for x in model_directions.values())
    conclusion = ("13:25 移除了已確認的退化收盤 K 棒，且三模型在平均、最差 Fold、穩定度與股票方向上大致一致改善；作為訊號錨點較合理。"
                  if all_consistent else
                  "13:25 在資料結構上較合理，因為最後 position 恢復正常變化；但模型與股票指標未呈現一致改善，因此預測效益仍屬混合結果，不能只靠平均值宣稱提升。")
    return {"range_last_position_restored": range_check["experimental_1325"]["standard_deviation"] > 0,
            "model_directions": model_directions, "three_models_consistent_improvement": all_consistent,
            "answer": conclusion}


def fmt(value: Any, digits: int = 4) -> str: return "—" if value is None else f"{float(value):.{digits}f}"
def table(headers: list[str], rows: list[list[Any]]) -> str:
    return "<table><thead><tr>" + "".join(f"<th>{html.escape(str(x))}</th>" for x in headers) + \
           "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{html.escape(str(x))}</td>" for x in r) + "</tr>" for r in rows) + "</tbody></table>"


def render(report: dict[str, Any]) -> str:
    metric_rows, auc_rows, pred_rows, stock_rows = [], [], [], []
    for name in report["baseline_1330"]:
        b, e, d = report["baseline_1330"][name], report["experimental_1325"][name], report["comparison"][name]
        metric_rows += [[name, "Macro F1", k, fmt(b["macro_f1"][k]), fmt(e["macro_f1"][k]), fmt(d["macro_f1"][k])] for k in ("mean", "standard_deviation", "worst")]
        metric_rows += [[name, "Macro AUC", k, fmt(b["macro_auc"][k]), fmt(e["macro_auc"][k]), fmt(d["macro_auc"][k])] for k in ("mean", "standard_deviation", "worst")]
        for label in LABELS:
            auc_rows.append([name, label, fmt(b["individual_auc"][label]["mean"]), fmt(e["individual_auc"][label]["mean"]), fmt(d["individual_auc_mean"][label])])
            pred_rows.append([name, label, f"{b['class_prediction_distribution']['predicted_proportions'][label]:.2%}", f"{e['class_prediction_distribution']['predicted_proportions'][label]:.2%}", f"{d['predicted_proportion_delta'][label]:+.2%}"])
        for code, item in d["per_stock"].items():
            stock_rows.append([name, code, fmt(b["per_stock"][code]["macro_f1"]["mean"]), fmt(e["per_stock"][code]["macro_f1"]["mean"]), fmt(item["macro_f1"]["mean"]),
                               fmt(b["per_stock"][code]["macro_auc"]["mean"]), fmt(e["per_stock"][code]["macro_auc"]["mean"]), fmt(item["macro_auc"]["mean"])])
    return f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Signal Anchor Controlled Experiment</title><style>
body{{font-family:system-ui,'Noto Sans TC',sans-serif;max-width:1400px;margin:28px auto;padding:0 18px;background:#f6f8fb;color:#182536}}section{{background:#fff;border:1px solid #d9e3eb;border-radius:12px;padding:18px;margin:16px 0}}h1,h2{{color:#0b527a}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #d6e0e8;padding:7px;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#eaf3f8}}.answer{{background:#eaf7ee}}pre{{white-space:pre-wrap}}</style></head><body>
<h1>ML V3 Training-only Signal Anchor Controlled Experiment</h1>
<section class='answer'><h2>判讀</h2><p>{html.escape(report['interpretation']['answer'])}</p></section>
<section><h2>實驗控制</h2><p>Baseline：13:30；Experimental：排除 13:30，使最後位置為 13:25。Label、Features、模型參數、Walk-forward、Scaler 規則完全相同。</p><pre>{html.escape(json.dumps(report['sample_identity_check'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>range_vs_close 最後 Position</h2><pre>{html.escape(json.dumps(report['range_last_position_check'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>Macro 指標</h2>{table(['模型','指標','統計','Baseline','13:25','Delta'],metric_rows)}</section>
<section><h2>SELL / HOLD / BUY AUC</h2>{table(['模型','類別','Baseline','13:25','Delta'],auc_rows)}</section>
<section><h2>Prediction Distribution</h2>{table(['模型','預測類別','Baseline','13:25','Delta'],pred_rows)}</section>
<section><h2>各股票平均表現</h2>{table(['模型','股票','F1 Base','F1 13:25','F1 Δ','AUC Base','AUC 13:25','AUC Δ'],stock_rows)}</section>
<section><h2>一致性</h2><pre>{html.escape(json.dumps(report['interpretation']['model_directions'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>限制確認</h2><pre>{html.escape(json.dumps(report['execution_confirmation'],ensure_ascii=False,indent=2))}</pre></section>
</body></html>"""


def main() -> int:
    plan = load_trading_plan(PLAN_PATH)
    protocol = load_walk_forward_protocol(PROTOCOL_PATH)
    baseline_raw, _ = prepare_trading_data(DATABASE, plan, roles=("train",), apply_scaling=False,
                                            workflow_stage="signal_anchor_baseline_training_only")
    raw_kbars = _read_split(DATABASE, plan, "train")
    full_featured, _ = _intraday_features(raw_kbars, plan["features"])
    full_daily = _daily_market(full_featured)
    filtered = raw_kbars.loc[raw_kbars["datetime"].dt.strftime("%H:%M:%S") != "13:30:00"].copy()
    featured_1325, _ = _intraday_features(filtered, plan["features"])
    experimental_raw = experimental_samples(featured_1325, full_daily, plan)
    baseline_raw, experimental_raw, alignment = align_common_samples(baseline_raw, experimental_raw)
    identity = {**assert_identical_labels(baseline_raw, experimental_raw), **alignment}
    anchor_results = {}
    for anchor, source in (("baseline_1330", baseline_raw), ("experimental_1325", experimental_raw)):
        anchor_results[anchor] = {}
        for name, params in plan["models"].items():
            fold_results = []
            for spec in protocol["walk_forward"]["folds"]:
                fold = build_walk_forward_fold(source, spec)
                _, metrics, _ = run_multiclass_model(name, fold, params, evaluation_splits=("validation",))
                fold_results.append({"fold": int(spec["fold"]), **metrics["splits"]["validation"],
                                     "training": {key: metrics.get(key) for key in ("epochs_completed", "best_validation_loss", "early_stopping_source") if key in metrics}})
            anchor_results[anchor][name] = model_view(aggregate_fold_metrics(fold_results))
    baseline, experimental = anchor_results["baseline_1330"], anchor_results["experimental_1325"]
    comparison = compare_models(baseline, experimental)
    range_index = plan["features"].index("range_vs_close")
    base_last = baseline_raw.X["train"][:, -1, range_index]
    exp_last = experimental_raw.X["train"][:, -1, range_index]
    range_check = {"baseline_1330": {"mean": float(base_last.mean()), "standard_deviation": float(base_last.std(ddof=1)), "min": float(base_last.min()), "max": float(base_last.max()), "zero_ratio": float((base_last == 0).mean())},
                   "experimental_1325": {"mean": float(exp_last.mean()), "standard_deviation": float(exp_last.std(ddof=1)), "min": float(exp_last.min()), "max": float(exp_last.max()), "zero_ratio": float((exp_last == 0).mean())}}
    report = {"report_name": "ML V3 Training-only Signal Anchor Controlled Experiment",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": {"period": ["2023-01-01", "2024-12-31"], "walk_forward_folds": 4,
                  "baseline_anchor": "13:30", "experimental_anchor": "13:25",
                  "only_change": "exclude 13:30 closing-auction one-price bar from input sequence"},
        "sample_identity_check": identity, "range_last_position_check": range_check,
        "baseline_1330": baseline, "experimental_1325": experimental, "comparison": comparison,
        "interpretation": interpretation(comparison, range_check),
        "execution_confirmation": {"controlled_validation_used": False, "development_used": False,
            "forbidden_roles_used": False, "label_changed": False, "feature_definition_changed": False,
            "model_parameters_changed": False, "candidate_overwritten": False,
            "return_used_for_selection": False, "v4_created": False}}
    OUT.mkdir(parents=True, exist_ok=True)
    json_path, html_path = OUT / "signal_anchor_compare.json", OUT / "signal_anchor_compare.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    html_path.write_text(render(report), encoding="utf-8")
    print(json.dumps(report["interpretation"], ensure_ascii=False, indent=2))
    print(json.dumps(report["execution_confirmation"], ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

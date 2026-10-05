"""V4-A Training-only controlled experiment for volatility-adjusted labels."""
from __future__ import annotations

import html
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.data_pipeline import PreparedData
from ml.models import run_multiclass_model
from ml.trading_pipeline import _daily_market, _read_split, load_trading_plan, prepare_trading_data
from ml.walk_forward import aggregate_fold_metrics, build_walk_forward_fold, load_walk_forward_protocol

PLAN_PATH = ROOT / "configs/ml_trading_v3_20261004.json"
PROTOCOL_PATH = ROOT / "configs/ml_training_protocol_v3.json"
DATABASE = ROOT / "data/market_data.sqlite3"
OUT = ROOT / "exports/ml_v4a_label_compare_20261005"
LABEL_NAMES = {0: "SELL", 1: "HOLD", 2: "BUY"}
LABEL_VERSIONS = {"v3_fixed_1pct": None, "v4a_k_0_50": .50, "v4a_k_0_75": .75, "v4a_k_1_00": 1.00}
MODEL_SETTINGS = {
    "random_forest": {"config_name": "flexible", "params": {"n_estimators": 200, "max_depth": 10,
        "min_samples_leaf": 5, "class_weight": "balanced", "random_state": 42}},
    "xgboost": {"config_name": "baseline", "params": {"n_estimators": 150, "max_depth": 4,
        "learning_rate": .05, "subsample": .8, "colsample_bytree": .8, "random_state": 42}},
    "gru": {"config_name": "smaller", "params": {"hidden_size": 16, "num_layers": 1,
        "dropout": 0.0, "batch_size": 128, "max_epochs": 10, "patience": 2,
        "learning_rate": .001, "random_state": 42}},
}


def attach_past_volatility(metadata: pd.DataFrame, raw_kbars: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    daily = _daily_market(raw_kbars)
    daily = daily.sort_values(["stock_code", "trade_date"]).copy()
    daily["daily_return"] = daily.groupby("stock_code")["close"].pct_change(fill_method=None)
    daily["sigma_20d"] = daily.groupby("stock_code")["daily_return"].transform(
        lambda x: x.rolling(20, min_periods=20).std())
    daily["sigma_3d"] = daily["sigma_20d"] * math.sqrt(3)
    sigma = daily[["stock_code", "trade_date", "sigma_20d", "sigma_3d"]].copy()
    sigma["stock_code"] = sigma["stock_code"].astype(str)
    sigma["signal_date"] = sigma["trade_date"].astype(str)
    joined = metadata.copy()
    joined["stock_code"] = joined["stock_code"].astype(str)
    joined = joined.merge(sigma.drop(columns="trade_date"), on=["stock_code", "signal_date"], how="left", validate="many_to_one")
    eligible = np.isfinite(joined["sigma_3d"]) & (joined["sigma_3d"] > 0)
    audit = {"original_samples": int(len(joined)), "eligible_samples": int(eligible.sum()),
        "removed_missing_or_zero_sigma": int((~eligible).sum()),
        "rolling_definition": "per-stock daily close return rolling std(20), then multiply sqrt(3)",
        "uses_signal_day_or_earlier_only": True, "future_prices_used": False}
    return joined.loc[eligible].reset_index(drop=True), audit


def subset_prepared(raw: PreparedData, eligible_metadata: pd.DataFrame) -> PreparedData:
    keys = ["stock_code", "signal_date", "entry_date", "target_date"]
    original = raw.metadata["train"].reset_index().rename(columns={"index": "source_index"})
    selected = eligible_metadata[keys].merge(original[keys + ["source_index"]], on=keys, how="left", validate="one_to_one")
    if selected["source_index"].isna().any():
        raise RuntimeError("無法將 volatility eligibility 對回 Training samples。")
    indices = selected["source_index"].astype(int).to_numpy()
    meta = eligible_metadata.copy()
    return PreparedData(X={"train": raw.X["train"][indices]}, y={"train": raw.y["train"][indices]},
        metadata={"train": meta}, feature_names=list(raw.feature_names), scaler_mean=raw.scaler_mean,
        scaler_scale=raw.scaler_scale, audit={**raw.audit, "volatility_eligible_common_sample_set": True})


def make_label_version(common: PreparedData, name: str, k: float | None) -> PreparedData:
    meta = common.metadata["train"].copy()
    returns = meta["future_net_return"].to_numpy(dtype=float)
    threshold = np.full(len(meta), .01, dtype=float) if k is None else k * meta["sigma_3d"].to_numpy(dtype=float)
    labels = np.where(returns <= -threshold, 0, np.where(returns >= threshold, 2, 1)).astype(np.int64)
    meta["label_threshold"] = threshold
    meta["label_version"] = name
    return PreparedData(X={"train": common.X["train"].copy()}, y={"train": labels},
        metadata={"train": meta}, feature_names=list(common.feature_names), scaler_mean=common.scaler_mean,
        scaler_scale=common.scaler_scale, audit={**common.audit, "label_version": name,
            "threshold_formula": "fixed 0.01" if k is None else f"{k} * sigma_20d * sqrt(3)"})


def distribution(data: PreparedData) -> dict[str, Any]:
    output = {}
    meta, y = data.metadata["train"], data.y["train"]
    for code in sorted(meta["stock_code"].unique()):
        mask = meta["stock_code"].to_numpy() == code
        output[code] = distribution_values(y[mask], meta.loc[mask, "label_threshold"].to_numpy())
    return output


def distribution_values(y: np.ndarray, threshold: np.ndarray) -> dict[str, Any]:
    classes = {}
    for label, name in LABEL_NAMES.items():
        count = int((y == label).sum())
        classes[name] = {"count": count, "proportion": float(count / len(y))}
    flags = (["HOLD > 70%"] if classes["HOLD"]["proportion"] > .7 else []) + \
            (["SELL < 10%"] if classes["SELL"]["proportion"] < .1 else []) + \
            (["BUY < 10%"] if classes["BUY"]["proportion"] < .1 else [])
    return {"samples": int(len(y)), "classes": classes,
        "threshold": {"mean": float(np.mean(threshold)), "median": float(np.median(threshold)),
            "standard_deviation": float(np.std(threshold, ddof=1)) if len(threshold) > 1 else 0.0,
            "min": float(np.min(threshold)), "max": float(np.max(threshold))},
        "flags": flags, "severe_class_imbalance": bool(flags)}


def prediction_distribution(folds: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {str(i): 0 for i in LABEL_NAMES}
    for fold in folds:
        for key in counts: counts[key] += int(fold["predicted_class_counts"][key])
    total = sum(counts.values())
    return {LABEL_NAMES[int(k)]: {"count": value, "proportion": float(value / total)} for k, value in counts.items()}


def per_stock_summary(folds: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for code in sorted({c for f in folds for c in f["per_stock"]}):
        f1 = np.asarray([f["per_stock"][code]["macro_f1"] for f in folds], dtype=float)
        auc = np.asarray([f["per_stock"][code]["macro_roc_auc_ovr"] for f in folds
                          if f["per_stock"][code]["macro_roc_auc_ovr"] is not None], dtype=float)
        output[code] = {"macro_f1": stats(f1), "macro_auc": stats(auc), "auc_available_folds": int(len(auc))}
    return output


def stats(values: np.ndarray) -> dict[str, Any]:
    return {"mean": float(values.mean()) if len(values) else None,
            "standard_deviation": float(values.std(ddof=1)) if len(values) > 1 else 0.0 if len(values) else None,
            "worst": float(values.min()) if len(values) else None}


def model_view(summary: dict[str, Any]) -> dict[str, Any]:
    return {"completed_folds": summary["completed_folds"], "folds": summary["folds"],
        "macro_f1": summary["macro_f1"], "macro_auc": summary["macro_roc_auc_ovr"],
        "individual_auc": summary["individual_roc_auc_ovr"],
        "prediction_distribution": prediction_distribution(summary["folds"]),
        "per_stock": per_stock_summary(summary["folds"])}


def label_evidence(label_results: dict[str, Any], distributions: dict[str, Any], fold_distributions: list[dict[str, Any]]) -> dict[str, Any]:
    severe = sum(len(x["flags"]) for x in distributions.values()) + sum(len(x["distribution"]["flags"]) for x in fold_distributions)
    models = list(label_results.values())
    class_spreads = [max(m["individual_auc"][c]["mean"] for c in LABEL_NAMES.values()) -
                     min(m["individual_auc"][c]["mean"] for c in LABEL_NAMES.values()) for m in models]
    return {"severe_imbalance_flag_count": int(severe),
        "average_model_worst_macro_f1": float(np.mean([m["macro_f1"]["worst"] for m in models])),
        "average_model_mean_macro_f1": float(np.mean([m["macro_f1"]["mean"] for m in models])),
        "average_model_worst_macro_auc": float(np.mean([m["macro_auc"]["worst"] for m in models])),
        "average_model_mean_macro_auc": float(np.mean([m["macro_auc"]["mean"] for m in models])),
        "average_model_macro_f1_std": float(np.mean([m["macro_f1"]["standard_deviation"] for m in models])),
        "average_per_class_auc_spread": float(np.mean(class_spreads))}


def recommendation(evidence: dict[str, dict[str, Any]], all_results: dict[str, Any]) -> dict[str, Any]:
    order = list(LABEL_VERSIONS)
    def score(name):
        x = evidence[name]
        return (-x["severe_imbalance_flag_count"], x["average_model_worst_macro_f1"],
                x["average_model_mean_macro_f1"], x["average_model_worst_macro_auc"],
                x["average_model_mean_macro_auc"], -x["average_model_macro_f1_std"],
                -x["average_per_class_auc_spread"], -order.index(name))
    selected = max(order, key=score)
    baseline = all_results["v3_fixed_1pct"]
    if selected == "v3_fixed_1pct":
        text = "Training-only 證據不足以支持 volatility-adjusted Label 進入 V4 Candidate；維持 V3 fixed ±1%。"
        worth = False
    else:
        improvements = []
        for model in MODEL_SETTINGS:
            current, base = all_results[selected][model], baseline[model]
            improvements.append(current["macro_f1"]["worst"] > base["macro_f1"]["worst"] and
                                current["macro_f1"]["mean"] > base["macro_f1"]["mean"])
        worth = sum(improvements) >= 2 and evidence[selected]["severe_imbalance_flag_count"] <= evidence["v3_fixed_1pct"]["severe_imbalance_flag_count"]
        text = (f"{selected} 在預先固定的 Training-only 判讀規則下較合理，值得作為後續 V4 Candidate 的研究選項；本次不建立 Candidate。"
                if worth else f"{selected} 改善部分 Label 分布，但模型改善不夠一致；目前仍維持 V3 fixed ±1%，不進入 V4 Candidate。")
    return {"ordered_criteria": ["fewer severe imbalance flags", "higher average worst-fold Macro F1",
        "higher average mean Macro F1", "higher average worst-fold Macro AUC", "higher average mean Macro AUC",
        "lower Macro F1 std", "more balanced per-class AUC", "fixed tie order"],
        "training_only_preferred_label": selected, "worth_entering_v4_candidate": worth,
        "recommendation": text, "controlled_validation_started": False}


def fmt(x: Any, n: int = 4) -> str: return "—" if x is None else f"{float(x):.{n}f}"
def table(headers, rows):
    return "<table><thead><tr>" + "".join(f"<th>{html.escape(str(x))}</th>" for x in headers) + "</tr></thead><tbody>" + \
           "".join("<tr>" + "".join(f"<td>{html.escape(str(x))}</td>" for x in row) + "</tr>" for row in rows) + "</tbody></table>"


def render(report: dict[str, Any]) -> str:
    label_rows, fold_rows, model_rows, auc_rows, stock_rows = [], [], [], [], []
    for version, stocks in report["label_distributions"]["by_version_and_stock"].items():
        for code, d in stocks.items():
            label_rows.append([version, code] + [f"{d['classes'][c]['count']} ({d['classes'][c]['proportion']:.1%})" for c in LABEL_NAMES.values()] +
                              [fmt(d["threshold"]["mean"], 5), fmt(d["threshold"]["median"], 5), ", ".join(d["flags"]) or "—"])
    for version, items in report["label_distributions"]["by_version_fold_stock"].items():
        for item in items:
            d = item["distribution"]
            fold_rows.append([version, item["fold"], item["stock_code"]] + [f"{d['classes'][c]['proportion']:.1%}" for c in LABEL_NAMES.values()] + [", ".join(d["flags"]) or "—"])
    for version, models in report["model_results"].items():
        for name, m in models.items():
            model_rows.append([version, name, fmt(m["macro_f1"]["mean"]), fmt(m["macro_f1"]["standard_deviation"]), fmt(m["macro_f1"]["worst"]),
                               fmt(m["macro_auc"]["mean"]), fmt(m["macro_auc"]["standard_deviation"]), fmt(m["macro_auc"]["worst"])])
            auc_rows.append([version, name] + [fmt(m["individual_auc"][c]["mean"]) for c in LABEL_NAMES.values()] +
                            [" / ".join(f"{c}:{m['prediction_distribution'][c]['proportion']:.1%}" for c in LABEL_NAMES.values())])
            for code, s in m["per_stock"].items():
                stock_rows.append([version, name, code, fmt(s["macro_f1"]["mean"]), fmt(s["macro_f1"]["standard_deviation"]), fmt(s["macro_f1"]["worst"]),
                                   fmt(s["macro_auc"]["mean"]), fmt(s["macro_auc"]["standard_deviation"]), fmt(s["macro_auc"]["worst"])])
    return f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>V4-A Label Compare</title><style>
body{{font-family:system-ui,'Noto Sans TC',sans-serif;max-width:1500px;margin:28px auto;padding:0 18px;background:#f6f8fb;color:#182536}}section{{background:white;border:1px solid #d9e3eb;border-radius:12px;padding:18px;margin:16px 0}}h1,h2{{color:#0b527a}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d5dfe8;padding:6px;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#eaf3f8}}.answer{{background:#eaf7ee}}pre{{white-space:pre-wrap}}</style></head><body>
<h1>V4-A Training-only Label Controlled Experiment</h1>
<section class='answer'><h2>Recommendation</h2><p>{html.escape(report['recommendation']['recommendation'])}</p><pre>{html.escape(json.dumps(report['recommendation'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>1. Experiment Design</h2><pre>{html.escape(json.dumps(report['experiment_design'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>2. V3 Fixed Label</h2><p>SELL ≤ -1%；HOLD 在 -1% 與 +1% 之間；BUY ≥ +1%。</p></section>
<section><h2>3. Volatility-adjusted Label Formula</h2><p>daily return → rolling std(20) → sigma_3d = sigma_20d × √3 → threshold = k × sigma_3d。</p><pre>{html.escape(json.dumps(report['volatility_audit'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>4–5. Label Distribution Comparison / Per-stock</h2>{table(['版本','股票','SELL','HOLD','BUY','Threshold mean','median','警示'],label_rows)}<h3>各 Fold／股票</h3>{table(['版本','Fold','股票','SELL','HOLD','BUY','警示'],fold_rows)}</section>
<section><h2>6. Walk-forward Model Comparison</h2>{table(['版本','模型','F1 mean','F1 std','F1 worst','AUC mean','AUC std','AUC worst'],model_rows)}</section>
<section><h2>7. Per-class AUC</h2>{table(['版本','模型','SELL AUC','HOLD AUC','BUY AUC','Prediction distribution'],auc_rows)}</section>
<section><h2>8. Per-stock Stability</h2>{table(['版本','模型','股票','F1 mean','F1 std','F1 worst','AUC mean','AUC std','AUC worst'],stock_rows)}</section>
<section><h2>9. Diagnostic Findings</h2><pre>{html.escape(json.dumps(report['label_evidence'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>10. Recommendation</h2><p>{html.escape(report['recommendation']['recommendation'])}</p></section>
<section><h2>限制確認</h2><pre>{html.escape(json.dumps(report['execution_confirmation'],ensure_ascii=False,indent=2))}</pre></section>
</body></html>"""


def main() -> int:
    plan = load_trading_plan(PLAN_PATH)
    protocol = load_walk_forward_protocol(PROTOCOL_PATH)
    raw, _ = prepare_trading_data(DATABASE, plan, roles=("train",), apply_scaling=False,
                                  workflow_stage="v4a_label_experiment_training_only")
    raw_kbars = _read_split(DATABASE, plan, "train")
    eligible_meta, volatility_audit = attach_past_volatility(raw.metadata["train"], raw_kbars)
    common = subset_prepared(raw, eligible_meta)
    versions = {name: make_label_version(common, name, k) for name, k in LABEL_VERSIONS.items()}
    distributions, fold_distributions, model_results = {}, {}, {}
    for version, data in versions.items():
        distributions[version] = distribution(data)
        fold_distributions[version] = []
        model_folds = {name: [] for name in MODEL_SETTINGS}
        for spec in protocol["walk_forward"]["folds"]:
            fold = build_walk_forward_fold(data, spec)
            for code in sorted(fold.metadata["validation"]["stock_code"].unique()):
                mask = fold.metadata["validation"]["stock_code"].to_numpy() == code
                fold_distributions[version].append({"fold": int(spec["fold"]), "stock_code": code,
                    "distribution": distribution_values(fold.y["validation"][mask], fold.metadata["validation"].loc[mask, "label_threshold"].to_numpy())})
            for name, setting in MODEL_SETTINGS.items():
                _, metrics, _ = run_multiclass_model(name, fold, setting["params"], evaluation_splits=("validation",))
                model_folds[name].append({"fold": int(spec["fold"]), **metrics["splits"]["validation"],
                    "training": {key: metrics.get(key) for key in ("epochs_completed", "best_validation_loss", "early_stopping_source") if key in metrics}})
        model_results[version] = {name: model_view(aggregate_fold_metrics(items)) for name, items in model_folds.items()}
    evidence = {name: label_evidence(model_results[name], distributions[name], fold_distributions[name]) for name in versions}
    rec = recommendation(evidence, model_results)
    report = {"report_name": "V4-A Training-only Label Controlled Experiment",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "experiment_design": {"training_period": ["2023-01-01", "2024-12-31"], "walk_forward": "4-fold expanding-window",
            "signal_anchor": "13:30 unchanged", "features": "V3 24 features unchanged", "holding_sessions": 3,
            "trading_costs": plan["trading"], "model_settings": MODEL_SETTINGS,
            "label_versions": {name: ("fixed ±1%" if k is None else f"threshold={k}*sigma_20d*sqrt(3)") for name, k in LABEL_VERSIONS.items()},
            "selection_uses_trading_metrics": False},
        "volatility_audit": volatility_audit,
        "label_distributions": {"by_version_and_stock": distributions, "by_version_fold_stock": fold_distributions},
        "model_results": model_results, "label_evidence": evidence, "recommendation": rec,
        "execution_confirmation": {"training_only": True, "controlled_validation_used": False,
            "development_used": False, "forbidden_roles_used": False, "signal_anchor_changed": False,
            "features_changed": False, "model_parameters_changed": False, "trading_rules_changed": False,
            "v3_overwritten": False, "candidate_overwritten": False, "controlled_validation_started": False,
            "return_sharpe_profit_factor_used_for_selection": False}}
    OUT.mkdir(parents=True, exist_ok=True)
    json_path, html_path = OUT / "v4a_label_compare.json", OUT / "v4a_label_compare.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    html_path.write_text(render(report), encoding="utf-8")
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    print(json.dumps(report["execution_confirmation"], ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

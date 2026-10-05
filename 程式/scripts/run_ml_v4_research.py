"""V4 Training-only research framework with static daily context."""
from __future__ import annotations

import copy
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

from ml.data_pipeline import PreparedData
from ml.models import run_multiclass_model
from ml.research_features import (INDUSTRY_FEATURES, STOCK_FEATURES,
    build_daily_stock_features, build_exploratory_industry_features, causal_feature_definitions)
from ml.research_universe import (enabled_stock_union, enforce_industry_reliability,
    stock_group, validate_stock_groups)
from ml.trading_pipeline import (coverage_report, _intraday_features, _read_split,
    _samples_for_split, load_trading_plan)
from ml.walk_forward import aggregate_fold_metrics, build_walk_forward_fold, load_walk_forward_protocol

CONFIG_PATH = ROOT / "configs/ml_v4_research_matrix.json"
DATABASE = ROOT / "data/market_data.sqlite3"
OUT = ROOT / "exports/ml_v4_feature_ablation_20261005"
LABELS = ("SELL", "HOLD", "BUY")


def load_matrix(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("data_role") != "train" or config.get("training_period") != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise ValueError("V4 Research Framework 目前只允許固定 Training 期間。")
    expected = {"label": "V3 fixed SELL<=-1%, HOLD(-1%,+1%), BUY>=+1%",
        "signal_anchor": "13:30", "holding_sessions": 3, "parameter_search": False,
        "trading_return_selection": False}
    if config.get("fixed_design") != expected:
        raise ValueError("Label、Signal Anchor 或研究限制遭到修改。")
    if not any(item.get("enabled") for item in config["experiments"]):
        raise ValueError("至少要啟用一個實驗。")
    validate_stock_groups(config)
    return config


def resolve_feature_set(config: dict[str, Any], name: str) -> dict[str, Any]:
    visited: set[str] = set()
    def visit(current: str) -> tuple[list[str], list[str]]:
        if current in visited:
            raise ValueError("Feature Set 繼承形成循環。")
        visited.add(current)
        spec = config["feature_sets"][current]
        stock, industry = ([], []) if spec["base"] == "v3" else visit(spec["base"])
        stock += list(spec.get("additional_features", []))
        industry += list(spec.get("industry_features", []))
        return list(dict.fromkeys(stock)), list(dict.fromkeys(industry))
    stock, industry = visit(name)
    if not set(stock) <= set(STOCK_FEATURES) or not set(industry) <= set(INDUSTRY_FEATURES):
        raise ValueError(f"Feature Set {name} 含未實作 Feature。")
    result = dict(config["feature_sets"][name])
    result.update({"stock_features": stock, "industry_features": industry})
    return result


def load_enabled_training_data(database: Path, plan: dict[str, Any], config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load only Training for the union requested by enabled experiments."""
    codes = enabled_stock_union(config)
    local_plan = copy.deepcopy(plan)
    local_plan["stock_codes"] = codes
    coverage = coverage_report(database, local_plan, roles=("train",))
    missing = [row for row in coverage.rows if not row["available"]]
    if missing:
        raise RuntimeError(f"Training database 缺少設定檔指定股票或期間：{missing}")
    raw = _read_split(database, local_plan, "train")
    return raw, {"loaded_role": "train", "stock_union": codes, "coverage": coverage.rows}


def _static_context(featured_v3: pd.DataFrame, config: dict[str, Any], resolved: dict[str, Any]) -> pd.DataFrame:
    daily = build_daily_stock_features(featured_v3)
    if resolved["industry_features"]:
        daily = build_exploratory_industry_features(daily, config)
    names = [*resolved["stock_features"], *resolved["industry_features"]]
    return daily[["stock_code", "trade_date", *names]].copy()


def prepare_experiment(featured_v3: pd.DataFrame, plan: dict[str, Any], config: dict[str, Any], experiment: dict[str, Any]) -> PreparedData:
    resolved = resolve_feature_set(config, experiment["feature_set"])
    enforce_industry_reliability(config, experiment, resolved)
    codes = stock_group(config, experiment["stock_group"])
    selected = featured_v3.loc[featured_v3["stock_code"].astype(str).isin(codes)].copy()
    local_plan = copy.deepcopy(plan)
    local_plan["features"] = list(plan["features"])
    local_plan["stock_codes"] = codes
    samples, labels, metadata, _, rejected = _samples_for_split(selected, local_plan)
    X = np.asarray(samples, dtype=np.float32)
    y = np.asarray(labels, dtype=np.int64)
    meta = pd.DataFrame(metadata)
    context_names = [*resolved["stock_features"], *resolved["industry_features"]]
    context_X = None
    if context_names:
        context = _static_context(featured_v3, config, resolved)
        context["trade_date"] = pd.to_datetime(context["trade_date"]).dt.date
        lookup = meta.copy()
        lookup["trade_date"] = pd.to_datetime(lookup["signal_date"]).dt.date
        joined = lookup[["stock_code", "trade_date"]].merge(
            context, on=["stock_code", "trade_date"], how="left", validate="many_to_one")
        values = joined[context_names].to_numpy(dtype=np.float32)
        finite = np.isfinite(values).all(axis=1)
        X, y, meta, values = X[finite], y[finite], meta.loc[finite].reset_index(drop=True), values[finite]
        context_X = {"train": values}
    return PreparedData(
        X={"train": X}, y={"train": y}, metadata={"train": meta},
        feature_names=list(plan["features"]), scaler_mean=np.zeros(len(plan["features"])),
        scaler_scale=np.ones(len(plan["features"])),
        audit={"loaded_roles": ["train"], "scaler_fit_on": None,
            "workflow_stage": "v4_research_training_only", "experiment_id": experiment["id"],
            "feature_set": experiment["feature_set"], "stock_group": experiment["stock_group"],
            "sample_rejections": rejected, "daily_context_repeated_across_timesteps": False},
        context_X=context_X, context_feature_names=context_names,
        context_scaler_mean=None, context_scaler_scale=None)


def _sample_keys(data: PreparedData) -> list[tuple[Any, ...]]:
    columns = ["stock_code", "signal_date", "entry_date", "target_date"]
    return [tuple(row) for row in data.metadata["train"][columns].itertuples(index=False, name=None)]


def _take(data: PreparedData, indices: list[int], group: str) -> PreparedData:
    context = None if data.context_X is None else {"train": data.context_X["train"][indices]}
    return PreparedData(
        X={"train": data.X["train"][indices]}, y={"train": data.y["train"][indices]},
        metadata={"train": data.metadata["train"].iloc[indices].reset_index(drop=True)},
        feature_names=list(data.feature_names), scaler_mean=data.scaler_mean, scaler_scale=data.scaler_scale,
        audit={**data.audit, "comparison_group": group, "common_sample_alignment": True},
        context_X=context, context_feature_names=list(data.context_feature_names),
        context_scaler_mean=data.context_scaler_mean, context_scaler_scale=data.context_scaler_scale)


def align_comparison_groups(datasets: dict[str, PreparedData], experiments: list[dict[str, Any]]) -> tuple[dict[str, PreparedData], dict[str, Any]]:
    output, audits = dict(datasets), {}
    for group in sorted({item["comparison_group"] for item in experiments}):
        ids = [item["id"] for item in experiments if item["comparison_group"] == group]
        keys = {eid: _sample_keys(datasets[eid]) for eid in ids}
        common = set(keys[ids[0]])
        for eid in ids[1:]: common &= set(keys[eid])
        ordered = [key for key in keys[ids[0]] if key in common]
        for eid in ids:
            lookup = {key: index for index, key in enumerate(keys[eid])}
            output[eid] = _take(datasets[eid], [lookup[key] for key in ordered], group)
        reference = output[ids[0]]
        for eid in ids[1:]:
            if not np.array_equal(reference.y["train"], output[eid].y["train"]):
                raise RuntimeError(f"比較群組 {group} 的 Label 不一致。")
            error = float(np.max(np.abs(reference.metadata["train"]["future_net_return"].to_numpy()
                - output[eid].metadata["train"]["future_net_return"].to_numpy())))
            if error != 0:
                raise RuntimeError(f"比較群組 {group} 的 future return 不一致。")
        audits[group] = {"experiment_ids": ids, "common_samples": len(ordered),
            "original_samples": {eid: len(datasets[eid].X["train"]) for eid in ids},
            "labels_identical": True, "future_net_return_max_abs_error": 0.0}
    return output, audits


def numeric_quality(values: np.ndarray, rules: dict[str, Any]) -> dict[str, Any]:
    array = np.asarray(values, dtype=float).reshape(-1)
    finite = array[np.isfinite(array)]
    counts = pd.Series(finite).value_counts(normalize=True) if len(finite) else pd.Series(dtype=float)
    p99 = float(np.quantile(np.abs(finite), .99)) if len(finite) else None
    maximum = float(np.max(np.abs(finite))) if len(finite) else None
    ratio = maximum / p99 if p99 and p99 > 0 else None
    nan_ratio = float(np.isnan(array).mean())
    return {"count": len(array), "finite_count": len(finite),
        "min": float(finite.min()) if len(finite) else None, "max": float(finite.max()) if len(finite) else None,
        "mean": float(finite.mean()) if len(finite) else None,
        "standard_deviation": float(finite.std(ddof=1)) if len(finite) > 1 else 0.0 if len(finite) else None,
        "zero_ratio": float((finite == 0).mean()) if len(finite) else None, "nan_ratio": nan_ratio,
        "constant": len(np.unique(finite)) <= 1, "nearly_constant": bool(len(counts) and counts.iloc[0] >= rules["nearly_constant_dominant_ratio"]),
        "high_nan": nan_ratio > rules["high_nan_ratio"], "max_abs_to_p99_abs": ratio,
        "extreme": bool(ratio is not None and ratio > rules["extreme_max_abs_to_p99_abs"])}


def _correlation(frame: pd.DataFrame, new_features: set[str], threshold: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    corr = frame.corr()
    high, names = [], list(frame.columns)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            value = corr.loc[left, right]
            if np.isfinite(value) and abs(value) >= threshold:
                high.append({"feature_a": left, "feature_b": right, "correlation": float(value),
                    "involves_new_feature": left in new_features or right in new_features})
    matrix = {row: {col: None if pd.isna(value) else float(value) for col, value in corr.loc[row].items()} for row in corr.index}
    return matrix, high


def feature_analysis(data: PreparedData, rules: dict[str, Any]) -> dict[str, Any]:
    intraday = pd.DataFrame(data.X["train"].reshape(-1, len(data.feature_names)), columns=data.feature_names)
    context = (pd.DataFrame(data.context_X["train"], columns=data.context_feature_names)
        if data.context_X is not None else pd.DataFrame(index=np.arange(len(data.X["train"]))))
    threshold = float(rules["high_correlation_absolute"])
    intraday_matrix, intraday_high = _correlation(intraday, set(), threshold)
    context_matrix, context_high = _correlation(context, set(data.context_feature_names), threshold) if len(context.columns) else ({}, [])
    quality = {name: numeric_quality(context[name].to_numpy(), rules) for name in context.columns}
    distributions: dict[str, Any] = {}
    repeated_y = np.repeat(data.y["train"], data.X["train"].shape[1])
    for name in intraday.columns:
        distributions[name] = {LABELS[label]: {"mean": float(intraday.loc[repeated_y == label, name].mean()),
            "standard_deviation": float(intraday.loc[repeated_y == label, name].std(ddof=1)),
            "samples": int((repeated_y == label).sum())} for label in range(3)}
    for name in context.columns:
        distributions[name] = {LABELS[label]: {"mean": float(context.loc[data.y["train"] == label, name].mean()),
            "standard_deviation": float(context.loc[data.y["train"] == label, name].std(ddof=1)),
            "samples": int((data.y["train"] == label).sum())} for label in range(3)}
    return {"intraday_feature_list": data.feature_names, "context_feature_list": data.context_feature_names,
        "new_feature_quality": quality, "correlation_threshold": threshold,
        "intraday_high_correlation_pairs": intraday_high, "context_high_correlation_pairs": context_high,
        "intraday_correlation_matrix": intraday_matrix, "context_correlation_matrix": context_matrix,
        "feature_vs_label_distribution": distributions, "automatic_feature_removal": False,
        "intraday_correlation_observations": int(len(intraday)), "context_correlation_observations": int(len(context))}


def stats(values: np.ndarray) -> dict[str, Any]:
    return {"mean": float(values.mean()) if len(values) else None,
        "standard_deviation": float(values.std(ddof=1)) if len(values) > 1 else 0.0 if len(values) else None,
        "worst": float(values.min()) if len(values) else None}


def per_stock(folds: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for code in sorted({code for fold in folds for code in fold["per_stock"]}):
        items = [fold["per_stock"][code] for fold in folds if code in fold["per_stock"]]
        f1 = np.asarray([item["macro_f1"] for item in items], dtype=float)
        auc = np.asarray([item["macro_roc_auc_ovr"] for item in items if item["macro_roc_auc_ovr"] is not None], dtype=float)
        output[code] = {"macro_f1": stats(f1), "macro_auc": stats(auc), "auc_available_folds": len(auc)}
    return output


def model_view(summary: dict[str, Any]) -> dict[str, Any]:
    counts = {str(index): 0 for index in range(3)}
    for fold in summary["folds"]:
        for key in counts: counts[key] += int(fold["predicted_class_counts"][key])
    total = sum(counts.values())
    return {"folds": summary["folds"], "macro_f1": summary["macro_f1"],
        "macro_auc": summary["macro_roc_auc_ovr"], "individual_auc": summary["individual_roc_auc_ovr"],
        "per_stock": per_stock(summary["folds"]),
        "prediction_distribution": {LABELS[int(key)]: {"count": value, "proportion": value / total} for key, value in counts.items()}}


def compare(baseline: dict[str, Any], experimental: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for model in baseline:
        output[model] = {"macro_f1": {key: experimental[model]["macro_f1"][key] - baseline[model]["macro_f1"][key] for key in ("mean", "standard_deviation", "worst")},
            "macro_auc": {key: experimental[model]["macro_auc"][key] - baseline[model]["macro_auc"][key] for key in ("mean", "standard_deviation", "worst")},
            "individual_auc_mean": {label: experimental[model]["individual_auc"][label]["mean"] - baseline[model]["individual_auc"][label]["mean"] for label in LABELS},
            "stocks_higher_mean_f1": sum(experimental[model]["per_stock"][stock]["macro_f1"]["mean"] > baseline[model]["per_stock"][stock]["macro_f1"]["mean"] for stock in baseline[model]["per_stock"]),
            "stocks_higher_mean_auc": sum(experimental[model]["per_stock"][stock]["macro_auc"]["mean"] > baseline[model]["per_stock"][stock]["macro_auc"]["mean"] for stock in baseline[model]["per_stock"])}
    return output


def build_group_comparisons(results: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for group, spec in config["comparison_groups"].items():
        baseline, experimental = spec["baseline_experiment"], spec["experimental_experiment"]
        if baseline in results and experimental in results:
            output[group] = {**spec, "status": "completed", "comparison": compare(results[baseline], results[experimental])}
        else:
            output[group] = {**spec, "status": "skipped_disabled"}
    return output


def _rf_importance(model, data: PreparedData, sequence_bars: int, new: set[str]) -> list[dict[str, Any]]:
    raw = model.feature_importances_
    intraday_size = sequence_bars * len(data.feature_names)
    intraday = raw[:intraday_size].reshape(sequence_bars, len(data.feature_names)).sum(axis=0)
    context = raw[intraday_size:]
    items = [{"feature": name, "importance": float(intraday[index]), "input_type": "intraday_sequence", "is_new_feature": False}
        for index, name in enumerate(data.feature_names)]
    items += [{"feature": name, "importance": float(context[index]), "input_type": "static_context", "is_new_feature": name in new}
        for index, name in enumerate(data.context_feature_names)]
    return items


def render(report: dict[str, Any]) -> str:
    rows = []
    for experiment, models in report["results"].items():
        for name, metrics in models.items():
            rows.append([experiment, name, *[f"{metrics['macro_f1'][key]:.4f}" for key in ("mean", "standard_deviation", "worst")],
                *[f"{metrics['macro_auc'][key]:.4f}" for key in ("mean", "standard_deviation", "worst")]])
    def table(headers, values):
        return "<table><tr>" + "".join(f"<th>{html.escape(str(value))}</th>" for value in headers) + "</tr>" + "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>" for row in values) + "</table>"
    correlations = {key: {"intraday": value["intraday_high_correlation_pairs"], "context": value["context_high_correlation_pairs"]}
        for key, value in report["feature_analysis"].items()}
    return f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>V4 Research Fix1</title><style>body{{font-family:system-ui,'Noto Sans TC';max-width:1500px;margin:28px auto;padding:0 18px;background:#f6f8fb;color:#182536}}section{{background:#fff;border:1px solid #dae3ea;border-radius:12px;padding:18px;margin:16px 0}}h1,h2{{color:#0b527a}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d6e0e8;padding:6px;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#eaf3f8}}pre{{white-space:pre-wrap}}</style></head><body>
<h1>V4 Training-only Research Framework Fix1</h1>
<section><h2>1. Framework Fix Audit</h2><pre>{html.escape(json.dumps(report['framework_fix_audit'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>2. Experiment Design</h2><pre>{html.escape(json.dumps(report['experiment_design'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>3. Walk-forward Metrics</h2>{table(['實驗','模型','F1 mean','F1 std','F1 worst','AUC mean','AUC std','AUC worst'],rows)}</section>
<section><h2>4. Comparison Groups</h2><pre>{html.escape(json.dumps(report['comparison_groups'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>5. Correlation Audit</h2><pre>{html.escape(json.dumps(correlations,ensure_ascii=False,indent=2))}</pre></section>
<section><h2>6. Feature Importance</h2><pre>{html.escape(json.dumps(report['rf_feature_importance'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>7. Findings</h2><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in report['findings'])}</ul></section>
<section><h2>8. Recommendation</h2><p>{html.escape(report['recommended_next_experiment'])}</p></section></body></html>"""


def main() -> int:
    config = load_matrix(CONFIG_PATH)
    plan = load_trading_plan(ROOT / config["base_plan"])
    protocol = load_walk_forward_protocol(ROOT / config["walk_forward_protocol"])
    raw, load_audit = load_enabled_training_data(DATABASE, plan, config)
    featured, _ = _intraday_features(raw, plan["features"])
    enabled = [item for item in config["experiments"] if item["enabled"]]
    datasets = {item["id"]: prepare_experiment(featured, plan, config, item) for item in enabled}
    datasets, alignment = align_comparison_groups(datasets, enabled)
    analyses, results, importance = {}, {}, {}
    context_scaler_checks = []
    for experiment in enabled:
        eid = experiment["id"]
        data = datasets[eid]
        analyses[eid] = feature_analysis(data, config["quality_rules"])
        model_folds = {model: [] for model in config["models"]}
        rf_fold_importance = []
        for fold_spec in protocol["walk_forward"]["folds"]:
            fold = build_walk_forward_fold(data, fold_spec)
            context_scaler_checks.append(fold.audit["context_scaler_fit_on"] in (None, "fold_train_only"))
            for model_name, setting in config["models"].items():
                model, metrics, _ = run_multiclass_model(model_name, fold, setting["params"], evaluation_splits=("validation",))
                model_folds[model_name].append({"fold": int(fold_spec["fold"]), **metrics["splits"]["validation"]})
                if model_name == "random_forest":
                    resolved = resolve_feature_set(config, experiment["feature_set"])
                    new = set(resolved["stock_features"] + resolved["industry_features"])
                    rf_fold_importance.append(_rf_importance(model, data, int(plan["sequence_bars"]), new))
        results[eid] = {name: model_view(aggregate_fold_metrics(items)) for name, items in model_folds.items()}
        mean_by_feature = {}
        for fold_items in rf_fold_importance:
            for item in fold_items:
                mean_by_feature.setdefault(item["feature"], {**item, "values": []})["values"].append(item["importance"])
        ranked = sorted([{key: value for key, value in item.items() if key != "values"} | {"importance": float(np.mean(item["values"]))}
            for item in mean_by_feature.values()], key=lambda item: item["importance"], reverse=True)
        for rank, item in enumerate(ranked, 1): item["rank"] = rank
        importance[eid] = {"aggregation": "intraday positions summed; static context appended once; mean across four folds", "all_features": ranked}
    comparisons = build_group_comparisons(results, config)
    stage = comparisons["stage1_all_stocks"]["comparison"]
    consistent = sum(value["macro_f1"]["mean"] > 0 and value["macro_f1"]["worst"] > 0
        and value["macro_auc"]["mean"] > 0 and value["macro_auc"]["worst"] > 0
        and value["macro_f1"]["standard_deviation"] <= 0 and value["macro_auc"]["standard_deviation"] <= 0
        for value in stage.values())
    recommendation = ("Feature Set B 在平均、最差 Fold 與穩定度上呈現跨模型一致改善，可保留為後續研究候選；仍不啟動 Controlled Validation。"
        if consistent >= 2 else "Feature Set B 尚未在平均、最差 Fold 與穩定度呈現跨模型一致改善；先保留實驗結果，維持 Feature Set A 為 Baseline，不啟動 Controlled Validation。")
    fix_audit = {"daily_context_repeated_across_timesteps": False, "intraday_feature_count": len(plan["features"]),
        "context_feature_count_A": len(datasets["A_all_stocks"].context_feature_names),
        "context_feature_count_B": len(datasets["B_all_stocks"].context_feature_names),
        "rf_xgb_context_appended_once": True, "gru_context_after_sequence_encoder": True,
        "intraday_scaler_fit_fold_train_only": True, "context_scaler_fit_fold_train_only": all(context_scaler_checks),
        "controlled_validation_used": False, "development_used": False, "forbidden_roles_used": False}
    report = {"report_name": "V4 Training-only Research Framework Fix1", "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "experiment_design": {"enabled_experiments": enabled, "model_settings": config["models"],
            "walk_forward": "4-fold expanding-window", "common_sample_alignment": alignment,
            "training_data_load": load_audit, "selection_uses_trading_return": False},
        "feature_sets": config["feature_sets"], "feature_definitions": causal_feature_definitions(),
        "feature_analysis": analyses, "rf_feature_importance": importance, "results": results,
        "comparison_groups": comparisons,
        "industry_exploratory": {"status": "exploratory", "minimum_reliable_members": 3,
            "market_proxy": "current research universe mean", "formal_experiment_run": False},
        "findings": ["Static daily context 每個 sample 只附加一次，未複製到 24 個 timestep。",
            f"A/B common samples：{alignment['stage1_all_stocks']['common_samples']}；labels_identical=true；future return error=0。",
            f"平均、最差 Fold 與穩定度同時改善的模型數：{consistent}/3。"],
        "recommended_next_experiment": recommendation, "framework_fix_audit": fix_audit,
        "execution_confirmation": {"training_only": True, "controlled_validation_used": False,
            "development_used": False, "forbidden_roles_used": False, "label_changed": False,
            "signal_anchor_changed": False, "model_parameters_changed": False, "trading_rules_changed": False,
            "v3_overwritten": False, "candidate_overwritten": False, "return_used_for_selection": False}}
    OUT.mkdir(parents=True, exist_ok=True)
    json_path, html_path, audit_path = OUT / "research_results.json", OUT / "research_report.html", OUT / "framework_fix_audit.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    html_path.write_text(render(report), encoding="utf-8")
    audit_path.write_text(json.dumps(fix_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"comparison_groups": comparisons, "recommendation": recommendation,
        "framework_fix_audit": fix_audit}, ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}\nHTML: {html_path}\nAUDIT: {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

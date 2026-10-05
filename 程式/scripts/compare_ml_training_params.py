"""ML V3 Training-only 小範圍參數比較。

目的：
- 只使用 2023~2024 Training + 4-fold Expanding-window Walk-forward。
- RF / XGBoost / GRU 各測少量候選參數。
- 不讀 2025 Controlled Validation、2026 Development、Holdout、Final OOS。
- 不修改 V3 Features、Label、交易規則。
- 不依交易報酬選模型。
- 不自動覆寫原本 config，也不建立 V4。

輸出：
- exports/ml_training_param_compare_20261005/param_compare_results.json
- exports/ml_training_param_compare_20261005/param_compare_report.html
"""
from __future__ import annotations

import copy
import html
import json
import math
import sys
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.models import run_multiclass_model
from ml.trading_pipeline import load_trading_plan, prepare_trading_data
from ml.walk_forward import (
    aggregate_fold_metrics,
    build_walk_forward_fold,
    load_walk_forward_protocol,
)

PLAN = ROOT / "configs/ml_trading_v3_20261004.json"
PROTOCOL = ROOT / "configs/ml_training_protocol_v3.json"
DATABASE = ROOT / "data/market_data.sqlite3"
OUT = ROOT / "exports/ml_training_param_compare_20261005"

LABELS = ("SELL", "HOLD", "BUY")


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _candidate_sets(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """從目前 V3 base params 建立少量、可解釋的候選設定。"""
    output: dict[str, list[dict[str, Any]]] = {}

    # ---------- Random Forest ----------
    rf = copy.deepcopy(plan["models"]["random_forest"])
    rf_depth = _int(rf.get("max_depth"), 8)
    rf_leaf = _int(rf.get("min_samples_leaf"), 10)
    rf_trees = _int(rf.get("n_estimators"), 100)

    rf_conservative = copy.deepcopy(rf)
    rf_conservative.update({
        "n_estimators": max(rf_trees, 200),
        "max_depth": max(3, rf_depth - 2),
        "min_samples_leaf": max(rf_leaf + 5, 10),
    })

    rf_flexible = copy.deepcopy(rf)
    rf_flexible.update({
        "n_estimators": max(rf_trees, 200),
        "max_depth": rf_depth + 2,
        "min_samples_leaf": max(3, rf_leaf // 2),
    })

    output["random_forest"] = [
        {"name": "baseline", "params": rf},
        {"name": "conservative", "params": rf_conservative},
        {"name": "flexible", "params": rf_flexible},
    ]

    # ---------- XGBoost ----------
    xgb = copy.deepcopy(plan["models"]["xgboost"])
    xgb_depth = _int(xgb.get("max_depth"), 4)
    xgb_trees = _int(xgb.get("n_estimators"), 100)
    xgb_lr = _num(xgb.get("learning_rate"), 0.1)

    xgb_regularized = copy.deepcopy(xgb)
    xgb_regularized.update({
        "n_estimators": max(xgb_trees, 180),
        "max_depth": max(2, xgb_depth - 1),
        "learning_rate": max(0.01, xgb_lr * 0.7),
        "subsample": min(_num(xgb.get("subsample"), 1.0), 0.85),
        "colsample_bytree": min(_num(xgb.get("colsample_bytree"), 1.0), 0.85),
        "reg_lambda": max(_num(xgb.get("reg_lambda"), 1.0), 2.0),
    })

    xgb_flexible = copy.deepcopy(xgb)
    xgb_flexible.update({
        "n_estimators": max(xgb_trees, 160),
        "max_depth": xgb_depth + 1,
        "learning_rate": max(0.01, xgb_lr * 0.8),
    })

    output["xgboost"] = [
        {"name": "baseline", "params": xgb},
        {"name": "regularized", "params": xgb_regularized},
        {"name": "flexible", "params": xgb_flexible},
    ]

    # ---------- GRU ----------
    gru = copy.deepcopy(plan["models"]["gru"])
    hidden = _int(gru.get("hidden_size"), 64)
    dropout = _num(gru.get("dropout"), 0.2)
    lr = _num(gru.get("learning_rate"), 0.001)

    gru_smaller = copy.deepcopy(gru)
    gru_smaller.update({
        "hidden_size": max(16, hidden // 2),
    })

    gru_regularized = copy.deepcopy(gru)
    gru_regularized.update({
        "dropout": min(0.5, dropout + 0.10),
        "learning_rate": max(0.0001, lr * 0.5),
    })

    output["gru"] = [
        {"name": "baseline", "params": gru},
        {"name": "smaller", "params": gru_smaller},
        {"name": "regularized", "params": gru_regularized},
    ]

    return output


def _score(summary: dict[str, Any]) -> tuple[float, ...]:
    """沿用目前 Candidate 選擇精神：先看最差 Fold，再看平均，再看穩定度。"""
    f1 = summary["macro_f1"]
    auc = summary["macro_roc_auc_ovr"]

    def safe(value: Any, default: float = -1.0) -> float:
        return default if value is None else float(value)

    return (
        safe(f1["worst"]),
        safe(f1["mean"]),
        safe(auc["worst"]),
        safe(auc["mean"]),
        -safe(f1["standard_deviation"], 999.0),
        -safe(auc["standard_deviation"], 999.0),
    )


def _run_one(
    model_name: str,
    config_name: str,
    params: dict[str, Any],
    raw: Any,
    protocol: dict[str, Any],
) -> dict[str, Any]:
    fold_rows: list[dict[str, Any]] = []
    gru_epochs: list[int] = []

    for fold_spec in protocol["walk_forward"]["folds"]:
        fold = build_walk_forward_fold(raw, fold_spec)

        _, metrics, _ = run_multiclass_model(
            model_name,
            fold,
            params,
            evaluation_splits=("validation",),
        )

        split = metrics["splits"]["validation"]
        item = {
            "fold": int(fold_spec["fold"]),
            **split,
        }

        if model_name == "gru":
            item["epochs_completed"] = int(metrics["epochs_completed"])
            item["best_validation_loss"] = metrics["best_validation_loss"]
            gru_epochs.append(int(metrics["epochs_completed"]))

        fold_rows.append(item)

    summary = aggregate_fold_metrics(fold_rows)

    return {
        "model": model_name,
        "config": config_name,
        "params": params,
        "summary": summary,
        "gru_median_completed_epoch": int(median(gru_epochs)) if gru_epochs else None,
        "selection_score": list(_score(summary)),
    }


def _best_per_model(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in results:
        name = row["model"]
        if name not in best or tuple(row["selection_score"]) > tuple(best[name]["selection_score"]):
            best[name] = row
    return best


def _overall_best(best_by_model: dict[str, dict[str, Any]]) -> dict[str, Any]:
    # 同分時沿用目前 protocol 的 model order。
    order = ["random_forest", "xgboost", "gru"]
    rank = {name: -i for i, name in enumerate(order)}

    def score(item: tuple[str, dict[str, Any]]) -> tuple[float, ...]:
        name, row = item
        return (*tuple(row["selection_score"]), float(rank.get(name, -999)))

    return max(best_by_model.items(), key=score)[1]


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _param_diff(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(set(base) | set(candidate))
    return {
        key: candidate.get(key)
        for key in keys
        if base.get(key) != candidate.get(key)
    }


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(str(x))}</th>" for x in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{html.escape(str(x))}</td>" for x in row) + "</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _render_html(report: dict[str, Any]) -> str:
    rows = []
    for item in report["results"]:
        s = item["summary"]
        ia = s["individual_roc_auc_ovr"]
        rows.append([
            item["model"],
            item["config"],
            _fmt(s["macro_f1"]["mean"]),
            _fmt(s["macro_f1"]["worst"]),
            _fmt(s["macro_f1"]["standard_deviation"]),
            _fmt(s["macro_roc_auc_ovr"]["mean"]),
            _fmt(s["macro_roc_auc_ovr"]["worst"]),
            _fmt(s["macro_roc_auc_ovr"]["standard_deviation"]),
            _fmt(ia["SELL"]["mean"]),
            _fmt(ia["HOLD"]["mean"]),
            _fmt(ia["BUY"]["mean"]),
            _fmt(item["gru_median_completed_epoch"]),
        ])

    best_rows = []
    for model, item in report["best_per_model"].items():
        s = item["summary"]
        best_rows.append([
            model,
            item["config"],
            _fmt(s["macro_f1"]["mean"]),
            _fmt(s["macro_f1"]["worst"]),
            _fmt(s["macro_roc_auc_ovr"]["mean"]),
            _fmt(s["macro_roc_auc_ovr"]["worst"]),
            json.dumps(item["parameter_changes_from_baseline"], ensure_ascii=False),
        ])

    overall = report["overall_best_training_only"]

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>ML V3 Training-only 參數比較</title>
<style>
body{{font-family:system-ui,'Noto Sans TC',sans-serif;max-width:1400px;margin:30px auto;padding:0 16px;color:#182433}}
table{{border-collapse:collapse;width:100%;margin:16px 0;font-size:14px}}
th,td{{border:1px solid #d9e1e8;padding:7px;text-align:right}}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){{text-align:left}}
.card{{background:#f4f7fa;border-radius:12px;padding:16px;margin:16px 0}}
.warn{{background:#fff4cf}}
</style>
</head>
<body>
<h1>ML V3 Training-only 小範圍參數比較</h1>

<div class="card">
<strong>資料限制：</strong>
只使用 2023–2024 Training 的 4-fold Expanding-window。
沒有讀取 2025 Controlled Validation、2026 Development、Holdout 或 Final OOS。
沒有使用交易報酬選參數。
</div>

<h2>全部候選參數結果</h2>
{_html_table(
    ["模型","設定","F1平均","F1最差","F1標準差","AUC平均","AUC最差","AUC標準差",
     "SELL AUC","HOLD AUC","BUY AUC","GRU median epoch"],
    rows
)}

<h2>每個模型 Training-only 最佳設定</h2>
{_html_table(
    ["模型","最佳設定","F1平均","F1最差","AUC平均","AUC最差","相較 Baseline 修改"],
    best_rows
)}

<div class="card warn">
<strong>Training-only 暫時最佳：</strong>
{html.escape(overall["model"])} / {html.escape(overall["config"])}<br>
這不是 Final 結論，也不是正式 Validation 結果。
</div>

<p>排序依序考慮最差 Fold Macro F1、平均 Macro F1、最差 Fold Macro AUC、
平均 Macro AUC、F1/AUC 穩定度；沒有依交易報酬排名。</p>
</body>
</html>
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    plan = load_trading_plan(PLAN)
    protocol = load_walk_forward_protocol(PROTOCOL)

    # 最重要的防線：只讀 train。
    raw, _ = prepare_trading_data(
        DATABASE,
        plan,
        roles=("train",),
        apply_scaling=False,
        workflow_stage="parameter_compare_training_only",
    )

    candidate_sets = _candidate_sets(plan)
    results: list[dict[str, Any]] = []

    total = sum(len(items) for items in candidate_sets.values())
    current = 0

    for model_name, configs in candidate_sets.items():
        for config in configs:
            current += 1
            print(f"[{current}/{total}] {model_name} / {config['name']} ...")

            row = _run_one(
                model_name,
                config["name"],
                config["params"],
                raw,
                protocol,
            )

            base = plan["models"][model_name]
            row["parameter_changes_from_baseline"] = _param_diff(base, config["params"])
            results.append(row)

            s = row["summary"]
            print(
                "  Macro F1 mean/worst = "
                f"{s['macro_f1']['mean']:.4f}/{s['macro_f1']['worst']:.4f} | "
                "Macro AUC mean/worst = "
                f"{s['macro_roc_auc_ovr']['mean']:.4f}/{s['macro_roc_auc_ovr']['worst']:.4f}"
            )

    best = _best_per_model(results)
    overall = _overall_best(best)

    report = {
        "report_name": "ML V3 Training-only Parameter Comparison",
        "rules": {
            "training_only": True,
            "walk_forward": "4-fold expanding-window",
            "return_used_for_selection": False,
            "controlled_validation_used": False,
            "development_used": False,
            "forbidden_roles_used": False,
            "features_changed": False,
            "label_changed": False,
            "trading_rules_changed": False,
            "automatic_brute_force_search": False,
        },
        "results": results,
        "best_per_model": best,
        "overall_best_training_only": overall,
    }

    json_path = OUT / "param_compare_results.json"
    html_path = OUT / "param_compare_report.html"

    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(_render_html(report), encoding="utf-8")

    print()
    print("Training-only 參數比較完成")
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")
    print(
        "暫時最佳："
        f"{overall['model']} / {overall['config']} "
        "（只代表 Training walk-forward）"
    )
    print("沒有讀取 Controlled Validation / Development / forbidden data。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""ML V3 Training-only 診斷。

只讀取既有 Walk-forward 結果與 Candidate 模型，不重新訓練，
不讀取 2025 Controlled Validation、2026 Development、
Additional Holdout 或 Final Out-of-Sample。

輸出：
- training_diagnosis.json
- training_diagnosis.html
"""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import joblib
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATE_DIR = ROOT / "exports/ml_v3_candidate_20261005"

LABELS = ["SELL", "HOLD", "BUY"]

GROUPS = {
    "科技組": ["2303", "2330"],
    "金融組": ["2881", "2882"],
    "其他參考": ["2002", "2412"],
}


def _safe_stats(values: list[float | None]) -> dict[str, Any]:
    usable = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not usable:
        return {"mean": None, "std": None, "worst": None, "best": None, "n": 0}
    return {
        "mean": float(mean(usable)),
        "std": float(stdev(usable)) if len(usable) > 1 else 0.0,
        "worst": float(min(usable)),
        "best": float(max(usable)),
        "n": len(usable),
    }


def _sum_matrix(a: np.ndarray, b: Any) -> np.ndarray:
    return a + np.asarray(b, dtype=int)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _aggregate_stock(selected_summary: dict[str, Any], stock_code: str) -> dict[str, Any]:
    f1s: list[float | None] = []
    aucs: list[float | None] = []
    individual = {name: [] for name in LABELS}

    confusion = np.zeros((3, 3), dtype=int)
    actual_counts = np.zeros(3, dtype=int)
    predicted_counts = np.zeros(3, dtype=int)
    dominant_prediction_folds: list[dict[str, Any]] = []

    for fold in selected_summary["folds"]:
        item = fold["per_stock"].get(stock_code)
        if not item:
            continue

        f1s.append(item.get("macro_f1"))
        aucs.append(item.get("macro_roc_auc_ovr"))

        for name in LABELS:
            individual[name].append(item.get("individual_roc_auc_ovr", {}).get(name))

        confusion = _sum_matrix(confusion, item["confusion_matrix"])

        for idx in range(3):
            actual_counts[idx] += int(item["actual_class_counts"].get(str(idx), 0))
            predicted_counts[idx] += int(item["predicted_class_counts"].get(str(idx), 0))

        predicted_props = item.get("predicted_class_proportions", {})
        if predicted_props:
            dominant_idx = max(range(3), key=lambda i: float(predicted_props.get(str(i), 0.0)))
            dominant_prop = float(predicted_props.get(str(dominant_idx), 0.0))
            if dominant_prop >= 0.90:
                dominant_prediction_folds.append({
                    "fold": int(fold["fold"]),
                    "class": LABELS[dominant_idx],
                    "proportion": dominant_prop,
                })

    total_actual = int(actual_counts.sum())
    total_pred = int(predicted_counts.sum())

    return {
        "stock_code": stock_code,
        "macro_f1": _safe_stats(f1s),
        "macro_auc": _safe_stats(aucs),
        "individual_auc": {name: _safe_stats(values) for name, values in individual.items()},
        "confusion_matrix": confusion.tolist(),
        "actual_class_counts": {LABELS[i]: int(actual_counts[i]) for i in range(3)},
        "predicted_class_counts": {LABELS[i]: int(predicted_counts[i]) for i in range(3)},
        "actual_class_proportions": {
            LABELS[i]: (float(actual_counts[i] / total_actual) if total_actual else None)
            for i in range(3)
        },
        "predicted_class_proportions": {
            LABELS[i]: (float(predicted_counts[i] / total_pred) if total_pred else None)
            for i in range(3)
        },
        "dominant_prediction_folds": dominant_prediction_folds,
    }


def _aggregate_group(
    selected_summary: dict[str, Any],
    stock_summaries: dict[str, dict[str, Any]],
    stock_codes: list[str],
) -> dict[str, Any]:
    # 指標採「股票 × Fold」平均，不拿交易報酬排名。
    f1s: list[float | None] = []
    aucs: list[float | None] = []
    individual = {name: [] for name in LABELS}
    confusion = np.zeros((3, 3), dtype=int)
    actual_counts = np.zeros(3, dtype=int)
    predicted_counts = np.zeros(3, dtype=int)

    for fold in selected_summary["folds"]:
        for code in stock_codes:
            item = fold["per_stock"].get(code)
            if not item:
                continue
            f1s.append(item.get("macro_f1"))
            aucs.append(item.get("macro_roc_auc_ovr"))
            for name in LABELS:
                individual[name].append(item.get("individual_roc_auc_ovr", {}).get(name))

    for code in stock_codes:
        summary = stock_summaries[code]
        confusion += np.asarray(summary["confusion_matrix"], dtype=int)
        for idx, name in enumerate(LABELS):
            actual_counts[idx] += int(summary["actual_class_counts"][name])
            predicted_counts[idx] += int(summary["predicted_class_counts"][name])

    total_actual = int(actual_counts.sum())
    total_pred = int(predicted_counts.sum())

    return {
        "stocks": stock_codes,
        "macro_f1": _safe_stats(f1s),
        "macro_auc": _safe_stats(aucs),
        "individual_auc": {name: _safe_stats(values) for name, values in individual.items()},
        "confusion_matrix": confusion.tolist(),
        "actual_class_proportions": {
            LABELS[i]: (float(actual_counts[i] / total_actual) if total_actual else None)
            for i in range(3)
        },
        "predicted_class_proportions": {
            LABELS[i]: (float(predicted_counts[i] / total_pred) if total_pred else None)
            for i in range(3)
        },
    }


def _feature_importance(model: Any, features: list[str]) -> dict[str, Any]:
    if not hasattr(model, "feature_importances_"):
        return {
            "available": False,
            "reason": "Candidate 模型沒有 feature_importances_。",
        }

    raw = np.asarray(model.feature_importances_, dtype=float)
    feature_count = len(features)

    if feature_count == 0 or len(raw) % feature_count != 0:
        return {
            "available": False,
            "reason": (
                f"模型輸入維度 {len(raw)} 無法被 Feature 數 {feature_count} 整除，"
                "無法還原 sequence × feature。"
            ),
        }

    sequence_bars = len(raw) // feature_count
    matrix = raw.reshape(sequence_bars, feature_count)

    feature_scores = matrix.sum(axis=0)
    temporal_scores = matrix.sum(axis=1)

    feature_rows = sorted(
        [
            {"feature": features[i], "importance": float(feature_scores[i])}
            for i in range(feature_count)
        ],
        key=lambda row: row["importance"],
        reverse=True,
    )

    temporal_rows = [
        {
            "sequence_position": int(i + 1),
            "importance": float(temporal_scores[i]),
        }
        for i in range(sequence_bars)
    ]

    return {
        "available": True,
        "sequence_bars": int(sequence_bars),
        "feature_count": int(feature_count),
        "top_features": feature_rows[:15],
        "all_features": feature_rows,
        "temporal_importance": temporal_rows,
    }


def _build_flags(
    stock_summaries: dict[str, dict[str, Any]],
    group_summaries: dict[str, dict[str, Any]],
    selected_summary: dict[str, Any],
) -> list[str]:
    flags: list[str] = []

    sell_auc = selected_summary["individual_roc_auc_ovr"]["SELL"]["mean"]
    hold_auc = selected_summary["individual_roc_auc_ovr"]["HOLD"]["mean"]
    buy_auc = selected_summary["individual_roc_auc_ovr"]["BUY"]["mean"]

    if sell_auc is not None and sell_auc < hold_auc and sell_auc < buy_auc:
        flags.append(
            f"整體 SELL AUC 最弱（{sell_auc:.4f}），低於 HOLD（{hold_auc:.4f}）與 BUY（{buy_auc:.4f}）。"
        )

    for code, summary in stock_summaries.items():
        auc_mean = summary["macro_auc"]["mean"]
        if auc_mean is not None and auc_mean < 0.50:
            flags.append(f"{code} 的跨 Fold Macro AUC 平均低於 0.50（{auc_mean:.4f}）。")
        if summary["dominant_prediction_folds"]:
            for item in summary["dominant_prediction_folds"]:
                flags.append(
                    f"{code} Fold {item['fold']} 有預測塌縮傾向："
                    f"{item['class']} 佔 {item['proportion']:.1%}。"
                )

    tech = group_summaries.get("科技組", {}).get("macro_auc", {}).get("mean")
    finance = group_summaries.get("金融組", {}).get("macro_auc", {}).get("mean")
    if tech is not None and finance is not None:
        flags.append(
            f"產業組 Macro AUC（僅供診斷，不做最終結論）：科技組 {tech:.4f}、金融組 {finance:.4f}。"
        )

    return flags


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(str(x))}</th>" for x in headers)
    trs = []
    for row in rows:
        tds = "".join(f"<td>{html.escape(str(x))}</td>" for x in row)
        trs.append(f"<tr>{tds}</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"


def _render_html(report: dict[str, Any]) -> str:
    stock_rows = []
    for code, item in report["stocks"].items():
        stock_rows.append([
            code,
            _pct(item["macro_f1"]["mean"]),
            _pct(item["macro_auc"]["mean"]),
            _pct(item["individual_auc"]["SELL"]["mean"]),
            _pct(item["individual_auc"]["HOLD"]["mean"]),
            _pct(item["individual_auc"]["BUY"]["mean"]),
        ])

    group_rows = []
    for name, item in report["groups"].items():
        group_rows.append([
            name,
            ", ".join(item["stocks"]),
            _pct(item["macro_f1"]["mean"]),
            _pct(item["macro_auc"]["mean"]),
            _pct(item["individual_auc"]["SELL"]["mean"]),
            _pct(item["individual_auc"]["HOLD"]["mean"]),
            _pct(item["individual_auc"]["BUY"]["mean"]),
        ])

    feature_rows = []
    importance = report["feature_importance"]
    if importance.get("available"):
        for rank, item in enumerate(importance["top_features"], 1):
            feature_rows.append([
                str(rank),
                item["feature"],
                f"{item['importance']:.6f}",
            ])

    flags_html = "".join(f"<li>{html.escape(flag)}</li>" for flag in report["diagnostic_flags"])

    feature_section = (
        "<p>Feature Importance 無法取得。</p>"
        if not importance.get("available")
        else (
            f"<p>Random Forest 輸入還原為 {importance['sequence_bars']} 個時間位置 × "
            f"{importance['feature_count']} 個 Features。</p>"
            + _html_table(["排名", "Feature", "Importance"], feature_rows)
        )
    )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>ML V3 Training-only Diagnosis</title>
<style>
body{{font-family:system-ui,'Noto Sans TC',sans-serif;max-width:1200px;margin:30px auto;padding:0 16px;color:#182433}}
table{{border-collapse:collapse;width:100%;margin:14px 0}}
th,td{{border:1px solid #d9e1e8;padding:8px;text-align:right}}
th:first-child,td:first-child{{text-align:left}}
.card{{background:#f4f7fa;border-radius:12px;padding:16px;margin:16px 0}}
.warn{{background:#fff4cf}}
code{{background:#edf2f7;padding:2px 5px}}
</style>
</head>
<body>
<h1>ML V3 Training-only 診斷</h1>
<div class="card">
<p><strong>資料限制：</strong>本報告只使用既有 2023–2024 Walk-forward 結果與已重訓 Candidate 模型，
不讀取 Controlled Validation、Development、Holdout 或 Final OOS。</p>
<p><strong>Candidate：</strong>{html.escape(report["selected_model"])}</p>
</div>

<h2>單股跨 Fold 指標</h2>
{_html_table(
    ["股票", "Macro F1 平均", "Macro AUC 平均", "SELL AUC", "HOLD AUC", "BUY AUC"],
    stock_rows
)}

<h2>產業分組診斷</h2>
{_html_table(
    ["群組", "股票", "Macro F1 平均", "Macro AUC 平均", "SELL AUC", "HOLD AUC", "BUY AUC"],
    group_rows
)}

<h2>Random Forest Feature Importance</h2>
{feature_section}

<h2>自動診斷提醒</h2>
<div class="card warn"><ul>{flags_html}</ul></div>

<p>注意：本報告是 Training-only diagnosis，不應直接用來宣稱最終泛化能力，也沒有以交易報酬挑選模型。</p>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        default=DEFAULT_CANDIDATE_DIR,
        help="包含 walk_forward_results.json 與 model.joblib 的 Candidate 資料夾",
    )
    args = parser.parse_args()

    candidate_dir = args.candidate_dir.resolve()
    results_path = candidate_dir / "walk_forward_results.json"
    model_path = candidate_dir / "model.joblib"

    if not results_path.exists():
        raise FileNotFoundError(f"找不到：{results_path}")
    if not model_path.exists():
        raise FileNotFoundError(f"找不到：{model_path}")

    manifest = json.loads(results_path.read_text(encoding="utf-8"))

    # 防止誤把已使用外部資料的結果當成純 Training 診斷。
    if manifest.get("controlled_validation_used") is not False:
        raise RuntimeError("這份 Candidate 已標示使用 Controlled Validation，不符合 Training-only 診斷。")
    if manifest.get("development_used") is not False:
        raise RuntimeError("這份 Candidate 已標示使用 Development，不符合 Training-only 診斷。")
    if manifest.get("forbidden_roles_used") is not False:
        raise RuntimeError("這份 Candidate 曾使用 forbidden data，停止診斷。")

    selected_model = manifest["selected_model"]
    if selected_model not in manifest["walk_forward"]:
        raise RuntimeError(f"walk_forward 裡找不到 Candidate 模型：{selected_model}")

    selected_summary = manifest["walk_forward"][selected_model]
    stock_codes = sorted({
        code
        for fold in selected_summary["folds"]
        for code in fold.get("per_stock", {})
    })

    stock_summaries = {
        code: _aggregate_stock(selected_summary, code)
        for code in stock_codes
    }

    group_summaries = {}
    for name, codes in GROUPS.items():
        available_codes = [code for code in codes if code in stock_summaries]
        if available_codes:
            group_summaries[name] = _aggregate_group(
                selected_summary,
                stock_summaries,
                available_codes,
            )

    model = joblib.load(model_path)
    importance = _feature_importance(model, manifest.get("features", []))

    report = {
        "report_name": "ML V3 Training-only Diagnosis",
        "source": str(results_path),
        "selected_model": selected_model,
        "data_usage": {
            "training_only": True,
            "controlled_validation_used": False,
            "development_used": False,
            "forbidden_roles_used": False,
        },
        "candidate_overall": {
            "macro_f1": selected_summary["macro_f1"],
            "macro_auc": selected_summary["macro_roc_auc_ovr"],
            "individual_auc": selected_summary["individual_roc_auc_ovr"],
        },
        "stocks": stock_summaries,
        "groups": group_summaries,
        "feature_importance": importance,
    }

    report["diagnostic_flags"] = _build_flags(
        stock_summaries,
        group_summaries,
        selected_summary,
    )

    json_path = candidate_dir / "training_diagnosis.json"
    html_path = candidate_dir / "training_diagnosis.html"

    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(_render_html(report), encoding="utf-8")

    print("Training-only 診斷完成")
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")
    print("沒有重新訓練，也沒有讀取 2025/2026 或 forbidden 資料。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

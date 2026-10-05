"""Training-only audit of V3 feature values, indexes, and sequence layout."""
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

from ml.trading_pipeline import _intraday_features, _read_split, load_trading_plan, prepare_trading_data

PLAN_PATH = ROOT / "configs/ml_trading_v3_20261004.json"
DATABASE = ROOT / "data/market_data.sqlite3"
DIAGNOSIS_PATH = ROOT / "exports/ml_label_feature_diagnosis_20261005/label_feature_diagnosis.json"
OUT = ROOT / "exports/ml_feature_audit_20261005"
EXPECTED_SEQUENCE_BARS = 24
EXPECTED_FEATURES = 24


def finite_stats(values: np.ndarray) -> dict[str, Any]:
    a = np.asarray(values, dtype=float).reshape(-1)
    nan = np.isnan(a)
    finite = a[np.isfinite(a)]
    zeros = finite == 0
    if not len(finite):
        return {"count": int(len(a)), "finite_count": 0, "min": None, "max": None, "mean": None,
                "median": None, "standard_deviation": None, "zero_ratio": None,
                "nan_ratio": float(nan.mean()), "infinite_ratio": float(np.isinf(a).mean())}
    return {"count": int(len(a)), "finite_count": int(len(finite)), "min": float(finite.min()),
            "max": float(finite.max()), "mean": float(finite.mean()), "median": float(np.median(finite)),
            "standard_deviation": float(finite.std(ddof=1)) if len(finite) > 1 else 0.0,
            "zero_ratio": float(zeros.mean()), "nan_ratio": float(nan.mean()),
            "infinite_ratio": float(np.isinf(a).mean())}


def quality_flags(values: np.ndarray) -> dict[str, Any]:
    a = np.asarray(values, dtype=float).reshape(-1)
    finite = a[np.isfinite(a)]
    stats = finite_stats(a)
    unique = int(len(np.unique(finite))) if len(finite) else 0
    abs_values = np.abs(finite)
    p99 = float(np.quantile(abs_values, .99)) if len(finite) else None
    max_abs = float(abs_values.max()) if len(finite) else None
    extreme_ratio = (max_abs / p99) if p99 and p99 > 0 else None
    constant = unique <= 1
    nearly_constant = bool(not constant and ((stats["standard_deviation"] or 0) <= 1e-10 or
                           (len(finite) and pd.Series(finite).value_counts(normalize=True).iloc[0] >= .999)))
    return {**stats, "unique_values": unique, "constant_feature": constant,
            "nearly_constant_feature": nearly_constant,
            "all_zero": bool(len(finite) and np.all(finite == 0)),
            "high_nan_ratio": bool(stats["nan_ratio"] > .01),
            "p99_absolute": p99, "max_abs_to_p99_abs": extreme_ratio,
            "extreme_value_flag": bool(extreme_ratio is not None and extreme_ratio > 20)}


def raw_ohlcv_audit(raw: pd.DataFrame) -> dict[str, Any]:
    output = {}
    for code, g in raw.groupby("stock_code", sort=True):
        numeric = g[["open", "high", "low", "close", "volume"]]
        nan_rows = numeric.isna().any(axis=1)
        invalid = ((g[["open", "high", "low", "close"]] <= 0).any(axis=1) | (g["volume"] < 0) |
                   (g["high"] < g[["open", "close", "low"]].max(axis=1)) |
                   (g["low"] > g[["open", "close", "high"]].min(axis=1)) | nan_rows)
        output[str(code)] = {"rows": int(len(g)), "high_equals_low_count": int((g["high"] == g["low"]).sum()),
            "high_equals_low_ratio": float((g["high"] == g["low"]).mean()),
            "close_zero_count": int((g["close"] == 0).sum()), "close_nan_count": int(g["close"].isna().sum()),
            "nan_ohlcv_rows": int(nan_rows.sum()), "invalid_ohlcv_rows": int(invalid.sum()),
            "invalid_ohlcv_ratio": float(invalid.mean())}
    return output


def flat_bar_time_audit(raw: pd.DataFrame) -> dict[str, Any]:
    frame = raw.copy()
    frame["time"] = frame["datetime"].dt.strftime("%H:%M:%S")
    flat = frame.loc[frame["high"] == frame["low"]]
    counts = flat.groupby("time").size().sort_values(ascending=False)
    closing = flat.loc[flat["time"] == "13:30:00"]
    return {"all_flat_bars": int(len(flat)), "flat_bars_by_time": {str(k): int(v) for k, v in counts.items()},
            "13_30_flat_bars": int(len(closing)),
            "13_30_flat_bars_by_stock": {str(k): int(v) for k, v in closing.groupby("stock_code").size().items()},
            "interpretation": "13:30 收盤撮合 K 棒只有單一成交價，因此 high == low；每日序列最後位置固定取到此 K 棒。"}


def mapping_audit(data, featured: pd.DataFrame, plan: dict[str, Any]) -> dict[str, Any]:
    names = list(plan["features"])
    actual_names = list(data.feature_names)
    index = names.index("range_vs_close")
    shape = list(data.X["train"].shape)
    flat_width = int(data.X["train"].reshape(len(data.X["train"]), -1).shape[1])
    expected_flat_width = int(plan["sequence_bars"] * len(names))
    meta = data.metadata["train"].reset_index(drop=True).copy()
    lookup = featured[["stock_code", "datetime", "range_vs_close"]].copy()
    lookup["signal_time"] = lookup["datetime"].map(lambda x: x.isoformat())
    lookup["stock_code"] = lookup["stock_code"].astype(str)
    joined = meta[["stock_code", "signal_time"]].merge(
        lookup[["stock_code", "signal_time", "range_vs_close"]], on=["stock_code", "signal_time"], how="left")
    tensor_last = data.X["train"][:, -1, index].astype(float)
    recomputed = joined["range_vs_close"].to_numpy(dtype=float)
    comparable = np.isfinite(recomputed)
    max_error = float(np.max(np.abs(tensor_last[comparable] - recomputed[comparable]))) if comparable.any() else None
    return {"plan_feature_names": names, "prepared_feature_names": actual_names,
            "feature_name_order_matches": names == actual_names,
            "range_vs_close_index_zero_based": index, "range_vs_close_index_one_based": index + 1,
            "tensor_shape": shape, "expected_tensor_shape_tail": [EXPECTED_SEQUENCE_BARS, EXPECTED_FEATURES],
            "sequence_feature_shape_matches": shape[1:] == [plan["sequence_bars"], len(names)],
            "flatten_order": "C-order: position 1 all features, then position 2 all features, ...",
            "flat_width": flat_width, "expected_flat_width": expected_flat_width,
            "feature_importance_reshape": [plan["sequence_bars"], len(names)],
            "reshape_is_valid": flat_width == expected_flat_width,
            "last_position_crosscheck_samples": int(comparable.sum()),
            "last_position_vs_recomputed_max_abs_error": max_error,
            "last_position_mapping_matches": bool(max_error is not None and max_error < 1e-7),
            "diagnosis_top_feature_index_method": "plan['features'].index(feature)",
            "diagnosis_index_mapping_correct": names == actual_names and max_error is not None and max_error < 1e-7}


def fmt(value: Any, digits: int = 6) -> str:
    return "—" if value is None else f"{float(value):.{digits}g}"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return "<table><thead><tr>" + "".join(f"<th>{html.escape(str(v))}</th>" for v in headers) + \
           "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in row) + "</tr>" for row in rows) + "</tbody></table>"


def render(report: dict[str, Any]) -> str:
    ohlcv_rows = [[code, x["rows"], x["high_equals_low_count"], f"{x['high_equals_low_ratio']:.2%}",
                   x["close_zero_count"], x["close_nan_count"], x["invalid_ohlcv_rows"]]
                  for code, x in report["raw_ohlcv_audit"].items()]
    stock_rows = [[code, fmt(x["min"]), fmt(x["max"]), fmt(x["mean"]), fmt(x["median"]),
                   fmt(x["standard_deviation"]), f"{x['zero_ratio']:.2%}", f"{x['nan_ratio']:.2%}"]
                  for code, x in report["range_vs_close"]["by_stock"].items()]
    pos_rows = [[x["position_one_based"], fmt(x["mean"]), fmt(x["standard_deviation"]), fmt(x["min"]),
                 fmt(x["max"]), f"{x['zero_ratio']:.2%}", "是" if x["near_zero"] else "否"]
                for x in report["range_vs_close"]["by_sequence_position"]]
    top_rows = []
    for name, x in report["top_10_feature_audit"].items():
        flags = [label for key, label in (("constant_feature", "constant"), ("nearly_constant_feature", "nearly constant"),
                 ("all_zero", "全 0"), ("high_nan_ratio", "高 NaN"), ("extreme_value_flag", "極端值")) if x[key]]
        last = x["last_position"]
        top_rows.append([name, fmt(x["min"]), fmt(x["max"]), fmt(x["mean"]), fmt(x["standard_deviation"]),
                         f"{x['zero_ratio']:.2%}", f"{x['nan_ratio']:.2%}", f"{last['zero_ratio']:.2%}",
                         fmt(last["standard_deviation"]), ", ".join(flags) or "—"])
    m = report["feature_mapping_audit"]
    answers = report["answers"]
    return f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>ML V3 Feature Audit</title><style>
body{{font-family:system-ui,'Noto Sans TC',sans-serif;max-width:1400px;margin:28px auto;padding:0 18px;background:#f6f8fb;color:#182536}}section{{background:#fff;border:1px solid #dae4ec;border-radius:12px;padding:18px;margin:16px 0}}h1,h2{{color:#0b527a}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #d5dfe8;padding:7px;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#eaf3f8}}.ok{{background:#e9f8ed}}.warn{{background:#fff1d6}}code{{background:#eef2f5;padding:2px 5px}}li{{margin:8px 0}}</style></head><body>
<h1>ML V3 Training-only Feature Audit</h1>
<section class='{"ok" if not answers["program_fix_required"] else "warn"}'><h2>結論</h2><ol>
<li>range_vs_close 計算是否正常：<strong>{answers['range_calculation_normal']}</strong></li>
<li>資料是否本身全 0：<strong>{answers['range_data_all_zero']}</strong></li>
<li>是否只有最後 position 異常：<strong>{answers['only_last_position_abnormal']}</strong></li>
<li>Feature index mapping 錯誤：<strong>{answers['feature_index_mapping_error']}</strong></li>
<li>Importance reshape 錯誤：<strong>{answers['importance_reshape_error']}</strong></li>
<li>Top 10 constant／NaN 問題：<strong>{answers['top10_constant_or_nan_problem']}</strong></li>
<li>是否需要修正程式：<strong>{answers['program_fix_required']}</strong></li></ol><p>{html.escape(answers['explanation'])}</p></section>
<section><h2>原始 OHLCV</h2>{table(['股票','Rows','high==low','比例','close=0','close NaN','invalid OHLCV'],ohlcv_rows)}</section>
<section><h2>range_vs_close 各股票</h2><p><code>(high-low)/close</code></p>{table(['股票','Min','Max','Mean','Median','Std','Zero','NaN'],stock_rows)}</section>
<section><h2>24 個 Sequence Positions</h2>{table(['Position','Mean','Std','Min','Max','Zero','接近全 0'],pos_rows)}</section>
<section><h2>Feature Index 與 Reshape</h2><pre>{html.escape(json.dumps(m, ensure_ascii=False, indent=2))}</pre></section>
<section><h2>13:30 平盤 K 棒來源</h2><pre>{html.escape(json.dumps(report['flat_bar_time_audit'], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Top 10 Features 快速檢查</h2><p>前五項統計涵蓋全部 24 positions；另列最後 position。</p>{table(['Feature','Min','Max','Mean','Std','Zero','NaN','最後位置 Zero','最後位置 Std','Flag'],top_rows)}</section>
<section><h2>限制確認</h2><pre>{html.escape(json.dumps(report['execution_confirmation'], ensure_ascii=False, indent=2))}</pre></section>
</body></html>"""


def main() -> int:
    plan = load_trading_plan(PLAN_PATH)
    diagnosis = json.loads(DIAGNOSIS_PATH.read_text(encoding="utf-8"))
    raw_kbars = _read_split(DATABASE, plan, "train")
    ohlcv = raw_ohlcv_audit(raw_kbars)
    flat_times = flat_bar_time_audit(raw_kbars)
    featured, cleaning = _intraday_features(raw_kbars, plan["features"])
    data, _ = prepare_trading_data(DATABASE, plan, roles=("train",), apply_scaling=False,
                                   workflow_stage="feature_audit_training_only")
    feature_index = plan["features"].index("range_vs_close")
    by_stock = {str(code): finite_stats(g["range_vs_close"].to_numpy())
                for code, g in featured.groupby("stock_code", sort=True)}
    positions = []
    for position in range(plan["sequence_bars"]):
        item = finite_stats(data.X["train"][:, position, feature_index])
        item.update({"position_zero_based": position, "position_one_based": position + 1,
                     "near_zero": bool(item["max"] is not None and max(abs(item["min"]), abs(item["max"])) < 1e-8)})
        positions.append(item)
    mapping = mapping_audit(data, featured, plan)
    top_names = [x["feature"] for x in diagnosis["feature_diagnosis"]["top_10"]]
    top_audit = {}
    for name in top_names:
        feature_values = data.X["train"][:, :, plan["features"].index(name)]
        top_audit[name] = quality_flags(feature_values)
        top_audit[name]["last_position"] = quality_flags(feature_values[:, -1])
    all_range = data.X["train"][:, :, feature_index]
    last_abnormal = positions[-1]["near_zero"] or positions[-1]["zero_ratio"] > .99
    earlier_normal = any(not x["near_zero"] and x["standard_deviation"] > 0 for x in positions[:-1])
    top_problem = any(x["constant_feature"] or x["all_zero"] or x["high_nan_ratio"] for x in top_audit.values())
    mapping_error = not (mapping["feature_name_order_matches"] and mapping["last_position_mapping_matches"])
    reshape_error = not mapping["reshape_is_valid"]
    range_normal = bool(np.isfinite(all_range).all() and np.nanmax(all_range) > 0 and
                        mapping["last_position_mapping_matches"])
    program_fix = mapping_error or reshape_error or top_problem or not range_normal or (last_abnormal and earlier_normal)
    answers = {"range_calculation_normal": range_normal,
        "range_data_all_zero": bool(np.all(all_range == 0)),
        "only_last_position_abnormal": bool(last_abnormal and earlier_normal),
        "feature_index_mapping_error": mapping_error, "importance_reshape_error": reshape_error,
        "top10_constant_or_nan_problem": top_problem, "program_fix_required": program_fix,
        "explanation": ("Feature index 與 reshape 正確，range_vs_close 公式也正確；但每日最後 position 固定對到 13:30 收盤撮合的一價 K 棒，因此 range_vs_close 全為 0。建議未來版本排除 13:30 一價 K 棒或改用前一根完整 K 棒作為訊號錨點；本次未修改 V3。"
                        if last_abnormal and earlier_normal and not mapping_error and not reshape_error
                        else "未發現 Feature index、reshape、NaN 或 constant 問題。")}
    report = {"report_name": "ML V3 Training-only Feature Audit",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": {"period": ["2023-01-01", "2024-12-31"], "roles_loaded": data.audit["loaded_roles"]},
        "range_vs_close": {"definition": "(high - low) / close", "calculation_source": "ml.trading_pipeline._intraday_features",
                           "by_stock": by_stock, "by_sequence_position": positions,
                           "tensor_all_positions": finite_stats(all_range)},
        "raw_ohlcv_audit": ohlcv, "flat_bar_time_audit": flat_times, "cleaning_audit": cleaning,
        "feature_mapping_audit": mapping, "top_10_feature_audit": top_audit, "answers": answers,
        "bug_fix_suggestions": ([] if not program_fix else [
            "未來新版本可排除 13:30 收盤撮合的一價 K 棒，或將訊號錨點改為 13:25 的完整 5 分 K。",
            "修改後必須重新建立 Training-only 比較；不要覆寫目前 V3 與 Candidate。",
        ]),
        "execution_confirmation": {"controlled_validation_used": False, "development_used": False,
            "forbidden_roles_used": False, "label_changed": False, "features_changed": False,
            "model_parameters_changed": False, "v4_created": False, "candidate_overwritten": False}}
    OUT.mkdir(parents=True, exist_ok=True)
    json_path, html_path = OUT / "feature_audit.json", OUT / "feature_audit.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    html_path.write_text(render(report), encoding="utf-8")
    print(json.dumps(report["answers"], ensure_ascii=False, indent=2))
    print(json.dumps(report["execution_confirmation"], ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

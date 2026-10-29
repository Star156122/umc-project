"""Audit TSMC HOLD/SELL candidate outcomes without changing the frozen strategy."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "exports/multi_strategy_dev_v1_1793077681605294200"
PREDICTION_ARTIFACT = ROOT / "exports/multi_strategy_model_gate_v1_1793077034831608600.json"
OUTPUT = ROOT / "exports/diagnostic_correctness_20261027/2330_hold_sell_audit"
DATA_DB = ROOT / "data/market_data.sqlite3"
END_DATE = "2024-12-30"  # Do not read the protected 2025 holdout.
COMMISSION = 0.001425
TAX = 0.003
MODELS = ("rf", "xgboost", "gru")
MODEL_ARTIFACT_KEYS = {"rf": "rf", "xgboost": "xgboost", "gru": "gru"}
TAIPEI = ZoneInfo("Asia/Taipei")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)


def session_bars() -> dict[str, dict[str, float]]:
    start = int(datetime(2024, 6, 1, tzinfo=TAIPEI).timestamp())
    end = int(datetime(2024, 12, 31, tzinfo=TAIPEI).timestamp())
    with sqlite3.connect(f"file:{DATA_DB.as_posix()}?mode=ro", uri=True) as connection:
        data = connection.execute(
            "SELECT kbar_timestamp, open, close FROM market_kbars "
            "WHERE stock_code='2330' AND freq_minutes=5 AND kbar_timestamp>=? "
            "AND kbar_timestamp<? ORDER BY kbar_timestamp",
            (start, end),
        ).fetchall()
    daily: dict[str, dict[str, float]] = {}
    for stamp, opening, closing in data:
        day = datetime.fromtimestamp(int(stamp), TAIPEI).date().isoformat()
        daily.setdefault(day, {"open": float(opening), "close": float(closing)})
        daily[day]["close"] = float(closing)
    return daily


def summarize(values: list[float]) -> dict:
    return {
        "count": len(values),
        "mean_net_return_pct": 100 * mean(values) if values else None,
        "median_net_return_pct": 100 * median(values) if values else None,
        "positive_net_return_count": sum(value > 0 for value in values),
        "positive_net_return_rate_pct": (
            100 * sum(value > 0 for value in values) / len(values) if values else None
        ),
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    technical = read_csv(RUN / "technical_candidate_diagnostics.csv")
    candidate_rows = read_csv(RUN / "gate_candidate_diagnostics.csv")
    runtime_gates = read_csv(RUN / "gate_decisions.csv")
    risk_rows = read_csv(RUN / "risk_decisions.csv")
    with PREDICTION_ARTIFACT.open("r", encoding="utf-8") as handle:
        artifact = json.load(handle)

    # The runtime gate logs use technical_signal_time; older diagnostics tried
    # to join on technical_earliest_entry_time and falsely marked decisions unknown.
    runtime_by_key = {
        (row["strategy_id"], row.get("technical_signal_time", "")): row
        for row in runtime_gates
    }
    eligible = [row for row in technical if row.get("market_regime_pass", "").lower() == "true"]
    eligible_dates = sorted({row["candidate_entry_time"][:10] for row in eligible})
    predictions = {}
    for model in MODELS:
        for row in artifact["prediction_rows_by_strategy"][MODEL_ARTIFACT_KEYS[model]]:
            if str(row["stock_code"]).zfill(4) == "2330" and row["entry_date"] in eligible_dates:
                predictions[(model, row["entry_date"])] = row

    sessions = session_bars()
    ordered_days = sorted(sessions)
    day_index = {day: i for i, day in enumerate(ordered_days)}
    actual_targets = {}
    prediction_audit_rows = []
    seen_prediction_keys = set()
    for model in MODELS:
        for candidate_date in eligible_dates:
            prediction = predictions.get((model, candidate_date))
            if prediction is None:
                continue
            key = (model, prediction["signal_date"], prediction["entry_date"])
            if key in seen_prediction_keys:
                continue
            seen_prediction_keys.add(key)
            signal_date = prediction["signal_date"]
            signal_index = day_index.get(signal_date)
            status = "EVALUATED"
            target_date = None
            net_return = None
            actual_class = None
            if signal_index is None or signal_index + 3 >= len(ordered_days):
                status = "TARGET_OUTSIDE_2024-12-30_SCOPE"
            else:
                expected_entry = ordered_days[signal_index + 1]
                target_date = ordered_days[signal_index + 3]
                if expected_entry != prediction["entry_date"]:
                    status = "ENTRY_SESSION_ALIGNMENT_ERROR"
                elif target_date > END_DATE:
                    status = "TARGET_OUTSIDE_2024-12-30_SCOPE"
                else:
                    entry_open = sessions[expected_entry]["open"]
                    target_close = sessions[target_date]["close"]
                    net_return = (
                        target_close * (1.0 - COMMISSION - TAX)
                        / (entry_open * (1.0 + COMMISSION)) - 1.0
                    )
                    actual_class = "SELL" if net_return <= -0.01 else "BUY" if net_return >= 0.01 else "HOLD"
            actual_targets[(model, candidate_date)] = {
                "actual_target_date": target_date,
                "actual_3_session_net_return_pct": None if net_return is None else 100 * net_return,
                "actual_3_session_label": actual_class,
                "label_status": status,
            }
            prediction_audit_rows.append({
                "model": model,
                "signal_date": signal_date,
                "entry_date": prediction["entry_date"],
                "target_date": target_date or "",
                "predicted_class": prediction["prediction"],
                "prob_buy": prediction["prob_buy"],
                "prob_hold": prediction["prob_hold"],
                "prob_sell": prediction["prob_sell"],
                "actual_3_session_net_return_pct": "" if net_return is None else round(100 * net_return, 6),
                "actual_3_session_label": actual_class or "",
                "label_status": status,
            })

    corrected_rows = []
    for source in candidate_rows:
        row = dict(source)
        model = row["strategy_id"]
        gate = runtime_by_key.get((model, row.get("candidate_entry_time", "")))
        if model in MODELS and gate is not None:
            action = gate.get("hybrid_action", "")
            row["gate_actually_evaluated"] = "True"
            row["gate_decision"] = action
            if action == "ENTRY":
                row["not_entered_reason"] = "ENTERED" if row.get("entered", "").lower() == "true" else "ENTRY_PASSED_BUT_NO_FILL_REVIEW"
            else:
                row["not_entered_reason"] = action
        if model in MODELS:
            extra = actual_targets.get((model, row["candidate_entry_time"][:10]), {})
            row.update(extra)
        corrected_rows.append(row)
    write_csv(OUTPUT / "2330_hold_sell_candidate_detail.csv", corrected_rows)
    write_csv(OUTPUT / "2330_model_three_session_outcomes.csv", prediction_audit_rows)

    runtime_summary = {}
    label_summary = {}
    event_summary = {}
    for model in MODELS:
        decisions = [row for row in runtime_gates if row["strategy_id"] == model]
        runtime_summary[model] = dict(Counter(row.get("hybrid_action", "") for row in decisions))
        for prediction_class in ("BUY", "HOLD", "SELL"):
            unique_rows = [
                row for row in prediction_audit_rows
                if row["model"] == model and row["predicted_class"] == prediction_class
                and row["label_status"] == "EVALUATED"
            ]
            values = [float(row["actual_3_session_net_return_pct"]) / 100 for row in unique_rows]
            label_summary[f"{model}:{prediction_class}"] = {
                **summarize(values),
                "actual_labels": dict(Counter(row["actual_3_session_label"] for row in unique_rows)),
                "unique_prediction_dates": len(unique_rows),
            }
            event_rows = [
                row for row in corrected_rows
                if row["strategy_id"] == model and row.get("predicted_class") == prediction_class
                and row.get("outcome_status") == "EVALUATED"
            ]
            event_values = [float(row["fixed_horizon_net_return"]) for row in event_rows]
            event_summary[f"{model}:{prediction_class}"] = {
                **summarize(event_values),
                "technical_candidate_events": len(event_rows),
                "horizon_bars": int(event_rows[0]["fixed_horizon_bars"]) if event_rows else 12,
                "average_mfe_pct": (
                    100 * mean(float(row["fixed_horizon_mfe"]) for row in event_rows)
                    if event_rows else None
                ),
                "average_mae_pct": (
                    100 * mean(float(row["fixed_horizon_mae"]) for row in event_rows)
                    if event_rows else None
                ),
            }

    audit = {
        "experiment_id": "2330-hold-sell-outcome-audit-20261027",
        "stock_code": "2330",
        "period": "2024-07-01 through 2024-12-30 historical development",
        "data_role": "historical_development_only",
        "protected_2025_data_read": False,
        "technical_candidate_events": len(technical),
        "market_regime_blocked": sum(row.get("market_regime_pass", "").lower() == "false" for row in technical),
        "market_regime_passed_candidate_events": len(eligible),
        "unique_model_prediction_dates": len(eligible_dates),
        "runtime_gate_decisions": runtime_summary,
        "three_session_label_outcomes_by_predicted_class": label_summary,
        "twelve_bar_candidate_outcomes_by_predicted_class": event_summary,
        "risk_blocks": sum(row["strategy_id"] in MODELS and row.get("decision", "").startswith("BLOCK") for row in risk_rows),
        "notes": [
            "Three-session target matches frozen ML V1 label: prior-session signal, next-session open to third-session close, less one-round-trip commission/tax; labels are SELL <= -1%, HOLD between, BUY >= +1%.",
            "12-bar statistics are 5-minute candidate-event forward outcomes, not completed strategy trades and not a replacement for the technical exit backtest.",
            "Repeated candidate events on one date share the same once-daily model prediction; candidate-event statistics are not independent observations.",
            "No model, threshold, technical rule, risk setting, or execution cost was changed. Risk settings remain frozen; 0 bps scenario was used for this audit.",
            "Unclassified diagnostic rows were corrected by joining runtime technical_signal_time to candidate_entry_time. The original report's unclassified count was a field-name join error.",
        ],
    }
    (OUTPUT / "2330_hold_sell_analysis.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 2330 HOLD／SELL 候選結果診斷",
        "",
        "期間：2024 下半年歷史開發資料；0 bps 基準成本。未讀取 2025 Holdout，未修改模型、門檻、技術規則或風控。",
        "",
        f"技術候選 {len(technical)} 筆；市場濾網擋下 {audit['market_regime_blocked']} 筆；通過後 {len(eligible)} 筆候選事件、{len(eligible_dates)} 個唯一模型預測日。",
        "",
        "## 實際 ML Gate 決策",
        "",
        "| 模型 | ENTRY | BLOCK_HOLD | BLOCK_SELL | 未進 gate（帳戶狀態） |",
        "|---|---:|---:|---:|---:|",
    ]
    for model in MODELS:
        s = runtime_summary[model]
        skipped = max(0, len(eligible) - sum(s.get(key, 0) for key in ("ENTRY", "BLOCK_HOLD", "BLOCK_SELL", "BLOCK_MISSING_OR_LATE_PREDICTION")))
        lines.append(f"| {model} | {s.get('ENTRY', 0)} | {s.get('BLOCK_HOLD', 0)} | {s.get('BLOCK_SELL', 0)} | {skipped} |")
    lines += ["", "## 模型三交易日標籤的實際結果（唯一預測日，扣費稅）", "", "| 模型 | 預測類別 | 樣本日數 | 實際淨報酬均值 | 中位數 | 正報酬日數 | 實際標籤分布 |", "|---|---|---:|---:|---:|---:|---|"]
    for model in MODELS:
        for cls in ("HOLD", "SELL"):
            s = label_summary[f"{model}:{cls}"]
            actual = ", ".join(f"{key}:{value}" for key, value in sorted(s["actual_labels"].items())) or "—"
            lines.append(f"| {model} | {cls} | {s['count']} | {s['mean_net_return_pct'] if s['mean_net_return_pct'] is not None else '—':.3f}% | {s['median_net_return_pct'] if s['median_net_return_pct'] is not None else '—':.3f}% | {s['positive_net_return_count']} | {actual} |")
    lines += ["", "## 技術候選後 12 根五分 K 的反事實方向檢查", "", "這是各候選點固定持有 12 根 K 的淨報酬診斷，不是沿用技術出場規則的完整交易 PnL。重複日期各候選點分別計算，但它們共用同一日的模型預測。", "", "| 模型 | 類別 | 候選事件 | 平均淨報酬 | 中位數 | 正報酬比例 | 平均 MFE | 平均 MAE |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for model in MODELS:
        for cls in ("HOLD", "SELL"):
            s = event_summary[f"{model}:{cls}"]
            def fmt(value): return "—" if value is None else f"{value:.3f}%"
            lines.append(f"| {model} | {cls} | {s['technical_candidate_events']} | {fmt(s['mean_net_return_pct'])} | {fmt(s['median_net_return_pct'])} | {fmt(s['positive_net_return_rate_pct'])} | {fmt(s['average_mfe_pct'])} | {fmt(s['average_mae_pct'])} |")
    lines += ["", "## 限制與下一步", "", "- 這些是單一股票、單一半年期的描述性結果，且每個模型預測日少、同日候選相依，不能由平均值選新規則。", "- HOLD／SELL 在原 Gate 中仍然是不進場；本表只評估其後續結果，不把類別改成 BUY。", "- 12 根 K 結果不是 Technical V2-B 的完整出場回測。若要比較 HOLD／SELL 作為進場條件，下一個獨立實驗才會用原技術出場規則做反事實重播；風控與成本保持不變。", "- 原始 gate 逐筆紀錄的欄位對接已修正；原總報告可由 corrected CSV 與 JSON 核對。", ""]
    (OUTPUT / "README.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"status": "COMPLETED", "output": str(OUTPUT), "eligible_candidates": len(eligible), "unique_dates": len(eligible_dates)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

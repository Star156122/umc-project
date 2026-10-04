"""診斷 ML Trading V1 的 Label、機率、訊號與交易穩定度，不做調參。"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.models import run_model
from ml.trading_backtest import run_probability_backtest
from ml.trading_diagnostics import prediction_frame, probability_diagnostics, stability_summary
from ml.trading_pipeline import load_trading_plan, prepare_trading_data
from trading_system.research_guard import assert_payload

PLAN = ROOT / "configs/ml_trading_v1_20261003.json"
DATABASE = ROOT / "data/market_data.sqlite3"
OUT = ROOT / "exports/ml_trading_diagnosis_20261004"
LATEST = ROOT / "exports/ml_trading_diagnosis_latest.html"


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def render(diagnostics: dict) -> str:
    signal_rows = []
    stability_rows = []
    for model, payload in diagnostics["models"].items():
        s = payload["stability"]
        stability_rows.append(
            f"<tr><td>{html.escape(model)}</td><td>{s['active_stocks']}/6</td><td>{s['total_round_trips']}</td>"
            f"<td>{s['mean_return_pct']:.2f}%</td><td>{s['median_return_pct']:.2f}%</td>"
            f"<td>{s['return_std_pct']:.2f}</td><td>{s['profitable_stocks']}/6</td></tr>"
        )
        for code, item in payload["test_probability_by_stock"].items():
            counts = item["signal_counts"]
            reason = item["zero_buy_reason"] or "—"
            signal_rows.append(
                f"<tr><td>{html.escape(model)}</td><td>{code}</td><td>{item['samples']}</td>"
                f"<td>{counts['BUY']}</td><td>{counts['HOLD']}</td><td>{counts['SELL']}</td>"
                f"<td>{item['mean_probability']:.3f}</td><td>{item['max_probability']:.3f}</td><td>{html.escape(reason)}</td></tr>"
            )
    return f"""<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>ML Trading V1 診斷</title><style>body{{font-family:system-ui,'Noto Sans TC',sans-serif;max-width:1250px;margin:30px auto;padding:0 16px;color:#182433}}table{{border-collapse:collapse;width:100%;margin:14px 0}}th,td{{border:1px solid #d8e0e8;padding:8px;text-align:right}}th:first-child,td:first-child,td:last-child{{text-align:left}}.card{{background:#f2f6fa;border-radius:12px;padding:18px;margin:16px 0}}.warn{{background:#fff4cf}}</style>
<h1>ML Trading V1：Label、訊號與穩定度診斷</h1>
<div class='card warn'><b>重要發現：</b>目前模型不是三分類。Label 只有「三日後淨報酬是否超過 1%」的 0／1；BUY、HOLD、SELL 是把單一上漲機率套入 0.60／0.45 門檻後才產生。0 次交易主要要看最高預測機率是否曾達到 0.60。</div>
<h2>跨股票穩定度</h2><table><tr><th>模型</th><th>有交易股票</th><th>總交易</th><th>平均報酬</th><th>中位報酬</th><th>報酬標準差</th><th>獲利股票</th></tr>{''.join(stability_rows)}</table>
<h2>測試期訊號分布</h2><table><tr><th>模型</th><th>股票</th><th>樣本</th><th>BUY</th><th>HOLD</th><th>SELL</th><th>平均機率</th><th>最高機率</th><th>0 BUY 原因</th></tr>{''.join(signal_rows)}</table>
<div class='card'>本報告只診斷現有模型，沒有改 Label、Features、模型參數或交易門檻；locked / forbidden 資料未讀取。</div></html>"""


def main() -> int:
    plan = load_trading_plan(PLAN)
    data, markets = prepare_trading_data(DATABASE, plan)
    OUT.mkdir(parents=True, exist_ok=True)
    buy = float(plan["trading"]["buy_probability"])
    sell = float(plan["trading"]["sell_probability"])
    rules = {**plan["trading"], "max_holding_sessions": plan["target"]["holding_sessions"]}
    diagnostics = {
        "experiment_id": "ml-trading-diagnosis-20261004-v1",
        "status": "completed_diagnosis_only",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "holdout_used": False,
        "label_audit": {
            "model_target_type": "binary",
            "label_1": "next open to third session close return > 1%",
            "label_0": "all other outcomes",
            "buy_hold_sell_are_labels": False,
            "signal_mapping": {"BUY": "p >= 0.60", "HOLD": "0.45 < p < 0.60", "SELL": "p <= 0.45"},
            "positive_rate": data.audit["positive_rate"],
        },
        "models": {},
    }
    prediction_parts = []
    for name in ("random_forest", "xgboost", "gru"):
        print(f"診斷 {name}...", flush=True)
        _, metrics, probabilities = run_model(name, data, plan["models"][name])
        frames = []
        for split in ("validation", "test"):
            frame = prediction_frame(name, split, data.metadata[split], data.y[split], probabilities[split], buy, sell)
            frames.append(frame); prediction_parts.append(frame)
        test_frame = next(frame for frame in frames if frame["split"].iloc[0] == "test")
        trading = run_probability_backtest(markets["test"], data.metadata["test"], probabilities["test"], rules)
        diagnostics["models"][name] = {
            "classification": metrics,
            "validation_probability_by_stock": probability_diagnostics(frames[0], buy),
            "test_probability_by_stock": probability_diagnostics(test_frame, buy),
            "trading_by_stock": trading,
            "stability": stability_summary(trading),
        }
    predictions = pd.concat(prediction_parts, ignore_index=True)
    predictions.to_csv(OUT / "predictions.csv", index=False, encoding="utf-8-sig")
    assert_payload(diagnostics)
    (OUT / "diagnostics.json").write_text(dump(diagnostics), encoding="utf-8")
    page = render(diagnostics)
    (OUT / "report.html").write_text(page, encoding="utf-8")
    LATEST.write_text(page, encoding="utf-8")
    print(f"完成：{OUT / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

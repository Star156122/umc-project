"""執行固定規格的成本感知 BUY／HOLD／SELL 三分類回測。"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.models import run_multiclass_model
from ml.trading_backtest import buy_and_hold, run_signal_backtest
from ml.trading_diagnostics import stability_summary
from ml.trading_pipeline import load_trading_plan, prepare_trading_data
from trading_system.research_guard import assert_payload

PLAN_PATH = ROOT / "configs/ml_trading_v2_20261004.json"
DATABASE = ROOT / "data/market_data.sqlite3"
OUT = ROOT / "exports/ml_trading_v2_20261004"
LATEST = ROOT / "exports/ml_trading_v2_latest.html"
LABELS = np.asarray(["SELL", "HOLD", "BUY"])


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def render(
    results: dict,
    title: str = "ML Trading V2：成本感知三分類",
    change_note: str = "唯一核心修改：把原本二元 Label 改為扣除買賣手續費與交易稅後的 SELL／HOLD／BUY 三分類。Features 與模型主要參數維持 V1。",
    conclusion_note: str | None = None,
) -> str:
    rows, stability_rows, classification_rows = [], [], []
    for model, payload in results["models"].items():
        s = payload["stability"]
        test = payload["classification"]["splits"]["test"]
        signals = payload["test_signal_counts"]
        classification_rows.append(
            f"<tr><td>{html.escape(model)}</td><td>{test['accuracy']:.3f}</td>"
            f"<td>{test['macro_f1']:.3f}</td><td>{test['macro_roc_auc_ovr']:.3f}</td>"
            f"<td>{signals['SELL']}</td><td>{signals['HOLD']}</td><td>{signals['BUY']}</td></tr>"
        )
        stability_rows.append(
            f"<tr><td>{html.escape(model)}</td><td>{s['active_stocks']}/6</td><td>{s['total_round_trips']}</td>"
            f"<td>{s['mean_return_pct']:.2f}%</td><td>{s['median_return_pct']:.2f}%</td><td>{s['return_std_pct']:.2f}</td><td>{s['profitable_stocks']}/6</td></tr>"
        )
        for code, item in payload["trading_by_stock"].items():
            m = item["metrics"]
            pf = "—" if m["profit_factor"] is None else f"{m['profit_factor']:.2f}"
            sharpe = "—" if m["sharpe_ratio"] is None else f"{m['sharpe_ratio']:.2f}"
            rows.append(
                f"<tr><td>{html.escape(model)}</td><td>{code}</td><td>{m['return_pct']:.2f}%</td>"
                f"<td>{m['round_trips']}</td><td>{m['net_pnl_after_costs']:.0f}</td><td>{m['transaction_cost']:.0f}</td>"
                f"<td>{pf}</td><td>{sharpe}</td><td>{m['max_drawdown_pct']:.2f}%</td></tr>"
            )
    benchmark = "".join(
        f"<tr><td>Buy &amp; Hold</td><td>{code}</td><td>{item['return_pct']:.2f}%</td><td>1</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>"
        for code, item in results["benchmarks"]["buy_and_hold"].items()
    )
    if conclusion_note is None:
        best_name, best_payload = max(results["models"].items(), key=lambda item: item[1]["stability"]["mean_return_pct"])
        best = best_payload["stability"]
        conclusion_note = (
            f"本輪平均報酬最高的是 {best_name}（{best['mean_return_pct']:.2f}%），"
            f"中位數 {best['median_return_pct']:.2f}%，{best['profitable_stocks']}/6 股票獲利，"
            f"{best['active_stocks']}/6 股票有交易。仍須同時檢查各股票樣本數與 Buy & Hold，不能只看平均報酬。"
        )
    return f"""<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>ML Trading V2 三分類回測</title><style>body{{font-family:system-ui,'Noto Sans TC',sans-serif;max-width:1200px;margin:30px auto;padding:0 16px;color:#172434}}table{{border-collapse:collapse;width:100%;margin:16px 0}}th,td{{border:1px solid #d7e0e8;padding:8px;text-align:right}}th:first-child,td:first-child,td:nth-child(2){{text-align:left}}.card{{padding:18px;border-radius:12px;background:#edf5fb;margin:16px 0}}.warn{{background:#fff4cf}}</style>
<h1>{html.escape(title)}</h1>
<div class='card'>{html.escape(change_note)}</div>
<h2>分類與訊號分布</h2><table><tr><th>模型</th><th>Accuracy</th><th>Macro F1</th><th>Macro AUC</th><th>SELL</th><th>HOLD</th><th>BUY</th></tr>{''.join(classification_rows)}</table>
<h2>跨股票穩定度</h2><table><tr><th>模型</th><th>有交易股票</th><th>總交易</th><th>平均報酬</th><th>中位報酬</th><th>報酬標準差</th><th>獲利股票</th></tr>{''.join(stability_rows)}</table>
<h2>各股票交易績效</h2><table><tr><th>模型</th><th>股票</th><th>報酬</th><th>交易</th><th>淨損益</th><th>交易成本</th><th>Profit Factor</th><th>Sharpe</th><th>最大回撤</th></tr>{''.join(rows)}{benchmark}</table>
<div class='card'><strong>判讀：</strong>{html.escape(conclusion_note)}</div>
<div class='card warn'>2026 上半年已是看過的開發資料，本結果只能比較設計差異，不能當獨立驗證。2025 下半年 locked / forbidden 資料完全未讀取。</div></html>"""


def execute(plan_path: Path, out: Path, latest: Path, title: str, change_note: str, conclusion_note: str | None = None) -> int:
    plan = load_trading_plan(plan_path)
    data, markets = prepare_trading_data(DATABASE, plan)
    out.mkdir(parents=True, exist_ok=True)
    rules = {**plan["trading"], "max_holding_sessions": plan["target"]["holding_sessions"]}
    results = {
        "experiment_id": plan["experiment_id"], "status": "completed",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "holdout_used": False, "audit": data.audit, "models": {},
        "benchmarks": {"buy_and_hold": buy_and_hold(markets["test"], rules), "cash_return_pct": 0.0},
    }
    prediction_parts = []
    for name in ("random_forest", "xgboost", "gru"):
        print(f"訓練 {name} 三分類模型...", flush=True)
        _, metrics, probabilities = run_multiclass_model(name, data, plan["models"][name])
        for split in ("validation", "test"):
            frame = data.metadata[split].reset_index(drop=True).copy()
            frame["model"] = name; frame["split"] = split; frame["actual_label"] = data.y[split]
            frame["p_sell"] = probabilities[split][:, 0]
            frame["p_hold"] = probabilities[split][:, 1]
            frame["p_buy"] = probabilities[split][:, 2]
            frame["predicted_signal"] = LABELS[probabilities[split].argmax(axis=1)]
            prediction_parts.append(frame)
        test_signals = LABELS[probabilities["test"].argmax(axis=1)]
        trading = run_signal_backtest(markets["test"], data.metadata["test"], test_signals, rules)
        results["models"][name] = {
            "classification": metrics,
            "test_signal_counts": {signal: int((test_signals == signal).sum()) for signal in LABELS},
            "trading_by_stock": trading,
            "stability": stability_summary(trading),
        }
    assert_payload(results)
    pd.concat(prediction_parts, ignore_index=True).to_csv(out / "predictions.csv", index=False, encoding="utf-8-sig")
    (out / "data_audit.json").write_text(dump(data.audit), encoding="utf-8")
    (out / "results.json").write_text(dump(results), encoding="utf-8")
    report = render(results, title, change_note, conclusion_note)
    (out / "report.html").write_text(report, encoding="utf-8")
    latest.write_text(report, encoding="utf-8")
    print(f"完成：{out / 'report.html'}")
    return 0


def main() -> int:
    return execute(
        PLAN_PATH, OUT, LATEST,
        "ML Trading V2：成本感知三分類",
        "唯一核心修改：把原本二元 Label 改為扣除買賣手續費與交易稅後的 SELL／HOLD／BUY 三分類。Features 與模型主要參數維持 V1。",
        "三分類讓 Random Forest 與 GRU 六檔都有交易，證實 V1 的固定 0.60 買進門檻是零交易主因之一。但 Random Forest 仍只有 3/6 股票獲利，XGBoost 仍有一檔零交易，三個模型都沒有穩定超越 Buy & Hold，因此目前只適合作為開發結果，尚未達到可凍結候選。",
    )


if __name__ == "__main__":
    raise SystemExit(main())

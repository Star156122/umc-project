"""執行 ML Trading V1；資料不足時仍產生可閱讀的缺口報告。"""
from __future__ import annotations

import csv
import html
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.models import run_model
from ml.trading_backtest import buy_and_hold, run_probability_backtest
from ml.trading_pipeline import coverage_report, load_trading_plan, prepare_trading_data
from trading_system.research_guard import assert_payload

PLAN_PATH = ROOT / "configs/ml_trading_v1_20261003.json"
DATABASE = ROOT / "data/market_data.sqlite3"
OUTPUT_DIR = ROOT / "exports/ml_trading_v1_20261003"
LATEST_REPORT = ROOT / "exports/ml_trading_v1_latest.html"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _coverage_table(rows: list[dict]) -> str:
    body = "".join(
        f"<tr><td>{html.escape(r['split'])}</td><td>{html.escape(r['stock_code'])}</td>"
        f"<td>{r['start']}～{r['end']}</td><td>{r['kbar_rows']:,}</td>"
        f"<td class={'ok' if r['available'] else 'bad'}>{'可用' if r['available'] else '缺少'}</td></tr>"
        for r in rows
    )
    return f"<table><thead><tr><th>用途</th><th>股票</th><th>期間</th><th>5 分 K</th><th>狀態</th></tr></thead><tbody>{body}</tbody></table>"


def _page(title: str, content: str) -> str:
    return f"""<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>{html.escape(title)}</title><style>
body{{font-family:system-ui,'Noto Sans TC',sans-serif;margin:32px auto;max-width:1120px;padding:0 18px;color:#182433;background:#f5f7fa}}
.card{{background:white;border:1px solid #dbe3ec;border-radius:14px;padding:20px;margin:16px 0}}table{{width:100%;border-collapse:collapse;background:white}}
th,td{{padding:9px;border:1px solid #dbe3ec;text-align:left}}.ok{{color:#087a45;font-weight:700}}.bad{{color:#b42318;font-weight:700}}
code{{background:#edf2f7;padding:2px 5px}}h1,h2{{color:#123b5d}}</style><h1>{html.escape(title)}</h1>{content}</html>"""


def render_blocked(plan: dict, coverage_rows: list[dict], generated_at: str) -> str:
    missing = [r for r in coverage_rows if not r["available"]]
    return _page("ML Trading V1：資料不足，尚未產生績效", f"""
<div class='card'><p class='bad'>本次程式已完成資料檢查，但缺少 {len(missing)} 組必要資料，因此沒有訓練模型，也沒有產生虛假的報酬率。</p>
<p>固定規格：六檔股票、5 分 K、次日開盤成交、最多持有 3 個交易日、買進門檻 0.60、賣出門檻 0.45、停損 3%。</p>
<p>保留區間 <code>2025-07-01～2025-12-31</code> 維持 locked / forbidden，本次未讀取。</p></div>
<h2>資料庫涵蓋檢查</h2>{_coverage_table(coverage_rows)}
<div class='card'><p>要完成正式執行，必須先匯入訓練期 2023–2024、驗證期 2025 上半年，以及六檔股票完整的 2026 上半年 5 分 K。</p>
<p>產生時間：{html.escape(generated_at)}</p></div>""")


def render_completed(plan: dict, results: dict, coverage_rows: list[dict]) -> str:
    rows = []
    for model, by_stock in results["trading_results"].items():
        for code, payload in by_stock.items():
            m = payload["metrics"]
            win_rate = "—" if m["win_rate_pct"] is None else f"{m['win_rate_pct']:.1f}%"
            rows.append(f"<tr><td>{html.escape(model)}</td><td>{code}</td><td>{m['return_pct']:.2f}%</td><td>{m['round_trips']}</td><td>{win_rate}</td><td>{m['max_drawdown_pct']:.2f}%</td></tr>")
    benchmark_rows = "".join(f"<tr><td>買進持有</td><td>{code}</td><td>{m['return_pct']:.2f}%</td><td>1</td><td>—</td><td>—</td></tr>" for code, m in results["benchmarks"]["buy_and_hold"].items())
    return _page("ML Trading V1：六檔股票回測", f"""
<div class='card ok'>訓練、驗證及回測均已完成。保留區間仍為 locked / forbidden，未讀取也未用來建立特徵。</div>
<table><thead><tr><th>方法</th><th>股票</th><th>報酬率</th><th>交易</th><th>勝率</th><th>最大回撤</th></tr></thead><tbody>{''.join(rows)}{benchmark_rows}</tbody></table>
<h2>資料涵蓋</h2>{_coverage_table(coverage_rows)}
<div class='card'>測試結果只作開發比較，不是獨立驗證，也沒有依測試績效調整門檻。</div>""")


def write_coverage(rows: list[dict]) -> None:
    with (OUTPUT_DIR / "data_coverage.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def main() -> int:
    plan = load_trading_plan(PLAN_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    coverage = coverage_report(DATABASE, plan)
    write_coverage(coverage.rows)
    if not coverage.complete:
        result = {
            "experiment_id": plan["experiment_id"], "status": "blocked_data",
            "generated_at": generated_at, "holdout_used": False,
            "missing_datasets": [r for r in coverage.rows if not r["available"]],
            "message": "必要的 5 分 K 尚未完整匯入；模型與交易回測未執行。",
        }
        assert_payload(result)
        report = render_blocked(plan, coverage.rows, generated_at)
        (OUTPUT_DIR / "results.json").write_text(_json(result), encoding="utf-8")
        (OUTPUT_DIR / "report.html").write_text(report, encoding="utf-8")
        LATEST_REPORT.write_text(report, encoding="utf-8")
        print(f"資料不足，缺少 {len(result['missing_datasets'])} 組資料。")
        print(f"缺口報告：{OUTPUT_DIR / 'report.html'}")
        return 2

    data, markets = prepare_trading_data(DATABASE, plan)
    results = {
        "experiment_id": plan["experiment_id"], "status": "completed",
        "generated_at": generated_at, "holdout_used": False,
        "classification_metrics": {}, "trading_results": {},
        "benchmarks": {"buy_and_hold": buy_and_hold(markets["test"], plan["trading"]), "cash_return_pct": 0.0},
        "audit": data.audit,
    }
    rules = {**plan["trading"], "max_holding_sessions": plan["target"]["holding_sessions"]}
    for name in ("random_forest", "xgboost", "gru"):
        print(f"訓練 {name}...", flush=True)
        _, metrics, probabilities = run_model(name, data, plan["models"][name])
        results["classification_metrics"][name] = metrics
        results["trading_results"][name] = run_probability_backtest(markets["test"], data.metadata["test"], probabilities["test"], rules)
    assert_payload(results)
    report = render_completed(plan, results, coverage.rows)
    (OUTPUT_DIR / "data_audit.json").write_text(_json(data.audit), encoding="utf-8")
    (OUTPUT_DIR / "results.json").write_text(_json(results), encoding="utf-8")
    (OUTPUT_DIR / "report.html").write_text(report, encoding="utf-8")
    LATEST_REPORT.write_text(report, encoding="utf-8")
    print(f"完成：{OUTPUT_DIR / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

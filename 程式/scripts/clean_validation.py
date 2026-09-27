"""一次性 Clean Validation：凍結 MACD 與多數決候選，不提供調參入口。"""
from __future__ import annotations

import contextlib
import csv
import dataclasses
import hashlib
import html
import io
import json
import sqlite3
import statistics
from datetime import datetime
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

from scripts.multi_stock_batch import build_runtime_config, load_plan
from scripts.routing_simulation import ROOT, simulate
from scripts.risk_ablation import check_period, diagnostics, read_frame
from trading_system import backtest as bt
from trading_system.early_followthrough_candidate import build_early_followthrough
from trading_system.entry_exit_candidate import daily_entry_flags
from trading_system.strategy_logic_candidate import build_strategy_logic
from trading_system.research_guard import assert_development_period

PLAN = ROOT / "configs/clean_validation_plan_draft.json"
STOCKS = ROOT / "configs/clean_validation_stocks.json"
EXPERIMENT = ROOT / "configs/clean_validation_experiment.json"
TEMPLATES = ROOT / "configs/strategy_templates_v2_candidate.json"
RULES = ROOT / "configs/six_strategy_logic_20260926.json"
FREEZE = ROOT / "configs/strategy_freeze_registry.json"
OUT = ROOT / "exports/clean_validation_20260926"
LATEST = ROOT / "exports/clean_validation_latest.html"
STATE = OUT / "execution_state.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def verify_frozen() -> dict:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    if plan.get("status") != "approved_ready_for_single_execution" or plan.get("must_not_execute"):
        raise RuntimeError("Clean Validation 尚未取得使用者確認。")
    assert_development_period(plan["proposed_period"]["start"], plan["proposed_period"]["end"])
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    entries = {(row["strategy"], row["variant"]): row for row in freeze["entries"]}
    macd = entries[("macd", "previous_daily_trend_filter")]
    vote = entries[("vote", "previous_daily_trend_plus_early_followthrough")]
    checks = [
        (ROOT / macd["config"], macd["config_sha256"]),
        (ROOT / macd["implementation"], macd["implementation_sha256"]),
        (ROOT / vote["followthrough_plan"], vote["plan_sha256"]),
        (ROOT / vote["implementation"], vote["implementation_sha256"]),
    ]
    for path, expected in checks:
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"凍結檔案雜湊不符，拒絕執行：{path}")
    if STATE.exists():
        old = json.loads(STATE.read_text(encoding="utf-8"))
        if old.get("performance_access_started"):
            raise RuntimeError("這批 Clean Validation 已開始讀取績效，不得重跑。")
    return plan


def runtime_items():
    stocks, templates, experiment = load_plan(TEMPLATES, STOCKS, EXPERIMENT)
    fields = {field.name for field in dataclasses.fields(bt.AppConfig)}
    for code in experiment["stock_codes"]:
        runtime = build_runtime_config(stocks[code], templates, experiment)
        configs = {}
        for strategy in ("macd", "vote"):
            settings = dict(runtime, **runtime["profiles"][f"{code}_{strategy}"])
            settings.update(llm_enabled=False, db_enabled=False, allow_real_trading=False,
                            is_backtest=True, is_simulation=True, only_backtest=True,
                            tick_source="sinopac")
            configs[strategy] = bt.AppConfig(**{k: v for k, v in settings.items() if k in fields})
        yield stocks[code], configs


def ensure_cache() -> None:
    load_dotenv(ROOT / ".env")
    api = None
    try:
        for stock, configs in runtime_items():
            config = configs["macd"]
            if not bt.is_cache_range_complete(config):
                if api is None:
                    api = bt.login_sinopac()
                bt.fetch_sinopac_ticks(api, config, collect_ticks=False)
            else:
                print(f"{config.code} 已有完整 tick 快取", flush=True)
            frame, stats = bt.load_cached_kbar_frame(config)
            print(f"{config.code} {stock['name']}：{len(frame):,} 根5分K，來源 {stats.tick_count:,} 筆tick", flush=True)
    finally:
        if api is not None:
            api.logout()


def aggregate(rows: list[dict]) -> dict:
    returns = [row["metrics"]["total_return"] for row in rows]
    pfs = [row["metrics"]["profit_factor"] for row in rows]
    drawdowns = [row["metrics"]["max_drawdown"] for row in rows]
    trades = [row["metrics"]["completed_trades"] for row in rows]
    gross = sum(row["metrics"]["gross_pnl"] for row in rows)
    cost = sum(row["metrics"]["transaction_cost"] for row in rows)
    sufficient = sum(value >= 5 for value in trades)
    checks = {
        "at_least_6_stocks_with_5_trades": sufficient >= 6,
        "at_least_4_profitable_stocks": sum(value > 0 for value in returns) >= 4,
        "positive_median_return": statistics.median(returns) > 0,
        "median_profit_factor_at_least_1": statistics.median(pfs) >= 1,
        "median_drawdown_at_most_15pct": statistics.median(drawdowns) <= 0.15,
        "positive_gross_and_cost_below_60pct": gross > 0 and cost / gross <= 0.60,
    }
    if sufficient < 6:
        decision = "INSUFFICIENT"
    else:
        decision = "PASS" if all(checks.values()) else "FAIL"
    return {
        "mean_return": statistics.fmean(returns),
        "median_return": statistics.median(returns),
        "profitable_stocks": sum(value > 0 for value in returns),
        "positive_rate": sum(value > 0 for value in returns) / len(rows),
        "median_profit_factor": statistics.median(pfs),
        "median_max_drawdown": statistics.median(drawdowns),
        "mean_trades_per_stock": statistics.fmean(trades),
        "sufficient_sample_stocks": sufficient,
        "gross_pnl": gross,
        "net_pnl": sum(row["metrics"]["net_pnl"] for row in rows),
        "transaction_cost": cost,
        "cost_to_gross_pnl": cost / gross if gross > 0 else None,
        "decision": decision,
        "checks": checks,
    }


def write_outputs(payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    columns = ["strategy", "stock_code", "stock_name", "industry", "initial_capital", "ending_capital",
               "gross_pnl", "net_pnl", "return_pct", "trade_count", "win_rate_pct", "profit_factor",
               "max_drawdown_pct", "sharpe", "transaction_cost", "sample_status"]
    with (OUT / "stock_results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in payload["records"]:
            m = row["metrics"]
            writer.writerow({
                "strategy": row["strategy"], "stock_code": row["code"], "stock_name": row["name"],
                "industry": row["industry"], "initial_capital": 100000,
                "ending_capital": m["ending_capital"], "gross_pnl": m["gross_pnl"], "net_pnl": m["net_pnl"],
                "return_pct": m["total_return"] * 100, "trade_count": m["completed_trades"],
                "win_rate_pct": m["win_rate"] * 100, "profit_factor": m["profit_factor"],
                "max_drawdown_pct": m["max_drawdown"] * 100, "sharpe": m["sharpe_ratio"],
                "transaction_cost": m["transaction_cost"],
                "sample_status": "ENOUGH" if m["completed_trades"] >= 5 else "INSUFFICIENT",
            })
    blocks = []
    labels = {"macd": "MACD 凍結候選", "vote": "三指標多數決＋早期延續凍結候選"}
    for strategy in ("macd", "vote"):
        rows = [r for r in payload["records"] if r["strategy"] == strategy]
        summary = payload["summary"][strategy]
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in (
                r["code"], r["name"], r["industry"], f'{r["metrics"]["total_return"]:.2%}',
                r["metrics"]["completed_trades"], f'{r["metrics"]["win_rate"]:.2%}',
                f'{r["metrics"]["profit_factor"]:.2f}', f'{r["metrics"]["max_drawdown"]:.2%}',
                f'{r["metrics"]["sharpe_ratio"]:.3f}', f'{r["metrics"]["transaction_cost"]:,.0f}',
                "足夠" if r["metrics"]["completed_trades"] >= 5 else "證據不足")) + "</tr>"
            for r in rows)
        blocks.append(f"""
        <section><h2>{labels[strategy]}：{summary['decision']}</h2>
        <div class="cards"><b>平均報酬 {summary['mean_return']:.2%}</b><b>中位數 {summary['median_return']:.2%}</b>
        <b>獲利股票 {summary['profitable_stocks']}/8</b><b>PF中位數 {summary['median_profit_factor']:.2f}</b>
        <b>回撤中位數 {summary['median_max_drawdown']:.2%}</b><b>足夠樣本 {summary['sufficient_sample_stocks']}/8</b></div>
        <table><thead><tr><th>代號</th><th>名稱</th><th>產業</th><th>報酬</th><th>交易</th><th>勝率</th><th>PF</th><th>最大回撤</th><th>Sharpe</th><th>成本</th><th>樣本</th></tr></thead><tbody>{body}</tbody></table></section>""")
    page = f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>Clean Validation</title>
    <style>body{{font-family:Arial,'Microsoft JhengHei';margin:28px;color:#243047}}h1,h2{{color:#173b67}}.warn{{background:#fff2cc;padding:14px;border-radius:8px}}.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}.cards b{{background:#e8f1fb;padding:10px;border-radius:7px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #ccd5df;padding:7px;text-align:right}}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2),th:nth-child(3),td:nth-child(3){{text-align:left}}section{{margin-top:30px}}</style>
    <body><h1>凍結候選策略 Clean Validation</h1><p>期間：2026/01/01～2026/06/30；8檔全新股票。Development 與本頁沒有混算。</p>
    <p class="warn">這批資料已在本次執行後轉為 validation_seen。不得依本頁結果修改策略後，再重跑同一批並稱為獨立驗證。Holdout 2025/07/01～2025/12/31 未使用。</p>
    {''.join(blocks)}</body></html>"""
    (OUT / "report.html").write_text(page, encoding="utf-8")
    LATEST.write_text(page, encoding="utf-8")


def main() -> None:
    plan = verify_frozen()
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"started_at": datetime.now().isoformat(), "performance_access_started": False,
                                 "status": "downloading_cache"}, ensure_ascii=False, indent=2), encoding="utf-8")
    ensure_cache()
    STATE.write_text(json.dumps({"started_at": datetime.now().isoformat(), "performance_access_started": True,
                                 "status": "running_once"}, ensure_ascii=False, indent=2), encoding="utf-8")
    rules = json.loads(RULES.read_text(encoding="utf-8"))["candidate_rules"]
    records = []
    hashes = {}
    with contextlib.closing(sqlite3.connect((ROOT / "data/market_data.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        for stock, configs in runtime_items():
            for strategy in ("macd", "vote"):
                config = configs[strategy]
                frame, stats, hashes[config.code] = read_frame(conn, config)
                closes = {row["kbar_time"].date().isoformat(): float(row["Close"]) for row in frame.iter_rows(named=True)}
                flags = daily_entry_flags(list(closes.items()))
                builder = build_strategy_logic if strategy == "macd" else build_early_followthrough
                factory = partial(builder, rules=rules, daily_flags=flags)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = simulate({"ma": config, strategy: config}, {"ma": frame, strategy: frame}, stats, strategy, factory=factory)
                records.append({"code": config.code, "name": stock["name"], "industry": stock["industry"],
                                "strategy": strategy, "metrics": result["metrics"], "trades": result["trades"],
                                "signals": result["signals"], "diagnostics": diagnostics(frame, stats, result["trades"],
                                config.initial_capital, 5)})
            print(f"{config.code} 完成兩個凍結候選", flush=True)
    summary = {strategy: aggregate([row for row in records if row["strategy"] == strategy]) for strategy in ("macd", "vote")}
    payload = {"plan": plan, "completed_at": datetime.now().isoformat(), "records": records,
               "summary": summary, "market_sha256": hashes, "holdout_accessed": False,
               "data_role_after_run": "validation_seen"}
    write_outputs(payload)
    STATE.write_text(json.dumps({"completed_at": datetime.now().isoformat(), "performance_access_started": True,
                                 "status": "completed_consumed", "rerun_forbidden": True}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("完成：exports/clean_validation_latest.html", flush=True)


if __name__ == "__main__":
    main()

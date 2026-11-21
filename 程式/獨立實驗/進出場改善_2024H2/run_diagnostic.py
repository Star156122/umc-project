"""D0: reproduce the frozen website profile and audit trade lifecycle timing."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROGRAM_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = Path(__file__).resolve().parent
OUTPUT = EXPERIMENT_ROOT / "結果" / "D0_baseline_lifecycle"
sys.path.insert(0, str(PROGRAM_ROOT))

from hybrid.core import main_buy_strategy_v1 as engine  # noqa: E402
from hybrid.core.web_main_backtest import MODELS, PROFILE, STRATEGIES, request_config  # noqa: E402

EXPERIMENT_ID = "isolated-entry-exit-diagnostic-20261120"
START = "2024-07-01"
END = "2024-12-30"
STOCKS = ["2303", "2330", "2379", "2454", "3034"]
ARMS = ["technical", "rf", "xgboost", "gru"]


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows([{key: _finite(row.get(key)) for key in columns} for row in rows])


def _minutes(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60


def _entry_event(result: dict[str, Any], order: dict[str, Any]) -> dict[str, Any] | None:
    matches = [
        event for event in result["events"]
        if int(event.get("filled_quantity", 0)) > 0 and event["time"] == order["time"]
    ]
    return matches[0] if len(matches) == 1 else None


def _run_one(stock: str, arm: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    config = request_config(stock, START, END)
    data = engine.load_inputs(config)
    selected = dict(data)
    selected["ml"] = data["ml_by_model"][MODELS[arm]]
    result = engine.simulate(config, selected, STRATEGIES[arm], 0.0005, "common", (START, END))
    if result["metrics"]["adds"] or result["metrics"]["holding_extensions"]:
        raise AssertionError("Frozen website profile unexpectedly added or extended a position")

    buy_orders = {int(row["trade_id"]): row for row in result["orders"] if row["action"] == "BUY"}
    sell_orders = {int(row["trade_id"]): row for row in result["orders"] if row["action"] == "SELL"}
    lifecycle: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()

    for trade in result["trades"]:
        trade_id = int(trade["trade_id"])
        buy = buy_orders[trade_id]
        sell = sell_orders[trade_id]
        event = _entry_event(result, buy)
        episodes = list(trade.get("deferred_exits", []))
        first_deferred = episodes[0] if episodes else None
        trigger_time = first_deferred["time"] if first_deferred else sell["time"]
        reasons[str(trade["exit_reason"])] += 1
        lifecycle.append({
            "experiment_id": EXPERIMENT_ID,
            "stock_code": stock,
            "arm": arm,
            "trade_id": trade_id,
            "signal_bar_start": datetime.fromtimestamp(event["timestamp"], engine.TZ).isoformat() if event else None,
            "signal_completed_at": event["time"] if event else None,
            "entry_fill_time": buy["time"],
            "entry_delay_minutes_from_signal_completion": _minutes(event["time"], buy["time"]) if event else None,
            "signal_close": event.get("close") if event else None,
            "entry_reference_open": buy["reference"],
            "entry_fill_price": buy["price"],
            "quantity": buy["quantity"],
            "exit_reason": trade["exit_reason"],
            "exit_trigger_or_decision_time": trigger_time,
            "exit_fill_time": sell["time"],
            "exit_delay_minutes": _minutes(trigger_time, sell["time"]),
            "exit_reference": sell["reference"],
            "exit_fill_price": sell["price"],
            "deferred_episode_count": len(episodes),
            "first_deferred_constraint": first_deferred["constraint"] if first_deferred else None,
            "first_deferred_trigger": first_deferred["trigger"] if first_deferred else None,
            "holding_bars": trade["holding_bars"],
            "gross_pnl": trade["gross_pnl"],
            "net_pnl": trade["net_pnl"],
            "net_return": trade["net_return"],
            "fees": buy["fee"] + sell["fee"],
            "tax": sell["tax"],
            "slippage_cost": buy["slippage_cost"] + sell["slippage_cost"],
        })
        if event:
            entries.append({
                "experiment_id": EXPERIMENT_ID,
                "stock_code": stock,
                "arm": arm,
                "trade_id": trade_id,
                "signal_bar_start": datetime.fromtimestamp(event["timestamp"], engine.TZ).isoformat(),
                "signal_completed_at": event["time"],
                "fill_time": buy["time"],
                "signal_close": event["close"],
                "fill_reference_open": buy["reference"],
                "fill_price": buy["price"],
                "quantity": buy["quantity"],
                "ml": event["ml"],
                "market": event["market"],
            })
        for sequence, episode in enumerate(episodes, start=1):
            deferred.append({
                "experiment_id": EXPERIMENT_ID,
                "stock_code": stock,
                "arm": arm,
                "trade_id": trade_id,
                "episode": sequence,
                "trigger_time": episode["time"],
                "trigger": episode["trigger"],
                "constraint": episode["constraint"],
                "event_date": episode["date"],
                "eventual_exit_time": sell["time"],
                "delay_minutes": _minutes(episode["time"], sell["time"]),
                "eventual_exit_reason": trade["exit_reason"],
                "eventual_exit_reference": sell["reference"],
            })

    metrics = result["metrics"]
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "stock_code": stock,
        "arm": arm,
        "return": metrics["return"],
        "net_pnl": metrics["net_pnl"],
        "gross_pnl": metrics["gross_pnl"],
        "trade_count": metrics["trade_count"],
        "win_rate": metrics["win_rate"],
        "average_win": metrics["average_win"],
        "average_loss": metrics["average_loss"],
        "profit_factor": metrics["profit_factor"],
        "max_drawdown": metrics["max_drawdown"],
        "sharpe": metrics["sharpe"],
        "fees": metrics["fees"],
        "tax": metrics["tax"],
        "slippage_cost": metrics["slippage_cost"],
        "transaction_cost_excluding_slippage": metrics["fees"] + metrics["tax"],
        "average_holding_bars": metrics["average_holding_bars"],
        "deferred_exit_episodes": metrics["deferred_exit_episodes"],
        "deferred_trade_count": sum(bool(row.get("deferred_exits")) for row in result["trades"]),
        "stop_exit_count": sum(str(row["exit_reason"]).startswith("STOP") for row in result["trades"]),
        "exit_reasons": json.dumps(reasons, ensure_ascii=False, sort_keys=True),
        "candidate_events": metrics["candidate_events"],
        "execution_validity": metrics["execution_validity"],
    }
    spec = config["datasets"][stock]
    sources = {key: str(PROGRAM_ROOT / spec[key]) for key in ("database", "index", "predictions")}
    if spec.get("dividends"):
        sources["dividends"] = str(PROGRAM_ROOT / spec["dividends"])
    return summary, lifecycle, entries, deferred, sources


def _report(summary: list[dict[str, Any]], lifecycle: list[dict[str, Any]], deferred: list[dict[str, Any]]) -> str:
    total_trades = len(lifecycle)
    deferred_trades = sum(int(row["deferred_episode_count"] > 0) for row in lifecycle)
    constraints = Counter(row["constraint"] for row in deferred)
    reasons = Counter(row["exit_reason"] for row in lifecycle)
    protective_first = [row for row in lifecycle if str(row.get("first_deferred_trigger", "")).startswith("STOP")]
    overwritten_protective = [row for row in protective_first if not str(row["exit_reason"]).startswith("STOP")]
    median_delay = None
    delays = sorted(float(row["delay_minutes"]) for row in deferred if row["delay_minutes"] is not None)
    if delays:
        middle = len(delays) // 2
        median_delay = delays[middle] if len(delays) % 2 else (delays[middle - 1] + delays[middle]) / 2
    lines = [
        "# D0 現行策略交易生命週期診斷",
        "",
        f"- 實驗：`{EXPERIMENT_ID}`",
        f"- 期間：{START}～{END}（已曝光 Training）",
        f"- 股票／策略：{len(STOCKS)} 檔 × {len(ARMS)} 組；共 {len(summary)} 個回測單元。",
        f"- 完整交易：{total_trades} 筆；含延後出場：{deferred_trades} 筆。",
        f"- 延後事件：{len(deferred)} 次；中位延後時間：{median_delay if median_delay is not None else '無'} 分鐘。",
        f"- 延後限制分布：`{json.dumps(constraints, ensure_ascii=False, sort_keys=True)}`",
        f"- 出場原因分布：`{json.dumps(reasons, ensure_ascii=False, sort_keys=True)}`",
        f"- 第一次延後觸發為保護性停損：{len(protective_first)} 筆；其後被非停損標籤覆寫：{len(overwritten_protective)} 筆。",
        "",
        "## 判讀原則",
        "",
        "D0 未修改規則，也不以績效選參數。`signal_completed_at` 與 `entry_fill_time` 相同，是因完成的 5 分 K 與下一根開盤共用同一時間邊界；成交價仍使用下一根 Open。停損延後事件則另外保留第一次觸發時間與最終成交時間。",
        "",
        "若延後出場集中於同日零股停損且延後時間具實質幅度，才進入單一保護性出場執行模型 A/B；否則不修改該項。任何 A/B 都須另行事前登記。",
        "另發現一筆等待中的停損原因被後來的技術出場標籤覆寫；這會影響出場原因解讀，未改變該筆實際 09:10 成交價。本輪候選不包含第二項修改。",
        "",
        "## 限制",
        "",
        "普通 5 分 K 只是零股成交的代理，沒有逐筆零股委託簿、部分成交與真實價差。本診斷只能辨識引擎造成的時間與價格路徑，不能證明實盤一定以同樣價格成交。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise FileExistsError(f"Preserving prior result; output already exists: {OUTPUT}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    source_paths: dict[str, str] = {}
    for stock in STOCKS:
        for arm in ARMS:
            summary, trade_rows, entry_rows, deferred_rows, sources = _run_one(stock, arm)
            summaries.append(summary)
            lifecycle.extend(trade_rows)
            entries.extend(entry_rows)
            deferred.extend(deferred_rows)
            for key, value in sources.items():
                source_paths[f"{stock}:{key}"] = value

    _write_csv(OUTPUT / "summary.csv", summaries)
    _write_csv(OUTPUT / "trade_lifecycle.csv", lifecycle)
    _write_csv(OUTPUT / "entry_events.csv", entries)
    _write_csv(OUTPUT / "deferred_exits.csv", deferred)
    audit = {
        "experiment_id": EXPERIMENT_ID,
        "profile_id": PROFILE,
        "phase": "D0_BASELINE_TRADE_LIFECYCLE_DIAGNOSTIC",
        "period": {"start": START, "end": END, "role": "training_exposed"},
        "stocks": STOCKS,
        "arms": ARMS,
        "locked_data_used": False,
        "holdout_authorization_requested": False,
        "rule_changes": [],
        "source_sha256": {
            key: _hash(Path(value)) for key, value in sorted(source_paths.items())
        },
        "generated_at": datetime.now(engine.TZ).isoformat(),
        "row_counts": {
            "summary": len(summaries),
            "trade_lifecycle": len(lifecycle),
            "entry_events": len(entries),
            "deferred_exits": len(deferred),
        },
    }
    (OUTPUT / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    (OUTPUT / "report.md").write_text(_report(summaries, lifecycle, deferred), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), **audit["row_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

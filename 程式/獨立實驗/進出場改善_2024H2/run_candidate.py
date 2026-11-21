"""A1: paired execution replay for split whole-lot / odd-lot exit routing."""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROGRAM_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = Path(__file__).resolve().parent
OUTPUT = EXPERIMENT_ROOT / "結果" / "A1_mixed_lot_split"
sys.path.insert(0, str(PROGRAM_ROOT))

from hybrid.core import main_buy_strategy_v1 as engine  # noqa: E402
from hybrid.core.web_main_backtest import MODELS, PROFILE, STRATEGIES, request_config  # noqa: E402

EXPERIMENT_ID = "isolated-mixed-lot-exit-split-20261120-exp001"
START = "2024-07-01"
END = "2024-12-30"
STOCKS = ["2303", "2330", "2379", "2454", "3034"]
ARMS = ["technical", "rf", "xgboost", "gru"]
SLIP = 0.0005


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
        writer.writerows(rows)


def _to_ts(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


def _metric_set(equity_rows: list[dict[str, Any]], pnl: list[float]) -> dict[str, Any]:
    frame = pd.DataFrame(equity_rows)
    final = float(frame.iloc[-1]["equity"])
    max_drawdown = float((1 - frame.equity / frame.equity.cummax().clip(lower=250000)).max())
    daily = frame.groupby("date").equity.last().pct_change().fillna(0)
    sharpe = (
        float(daily.mean() / daily.std(ddof=1) * np.sqrt(252))
        if len(daily) > 1 and daily.std(ddof=1) > 0 else None
    )
    gain = sum(value for value in pnl if value > 0)
    loss = -sum(value for value in pnl if value < 0)
    wins = sum(value > 0 for value in pnl)
    losses = sum(value < 0 for value in pnl)
    return {
        "final_equity": final,
        "return": final / 250000 - 1,
        "net_pnl": final - 250000,
        "trade_count": len(pnl),
        "win_rate": wins / len(pnl) if pnl else None,
        "average_win": gain / wins if wins else None,
        "average_loss": -loss / losses if losses else None,
        "profit_factor": gain / loss if loss else None,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
    }


def _first_attempt_reference(
    episode: dict[str, Any], trade: dict[str, Any], bars_by_time: dict[str, dict[str, Any]],
    dividends: dict[str, dict[str, Any]], config: dict[str, Any],
) -> float:
    bar = bars_by_time[episode["time"]]
    trigger = str(episode["trigger"])
    if trigger.startswith("STOP_GAP") or trigger.startswith("TECHNICAL") or trigger.startswith("TIMEOUT"):
        return float(bar["Open"])
    if trigger.startswith("STOP_INTRABAR"):
        stop = float(trade["entry_price"]) * (1 - float(config["stop_pct"]))
        for day, event in dividends.items():
            if trade["entry_day"] < day <= episode["date"]:
                stop -= float(event["cash_per_share"])
        return stop
    return float(bar["Open"])


def _replay(stock: str, arm: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    config = request_config(stock, START, END)
    data = engine.load_inputs(config)
    selected = dict(data)
    selected["ml"] = data["ml_by_model"][MODELS[arm]]
    baseline = engine.simulate(config, selected, STRATEGIES[arm], SLIP, "common", (START, END))
    bars_by_time = {bar["time"]: bar for bar in data["bars"]}
    closes = {int(bar["kbar_timestamp"]): float(bar["Close"]) for bar in data["bars"]}
    sells = {int(row["trade_id"]): row for row in baseline["orders"] if row["action"] == "SELL"}
    buys = {int(row["trade_id"]): row for row in baseline["orders"] if row["action"] == "BUY"}
    split_rows: list[dict[str, Any]] = []
    candidate_trades: list[dict[str, Any]] = []
    adjustments: list[dict[str, Any]] = []

    for trade in baseline["trades"]:
        trade_id = int(trade["trade_id"])
        sell = sells[trade_id]
        buy = buys[trade_id]
        quantity = int(trade["quantity"])
        whole_quantity = quantity // 1000 * 1000
        residual_quantity = quantity - whole_quantity
        episodes = list(trade.get("deferred_exits", []))
        eligible = bool(episodes and whole_quantity and residual_quantity)
        candidate_net = float(trade["net_pnl"])
        candidate_gross = float(trade["gross_pnl"])
        fee_delta = 0.0
        tax_delta = 0.0
        slip_delta = 0.0
        early_reference = None
        early_price = None
        permanent_delta = 0.0

        if eligible:
            episode = episodes[0]
            early_reference = _first_attempt_reference(
                episode, trade, bars_by_time, data.get("dividends", {}), config
            )
            early_price = early_reference * (1 - SLIP)
            final_price = float(sell["price"])
            final_reference = float(sell["reference"])
            early_fee = engine.fee(whole_quantity * early_price, config)
            baseline_whole_fee = engine.fee(whole_quantity * final_price, config)
            same_day = episode["date"] == trade["entry_day"]
            early_tax_rate = config["same_day_sell_tax"] if same_day else config["normal_sell_tax"]
            early_tax = whole_quantity * early_price * early_tax_rate
            baseline_whole_tax = whole_quantity * final_price * config["normal_sell_tax"]
            early_proceeds = whole_quantity * early_price - early_fee - early_tax
            baseline_final_proceeds = whole_quantity * final_price - baseline_whole_fee - baseline_whole_tax
            permanent_delta = early_proceeds - baseline_final_proceeds
            gross_delta = whole_quantity * (early_price - final_price)
            fee_delta = early_fee - baseline_whole_fee
            tax_delta = early_tax - baseline_whole_tax
            slip_delta = whole_quantity * (abs(early_price - early_reference) - abs(final_price - final_reference))
            candidate_net += permanent_delta
            candidate_gross += gross_delta
            adjustments.append({
                "early_ts": _to_ts(episode["time"]),
                "final_ts": int(sell["timestamp"]),
                "whole_quantity": whole_quantity,
                "early_proceeds": early_proceeds,
                "permanent_delta": permanent_delta,
            })
            split_rows.append({
                "experiment_id": EXPERIMENT_ID,
                "stock_code": stock,
                "arm": arm,
                "trade_id": trade_id,
                "entry_time": trade["entry_time"],
                "original_quantity": quantity,
                "whole_quantity_early": whole_quantity,
                "odd_quantity_deferred": residual_quantity,
                "original_exit_attempt": episode["time"],
                "constraint": episode["constraint"],
                "trigger": episode["trigger"],
                "early_reference": early_reference,
                "early_fill_price": early_price,
                "residual_exit_time": sell["time"],
                "residual_reference": sell["reference"],
                "residual_fill_price": sell["price"],
                "net_pnl_delta": permanent_delta,
                "gross_pnl_delta": gross_delta,
                "fee_delta": fee_delta,
                "tax_delta": tax_delta,
                "slippage_cost_delta": slip_delta,
            })

        candidate_trades.append({
            "experiment_id": EXPERIMENT_ID,
            "stock_code": stock,
            "arm": arm,
            "trade_id": trade_id,
            "entry_time": trade["entry_time"],
            "baseline_exit_time": trade["exit_time"],
            "exit_reason": trade["exit_reason"],
            "quantity": quantity,
            "split_eligible": eligible,
            "whole_quantity_early": whole_quantity if eligible else 0,
            "odd_quantity_deferred": residual_quantity if eligible else quantity,
            "baseline_gross_pnl": trade["gross_pnl"],
            "candidate_gross_pnl": candidate_gross,
            "gross_pnl_delta": candidate_gross - float(trade["gross_pnl"]),
            "baseline_net_pnl": trade["net_pnl"],
            "candidate_net_pnl": candidate_net,
            "net_pnl_delta": candidate_net - float(trade["net_pnl"]),
            "candidate_fee_delta": fee_delta,
            "candidate_tax_delta": tax_delta,
            "candidate_slippage_cost_delta": slip_delta,
        })

    candidate_equity: list[dict[str, Any]] = []
    for row in baseline["equity"]:
        timestamp = int(row["timestamp"])
        equity = float(row["equity"])
        for adjustment in adjustments:
            if adjustment["early_ts"] <= timestamp < adjustment["final_ts"]:
                equity += adjustment["early_proceeds"] - adjustment["whole_quantity"] * closes[timestamp]
            elif timestamp >= adjustment["final_ts"]:
                equity += adjustment["permanent_delta"]
        candidate_equity.append({"timestamp": timestamp, "date": row["date"], "equity": equity})

    candidate_pnl = [float(row["candidate_net_pnl"]) for row in candidate_trades]
    candidate_metrics = _metric_set(candidate_equity, candidate_pnl)
    baseline_metrics = baseline["metrics"]
    baseline_cost = baseline_metrics["fees"] + baseline_metrics["tax"] + baseline_metrics["slippage_cost"]
    candidate_fees = baseline_metrics["fees"] + sum(row["candidate_fee_delta"] for row in candidate_trades)
    candidate_tax = baseline_metrics["tax"] + sum(row["candidate_tax_delta"] for row in candidate_trades)
    candidate_slip = baseline_metrics["slippage_cost"] + sum(row["candidate_slippage_cost_delta"] for row in candidate_trades)
    comparison = {
        "experiment_id": EXPERIMENT_ID,
        "stock_code": stock,
        "arm": arm,
        "baseline_return": baseline_metrics["return"],
        "candidate_return": candidate_metrics["return"],
        "return_delta": candidate_metrics["return"] - baseline_metrics["return"],
        "baseline_net_pnl": baseline_metrics["net_pnl"],
        "candidate_net_pnl": candidate_metrics["net_pnl"],
        "net_pnl_delta": candidate_metrics["net_pnl"] - baseline_metrics["net_pnl"],
        "baseline_gross_pnl": baseline_metrics["gross_pnl"],
        "candidate_gross_pnl": baseline_metrics["gross_pnl"] + sum(row["gross_pnl_delta"] for row in candidate_trades),
        "baseline_trade_count": baseline_metrics["trade_count"],
        "candidate_trade_count": candidate_metrics["trade_count"],
        "baseline_win_rate": baseline_metrics["win_rate"],
        "candidate_win_rate": candidate_metrics["win_rate"],
        "baseline_average_win": baseline_metrics["average_win"],
        "candidate_average_win": candidate_metrics["average_win"],
        "baseline_average_loss": baseline_metrics["average_loss"],
        "candidate_average_loss": candidate_metrics["average_loss"],
        "baseline_profit_factor": baseline_metrics["profit_factor"],
        "candidate_profit_factor": candidate_metrics["profit_factor"],
        "baseline_max_drawdown": baseline_metrics["max_drawdown"],
        "candidate_max_drawdown": candidate_metrics["max_drawdown"],
        "max_drawdown_delta": candidate_metrics["max_drawdown"] - baseline_metrics["max_drawdown"],
        "baseline_sharpe": baseline_metrics["sharpe"],
        "candidate_sharpe": candidate_metrics["sharpe"],
        "baseline_transaction_cost_including_slippage": baseline_cost,
        "candidate_transaction_cost_including_slippage": candidate_fees + candidate_tax + candidate_slip,
        "affected_trade_count": len(split_rows),
        "mixed_position_trade_count": sum(
            int(int(trade["quantity"]) >= 1000 and int(trade["quantity"]) % 1000 != 0)
            for trade in baseline["trades"]
        ),
        "paired_fixed_quantity_replay": True,
    }
    curve_rows = [
        {
            "experiment_id": EXPERIMENT_ID,
            "stock_code": stock,
            "arm": arm,
            "timestamp": row["timestamp"],
            "date": row["date"],
            "candidate_equity": row["equity"],
        }
        for row in candidate_equity
    ] if split_rows else []
    return comparison, candidate_trades, split_rows, curve_rows


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.4f}%"


def _report(comparisons: list[dict[str, Any]], splits: list[dict[str, Any]]) -> str:
    affected = [row for row in comparisons if row["affected_trade_count"]]
    affected_stocks = sorted({row["stock_code"] for row in affected})
    total_delta = sum(float(row["net_pnl_delta"]) for row in comparisons)
    lines = [
        "# A1 混合整張／零股拆分出場結果",
        "",
        f"- 實驗：`{EXPERIMENT_ID}`",
        f"- 對照單元：{len(comparisons)}；實際受影響單元：{len(affected)}。",
        f"- 實際受影響股票：{', '.join(affected_stocks) if affected_stocks else '無'}。",
        f"- 拆分出場交易：{len(splits)} 筆；20 個單元合計 Net PnL 差：NT$ {total_delta:.2f}。",
        "- 判定：`INSUFFICIENT_CROSS_STOCK_EVIDENCE`；不納入網站主要策略。",
        "",
        "## 受影響單元",
        "",
        "|股票|策略|對照報酬|候選報酬|報酬差|對照 MDD|候選 MDD|Net PnL 差|受影響交易|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in affected:
        lines.append(
            f"|{row['stock_code']}|{row['arm']}|{_fmt_pct(row['baseline_return'])}|"
            f"{_fmt_pct(row['candidate_return'])}|{_fmt_pct(row['return_delta'])}|"
            f"{_fmt_pct(row['baseline_max_drawdown'])}|{_fmt_pct(row['candidate_max_drawdown'])}|"
            f"{row['net_pnl_delta']:.2f}|{row['affected_trade_count']}|"
        )
    lines += [
        "",
        "## 結論",
        "",
        "候選邏輯在制度上比『整個混合部位一律等待零股時間』更細緻，但本期只有一檔股票的一個策略臂出現可比較路徑，未達事前要求的至少兩檔股票。純零股的高價股部位完全沒有改善，因為它們仍受零股 09:10 與不得當沖限制。",
        "",
        "因此保留本實驗與失敗證據，不改網站主策略、不開第二輪參數或規則測試。若未來要真正改善高價股停損可執行性，需要先選擇不同的資本／交易單位／商品設計，這會是新的研究問題，不能在本輪偷偷併入。",
        "",
        "## 方法限制",
        "",
        "A1 是固定訊號、固定數量的配對執行重播，用來隔離委託路由差異；普通 5 分 K 仍只是成交代理。候選保持零股餘額到原合法時間，因此交易生命週期與後續訊號狀態不變。",
        "",
        "制度來源：臺灣證券交易所 2024 年零股交易辦法歷史版本，以及證交所當日沖銷交易專區。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise FileExistsError(f"Preserving prior result; output already exists: {OUTPUT}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    comparisons: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []
    for stock in STOCKS:
        for arm in ARMS:
            comparison, trades, splits, curves = _replay(stock, arm)
            comparisons.append(comparison)
            trade_rows.extend(trades)
            split_rows.extend(splits)
            curve_rows.extend(curves)

    _write_csv(OUTPUT / "comparison.csv", comparisons)
    _write_csv(OUTPUT / "candidate_trades.csv", trade_rows)
    _write_csv(OUTPUT / "split_orders.csv", split_rows)
    _write_csv(OUTPUT / "affected_equity_curve.csv", curve_rows)
    affected_stocks = sorted({row["stock_code"] for row in comparisons if row["affected_trade_count"]})
    decision = "INSUFFICIENT_CROSS_STOCK_EVIDENCE" if len(affected_stocks) < 2 else "REVIEW_REQUIRED"
    audit = {
        "experiment_id": EXPERIMENT_ID,
        "profile_id": PROFILE,
        "phase": "A1_SINGLE_CHANGE_PAIRED_EXECUTION_REPLAY",
        "single_change": "split executable whole-board-lot quantity from deferred odd-lot residual",
        "period": {"start": START, "end": END, "role": "training_exposed"},
        "stocks": STOCKS,
        "arms": ARMS,
        "locked_data_used": False,
        "holdout_authorization_requested": False,
        "parameter_search": False,
        "paired_fixed_quantity_replay": True,
        "affected_stocks": affected_stocks,
        "affected_trades": len(split_rows),
        "decision": decision,
        "official_rule_sources": [
            "https://twse-regulation.twse.com.tw/TW/law/DAT06.aspx?FLCODE=FL007115&FLDATE=20241216&LSER=001",
            "https://www.twse.com.tw/zh/products/system/day-trading.html",
        ],
        "generated_at": datetime.now(engine.TZ).isoformat(),
        "row_counts": {
            "comparison": len(comparisons),
            "candidate_trades": len(trade_rows),
            "split_orders": len(split_rows),
            "affected_equity_curve": len(curve_rows),
        },
    }
    (OUTPUT / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    (OUTPUT / "report.md").write_text(_report(comparisons, split_rows), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "decision": decision, **audit["row_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

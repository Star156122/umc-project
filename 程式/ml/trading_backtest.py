"""把每日 ML 機率轉成可重現的多日多頭回測。"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _buy_shares(cash: float, price: float, rules: dict[str, Any]) -> int:
    budget = cash * float(rules["capital_fraction"])
    unit_cost = price * (1 + float(rules["commission_rate"]))
    return max(0, min(int(rules["max_shares"]), int(budget // unit_cost)))


def _summary(initial: float, final: float, trades: list[dict[str, Any]], equity: list[float]) -> dict[str, Any]:
    closed = [item for item in trades if item["side"] == "SELL"]
    wins = [item for item in closed if item.get("pnl", 0) > 0]
    peaks = np.maximum.accumulate(np.asarray(equity or [initial], dtype=float))
    values = np.asarray(equity or [initial], dtype=float)
    drawdown = values / peaks - 1
    return {
        "initial_cash": initial,
        "final_equity": final,
        "return_pct": (final / initial - 1) * 100,
        "round_trips": len(closed),
        "win_rate_pct": (len(wins) / len(closed) * 100) if closed else None,
        "max_drawdown_pct": float(drawdown.min() * 100),
    }


def run_probability_backtest(
    market: pd.DataFrame,
    metadata: pd.DataFrame,
    probabilities: np.ndarray,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """訊號收盤後產生，所有一般買賣都在下一交易日開盤成交。"""
    initial = float(rules["initial_cash"])
    fee = float(rules["commission_rate"])
    tax = float(rules["transaction_tax_rate"])
    stop_loss = float(rules["stop_loss"])
    buy_threshold = float(rules["buy_probability"])
    sell_threshold = float(rules["sell_probability"])
    max_holding = int(rules.get("max_holding_sessions", 3))
    probability_by_key = {
        (str(row.stock_code), str(row.signal_date)): float(probabilities[i])
        for i, row in metadata.reset_index(drop=True).iterrows()
    }
    results: dict[str, Any] = {}
    for code, stock_market in market.groupby("stock_code", sort=True):
        stock = stock_market.sort_values("trade_date").reset_index(drop=True)
        cash, shares, entry_price, entry_cost = initial, 0, 0.0, 0.0
        held_sessions = 0
        pending: str | None = None
        trades: list[dict[str, Any]] = []
        equity: list[float] = []
        for _, day in stock.iterrows():
            date = str(day["trade_date"])
            open_price, low, close = float(day["open"]), float(day["low"]), float(day["close"])
            if pending == "SELL" and shares:
                gross = shares * open_price
                proceeds = gross * (1 - fee - tax)
                pnl = proceeds - entry_cost
                cash += proceeds
                trades.append({"date": date, "side": "SELL", "price": open_price, "shares": shares, "reason": "next_open_signal_or_time", "pnl": pnl})
                shares, entry_price, entry_cost, held_sessions = 0, 0.0, 0.0, 0
            elif pending == "BUY" and not shares:
                quantity = _buy_shares(cash, open_price, rules)
                if quantity:
                    cost = quantity * open_price * (1 + fee)
                    cash -= cost
                    shares, entry_price, entry_cost, held_sessions = quantity, open_price, cost, 0
                    trades.append({"date": date, "side": "BUY", "price": open_price, "shares": shares, "reason": "probability"})
            pending = None

            if shares:
                stop_price = entry_price * (1 - stop_loss)
                if low <= stop_price:
                    execution = min(open_price, stop_price) if open_price < stop_price else stop_price
                    gross = shares * execution
                    proceeds = gross * (1 - fee - tax)
                    pnl = proceeds - entry_cost
                    cash += proceeds
                    trades.append({"date": date, "side": "SELL", "price": execution, "shares": shares, "reason": "stop_loss", "pnl": pnl})
                    shares, entry_price, entry_cost, held_sessions = 0, 0.0, 0.0, 0
                else:
                    held_sessions += 1

            probability = probability_by_key.get((str(code), date))
            if shares and (held_sessions >= max_holding or (probability is not None and probability <= sell_threshold)):
                pending = "SELL"
            elif not shares and probability is not None and probability >= buy_threshold:
                pending = "BUY"
            equity.append(cash + shares * close)

        if shares:
            last = stock.iloc[-1]
            price = float(last["close"])
            proceeds = shares * price * (1 - fee - tax)
            pnl = proceeds - entry_cost
            cash += proceeds
            trades.append({"date": str(last["trade_date"]), "side": "SELL", "price": price, "shares": shares, "reason": "final_liquidation", "pnl": pnl})
            shares = 0
            equity[-1] = cash
        results[str(code)] = {"metrics": _summary(initial, cash, trades, equity), "trades": trades}
    return results


def buy_and_hold(market: pd.DataFrame, rules: dict[str, Any]) -> dict[str, Any]:
    initial = float(rules["initial_cash"])
    fee = float(rules["commission_rate"])
    tax = float(rules["transaction_tax_rate"])
    output: dict[str, Any] = {}
    for code, stock_market in market.groupby("stock_code", sort=True):
        stock = stock_market.sort_values("trade_date")
        entry = float(stock.iloc[0]["open"])
        exit_price = float(stock.iloc[-1]["close"])
        shares = _buy_shares(initial, entry, rules)
        final = initial - shares * entry * (1 + fee) + shares * exit_price * (1 - fee - tax)
        output[str(code)] = {"initial_cash": initial, "final_equity": final, "return_pct": (final / initial - 1) * 100}
    return output

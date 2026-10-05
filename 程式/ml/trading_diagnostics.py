"""ML Trading V1 的機率、訊號分布與跨股票穩定度診斷。"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def prediction_frame(model: str, split: str, metadata: pd.DataFrame, labels: np.ndarray,
                     probabilities: np.ndarray, buy_threshold: float, sell_threshold: float) -> pd.DataFrame:
    frame = metadata.reset_index(drop=True).copy()
    frame["model"] = model
    frame["split"] = split
    frame["label"] = labels.astype(int)
    frame["probability"] = probabilities.astype(float)
    frame["signal"] = np.where(
        frame["probability"] >= buy_threshold, "BUY",
        np.where(frame["probability"] <= sell_threshold, "SELL", "HOLD"),
    )
    return frame


def probability_diagnostics(frame: pd.DataFrame, buy_threshold: float) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for code, group in frame.groupby("stock_code", sort=True):
        counts = group["signal"].value_counts()
        quantiles = group["probability"].quantile([0, .1, .25, .5, .75, .9, 1])
        buy_count = int(counts.get("BUY", 0))
        output[str(code)] = {
            "samples": int(len(group)),
            "positive_labels": int(group["label"].sum()),
            "positive_label_rate": float(group["label"].mean()),
            "signal_counts": {key: int(counts.get(key, 0)) for key in ("BUY", "HOLD", "SELL")},
            "signal_rates": {key: float(counts.get(key, 0) / len(group)) for key in ("BUY", "HOLD", "SELL")},
            "probability_quantiles": {str(index): float(value) for index, value in quantiles.items()},
            "mean_probability": float(group["probability"].mean()),
            "max_probability": float(group["probability"].max()),
            "zero_buy_reason": (
                f"最高機率 {group['probability'].max():.4f} 低於買進門檻 {buy_threshold:.2f}"
                if buy_count == 0 else None
            ),
        }
    return output


def stability_summary(by_stock: dict[str, Any]) -> dict[str, Any]:
    metrics = [payload["metrics"] for payload in by_stock.values()]
    returns = np.asarray([item["return_pct"] for item in metrics], dtype=float)
    sharpes = [item["sharpe_ratio"] for item in metrics if item["sharpe_ratio"] is not None]
    factors = [item["profit_factor"] for item in metrics if item["profit_factor"] is not None]
    drawdowns = np.asarray([item["max_drawdown_pct"] for item in metrics], dtype=float)
    return {
        "stocks": len(metrics),
        "active_stocks": int(sum(item["round_trips"] > 0 for item in metrics)),
        "profitable_stocks": int(sum(item["return_pct"] > 0 for item in metrics)),
        "total_round_trips": int(sum(item["round_trips"] for item in metrics)),
        "mean_return_pct": float(returns.mean()),
        "median_return_pct": float(np.median(returns)),
        "return_std_pct": float(returns.std(ddof=1)) if len(returns) > 1 else 0.0,
        "worst_return_pct": float(returns.min()),
        "best_return_pct": float(returns.max()),
        "mean_sharpe_ratio": float(np.mean(sharpes)) if sharpes else None,
        "mean_profit_factor": float(np.mean(factors)) if factors else None,
        "mean_max_drawdown_pct": float(drawdowns.mean()),
        "worst_max_drawdown_pct": float(drawdowns.min()),
    }

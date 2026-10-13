"""Leakage-safe label construction for the ML V1 Training-only audit."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

LABEL_NAMES = {0: "SELL", 1: "HOLD", 2: "BUY"}
WARMUP_INSUFFICIENT = "INSUFFICIENT_20D_VOLATILITY"
WARMUP_ELIGIBLE = "ELIGIBLE"
VOLATILITY_NON_POSITIVE = "NON_POSITIVE_20D_VOLATILITY"


def aggregate_daily_market(kbars: pd.DataFrame) -> pd.DataFrame:
    """Aggregate already-authorized intraday bars without filling missing observations."""
    required = {"stock_code", "datetime", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(kbars.columns))
    if missing:
        raise ValueError("Missing K-bar columns: " + ", ".join(missing))
    frame = kbars.copy().sort_values(["stock_code", "datetime"])
    frame["trade_date"] = pd.to_datetime(frame["datetime"]).dt.date
    return (
        frame.groupby(["stock_code", "trade_date"], as_index=False)
        .agg(
            open=("open", "first"), high=("high", "max"), low=("low", "min"),
            close=("close", "last"), volume=("volume", "sum"),
            first_datetime=("datetime", "first"), last_datetime=("datetime", "last"),
            kbar_count=("datetime", "size"),
        )
        .sort_values(["stock_code", "trade_date"])
        .reset_index(drop=True)
    )


def build_label_frame(
    daily: pd.DataFrame,
    *,
    holding_sessions: int = 3,
    volatility_lookback: int = 20,
    commission_rate: float = 0.001425,
    transaction_tax_rate: float = 0.003,
    k: float = 0.5,
) -> pd.DataFrame:
    """Create common Fixed/Volatility label samples using only Day T-or-earlier sigma.

    The cost-aware future return is calculated exactly once from Day T+1 open to
    Day T+3 close. Warmup rows remain in the returned frame with an explicit
    status so the audit can account for every exclusion.
    """
    if holding_sessions != 3:
        raise ValueError("ML V1 Phase A fixes holding_sessions=3")
    if volatility_lookback != 20:
        raise ValueError("ML V1 Phase A fixes volatility_lookback=20")
    if not math.isclose(k, 0.5):
        raise ValueError("ML V1 Confirmatory Experiment fixes K=0.5; K search is forbidden")

    required = {"stock_code", "trade_date", "open", "close"}
    missing = sorted(required - set(daily.columns))
    if missing:
        raise ValueError("Missing daily columns: " + ", ".join(missing))

    pieces: list[pd.DataFrame] = []
    for code, source in daily.groupby("stock_code", sort=True):
        g = source.sort_values("trade_date").reset_index(drop=True).copy()
        g["daily_return"] = g["close"].pct_change(fill_method=None)
        # Non-centered trailing window includes Day T and never uses a future row.
        g["sigma_20d"] = g["daily_return"].rolling(
            volatility_lookback, min_periods=volatility_lookback, center=False
        ).std(ddof=1)
        g["sigma_3d"] = g["sigma_20d"] * math.sqrt(holding_sessions)
        g["entry_date"] = g["trade_date"].shift(-1)
        g["entry_open"] = g["open"].shift(-1)
        g["target_date"] = g["trade_date"].shift(-holding_sessions)
        g["target_close"] = g["close"].shift(-holding_sessions)
        g["future_net_return"] = (
            g["target_close"] * (1.0 - commission_rate - transaction_tax_rate)
            / (g["entry_open"] * (1.0 + commission_rate))
            - 1.0
        )
        g["warmup_status"] = np.select(
            [~np.isfinite(g["sigma_20d"]), g["sigma_20d"] <= 0],
            [WARMUP_INSUFFICIENT, VOLATILITY_NON_POSITIVE],
            default=WARMUP_ELIGIBLE,
        )
        g["volatility_threshold"] = k * g["sigma_3d"]
        g["z_return"] = g["future_net_return"] / g["sigma_3d"]
        g["fixed_label"] = np.where(
            g["future_net_return"] <= -0.01,
            0,
            np.where(g["future_net_return"] >= 0.01, 2, 1),
        )
        g["volatility_label"] = np.where(
            g["z_return"] <= -k,
            0,
            np.where(g["z_return"] >= k, 2, 1),
        )
        g["stock_code"] = str(code)
        # Rows without a complete future target are not samples and are distinct
        # from volatility warmup exclusions.
        pieces.append(g.loc[g["target_date"].notna()].copy())

    result = pd.concat(pieces, ignore_index=True)
    result["fixed_label"] = result["fixed_label"].astype(np.int64)
    result["volatility_label"] = result["volatility_label"].astype(np.int64)
    return result


def eligible_common_samples(frame: pd.DataFrame) -> pd.DataFrame:
    """Use the same volatility-eligible sample set for both label versions."""
    mask = (
        frame["warmup_status"].eq(WARMUP_ELIGIBLE)
        & np.isfinite(frame["future_net_return"])
        & np.isfinite(frame["sigma_20d"])
        & np.isfinite(frame["sigma_3d"])
        & np.isfinite(frame["z_return"])
        & (frame["sigma_3d"] > 0)
    )
    return frame.loc[mask].reset_index(drop=True)


def label_distribution(labels: pd.Series | np.ndarray) -> dict[str, Any]:
    values = np.asarray(labels, dtype=np.int64)
    total = int(len(values))
    classes: dict[str, Any] = {}
    for value, name in LABEL_NAMES.items():
        count = int((values == value).sum())
        classes[name] = {"count": count, "proportion": float(count / total) if total else None}
    return {"samples": total, "classes": classes}


def finite_distribution(values: pd.Series | np.ndarray) -> dict[str, Any]:
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    if not len(a):
        return {key: None for key in (
            "count", "mean", "median", "standard_deviation", "min", "max",
            "q10", "q25", "q50", "q75", "q90",
        )}
    return {
        "count": int(len(a)),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "standard_deviation": float(np.std(a, ddof=1)) if len(a) > 1 else 0.0,
        "min": float(np.min(a)),
        "max": float(np.max(a)),
        "q10": float(np.quantile(a, 0.10)),
        "q25": float(np.quantile(a, 0.25)),
        "q50": float(np.quantile(a, 0.50)),
        "q75": float(np.quantile(a, 0.75)),
        "q90": float(np.quantile(a, 0.90)),
    }

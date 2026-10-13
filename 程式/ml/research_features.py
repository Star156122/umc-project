"""Causal daily stock features and exploratory industry feature interfaces."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ml.research_universe import (
    build_leave_one_out_proxy_daily_returns,
    industry_membership,
    leave_one_stock_out_industry_return,
)
from ml.trading_pipeline import _daily_market

STOCK_FEATURES = ["stock_return_1d", "stock_return_3d", "stock_return_5d", "stock_return_20d", "volume_trend_5d",
    "volume_trend_20d", "volatility_5d", "volatility_20d", "volatility_ratio_5d_20d",
    "stock_volatility_20d", "distance_from_high_20d", "distance_from_low_20d", "trend_strength_5d", "trend_strength_20d"]

INDUSTRY_FEATURES = ["industry_return_1d", "industry_return_3d", "industry_return_5d",
    "industry_volatility_5d", "industry_volatility_20d", "stock_vs_industry_return_1d",
    "stock_vs_industry_return_3d", "stock_vs_industry_return_5d", "industry_vs_universe_return_1d",
    "industry_vs_universe_return_3d", "industry_vs_universe_return_5d"]

CONTEXT_CANDIDATE_FEATURES = [
    "stock_return_5d",
    "stock_return_20d",
    "stock_volatility_20d",
    "industry_proxy_return_5d",
    "industry_proxy_return_20d",
    "stock_vs_industry_proxy_5d",
    "market_proxy_return_5d",
    "market_proxy_return_20d",
    "stock_vs_market_proxy_5d",
]


def build_daily_stock_features(featured_intraday: pd.DataFrame) -> pd.DataFrame:
    daily = _daily_market(featured_intraday).sort_values(["stock_code", "trade_date"]).copy()
    pieces = []
    for _, g0 in daily.groupby("stock_code", sort=False):
        g = g0.copy()
        close, volume = g["close"], g["volume"]
        daily_return = close.pct_change(fill_method=None)
        for horizon in (1, 3, 5, 20):
            g[f"stock_return_{horizon}d"] = close.pct_change(horizon, fill_method=None)
        for horizon in (5, 20):
            g[f"volume_trend_{horizon}d"] = volume / volume.rolling(horizon, min_periods=horizon).mean().replace(0, np.nan) - 1
            g[f"volatility_{horizon}d"] = daily_return.rolling(horizon, min_periods=horizon).std()
            raw_trend = close.pct_change(horizon, fill_method=None)
            g[f"trend_strength_{horizon}d"] = raw_trend / (g[f"volatility_{horizon}d"] * np.sqrt(horizon)).replace(0, np.nan)
        g["volatility_ratio_5d_20d"] = g["volatility_5d"] / g["volatility_20d"].replace(0, np.nan)
        g["stock_volatility_20d"] = g["volatility_20d"]
        g["distance_from_high_20d"] = close / close.rolling(20, min_periods=20).max() - 1
        g["distance_from_low_20d"] = close / close.rolling(20, min_periods=20).min() - 1
        pieces.append(g)
    return pd.concat(pieces, ignore_index=True)


def attach_stock_features(featured_intraday: pd.DataFrame, feature_names: list[str]) -> pd.DataFrame:
    unknown = sorted(set(feature_names) - set(STOCK_FEATURES))
    if unknown:
        raise ValueError(f"未知個股研究 Features：{unknown}")
    if not feature_names:
        return featured_intraday.copy()
    daily = build_daily_stock_features(featured_intraday)
    columns = ["stock_code", "trade_date", *feature_names]
    return featured_intraday.merge(daily[columns], on=["stock_code", "trade_date"], how="left", validate="many_to_one")


def _strict_compound_return(values: pd.Series, horizon: int) -> pd.Series:
    """Compound exactly ``horizon`` finite daily returns without filling gaps."""
    return values.rolling(horizon, min_periods=horizon, center=False).apply(
        lambda window: float(np.prod(1.0 + window) - 1.0), raw=True
    )


def build_context_candidate_features(
    featured_intraday: pd.DataFrame,
    industry_groups: dict[str, list[str]],
    core_universe: list[str],
) -> pd.DataFrame:
    """Build the fixed nine-feature ML Improvement Round 1 candidate.

    Stock features use only closes through Day T. Proxy features first build a
    strict leave-one-stock-out daily return and then compound trailing 5/20-day
    windows. A missing peer return makes the affected window unavailable.
    """
    daily = build_daily_stock_features(featured_intraday)
    frame = build_leave_one_out_proxy_daily_returns(daily, industry_groups, core_universe)
    pieces = []
    for _, group in frame.groupby("stock_code", sort=False):
        current = group.sort_values("trade_date").copy()
        for horizon in (5, 20):
            current[f"industry_proxy_return_{horizon}d"] = _strict_compound_return(
                current["industry_proxy_return_1d"], horizon
            )
            current[f"market_proxy_return_{horizon}d"] = _strict_compound_return(
                current["market_proxy_return_1d"], horizon
            )
        current["stock_vs_industry_proxy_5d"] = (
            current["stock_return_5d"] - current["industry_proxy_return_5d"]
        )
        current["stock_vs_market_proxy_5d"] = (
            current["stock_return_5d"] - current["market_proxy_return_5d"]
        )
        pieces.append(current)
    result = pd.concat(pieces, ignore_index=True)
    if len(CONTEXT_CANDIDATE_FEATURES) != 9:
        raise RuntimeError("Context Candidate must contain exactly nine features")
    return result


def build_exploratory_industry_features(daily_stock: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Feature Set C interface. Current two-stock industries remain exploratory only."""
    frame = daily_stock.copy()
    frame["industry"] = frame["stock_code"].astype(str).map(industry_membership(config))
    for horizon in (1, 3, 5):
        frame[f"industry_return_{horizon}d"] = leave_one_stock_out_industry_return(frame, config, horizon)
        frame[f"stock_vs_industry_return_{horizon}d"] = frame[f"stock_return_{horizon}d"] - frame[f"industry_return_{horizon}d"]
        universe_proxy = frame.groupby("trade_date")[f"stock_return_{horizon}d"].transform("mean")
        frame[f"industry_vs_universe_return_{horizon}d"] = frame[f"industry_return_{horizon}d"] - universe_proxy
    for horizon in (5, 20):
        frame[f"industry_volatility_{horizon}d"] = frame.groupby("stock_code")["industry_return_1d"].transform(
            lambda x: x.rolling(horizon, min_periods=horizon).std())
    return frame


def causal_feature_definitions() -> dict[str, str]:
    return {
        "stock_return_1d": "close_t / close_t-1 - 1", "stock_return_3d": "close_t / close_t-3 - 1",
        "stock_return_5d": "close_t / close_t-5 - 1", "stock_return_20d": "close_t / close_t-20 - 1",
        "volume_trend_5d": "volume_t / trailing_mean_5 - 1",
        "volume_trend_20d": "volume_t / trailing_mean_20 - 1", "volatility_5d": "trailing std of daily_return, 5 days",
        "volatility_20d": "trailing std of daily_return, 20 days", "volatility_ratio_5d_20d": "volatility_5d / volatility_20d",
        "distance_from_high_20d": "close_t / trailing_highest_close_20 - 1",
        "distance_from_low_20d": "close_t / trailing_lowest_close_20 - 1",
        "trend_strength_5d": "stock_return_5d / (volatility_5d * sqrt(5))",
        "trend_strength_20d": "stock_return_20d / (volatility_20d * sqrt(20))",
        "stock_volatility_20d": "trailing std of daily_return through Day T, 20 days",
        "industry_proxy_return_5d": "compound trailing 5-day equal-weight return of the other 4 industry stocks",
        "industry_proxy_return_20d": "compound trailing 20-day equal-weight return of the other 4 industry stocks",
        "stock_vs_industry_proxy_5d": "stock_return_5d - industry_proxy_return_5d",
        "market_proxy_return_5d": "compound trailing 5-day equal-weight return of the other 9 core stocks",
        "market_proxy_return_20d": "compound trailing 20-day equal-weight return of the other 9 core stocks",
        "stock_vs_market_proxy_5d": "stock_return_5d - market_proxy_return_5d",
    }

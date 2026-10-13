"""Config-driven stock and exploratory industry universe helpers."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def validate_stock_groups(config: dict[str, Any], allowed_stocks: list[str] | None = None) -> None:
    """Validate config structure without pinning V4 to the V3 six-stock universe."""
    groups = config["stock_groups"]
    for name, stocks in groups.items():
        normalized = list(map(str, stocks))
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError(f"股票群組 {name} 為空或含重複股票。")
        if not all(code.isdigit() and len(code) == 4 for code in normalized):
            raise ValueError(f"股票群組 {name} 含無效股票代碼。")


def enabled_stock_union(config: dict[str, Any]) -> list[str]:
    enabled = [item for item in config["experiments"] if item.get("enabled")]
    return sorted({code for item in enabled for code in stock_group(config, item["stock_group"])})


def enforce_industry_reliability(config: dict[str, Any], experiment: dict[str, Any], feature_set: dict[str, Any]) -> None:
    """Block formal industry claims when a configured industry has too few members."""
    if not experiment.get("formal") or not feature_set.get("industry_features"):
        return
    minimum = int(feature_set.get("minimum_reliable_industry_members", 3))
    selected = set(stock_group(config, experiment["stock_group"]))
    membership = config["industry_groups"]
    insufficient = {
        name: len(selected.intersection(map(str, members)))
        for name, members in membership.items()
        if selected.intersection(map(str, members)) and len(selected.intersection(map(str, members))) < minimum
    }
    if insufficient:
        raise ValueError(f"正式產業實驗成員不足（minimum={minimum}）：{insufficient}")


def stock_group(config: dict[str, Any], name: str) -> list[str]:
    if name not in config["stock_groups"]:
        raise KeyError(f"未知股票群組：{name}")
    return list(map(str, config["stock_groups"][name]))


def industry_membership(config: dict[str, Any]) -> dict[str, str]:
    result = {}
    for industry, stocks in config["industry_groups"].items():
        for stock in stocks:
            if str(stock) in result:
                raise ValueError(f"股票 {stock} 被分配到多個產業群組。")
            result[str(stock)] = industry
    return result


def leave_one_stock_out_industry_return(daily_returns: pd.DataFrame, config: dict[str, Any], horizon: int) -> pd.Series:
    """Exploratory interface: mean peer return, explicitly excluding the target stock."""
    membership = industry_membership(config)
    frame = daily_returns[["stock_code", "trade_date", f"stock_return_{horizon}d"]].copy()
    frame["industry"] = frame["stock_code"].astype(str).map(membership)
    value = f"stock_return_{horizon}d"
    totals = frame.groupby(["industry", "trade_date"])[value].transform("sum")
    counts = frame.groupby(["industry", "trade_date"])[value].transform("count")
    peers = counts - frame[value].notna().astype(int)
    return (totals - frame[value].fillna(0)) / peers.replace(0, pd.NA)


def validate_context_proxy_universe(
    industry_groups: dict[str, list[str]], core_universe: list[str]
) -> dict[str, str]:
    """Validate the fixed ML V1 universe and return stock-to-industry membership."""
    normalized_core = list(map(str, core_universe))
    if not normalized_core or len(normalized_core) != len(set(normalized_core)):
        raise ValueError("Core universe must be non-empty and contain unique stocks")

    membership: dict[str, str] = {}
    for industry, members in industry_groups.items():
        normalized_members = list(map(str, members))
        if len(normalized_members) != 5 or len(set(normalized_members)) != 5:
            raise ValueError(f"Industry {industry} must contain exactly five unique stocks")
        for code in normalized_members:
            if code in membership:
                raise ValueError(f"Stock {code} belongs to more than one industry")
            membership[code] = str(industry)
    if set(membership) != set(normalized_core):
        raise ValueError("Industry groups must exactly cover the core universe")
    return membership


def build_leave_one_out_proxy_daily_returns(
    daily_stock: pd.DataFrame,
    industry_groups: dict[str, list[str]],
    core_universe: list[str],
) -> pd.DataFrame:
    """Attach strict leave-one-stock-out industry and market daily-return proxies.

    A proxy is available only when every required peer has a finite return for
    that date. Missing components remain NaN; no fill or synthetic return is
    created.
    """
    required = {"stock_code", "trade_date", "stock_return_1d"}
    missing = sorted(required - set(daily_stock.columns))
    if missing:
        raise ValueError(f"Proxy input is missing columns: {missing}")

    membership = validate_context_proxy_universe(industry_groups, core_universe)
    core = list(map(str, core_universe))
    frame = daily_stock.copy()
    frame["stock_code"] = frame["stock_code"].astype(str)
    frame = frame.loc[frame["stock_code"].isin(core)].copy()
    if frame[["stock_code", "trade_date"]].duplicated().any():
        raise ValueError("Proxy input contains duplicate stock-date rows")
    if set(frame["stock_code"].unique()) != set(core):
        raise ValueError("Proxy input does not contain the complete core universe")

    pivot = frame.pivot(index="trade_date", columns="stock_code", values="stock_return_1d")
    pivot = pivot.reindex(columns=core)
    industry_values: list[float] = []
    market_values: list[float] = []
    industry_counts: list[int] = []
    market_counts: list[int] = []

    normalized_groups = {name: list(map(str, stocks)) for name, stocks in industry_groups.items()}
    for row in frame.itertuples(index=False):
        code = str(row.stock_code)
        date = row.trade_date
        industry_peers = [peer for peer in normalized_groups[membership[code]] if peer != code]
        market_peers = [peer for peer in core if peer != code]

        industry_returns = pd.to_numeric(pivot.loc[date, industry_peers], errors="coerce").to_numpy(dtype=float)
        market_returns = pd.to_numeric(pivot.loc[date, market_peers], errors="coerce").to_numpy(dtype=float)
        industry_valid = int(np.isfinite(industry_returns).sum())
        market_valid = int(np.isfinite(market_returns).sum())
        industry_counts.append(industry_valid)
        market_counts.append(market_valid)
        industry_values.append(float(industry_returns.mean()) if industry_valid == 4 else np.nan)
        market_values.append(float(market_returns.mean()) if market_valid == 9 else np.nan)

    frame["industry_proxy_return_1d"] = industry_values
    frame["market_proxy_return_1d"] = market_values
    frame["industry_proxy_component_count"] = industry_counts
    frame["market_proxy_component_count"] = market_counts
    return frame

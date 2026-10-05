"""Config-driven stock and exploratory industry universe helpers."""
from __future__ import annotations

from typing import Any

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

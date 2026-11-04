"""Causal, transparent expected-edge estimator for the isolated Exp001 study."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Any, Iterable


@dataclass(frozen=True)
class EdgeEstimate:
    status: str
    matured_trade_count: int
    latest_training_exit_timestamp: int | None
    estimated_gross_return: float | None
    estimated_gross_pnl: float | None
    estimated_cost: float | None
    estimated_net_pnl: float | None
    estimated_net_return: float | None
    estimated_quantity: int


def prior_completed_trade_returns(
    completed_trades: Iterable[dict[str, Any]], candidate_timestamp: int,
) -> list[dict[str, Any]]:
    """Return only valid trades whose exits occurred strictly before the signal."""
    result = []
    for trade in completed_trades:
        exit_ts = int(trade["exit_timestamp"])
        gross_return = float(trade["gross_return"])
        if exit_ts < int(candidate_timestamp) and gross_return == gross_return:
            result.append({"exit_timestamp": exit_ts, "gross_return": gross_return})
    return sorted(result, key=lambda row: row["exit_timestamp"])


def estimate_expected_net_edge(
    *, completed_trades: Iterable[dict[str, Any]], candidate_timestamp: int,
    entry_price: float, quantity: int, min_matured_trades: int,
    commission_rate: float, tax_rate: float, slippage_bps_per_side: float,
) -> EdgeEstimate:
    """Estimate expected net PnL from prior gross returns and round-trip costs.

    Historical outcomes are filtered by their exit timestamp before the estimator
    sees them. Ordinary stock tax is used conservatively; no future same-day tax
    classification or realized candidate outcome is consulted.
    """
    if entry_price <= 0 or quantity < 0 or min_matured_trades < 1:
        raise ValueError("Invalid edge-estimate inputs")
    if not 0 <= commission_rate < 1 or not 0 <= tax_rate < 1 or slippage_bps_per_side < 0:
        raise ValueError("Invalid cost assumptions")
    prior = prior_completed_trade_returns(completed_trades, candidate_timestamp)
    q = int(quantity)
    if len(prior) < min_matured_trades or q <= 0:
        return EdgeEstimate(
            status="INSUFFICIENT_MATURED_BASELINE_TRADES" if len(prior) < min_matured_trades else "NO_ESTIMABLE_QUANTITY",
            matured_trade_count=len(prior),
            latest_training_exit_timestamp=prior[-1]["exit_timestamp"] if prior else None,
            estimated_gross_return=None, estimated_gross_pnl=None, estimated_cost=None,
            estimated_net_pnl=None, estimated_net_return=None, estimated_quantity=q,
        )

    expected_return = fmean(row["gross_return"] for row in prior)
    exit_price = max(0.01, float(entry_price) * (1.0 + expected_return))
    buy_notional = float(entry_price) * q
    sell_notional = exit_price * q
    slip_rate = float(slippage_bps_per_side) / 10_000.0
    costs = (
        buy_notional * commission_rate
        + sell_notional * commission_rate
        + sell_notional * tax_rate
        + buy_notional * slip_rate
        + sell_notional * slip_rate
    )
    gross_pnl = (exit_price - float(entry_price)) * q
    net_pnl = gross_pnl - costs
    return EdgeEstimate(
        status="ALLOW_EXPECTED_NET_POSITIVE" if net_pnl > 0 else "BLOCK_EXPECTED_NET_NOT_POSITIVE",
        matured_trade_count=len(prior),
        latest_training_exit_timestamp=prior[-1]["exit_timestamp"],
        estimated_gross_return=expected_return,
        estimated_gross_pnl=gross_pnl,
        estimated_cost=costs,
        estimated_net_pnl=net_pnl,
        estimated_net_return=net_pnl / buy_notional if buy_notional else None,
        estimated_quantity=q,
    )


def validate_no_lookahead(rows: Iterable[dict[str, Any]]) -> None:
    """Raise if any saved edge decision includes an outcome exiting at/after signal."""
    for row in rows:
        latest = row.get("latest_training_exit_timestamp")
        signal = row.get("completed_signal_timestamp")
        if latest is not None and signal is not None and int(latest) >= int(signal):
            raise AssertionError("Expected-edge estimator used an outcome not yet mature at signal time")

"""Pure diagnostic helpers for the fixed multi-strategy development replay.

The helpers in this module never read market data on their own.  Callers must
first pass the normal research guards and provide only the already-authorized
bars used by the replay.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np


TAIPEI = ZoneInfo("Asia/Taipei")


def _timestamp(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI)
    return int(parsed.timestamp())


def candidate_event_id(row: dict[str, Any]) -> str:
    """Return a deterministic per-signal id while preserving the rule id."""
    identity = "|".join((
        str(row.get("stock_code", "")).zfill(4),
        str(row.get("earliest_entry_time", "")),
        str(row.get("candidate_id", "")),
        str(row.get("parameter_fingerprint", "")),
    ))
    return "CAND-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def probability_summary(values: Iterable[float]) -> dict[str, float | int | None]:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return {key: None for key in (
            "count", "min", "max", "mean", "median", "p10", "p25", "p75", "p90",
        )}
    return {
        "count": int(len(array)),
        "min": float(array.min()),
        "max": float(array.max()),
        "mean": float(array.mean()),
        "median": float(np.median(array)),
        "p10": float(np.quantile(array, 0.10)),
        "p25": float(np.quantile(array, 0.25)),
        "p75": float(np.quantile(array, 0.75)),
        "p90": float(np.quantile(array, 0.90)),
    }


def pnl_decomposition(
    pnl_rows: Iterable[dict[str, Any]],
    initial_capital: float | None = None,
) -> dict[str, float | int | None]:
    rows = list(pnl_rows)
    gross = sum(float(row.get("gross_pnl", 0.0)) for row in rows)
    commission = sum(float(row.get("fee", 0.0)) for row in rows)
    tax = sum(float(row.get("tax", 0.0)) for row in rows)
    slippage = sum(float(row.get("slippage", 0.0)) for row in rows)
    net = sum(float(row.get("net_pnl", 0.0)) for row in rows)
    identity_delta = gross - commission - tax - slippage - net
    if not math.isclose(identity_delta, 0.0, abs_tol=1e-8):
        raise ValueError("PNL_IDENTITY_MISMATCH")
    total_cost = commission + tax + slippage
    positive_gross_profit = sum(
        max(0.0, float(row.get("gross_pnl", 0.0))) for row in rows
    )
    return {
        "gross_pnl": gross,
        "commission": commission,
        "tax": tax,
        "slippage": slippage,
        "total_cost": total_cost,
        "net_pnl": net,
        "identity_delta": identity_delta,
        "completed_trade_count": len(rows),
        "average_cost_per_trade": total_cost / len(rows) if rows else None,
        "positive_gross_profit": positive_gross_profit,
        "cost_to_positive_gross_profit_ratio": (
            total_cost / positive_gross_profit if positive_gross_profit else None
        ),
        "cost_to_absolute_gross_pnl_ratio": total_cost / abs(gross) if gross else None,
        "cost_to_initial_capital_ratio": (
            total_cost / initial_capital if initial_capital else None
        ),
    }


def exposure_metrics(
    curve: Iterable[dict[str, Any]],
    pnl_rows: Iterable[dict[str, Any]],
    initial_capital: float,
) -> dict[str, float | None]:
    points = list(curve)
    fractions = []
    invested_when_active = []
    for point in points:
        position_value = float(point.get("position_value", 0.0))
        equity = float(point.get("equity", 0.0))
        fractions.append(position_value / equity if equity else 0.0)
        if position_value > 0:
            invested_when_active.append(position_value)
    active = sum(value > 0 for value in fractions)
    breakdown = pnl_decomposition(pnl_rows, initial_capital)
    average_invested = float(np.mean(invested_when_active)) if invested_when_active else None
    return {
        "time_in_market": active / len(points) if points else 0.0,
        "average_exposure": float(np.mean(fractions)) if fractions else 0.0,
        "max_exposure": max(fractions, default=0.0),
        "average_invested_capital_when_active": average_invested,
        "return_on_invested_capital": (
            breakdown["net_pnl"] / average_invested if average_invested else None
        ),
        "gross_return": breakdown["gross_pnl"] / initial_capital if initial_capital else None,
        "total_cost": breakdown["total_cost"],
        "net_return": breakdown["net_pnl"] / initial_capital if initial_capital else None,
    }


def _bar_index(bars: list[dict[str, Any]]) -> tuple[dict[int, int], list[int]]:
    stamps = [int(row.get("time", row.get("timestamp"))) for row in bars]
    return {stamp: index for index, stamp in enumerate(stamps)}, stamps


def annotate_technical_candidates(
    candidate_rows: Iterable[dict[str, Any]],
    bars: list[dict[str, Any]],
    *,
    horizon_bars: int,
    commission_rate: float,
    transaction_tax_rate: float,
    slippage_rate: float,
) -> list[dict[str, Any]]:
    """Add outcome-only fields to raw Technical candidates.

    Outcome values are diagnostic targets and are never passed back into the
    signal, gate, sizing, or execution code.
    """
    positions, _ = _bar_index(bars)
    output: list[dict[str, Any]] = []
    for source in candidate_rows:
        row = dict(source)
        event_id = candidate_event_id(row)
        index = positions.get(_timestamp(row["earliest_entry_time"]))
        annotated = {
            "candidate_id": event_id,
            "technical_candidate_id": row.get("candidate_id"),
            "stock_code": str(row.get("stock_code", "")).zfill(4),
            "signal_time": row.get("signal_time"),
            "candidate_entry_time": row.get("earliest_entry_time"),
            "technical_conditions": row.get("reason", ""),
            "market_regime": row.get("market_regime", "UNKNOWN"),
            "market_regime_pass": bool(row.get("market_regime_pass", True)),
            "market_regime_reason": row.get("market_regime_reason"),
            "eligible_for_gate": bool(row.get("market_regime_pass", True)),
            "not_entered_reason": (
                None if bool(row.get("market_regime_pass", True)) else "BLOCK_MARKET_REGIME"
            ),
            "fixed_horizon_bars": int(horizon_bars),
        }
        if index is None or index + horizon_bars - 1 >= len(bars):
            annotated.update({
                "candidate_entry_price": None,
                "fixed_horizon_exit_time": None,
                "fixed_horizon_exit_price": None,
                "fixed_horizon_gross_return": None,
                "estimated_round_trip_cost_return": None,
                "fixed_horizon_net_return": None,
                "fixed_horizon_mfe": None,
                "fixed_horizon_mae": None,
                "outcome_status": "INSUFFICIENT_FORWARD_BARS",
            })
            output.append(annotated)
            continue
        future_index = index + horizon_bars - 1
        window = bars[index : future_index + 1]
        entry = float(window[0]["open"])
        exit_price = float(window[-1]["close"])
        gross_return = exit_price / entry - 1.0
        net_return = (
            exit_price * (1.0 - commission_rate - transaction_tax_rate - slippage_rate)
            / (entry * (1.0 + commission_rate + slippage_rate))
            - 1.0
        )
        annotated.update({
            "candidate_entry_price": entry,
            "fixed_horizon_exit_time": window[-1]["datetime"],
            "fixed_horizon_exit_price": exit_price,
            "fixed_horizon_gross_return": gross_return,
            "estimated_round_trip_cost_return": gross_return - net_return,
            "fixed_horizon_net_return": net_return,
            "fixed_horizon_mfe": max(float(item["high"]) for item in window) / entry - 1.0,
            "fixed_horizon_mae": min(float(item["low"]) for item in window) / entry - 1.0,
            "outcome_status": "EVALUATED",
        })
        output.append(annotated)
    return output


def _paired_trades(result: dict[str, Any]) -> dict[int, dict[str, Any]]:
    open_trade: dict[str, Any] | None = None
    pairs: dict[int, dict[str, Any]] = {}
    sell_signals = sorted((
        (int(row["timestamp"]), row)
        for row in result.get("signals", [])
        if row.get("action") == "Sell"
    ), key=lambda item: item[0])
    pnl_lookup = {
        (str(row.get("buy_datetime")), str(row.get("sell_datetime"))): row
        for row in result.get("completed_trade_pnl", [])
    }
    for trade in sorted(result.get("trades", []), key=lambda row: int(row["timestamp"])):
        if trade.get("action") == "Buy":
            open_trade = trade
            pairs[int(open_trade["timestamp"])] = {
                "entry_timestamp": int(open_trade["timestamp"]),
                "entry_time": open_trade.get("datetime"),
                "entry_price": float(open_trade["price"]),
                "exit_timestamp": None,
                "exit_time": None,
                "exit_price": None,
                "exit_reason": None,
                "exit_reason_detail": None,
                "realized_gross_pnl": None,
                "realized_commission": None,
                "realized_tax": None,
                "realized_slippage": None,
                "realized_cost": None,
                "realized_net_pnl": None,
            }
        elif open_trade is not None:
            exit_signal = next((
                row for stamp, row in reversed(sell_signals)
                if stamp <= int(trade["timestamp"])
            ), {})
            pnl = pnl_lookup.get((str(open_trade.get("datetime")), str(trade.get("datetime"))), {})
            pairs[int(open_trade["timestamp"])].update({
                "entry_timestamp": int(open_trade["timestamp"]),
                "entry_time": open_trade.get("datetime"),
                "entry_price": float(open_trade["price"]),
                "exit_timestamp": int(trade["timestamp"]),
                "exit_time": trade.get("datetime"),
                "exit_price": float(trade["price"]),
                "exit_reason": trade.get("execution_reason") or exit_signal.get("signal_type", "UNKNOWN"),
                "exit_reason_detail": exit_signal.get("reason", trade.get("execution_reason", "")),
                "realized_gross_pnl": pnl.get("gross_pnl"),
                "realized_commission": pnl.get("fee"),
                "realized_tax": pnl.get("tax"),
                "realized_slippage": pnl.get("slippage", 0.0) if pnl else None,
                "realized_cost": (
                    float(pnl.get("fee", 0.0)) + float(pnl.get("tax", 0.0))
                    + float(pnl.get("slippage", 0.0))
                    if pnl else None
                ),
                "realized_net_pnl": pnl.get("net_pnl"),
            })
            open_trade = None
    return pairs


def build_gate_candidate_rows(
    candidates: Iterable[dict[str, Any]],
    results: Iterable[dict[str, Any]],
    prediction_lookups: dict[str, dict[tuple[str, str], dict[str, Any]]],
    bars: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Create one auditable row for every candidate and strategy gate."""
    result_map = {str(row["strategy_id"]): row for row in results}
    entries = {key: _paired_trades(value) for key, value in result_map.items()}
    bar_rows = list(bars or [])
    bar_positions, _ = _bar_index(bar_rows) if bar_rows else ({}, [])
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        event_id = str(candidate["candidate_id"])
        stamp = _timestamp(candidate["candidate_entry_time"])
        code = str(candidate["stock_code"]).zfill(4)
        entry_date = datetime.fromtimestamp(stamp, TAIPEI).date().isoformat()
        for strategy_id in ("technical", "rf", "xgboost", "gru"):
            if strategy_id not in result_map:
                continue
            actual_decision_times = {
                _timestamp(row.get("technical_earliest_entry_time") or row.get("technical_signal_time"))
                for row in result_map[strategy_id].get("details", {}).get("gate_decisions", [])
                if row.get("technical_earliest_entry_time") or row.get("technical_signal_time")
            }
            prediction = None
            if strategy_id == "technical":
                action, block_reason = "ENTRY", None
            else:
                prediction = prediction_lookups.get(strategy_id, {}).get((code, entry_date))
                if prediction is None:
                    action, block_reason = "BLOCK_MISSING_OR_LATE_PREDICTION", "MISSING_PREDICTION"
                else:
                    predicted = str(prediction["prediction"])
                    action = "ENTRY" if predicted == "BUY" else "BLOCK_" + predicted
                    block_reason = None if action == "ENTRY" else action
            pair = entries[strategy_id].get(stamp)
            entered = pair is not None
            actually_evaluated = stamp in actual_decision_times
            predicted_action = action
            skip_reason = None
            if not entered and not actually_evaluated:
                candidate_index = bar_positions.get(stamp)
                account_blocks = {
                    _timestamp(row["technical_earliest_entry_time"]): str(row.get("reason_code"))
                    for row in result_map[strategy_id].get("details", {}).get("account_state_blocks", [])
                    if row.get("technical_earliest_entry_time")
                }
                overlapping = False
                cooldown = False
                if candidate_index is not None:
                    for pair_candidate in entries[strategy_id].values():
                        entry_index = bar_positions.get(int(pair_candidate["entry_timestamp"]))
                        exit_index = bar_positions.get(int(pair_candidate["exit_timestamp"])) if pair_candidate["exit_timestamp"] is not None else None
                        if entry_index is not None and entry_index <= candidate_index and (exit_index is None or candidate_index <= exit_index):
                            overlapping = True
                            break
                        if exit_index is not None and exit_index < candidate_index <= exit_index + 6:
                            cooldown = True
                if stamp in account_blocks:
                    skip_reason = account_blocks[stamp]
                elif overlapping:
                    skip_reason = "BLOCK_ALREADY_IN_POSITION"
                elif cooldown:
                    skip_reason = "BLOCK_COOLDOWN"
                elif candidate_index is None or candidate_index >= len(bar_rows) - 1:
                    skip_reason = "NO_NEXT_TRADABLE_BAR"
                else:
                    skip_reason = "BLOCK_ENTRY_STATE_UNCLASSIFIED"
                action = "NOT_EVALUATED_ACCOUNT_STATE"
                block_reason = skip_reason
            trade_holding_bars = None
            trade_mfe = None
            trade_mae = None
            holding_minutes = None
            holding_trading_days = None
            max_favorable_price = None
            max_adverse_price = None
            if pair is not None and bar_rows:
                left = bar_positions.get(int(pair["entry_timestamp"]))
                right = (
                    bar_positions.get(int(pair["exit_timestamp"]))
                    if pair["exit_timestamp"] is not None else None
                )
                if left is not None:
                    right = len(bar_rows) - 1 if right is None else right
                if left is not None and right is not None and right >= left:
                    trade_window = bar_rows[left : right + 1]
                    trade_holding_bars = len(trade_window)
                    entry_price = float(pair["entry_price"])
                    max_favorable_price = max(float(item["high"]) for item in trade_window)
                    max_adverse_price = min(float(item["low"]) for item in trade_window)
                    trade_mfe = max_favorable_price / entry_price - 1.0
                    trade_mae = max_adverse_price / entry_price - 1.0
                    end_stamp = int(pair.get("exit_timestamp") or _timestamp(bar_rows[-1]["time"]))
                    holding_minutes = max(0, (end_stamp - int(pair["entry_timestamp"])) // 60)
                    holding_trading_days = len({str(item.get("datetime", ""))[:10] for item in trade_window})
            if entered:
                final_reason = "ENTERED"
            elif not candidate.get("market_regime_pass", True):
                final_reason = "BLOCK_MARKET_REGIME"
            elif not actually_evaluated:
                final_reason = skip_reason or "BLOCK_ACCOUNT_STATE"
            elif action != "ENTRY":
                final_reason = block_reason
            else:
                candidate_index = bar_positions.get(stamp)
                overlapping = False
                cooldown = False
                if candidate_index is not None:
                    for pair_candidate in entries[strategy_id].values():
                        entry_index = bar_positions.get(int(pair_candidate["entry_timestamp"]))
                        exit_index = bar_positions.get(int(pair_candidate["exit_timestamp"])) if pair_candidate["exit_timestamp"] is not None else None
                        if entry_index is not None and entry_index <= candidate_index and (exit_index is None or candidate_index <= exit_index):
                            overlapping = True
                            break
                        if exit_index is not None and exit_index < candidate_index <= exit_index + 6:
                            cooldown = True
                if overlapping:
                    final_reason = "BLOCK_ALREADY_IN_POSITION"
                elif cooldown:
                    final_reason = "BLOCK_COOLDOWN"
                elif candidate_index is None or candidate_index >= len(bar_rows) - 1:
                    final_reason = "NO_NEXT_TRADABLE_BAR"
                else:
                    final_reason = "BLOCK_ENTRY_STATE_UNCLASSIFIED"
            rows.append({
                **candidate,
                "strategy_id": strategy_id,
                "gate_rule": "NOT_APPLICABLE" if strategy_id == "technical" else "ARGMAX_SELL_HOLD_BUY",
                "gate_threshold": None,
                "predicted_class": None if prediction is None else prediction.get("prediction"),
                "prob_sell": None if prediction is None else prediction.get("prob_sell"),
                "prob_hold": None if prediction is None else prediction.get("prob_hold"),
                "prob_buy": None if prediction is None else prediction.get("prob_buy"),
                "gate_decision": action,
                "precomputed_prediction_action": predicted_action,
                "gate_actually_evaluated": actually_evaluated,
                "entered": entered,
                "not_entered_reason": final_reason,
                "exit_time": None if pair is None else pair["exit_time"],
                "exit_price": None if pair is None else pair["exit_price"],
                "entry_timestamp": None if pair is None else pair["entry_timestamp"],
                "entry_time": None if pair is None else pair["entry_time"],
                "entry_price": None if pair is None else pair["entry_price"],
                "exit_reason": None if pair is None else pair["exit_reason"],
                "exit_reason_detail": None if pair is None else pair["exit_reason_detail"],
                "holding_bars": trade_holding_bars,
                "holding_minutes": holding_minutes,
                "holding_trading_days": holding_trading_days,
                "max_favorable_price": max_favorable_price,
                "max_adverse_price": max_adverse_price,
                "trade_mfe": trade_mfe,
                "trade_mae": trade_mae,
                "realized_gross_pnl": None if pair is None else pair["realized_gross_pnl"],
                "realized_commission": None if pair is None else pair["realized_commission"],
                "realized_tax": None if pair is None else pair["realized_tax"],
                "realized_slippage": None if pair is None else pair["realized_slippage"],
                "realized_cost": None if pair is None else pair["realized_cost"],
                "realized_net_pnl": None if pair is None else pair["realized_net_pnl"],
            })
    return rows


def merge_technical_execution_rows(
    technical_rows: Iterable[dict[str, Any]],
    gate_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach Technical/Risk execution outcomes to the raw candidate ledger."""
    execution = {
        str(row["candidate_id"]): row
        for row in gate_rows
        if row.get("strategy_id") == "technical"
    }
    fields = (
        "gate_decision", "entered", "not_entered_reason", "exit_time", "exit_price",
        "exit_reason", "exit_reason_detail", "holding_bars", "trade_mfe", "trade_mae",
        "realized_gross_pnl", "realized_commission", "realized_tax",
        "realized_slippage", "realized_cost", "realized_net_pnl",
    )
    output = []
    for source in technical_rows:
        row = dict(source)
        matched = execution.get(str(row["candidate_id"]))
        if matched is not None:
            row.update({key: matched.get(key) for key in fields})
        else:
            row.update({key: None for key in fields})
            row["entered"] = False
            row["not_entered_reason"] = (
                "BLOCK_MARKET_REGIME"
                if not row.get("market_regime_pass", False)
                else "NOT_EVALUATED_BY_TECHNICAL_EXECUTION"
            )
        output.append(row)
    return output


def build_diagnostics(
    technical_rows: list[dict[str, Any]],
    gate_rows: list[dict[str, Any]],
    results: Iterable[dict[str, Any]],
    model_training_audit: dict[str, Any],
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evaluated = [row for row in technical_rows if row.get("outcome_status") == "EVALUATED"]
    regime_passed = [row for row in evaluated if row.get("market_regime_pass")]
    technical_summary = {
        "raw_candidate_count": len(technical_rows),
        "market_regime_passed_count": sum(bool(row.get("market_regime_pass")) for row in technical_rows),
        "fixed_horizon_evaluated_count": len(evaluated),
        "gross_return_distribution": probability_summary(row["fixed_horizon_gross_return"] for row in evaluated),
        "net_return_distribution": probability_summary(row["fixed_horizon_net_return"] for row in evaluated),
        "positive_gross_return_ratio": (
            sum(float(row["fixed_horizon_gross_return"]) > 0 for row in evaluated) / len(evaluated)
            if evaluated else None
        ),
        "positive_net_return_ratio": (
            sum(float(row["fixed_horizon_net_return"]) > 0 for row in evaluated) / len(evaluated)
            if evaluated else None
        ),
        "market_regime_pass_net_expectancy": (
            float(np.mean([row["fixed_horizon_net_return"] for row in regime_passed]))
            if regime_passed else None
        ),
        "by_market_regime": {
            regime: {
                "candidate_count": len(group),
                "net_expectancy": float(np.mean([
                    row["fixed_horizon_net_return"] for row in group
                    if row.get("fixed_horizon_net_return") is not None
                ])) if any(row.get("fixed_horizon_net_return") is not None for row in group) else None,
            }
            for regime in sorted({str(row.get("market_regime", "UNKNOWN")) for row in technical_rows})
            for group in [[row for row in technical_rows if str(row.get("market_regime", "UNKNOWN")) == regime]]
        },
    }
    result_map = {str(result["strategy_id"]): result for result in results}
    strategy_diagnostics = {}
    for strategy_id, result in result_map.items():
        entered_rows = [row for row in gate_rows if row["strategy_id"] == strategy_id and row["entered"]]
        exit_reason_summary: dict[str, dict[str, float | int]] = {}
        for row in entered_rows:
            reason = str(row.get("exit_reason") or "UNKNOWN")
            bucket = exit_reason_summary.setdefault(reason, {"count": 0, "gross_pnl": 0.0, "cost": 0.0, "net_pnl": 0.0})
            bucket["count"] += 1
            bucket["gross_pnl"] += float(row.get("realized_gross_pnl") or 0.0)
            bucket["cost"] += float(row.get("realized_cost") or 0.0)
            bucket["net_pnl"] += float(row.get("realized_net_pnl") or 0.0)
        strategy_diagnostics[strategy_id] = {
            "pnl": pnl_decomposition(
                result.get("completed_trade_pnl", []),
                float(result["summary"]["initial_capital"]),
            ),
            "exposure": exposure_metrics(
                result.get("equity_curve", []), result.get("completed_trade_pnl", []),
                float(result["summary"]["initial_capital"]),
            ),
            "trade_count": int(result["summary"].get("trade_count") or 0),
            "average_holding_bars": (
                float(np.mean([row["holding_bars"] for row in entered_rows if row.get("holding_bars") is not None]))
                if any(row.get("holding_bars") is not None for row in entered_rows) else None
            ),
            "average_holding_minutes": (
                float(np.mean([row["holding_minutes"] for row in entered_rows if row.get("holding_minutes") is not None]))
                if any(row.get("holding_minutes") is not None for row in entered_rows) else None
            ),
            "average_holding_trading_days": (
                float(np.mean([row["holding_trading_days"] for row in entered_rows if row.get("holding_trading_days") is not None]))
                if any(row.get("holding_trading_days") is not None for row in entered_rows) else None
            ),
            "exit_reasons": exit_reason_summary,
            "trade_quality": _trade_quality(entered_rows),
            "signal_funnel": _signal_funnel(strategy_id, result, gate_rows, technical_rows),
            "undefined_no_trade_metrics_are_null": (
                int(result["summary"].get("trade_count") or 0) > 0 or (
                    result["summary"].get("win_rate_pct") is None
                    and result["summary"].get("profit_factor") is None
                    and result.get("details", {}).get("average_trade_return_pct") is None
                    and result.get("details", {}).get("average_win") is None
                    and result.get("details", {}).get("average_loss") is None
                    and result.get("details", {}).get("payoff_ratio") is None
                )
            ),
        }
    model_key = {"rf": "random_forest", "xgboost": "xgboost", "gru": "gru"}
    ml_diagnostics = {}
    for strategy_id, audit_key in model_key.items():
        rows = [row for row in gate_rows if row["strategy_id"] == strategy_id]
        actions = {key: sum(row["gate_decision"] == key for row in rows)
                   for key in ("ENTRY", "BLOCK_HOLD", "BLOCK_SELL", "BLOCK_MISSING_OR_LATE_PREDICTION")}
        passed = [row["fixed_horizon_net_return"] for row in rows
                  if row["gate_decision"] == "ENTRY" and row.get("fixed_horizon_net_return") is not None]
        blocked = [row["fixed_horizon_net_return"] for row in rows
                   if str(row["gate_decision"]).startswith("BLOCK_") and row.get("fixed_horizon_net_return") is not None]
        predicted = {key: sum(row.get("predicted_class") == key for row in rows)
                     for key in ("BUY", "HOLD", "SELL")}
        validation = model_training_audit.get("models", {}).get(audit_key, {}).get(
            "validation_metrics_diagnostic_only", {}
        )
        training_audit = model_training_audit.get("models", {}).get(audit_key, {})
        ml_diagnostics[strategy_id] = {
            "label_names": {"0": "SELL", "1": "HOLD", "2": "BUY"},
            "training_label_counts": training_audit.get("training_label_counts"),
            "training_samples": training_audit.get("training_samples"),
            "validation": validation,
            "candidate_predicted_class_counts": predicted,
            "buy_probability": probability_summary(
                row["prob_buy"] for row in rows if row.get("prob_buy") is not None
            ),
            "gate_rule": "ARGMAX_SELL_HOLD_BUY",
            "gate_threshold": None,
            "gate_action_counts": actions,
            "model_source": training_audit.get("model_source", "fitted_in_process"),
            "runtime_source": training_audit.get(
                "runtime_source", "fitted_in_current_process"
            ),
            "model_load_failure_fallback_used": bool(
                training_audit.get("model_load_failure_fallback_used", False)
            ),
            "model_failure_policy": training_audit.get(
                "model_failure_policy", "RAISE_BLOCKED_ERROR_NO_DEFAULT_HOLD"
            ),
            "scaler_fit_on_training_only": training_audit.get(
                "scaler_fit_on_training_only"
            ),
            "sequence_alignment": training_audit.get("sequence_alignment"),
            "future_leakage_checks": training_audit.get("future_leakage_checks"),
            "passed_candidate_net_expectancy": float(np.mean(passed)) if passed else None,
            "blocked_candidate_net_expectancy": float(np.mean(blocked)) if blocked else None,
        }
    input_audit = model_training_audit.get("input_audit", {})
    selected_feature_audit = model_training_audit.get(
        "selected_universe_feature_audit", {}
    )
    common_training_samples = [
        item.get("training_samples")
        for item in model_training_audit.get("models", {}).values()
        if item.get("training_samples") is not None
    ]
    data_checks = (preflight or {}).get("data_checks", [])
    five_minute_rows = {
        str(item.get("role")): int(item["rows"])
        for item in data_checks
        if item.get("rows") is not None
    }
    gate_counts = {
        strategy_id: {
            key: sum(
                row.get("strategy_id") == strategy_id and row.get("gate_decision") == key
                for row in gate_rows
            )
            for key in (
                "ENTRY", "BLOCK_HOLD", "BLOCK_SELL",
                "BLOCK_MISSING_OR_LATE_PREDICTION",
            )
        }
        for strategy_id in ("rf", "xgboost", "gru")
    }
    execution_counts = {}
    for strategy_id, result in result_map.items():
        risk_rows = list(result.get("details", {}).get("risk_decisions", []))
        approved = sum(row.get("decision") in {"ALLOW", "REDUCE_SIZE"} for row in risk_rows)
        rejected = sum(str(row.get("decision", "")).startswith("BLOCK") for row in risk_rows)
        execution_counts[strategy_id] = {
            "risk_v1_evaluated": len(risk_rows),
            "risk_v1_approved": approved,
            "risk_v1_rejected": rejected,
            "actual_entries": sum(row.get("action") == "Buy" for row in result.get("trades", [])),
            "completed_closes": len(result.get("completed_trade_pnl", [])),
        }
    data_flow = {
        "five_minute_kbar_rows_by_source": five_minute_rows,
        "feature_sample_counts": {
            "sequence_eligible_samples": input_audit.get("sequence_eligible_samples"),
            "nonfinite_feature_exclusions": input_audit.get("nonfinite_feature_exclusions"),
            "model_ready_samples": input_audit.get("model_ready_samples"),
            "shared_input_scope": "full ML V1 configured source universe",
            "selected_universe_model_ready_samples": selected_feature_audit.get(
                "model_ready_samples"
            ),
            "selected_universe_model_ready_samples_by_stock": selected_feature_audit.get(
                "model_ready_samples_by_stock"
            ),
        },
        "ml_training_samples_per_model": (
            common_training_samples[0]
            if common_training_samples and len(set(common_training_samples)) == 1
            else {
                name: item.get("training_samples")
                for name, item in model_training_audit.get("models", {}).items()
            }
        ),
        "training_label_counts_by_model": {
            name: item.get("training_label_counts")
            for name, item in model_training_audit.get("models", {}).items()
        },
        "technical_raw_candidates": len(technical_rows),
        "technical_after_market_regime": sum(
            bool(row.get("market_regime_pass")) for row in technical_rows
        ),
        "ml_gate_counts": gate_counts,
        "execution_counts_by_strategy": execution_counts,
        "sample_definition_note": (
            "ML training samples are labeled feature sequences used to fit a classifier; "
            "actual entries and completed closes are execution events and are not training samples."
        ),
    }
    return {
        "schema_version": "multi_strategy_diagnostics_v1",
        "data_flow": data_flow,
        "technical_candidates": technical_summary,
        "ml_gates": ml_diagnostics,
        "strategies": strategy_diagnostics,
        "notes": {
            "outcome_fields_are_diagnostic_only": True,
            "gate_threshold_note": "Current frozen baseline uses argmax; no probability threshold was selected in this run.",
            "final_oos_used": False,
        },
    }


def _trade_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [row for row in rows if row.get("realized_net_pnl") is not None]
    winners = [row for row in closed if float(row["realized_net_pnl"]) > 0]
    losers = [row for row in closed if float(row["realized_net_pnl"]) < 0]
    mfe_threshold = 0.01
    mae_threshold = -0.01
    def stats(items, key):
        values = [float(row[key]) for row in items if row.get(key) is not None]
        return {"mean": float(np.mean(values)) if values else None,
                "median": float(np.median(values)) if values else None,
                "count": len(values)}
    return {
        "mfe_pct": stats(closed, "trade_mfe"), "mae_pct": stats(closed, "trade_mae"),
        "winning_trade_mfe_pct": stats(winners, "trade_mfe"),
        "winning_trade_mae_pct": stats(winners, "trade_mae"),
        "losing_trade_mfe_pct": stats(losers, "trade_mfe"),
        "losing_trade_mae_pct": stats(losers, "trade_mae"),
        "mfe_threshold_name": "MFE_1PCT_DIAGNOSTIC_ONLY",
        "mae_threshold_name": "MAE_MINUS_1PCT_DIAGNOSTIC_ONLY",
        "profitable_then_lost_count": sum(
            row.get("trade_mfe") is not None and float(row["trade_mfe"]) >= mfe_threshold
            and float(row.get("realized_net_pnl", 0.0)) < 0 for row in closed
        ),
        "large_mae_then_profit_count": sum(
            row.get("trade_mae") is not None and float(row["trade_mae"]) <= mae_threshold
            and float(row.get("realized_net_pnl", 0.0)) > 0 for row in closed
        ),
        "thresholds_are_diagnostic_not_tuned": True,
        "ohlc_intrabar_order_known": False,
    }


def _signal_funnel(strategy_id, result, gate_rows, technical_rows):
    rows = [row for row in gate_rows if row.get("strategy_id") == strategy_id]
    decisions = list(result.get("details", {}).get("gate_decisions", []))
    actions = {key: sum(row.get("hybrid_action") == key for row in decisions)
               for key in ("ENTRY", "BLOCK_HOLD", "BLOCK_SELL", "BLOCK_MISSING_OR_LATE_PREDICTION")}
    risk = list(result.get("details", {}).get("risk_decisions", []))
    trades = list(result.get("trades", []))
    completed = list(result.get("completed_trade_pnl", []))
    reasons = {}
    for row in decisions:
        reason = row.get("hybrid_action") if str(row.get("hybrid_action", "")).startswith("BLOCK_") else None
        if reason:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    for row in rows:
        if row.get("gate_actually_evaluated"):
            continue
        reason = row.get("not_entered_reason")
        if reason and str(reason).startswith(("BLOCK_", "NO_NEXT_")):
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    for row in risk:
        if str(row.get("decision", "")).startswith("BLOCK"):
            code = str(row.get("decision"))
            reasons[code] = reasons.get(code, 0) + 1
    return {
        "raw_kbars": result.get("audit", {}).get("raw_bars"),
        "feature_complete_kbars": result.get("audit", {}).get("feature_complete_bars"),
        "technical_candidates": len(technical_rows),
        "model_evaluated_candidates": len(decisions) if strategy_id != "technical" else None,
        "model_predictions_available": sum(row.get("predicted_class") is not None for row in rows),
        "model_output_buy": sum(row.get("predicted_class") == "BUY" for row in rows),
        "model_output_hold": sum(row.get("predicted_class") == "HOLD" for row in rows),
        "model_output_sell": sum(row.get("predicted_class") == "SELL" for row in rows),
        "gate_passed": actions["ENTRY"] if strategy_id != "technical" else sum(row.get("hybrid_action") == "ENTRY" for row in decisions),
        "risk_evaluated": len(risk),
        "risk_blocked": sum(str(row.get("decision", "")).startswith("BLOCK") for row in risk),
        "orders_created": result.get("details", {}).get("orders_created"),
        "successful_fills": len(trades),
        "completed_trades": len(completed),
        "open_positions_at_end": result.get("details", {}).get("open_position_quantity_at_end", 0),
        "gate_action_counts": actions,
        "block_reasons": reasons,
        "count_scope_note": "Funnel rows follow frozen Technical schedule; candidate counts are not unique orders when fills are partial.",
    }

"""Preregistered ATR-adaptive risk helpers and Strategy V2-B/RF execution."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from hybrid.core.hybrid_poc_v1 import make_alignment_decision
from hybrid.core.risk_v1 import MAX_POSITION_FRACTION, RISK_PER_TRADE, risk_sizing_allowed
from hybrid.core.strategy_v2_batch import StrategyV2BatchStrategy, regime_confirmation_passes
from trading_system.technical_v1 import TechnicalV1Strategy

TAIPEI = ZoneInfo("Asia/Taipei")
MINIMUM_STOP_DISTANCE = 0.025
ATR_LOOKBACK = 14
ATR_MULTIPLIER = 2.0


def wilder_atr(daily: pd.DataFrame) -> pd.DataFrame:
    """Compute daily TR and Wilder ATR(14) from chronological OHLC rows.

    The first row uses high-low as true range; all later rows use the prior
    completed close. Input rows must be complete, unique and ascending.
    """
    required = {"date", "high", "low", "close"}
    if not required.issubset(daily.columns):
        raise ValueError(f"Daily OHLC missing columns: {sorted(required - set(daily.columns))}")
    frame = daily.loc[:, ["date", "high", "low", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        raise ValueError("Daily OHLC dates must be unique and chronological")
    for column in ("high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["high", "low", "close"]].isna().any().any() or (frame["close"] <= 0).any():
        raise ValueError("Daily OHLC contains invalid values")
    if (frame["high"] < frame["low"]).any() or (frame["high"] < frame["close"]).any() or (frame["low"] > frame["close"]).any():
        raise ValueError("Daily OHLC relationship is invalid")
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [frame["high"] - frame["low"], (frame["high"] - previous_close).abs(),
         (frame["low"] - previous_close).abs()], axis=1,
    ).max(axis=1)
    atr = pd.Series(float("nan"), index=frame.index, dtype="float64")
    if len(frame) >= ATR_LOOKBACK:
        atr.iloc[ATR_LOOKBACK - 1] = float(true_range.iloc[:ATR_LOOKBACK].mean())
        for i in range(ATR_LOOKBACK, len(frame)):
            atr.iloc[i] = (atr.iloc[i - 1] * (ATR_LOOKBACK - 1) + float(true_range.iloc[i])) / ATR_LOOKBACK
    frame["true_range"] = true_range
    frame["atr14"] = atr
    return frame


def atr_context_for_dates(daily: pd.DataFrame, target_dates: set[str]) -> dict[str, dict[str, Any]]:
    """Return ATR data for target date D using only completed dates < D."""
    calculated = wilder_atr(daily)
    by_date = calculated.set_index("date")
    dates = list(calculated["date"])
    output: dict[str, dict[str, Any]] = {}
    for target in sorted(target_dates):
        prior = [day for day in dates if day < target]
        if not prior:
            continue
        prior_day = prior[-1]
        row = by_date.loc[prior_day]
        if pd.isna(row["atr14"]) or float(row["close"]) <= 0:
            continue
        atr14 = float(row["atr14"])
        previous_close = float(row["close"])
        output[target] = {
            "target_date": target,
            "atr_as_of_date": prior_day,
            "previous_session_close": previous_close,
            "atr14": atr14,
            "atr_pct": atr14 / previous_close,
            "final_stop_distance": max(MINIMUM_STOP_DISTANCE, ATR_MULTIPLIER * atr14 / previous_close),
            "atr_history_count": len(prior),
        }
    return output


def calculate_atr_risk_size(
    *, current_equity: float, entry_price: float, original_quantity: int,
    stop_loss_pct: float, risk_per_trade: float = RISK_PER_TRADE,
    max_position_fraction: float = MAX_POSITION_FRACTION,
) -> dict[str, Any]:
    values = (current_equity, entry_price, original_quantity, stop_loss_pct, risk_per_trade, max_position_fraction)
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("Risk sizing inputs must be finite")
    if current_equity <= 0 or entry_price <= 0 or original_quantity < 0 or stop_loss_pct < MINIMUM_STOP_DISTANCE:
        raise ValueError("Risk sizing inputs are outside the preregistered range")
    if risk_per_trade != RISK_PER_TRADE or max_position_fraction != MAX_POSITION_FRACTION:
        raise ValueError("Risk budget and maximum exposure are fixed")
    risk_budget = current_equity * risk_per_trade
    stop_distance = entry_price * stop_loss_pct
    by_risk = math.floor(risk_budget / stop_distance)
    by_exposure = math.floor(current_equity * max_position_fraction / entry_price)
    quantity = max(0, min(by_risk, by_exposure, int(original_quantity)))
    estimated_loss = quantity * stop_distance
    return {
        "current_equity": float(current_equity), "entry_price": float(entry_price),
        "stop_loss_pct": float(stop_loss_pct), "stop_distance_per_share": float(stop_distance),
        "risk_per_trade": float(risk_per_trade), "risk_budget": float(risk_budget),
        "quantity_by_risk": int(by_risk), "quantity_by_exposure": int(by_exposure),
        "original_quantity": int(original_quantity), "final_quantity": int(quantity),
        "estimated_stop_loss_amount": float(estimated_loss),
        "estimated_risk_fraction": float(estimated_loss / current_equity),
        "position_fraction": float(quantity * entry_price / current_equity),
        "decision": "BLOCK_ZERO_QUANTITY" if quantity == 0 else "REDUCE_SIZE" if quantity < original_quantity else "ALLOW",
    }


class RiskV2ATRAdaptiveStrategy(StrategyV2BatchStrategy):
    """TECH_V2_B + RF BUY-only gate with fixed, lagged ATR-based risk sizing."""

    def __init__(self, *args: Any, atr_context: dict[tuple[str, str], dict[str, Any]], **kwargs: Any):
        super().__init__(*args, variant="TECH_V2_B", **kwargs)
        self.atr_context = atr_context
        self._risk_quantity_for_entry: int | None = None
        self._pending_stop_loss_pct: float | None = None
        self._active_stop_loss_pct: float | None = None
        self.risk_decisions: list[dict[str, Any]] = []
        self.technical_candidate_decisions: list[dict[str, Any]] = []

    def _try_entry(self, code: str, tick_time: datetime, close: float, snapshot: dict[str, float | None]) -> None:
        reasons = self._entry_reasons(close, snapshot)
        if not reasons:
            return
        aware = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TAIPEI)
        signal_time = aware.astimezone(TAIPEI)
        key = signal_time.isoformat(timespec="microseconds")
        if self.candidate_schedule is not None and key not in self.candidate_schedule:
            return
        context = self.regime_context.get((str(code), signal_time.date().isoformat()))
        self.filter_audit["candidate_checks"] += 1
        if not context or context.get("eligible") is not True:
            if not context:
                self.filter_audit["missing_context"] += 1
            self.filter_audit["rejected"] += 1
            return
        if not regime_confirmation_passes(context.get("last_20_proxy_closes", [])):
            self.filter_audit["rejected"] += 1
            return

        decision = make_alignment_decision(
            model=self.ml_model, stock_code=code, technical_fold_id=self.fold_id,
            technical_signal_time=signal_time, technical_earliest_entry_time=signal_time,
            technical_reason=", ".join(reasons), close_at_signal=close,
            prediction=self.prediction_lookup.get((str(code), signal_time.date().isoformat())),
        )
        decision.update({"candidate_date": signal_time.date().isoformat(), "candidate_stock": str(code)})
        self.technical_candidate_decisions.append(decision)
        self.hybrid_decisions.append(decision)
        if not risk_sizing_allowed(str(decision["hybrid_action"])):
            return

        atr = self.atr_context.get((str(code), signal_time.date().isoformat()))
        if atr is None:
            raise RuntimeError(f"No prior ATR context for eligible BUY candidate {code}/{signal_time.date()}")
        equity = float(self.local_cash or 0.0) + int(self.local_position) * float(close)
        original_quantity = int(TechnicalV1Strategy._order_quantity(self, close))
        sizing = calculate_atr_risk_size(
            current_equity=equity, entry_price=float(close), original_quantity=original_quantity,
            stop_loss_pct=float(atr["final_stop_distance"]),
        )
        self.risk_decisions.append({
            "timestamp": signal_time.isoformat(timespec="microseconds"), "stock_code": str(code),
            "fold_id": str(self.fold_id), "model": str(self.ml_model), **atr, **sizing,
        })
        if sizing["final_quantity"] <= 0:
            self.record_signal(code=code, action="Buy", signal_type="entry", signal_time=tick_time,
                               price=close, reason=", ".join(reasons), indicators=snapshot)
            return
        self._risk_quantity_for_entry = int(sizing["final_quantity"])
        self._pending_stop_loss_pct = float(atr["final_stop_distance"])
        try:
            TechnicalV1Strategy._try_entry(self, code, tick_time, close, snapshot)
        finally:
            self._risk_quantity_for_entry = None

    def _order_quantity(self, reference_price: float) -> int:
        if self._risk_quantity_for_entry is not None:
            return self._risk_quantity_for_entry
        return super()._order_quantity(reference_price)

    def on_deal(self, sender: str, response: dict[str, Any], **kwargs: Any) -> None:
        action = str(response.get("action", ""))
        succeeded = response.get("operation_code") == "BT-00000"
        super().on_deal(sender, response=response, **kwargs)
        if succeeded and action == "Buy":
            self._active_stop_loss_pct = self._pending_stop_loss_pct
            self._pending_stop_loss_pct = None
        elif succeeded and action == "Sell":
            self._active_stop_loss_pct = None
            self._pending_stop_loss_pct = None

    def on_order(self, sender: str, response: dict[str, Any], **kwargs: Any) -> None:
        super().on_order(sender, response=response, **kwargs)
        if response.get("order", {}).get("action") == "Buy" and response.get("operation_status_code") != "BT-00000":
            self._pending_stop_loss_pct = None

    def _risk_exit_signal(self, close: float):
        if self.entry_price is not None:
            return_pct = (close - self.entry_price) / self.entry_price
            stop_pct = self._active_stop_loss_pct or MINIMUM_STOP_DISTANCE
            if return_pct <= -stop_pct:
                return "stop_loss", [f"return {return_pct:.2%}; ATR stop {stop_pct:.2%}"]
            if self.config.take_profit_pct > 0 and return_pct >= self.config.take_profit_pct:
                return "take_profit", [f"return {return_pct:.2%}"]
        if self.config.max_hold_bars > 0 and self.bars_since_entry >= self.config.max_hold_bars:
            return "max_holding_period", [f"held {self.bars_since_entry} bars (limit {self.config.max_hold_bars})"]
        return None


def compare_gate_decisions(left: pd.DataFrame, right: pd.DataFrame) -> dict[str, Any]:
    key = ["stock_code", "fold_id", "technical_signal_time"]
    payload = ["ml_prediction", "hybrid_action", "ml_signal_date", "prob_sell", "prob_hold", "prob_buy"]
    a = left.set_index(key).sort_index()
    b = right.set_index(key).sort_index()
    if not a.index.equals(b.index):
        return {"status": "FAIL", "left_rows": len(a), "right_rows": len(b), "same_candidate_set": False}
    equal = a[payload].reset_index(drop=True).equals(b[payload].reset_index(drop=True))
    return {"status": "PASS" if equal else "FAIL", "left_rows": len(a), "right_rows": len(b),
            "same_candidate_set": True, "gate_payload_equal": bool(equal)}


def execution_fill_check(trades: list[dict[str, Any]], bars: dict[tuple[str, int], float]) -> dict[str, Any]:
    entries = [row for row in trades if row.get("action") == "Buy"]
    failures = []
    for row in entries:
        stock, stamp = str(row.get("code", row.get("stock_code", ""))), int(row["timestamp"])
        expected = bars.get((stock, stamp))
        if expected is None or not math.isclose(float(row["price"]), expected, rel_tol=0.0, abs_tol=1e-8):
            failures.append({"stock_code": stock, "timestamp": stamp, "fill_price": row["price"], "bar_open": expected})
    return {"status": "PASS" if not failures else "FAIL", "entry_fills_checked": len(entries),
            "entry_fills_at_next_bar_open": len(entries) - len(failures), "failures": failures[:20]}


def entry_candidate_parity(trades: list[dict[str, Any]], gate_rows: pd.DataFrame) -> dict[str, Any]:
    """Verify both arms execute the same BUY-gated Technical V2-B candidates."""
    allowed = set()
    for row in gate_rows.loc[gate_rows["hybrid_action"] == "ENTRY"].to_dict("records"):
        stamp = pd.Timestamp(row["technical_signal_time"])
        allowed.add((str(row["stock_code"]), str(row["fold_id"]), int(stamp.timestamp())))
    buys: dict[str, set[tuple[str, str, int]]] = {"RISK_V1": set(), "RISK_V2": set()}
    for row in trades:
        if row.get("action") != "Buy":
            continue
        variant = str(row["variant"])
        if variant in buys:
            buys[variant].add((str(row.get("stock_code", row.get("code"))), str(row["fold_id"]), int(row["timestamp"])))
    missing = {variant: sorted(values - allowed) for variant, values in buys.items()}
    same = buys["RISK_V1"] == buys["RISK_V2"]
    valid = same and not any(missing.values())
    return {"status": "PASS" if valid else "FAIL", "risk_v1_entries": len(buys["RISK_V1"]),
            "risk_v2_entries": len(buys["RISK_V2"]), "executed_entry_identity_equal": same,
            "entries_matching_buy_gate_candidates": {key: len(value) - len(missing[key]) for key, value in buys.items()},
            "unmatched_entries": missing}


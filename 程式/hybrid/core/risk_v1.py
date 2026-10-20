"""Fixed, non-optimized risk-based sizing for the Hybrid V1 research path."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from hybrid.core.hybrid_poc_v1 import (
    HybridGateTechnicalV1Strategy,
    make_alignment_decision,
)
from trading_system.technical_v1 import TechnicalV1Strategy

TAIPEI = ZoneInfo("Asia/Taipei")
RISK_PER_TRADE = 0.01
MAX_POSITION_FRACTION = 0.40
EXISTING_STOP_LOSS_PCT = 0.025


def calculate_risk_size(
    *, current_equity: float, entry_price: float, original_quantity: int,
    risk_per_trade: float = RISK_PER_TRADE,
    max_position_fraction: float = MAX_POSITION_FRACTION,
    stop_loss_pct: float = EXISTING_STOP_LOSS_PCT,
) -> dict[str, Any]:
    """Calculate integer shares using fixed risk, exposure and engine caps."""
    values = (current_equity, entry_price, risk_per_trade, max_position_fraction, stop_loss_pct)
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("Risk sizing inputs must be finite")
    if current_equity < 0 or entry_price <= 0 or original_quantity < 0:
        raise ValueError("Risk sizing inputs are outside the valid range")
    if risk_per_trade != RISK_PER_TRADE or max_position_fraction != MAX_POSITION_FRACTION:
        raise ValueError("Risk V1 parameters are fixed at 1% risk and 40% exposure")
    if stop_loss_pct != EXISTING_STOP_LOSS_PCT:
        raise ValueError("Risk V1 must retain the existing 2.5% stop loss")

    risk_budget = current_equity * risk_per_trade
    stop_distance = entry_price * stop_loss_pct
    by_risk = math.floor(risk_budget / stop_distance) if stop_distance > 0 else 0
    by_exposure = math.floor(current_equity * max_position_fraction / entry_price)
    final_quantity = max(0, min(by_risk, by_exposure, int(original_quantity)))
    estimated_stop_loss = final_quantity * stop_distance
    position_fraction = final_quantity * entry_price / current_equity if current_equity else 0.0
    estimated_risk_fraction = estimated_stop_loss / current_equity if current_equity else 0.0
    decision = (
        "BLOCK_ZERO_QUANTITY" if final_quantity <= 0
        else "REDUCE_SIZE" if final_quantity < original_quantity
        else "ALLOW"
    )
    return {
        "current_equity": float(current_equity),
        "entry_price": float(entry_price),
        "stop_loss_pct": float(stop_loss_pct),
        "stop_distance_per_share": float(stop_distance),
        "risk_per_trade": float(risk_per_trade),
        "risk_budget": float(risk_budget),
        "quantity_by_risk": int(by_risk),
        "quantity_by_exposure": int(by_exposure),
        "original_quantity": int(original_quantity),
        "final_quantity": int(final_quantity),
        "estimated_stop_loss_amount": float(estimated_stop_loss),
        "estimated_risk_fraction": float(estimated_risk_fraction),
        "position_fraction": float(position_fraction),
        "decision": decision,
    }


def risk_sizing_allowed(hybrid_action: str) -> bool:
    """Only the pre-existing ML BUY -> ENTRY action may reach sizing."""
    return hybrid_action == "ENTRY"


class RiskV1HybridStrategy(HybridGateTechnicalV1Strategy):
    """Apply sizing only after the existing prior-session ML BUY gate passes."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.risk_decisions: list[dict[str, Any]] = []
        self._risk_quantity_for_entry: int | None = None

    def _order_quantity(self, reference_price: float) -> int:
        if self._risk_quantity_for_entry is not None:
            return self._risk_quantity_for_entry
        return super()._order_quantity(reference_price)

    def _try_entry(self, code: str, tick_time: datetime, close: float, snapshot: dict[str, float | None]) -> None:
        reasons = self._entry_reasons(close, snapshot)
        if not reasons:
            return
        aware_entry = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TAIPEI)
        signal_time = aware_entry.astimezone(TAIPEI)
        schedule_key = signal_time.isoformat(timespec="microseconds")
        if self.candidate_schedule is not None and schedule_key not in self.candidate_schedule:
            return
        prediction = self.prediction_lookup.get((str(code), signal_time.date().isoformat()))
        gate_decision = make_alignment_decision(
            model=self.ml_model,
            stock_code=code,
            technical_fold_id=self.fold_id,
            technical_signal_time=signal_time,
            technical_earliest_entry_time=signal_time,
            technical_reason=", ".join(reasons),
            close_at_signal=close,
            prediction=prediction,
        )
        self.hybrid_decisions.append(gate_decision)
        if not risk_sizing_allowed(str(gate_decision["hybrid_action"])):
            return

        # Entry candidates are flat by the existing engine's _can_enter rule;
        # cash + marked position is therefore current, realized equity here.
        current_equity = float(self.local_cash or 0.0) + int(self.local_position) * float(close)
        original_quantity = int(TechnicalV1Strategy._order_quantity(self, close))
        sizing = calculate_risk_size(
            current_equity=current_equity,
            entry_price=float(close),
            original_quantity=original_quantity,
            stop_loss_pct=float(self.config.stop_loss_pct),
        )
        self.risk_decisions.append({
            "timestamp": signal_time.isoformat(timespec="microseconds"),
            "stock_code": str(code),
            "fold_id": str(self.fold_id),
            "model": str(self.ml_model),
            **sizing,
        })
        if sizing["final_quantity"] <= 0:
            # Preserve the unchanged BUY signal record while correctly omitting
            # an order and trade for a zero-share risk decision.
            self.record_signal(
                code=code, action="Buy", signal_type="entry", signal_time=tick_time,
                price=close, reason=", ".join(reasons), indicators=snapshot,
            )
            return

        self._risk_quantity_for_entry = int(sizing["final_quantity"])
        try:
            # Reuse the exact Technical V1 signal/order/exit implementation.
            TechnicalV1Strategy._try_entry(self, code, tick_time, close, snapshot)
        finally:
            self._risk_quantity_for_entry = None


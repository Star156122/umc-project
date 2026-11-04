"""Isolated pure-Technical A/B experiment helpers for 2023–2024 development data."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VARIANTS = ("A_ORIGINAL_V2B", "B_NO_BOLLINGER", "C_NO_MA120", "D_EXIT55", "E_EXIT80")
EXIT_LOOKBACK = {
    "A_ORIGINAL_V2B": 30,
    "B_NO_BOLLINGER": 30,
    "C_NO_MA120": 30,
    "D_EXIT55": 55,
    "E_EXIT80": 80,
}


@dataclass(frozen=True)
class EntryComponents:
    breakout: bool
    ma20_over_ma60: bool
    close_over_ma240: bool
    bollinger_upper: bool
    ma120_and_slope: bool

    def passes(self, variant: str) -> bool:
        if variant not in VARIANTS:
            raise ValueError(f"Unknown variant: {variant}")
        common = self.breakout and self.ma20_over_ma60 and self.close_over_ma240
        if variant == "A_ORIGINAL_V2B":
            return common and self.bollinger_upper and self.ma120_and_slope
        if variant == "B_NO_BOLLINGER":
            return common and self.ma120_and_slope
        return common

    def blockers(self, variant: str) -> list[str]:
        if variant not in VARIANTS:
            raise ValueError(f"Unknown variant: {variant}")
        result = []
        if not self.breakout:
            result.append("BREAKOUT_OR_CROSS")
        if not self.ma20_over_ma60:
            result.append("MA20_NOT_ABOVE_MA60")
        if not self.close_over_ma240:
            result.append("CLOSE_NOT_ABOVE_MA240")
        if variant == "A_ORIGINAL_V2B" and not self.bollinger_upper:
            result.append("BOLLINGER_UPPER")
        if variant in {"A_ORIGINAL_V2B", "B_NO_BOLLINGER"} and not self.ma120_and_slope:
            result.append("MA120_OR_SLOPE")
        return result


def entry_components(close: float, snapshot: dict[str, Any], previous: dict[str, Any] | None,
                     *, require_cross: bool = True) -> EntryComponents:
    """Evaluate only completed-bar values; prior breakout high excludes this bar."""
    high = snapshot.get("BREAKOUT_HIGH")
    previous_close = previous.get("CLOSE") if previous else None
    previous_high = previous.get("BREAKOUT_HIGH") if previous else None
    crossed = (not require_cross) or (
        previous_close is not None and previous_high is not None
        and float(previous_close) <= float(previous_high)
    )
    return EntryComponents(
        breakout=high is not None and float(close) > float(high) and crossed,
        ma20_over_ma60=(snapshot.get("MA_FAST") is not None and snapshot.get("MA_MID") is not None
                        and float(snapshot["MA_FAST"]) > float(snapshot["MA_MID"])),
        close_over_ma240=(snapshot.get("MA_SLOW") is not None and float(close) > float(snapshot["MA_SLOW"])),
        bollinger_upper=(snapshot.get("BB_UPPER") is not None and float(close) >= float(snapshot["BB_UPPER"])),
        ma120_and_slope=(snapshot.get("TREND_MA") is not None and snapshot.get("TREND_SLOPE") is not None
                         and float(close) > float(snapshot["TREND_MA"]) and float(snapshot["TREND_SLOPE"]) > 0),
    )


def validate_experiment_config(config: dict[str, Any]) -> None:
    if tuple(config.get("variants", ())) != VARIANTS:
        raise ValueError("Variant list must retain the preregistered A–E order")
    if config.get("data_role") != "historical_development":
        raise ValueError("Only historical_development data is permitted")
    if config.get("period") != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise ValueError("This experiment is restricted to 2023–2024")
    if config.get("final_oos_used") is not False:
        raise ValueError("Final OOS must remain unused")
    risk = config.get("risk", {})
    if (risk.get("risk_per_trade"), risk.get("max_position_fraction"), risk.get("stop_loss_pct")) != (0.01, 0.4, 0.025):
        raise ValueError("Risk V1 settings must remain fixed")
    if config.get("initial_capital") != 1_000_000:
        raise ValueError("Initial capital must remain NT$1,000,000")


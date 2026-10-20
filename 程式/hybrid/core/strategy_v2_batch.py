"""Frozen Strategy V2 Technical batch variants used by the Training-only runner."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from hybrid.core.risk_v1 import RiskV1HybridStrategy

TAIPEI = ZoneInfo("Asia/Taipei")
EXPERIMENTS = ("CONTROL", "TECH_V2_A", "TECH_V2_B", "TECH_V2_C")


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    label: str
    preregistration: str
    performance_authorized: bool


EXPERIMENT_REGISTRY = {
    "CONTROL": ExperimentSpec("strategy-v2-control", "Frozen V2 Control", "research/strategy_v2_baseline_closure.md", False),
    "TECH_V2_A": ExperimentSpec("strategy-v2-technical-exp-a", "Entry Quality Confirmation", "research/strategy_v2_technical_expA.md", False),
    "TECH_V2_B": ExperimentSpec("strategy-v2-technical-exp-b", "Market Regime Filter", "research/strategy_v2_technical_expB.md", False),
    "TECH_V2_C": ExperimentSpec("strategy-v2-technical-exp-c", "Exit Quality", "research/strategy_v2_technical_expC.md", False),
}


def build_plan(root: Path, requested: Iterable[str]) -> list[dict[str, Any]]:
    selected = list(requested)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Select a non-empty list of unique experiment IDs")
    if any(name not in EXPERIMENT_REGISTRY for name in selected):
        raise ValueError("Unknown Strategy V2 experiment ID")
    result = []
    for name in selected:
        spec = EXPERIMENT_REGISTRY[name]
        prereg = root / spec.preregistration
        if not prereg.is_file():
            raise FileNotFoundError(prereg)
        result.append({
            "selection": name,
            "experiment_id": spec.experiment_id,
            "label": spec.label,
            "preregistration": spec.preregistration,
            "preregistration_exists": True,
            "performance_authorized_by_this_runner": spec.performance_authorized,
            "auto_rank_or_select_winner": False,
        })
    return result


def volume_confirmation_passes(signal_volume: Any, prior_volumes: list[Any]) -> bool:
    """V2-A: strict comparison to exactly 20 prior completed bars, excluding signal bar."""
    try:
        current = float(signal_volume)
        values = [float(value) for value in prior_volumes]
    except (TypeError, ValueError):
        return False
    if len(values) != 20 or not pd.Series([current, *values]).map(pd.notna).all():
        return False
    if not all(pd.notna(value) and abs(value) != float("inf") for value in [current, *values]):
        return False
    return current > sum(values) / 20


def regime_confirmation_passes(prior_proxy_values: list[Any]) -> bool:
    """V2-B: latest completed prior-session proxy close above its trailing 20-session SMA."""
    try:
        values = [float(value) for value in prior_proxy_values]
    except (TypeError, ValueError):
        return False
    if len(values) != 20 or not all(pd.notna(value) and abs(value) != float("inf") for value in values):
        return False
    return values[-1] > sum(values) / 20


class StrategyV2BatchStrategy(RiskV1HybridStrategy):
    """Apply one preregistered Technical change while retaining Risk V1 and ML gate."""

    def __init__(
        self, *args: Any, variant: str,
        volume_context: dict[tuple[str, str, str], dict[str, Any]] | None = None,
        regime_context: dict[tuple[str, str], dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if variant not in EXPERIMENTS:
            raise ValueError(f"Unknown Strategy V2 variant: {variant}")
        self.variant = variant
        self.volume_context = volume_context or {}
        self.regime_context = regime_context or {}
        self._completed_closes: list[float] = []
        self._ema20: float | None = None
        self.filter_audit = {"candidate_checks": 0, "rejected": 0, "missing_context": 0}

    def _entry_context_key(self, code: str, tick_time: datetime) -> tuple[str, str, str]:
        aware = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TAIPEI)
        return str(code), str(self.fold_id), aware.astimezone(TAIPEI).isoformat(timespec="microseconds")

    def _try_entry(self, code: str, tick_time: datetime, close: float, snapshot: dict[str, float | None]) -> None:
        # Restrict filter accounting to actual base-strategy candidates on the
        # frozen Common-Coverage schedule. The Technical trigger itself is unchanged.
        if not self._entry_reasons(close, snapshot):
            return
        aware = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TAIPEI)
        entry_time = aware.astimezone(TAIPEI)
        if self.candidate_schedule is not None and entry_time.isoformat(timespec="microseconds") not in self.candidate_schedule:
            return
        if self.variant == "TECH_V2_A":
            self.filter_audit["candidate_checks"] += 1
            context = self.volume_context.get(self._entry_context_key(code, tick_time))
            if not context or context.get("prior_count") != 20:
                self.filter_audit["missing_context"] += 1
                self.filter_audit["rejected"] += 1
                return
            volume = snapshot.get("VOLUME")
            average = context.get("prior_20_volume_mean")
            if (
                volume is None or average is None or context.get("signal_volume") is None
                or not volume_confirmation_passes(volume, context.get("prior_volumes", []))
                or abs(float(volume) - float(context.get("signal_volume", float("nan")))) > 1e-9
                or abs(float(average) - float(context.get("prior_20_volume_mean", float("nan")))) > 1e-9
            ):
                self.filter_audit["rejected"] += 1
                return
        elif self.variant == "TECH_V2_B":
            self.filter_audit["candidate_checks"] += 1
            aware = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TAIPEI)
            context = self.regime_context.get((str(code), aware.astimezone(TAIPEI).date().isoformat()))
            if not context or context.get("eligible") is not True:
                if not context:
                    self.filter_audit["missing_context"] += 1
                self.filter_audit["rejected"] += 1
                return
            if not regime_confirmation_passes(context.get("last_20_proxy_closes", [])):
                self.filter_audit["rejected"] += 1
                return
        super()._try_entry(code, tick_time, close, snapshot)

    def process_completed_kbar(self, code: str, decision_time: datetime, completed_kbar: dict[str, Any]) -> None:
        close = completed_kbar.get("Close")
        try:
            value = float(close)
        except (TypeError, ValueError):
            value = float("nan")
        if pd.notna(value):
            self._completed_closes.append(value)
            self._ema20 = float(pd.Series(self._completed_closes, dtype="float64").ewm(span=20, adjust=False).mean().iloc[-1])
        super().process_completed_kbar(code, decision_time, completed_kbar)

    def _exit_signal(self, close: float, snapshot: dict[str, float | None]):
        if self.variant != "TECH_V2_C" or self.family_name != "breakout":
            return super()._exit_signal(close, snapshot)
        risk_signal = self._risk_exit_signal(close)
        if risk_signal is not None:
            return risk_signal
        if self.bars_since_entry < self.config.min_hold_bars:
            return None
        if self._ema20 is not None and close < self._ema20:
            return "technical_exit", ["completed close < EMA20 (Strategy V2-C)"]
        return None


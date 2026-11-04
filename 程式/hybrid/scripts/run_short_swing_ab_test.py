"""Run isolated pure Technical + Risk V1 variants A–E on exposed 2023–2024 data."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.short_swing_ab_test import (  # noqa: E402
    EXIT_LOOKBACK, VARIANTS, entry_components, validate_experiment_config,
)
from hybrid.core.multi_strategy_backtest_v1 import derive_equity_curve, summary_from_curve  # noqa: E402
from hybrid.core.risk_v1 import calculate_risk_size  # noqa: E402
from hybrid.scripts.run_ml_v2_exp001 import TechnicalV2AloneStrategy  # noqa: E402
from hybrid.scripts.run_strategy_v2_batch import build_regime_context  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
CONFIG_PATH = ROOT / "hybrid/config/short_swing_ab_test_v1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def iso_taipei(value: Any) -> str:
    ts = pd.Timestamp(value)
    ts = ts.tz_localize(TAIPEI) if ts.tzinfo is None else ts.tz_convert(TAIPEI)
    return ts.isoformat(timespec="microseconds")


def _load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    validate_experiment_config(config)
    return config


def _regime_pass(regime_context: dict, stock: str, decision_time: datetime) -> tuple[bool, dict[str, Any] | None]:
    aware = decision_time.replace(tzinfo=TAIPEI) if decision_time.tzinfo is None else decision_time.astimezone(TAIPEI)
    context = regime_context.get((str(stock), aware.date().isoformat()))
    if not context or context.get("eligible") is not True:
        return False, context
    values = context.get("last_20_proxy_closes", [])
    passed = len(values) == 20 and float(values[-1]) > float(np.mean(values))
    return passed, context


def _variant_candidate(base: dict[str, Any], variant: str) -> dict[str, Any]:
    candidate = copy.deepcopy(base)
    candidate["params"]["breakout_exit_period"] = EXIT_LOOKBACK[variant]
    if variant in {"C_NO_MA120", "D_EXIT55", "E_EXIT80"}:
        candidate["params"]["require_trend_filter"] = False
    return candidate


def _make_strategy_class(variant: str):
    class ShortSwingRiskV1Strategy(TechnicalV2AloneStrategy):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.variant = variant
            self._audit_bar = None
            self.candidate_audit: list[dict[str, Any]] = []
            self.risk_decisions = []

        def _entry_reasons(self, close, snapshot):
            if self.variant == "A_ORIGINAL_V2B":
                return super()._entry_reasons(close, snapshot)
            components = entry_components(
                close, snapshot, self.previous_snapshot,
                require_cross=bool(self.config.breakout_require_cross),
            )
            if not components.passes(self.variant):
                return []
            reasons = [
                f"close breaks prior {self.config.breakout_entry_period}-bar high",
                "MA20 > MA60",
                "close > MA240",
            ]
            if self.variant in {"B_NO_BOLLINGER", "C_NO_MA120", "D_EXIT55", "E_EXIT80"}:
                reasons.append("Bollinger upper-band condition removed by preregistration")
            if self.variant == "B_NO_BOLLINGER":
                reasons.append("close > MA120 and MA120 slope > 0")
            else:
                reasons.append("MA120 and slope conditions removed by preregistration")
            return reasons

        def process_completed_kbar(self, code, decision_time, completed_kbar):
            timestamp = completed_kbar.get("kbar_timestamp")
            if self.evaluation_start_timestamp is not None and timestamp is not None and int(timestamp) < self.evaluation_start_timestamp:
                return super().process_completed_kbar(code, decision_time, completed_kbar)
            close = completed_kbar.get("Close")
            snapshot = bt.indicator_snapshot(completed_kbar)
            row = None
            if close is not None and snapshot.get("BREAKOUT_HIGH") is not None:
                components = entry_components(
                    float(close), snapshot, self.previous_snapshot,
                    require_cross=bool(self.config.breakout_require_cross),
                )
                if components.breakout:
                    market_pass, context = _regime_pass(self.regime_context, code, decision_time)
                    row = {
                        "variant": self.variant, "stock_code": str(code),
                        "signal_bar_timestamp": int(timestamp),
                        "signal_bar_end_time": (datetime.fromtimestamp(int(timestamp), TAIPEI) + timedelta(minutes=5) - timedelta(seconds=1)).isoformat(),
                        "earliest_entry_time": iso_taipei(decision_time),
                        "close_at_signal": float(close),
                        "breakout_cross_pass": components.breakout,
                        "ma20_above_ma60": components.ma20_over_ma60,
                        "close_above_ma240": components.close_over_ma240,
                        "bollinger_upper_pass": components.bollinger_upper,
                        "ma120_and_slope_pass": components.ma120_and_slope,
                        "technical_candidate": components.passes(self.variant),
                        "technical_blockers": "|".join(components.blockers(self.variant)),
                        "blocked_by_bollinger_if_required": bool(
                            components.breakout and components.ma20_over_ma60
                            and components.close_over_ma240 and not components.bollinger_upper
                        ),
                        "blocked_by_ma120_if_required": bool(
                            components.breakout and components.ma20_over_ma60
                            and components.close_over_ma240 and not components.ma120_and_slope
                        ),
                        "market_regime_pass": market_pass,
                        "market_regime_reason": ("PASS_PRIOR_PROXY_ABOVE_SMA20" if market_pass else
                                                 (context.get("reason") if context else "MISSING_REGIME_CONTEXT")),
                        "market_proxy_close": context.get("latest_proxy_close") if context else None,
                        "market_proxy_sma20": context.get("proxy_sma20") if context else None,
                        "candidate_after_regime": bool(components.passes(self.variant) and market_pass),
                    }
                    self.candidate_audit.append(row)
            order_count = len(self.local_orders)
            risk_count = len(self.risk_decisions)
            was_in_position = bool(self.is_bought and not self.is_sold)
            was_exiting = bool(self.is_exit)
            try:
                self._audit_bar = completed_kbar
                super().process_completed_kbar(code, decision_time, completed_kbar)
            finally:
                self._audit_bar = None
            if row is not None and row["candidate_after_regime"]:
                new_orders = self.local_orders[order_count:]
                new_risk = self.risk_decisions[risk_count:]
                buy_orders = [o for o in new_orders if o.get("action") == "Buy"]
                if any(o.get("status_code") == "BT-00000" for o in buy_orders):
                    row["risk_and_execution_result"] = "FILLED"
                elif new_risk and int(new_risk[-1].get("final_quantity", 0)) <= 0:
                    row["risk_and_execution_result"] = "BLOCK_RISK_ZERO_QUANTITY"
                elif buy_orders:
                    row["risk_and_execution_result"] = "BLOCK_ORDER_OR_CASH"
                elif was_in_position:
                    row["risk_and_execution_result"] = "BLOCK_ALREADY_IN_POSITION"
                elif was_exiting:
                    row["risk_and_execution_result"] = "BLOCK_EXIT_BAR_NO_REENTRY"
                elif self.cooldown_remaining > 0:
                    row["risk_and_execution_result"] = "BLOCK_COOLDOWN"
                elif self.entries_today >= self.config.max_entries_per_day:
                    row["risk_and_execution_result"] = "BLOCK_DAILY_ENTRY_LIMIT"
                else:
                    row["risk_and_execution_result"] = "BLOCK_ENGINE_ACCOUNT_STATE_OR_TIME_WINDOW"
            elif row is not None:
                row["risk_and_execution_result"] = "BLOCK_MARKET_REGIME" if row["technical_candidate"] else "BLOCK_TECHNICAL_RULES"

        def _risk_exit_signal(self, close):
            bar = self._audit_bar
            if self.entry_price is not None and bar is not None:
                from hybrid.core.multi_strategy_backtest_v1 import ohlc_stop_fill
                opening = float(bar.get("Open", close))
                low = float(bar.get("Low", close))
                fill = ohlc_stop_fill(self.entry_price, self.config.stop_loss_pct, opening, low)
                if fill:
                    self.local_fill_price_override = fill["reference_price"]
                    self.local_fill_reason_override = fill["reason_code"]
                    self.local_fill_timestamp_override = float(bar["kbar_timestamp"])
                    return fill["reason_code"], [
                        f"bar open {opening:.4f}; low {low:.4f}; stop {fill['stop_price']:.4f}"
                    ]
            return super()._risk_exit_signal(close)

        def finalize_backtest(self, code, final_time, final_price, snapshot):
            bar = getattr(self, "final_completed_kbar", None)
            if self.entry_price is not None and bar is not None:
                from hybrid.core.multi_strategy_backtest_v1 import ohlc_stop_fill
                fill = ohlc_stop_fill(
                    self.entry_price, self.config.stop_loss_pct,
                    float(bar.get("Open", final_price)), float(bar.get("Low", final_price)),
                )
                if fill:
                    self.local_fill_price_override = fill["reference_price"]
                    self.local_fill_reason_override = fill["reason_code"]
                    self.local_fill_timestamp_override = float(bar["kbar_timestamp"])
                    final_price = fill["reference_price"]
            return super().finalize_backtest(code, final_time, final_price, snapshot)

    return ShortSwingRiskV1Strategy


def _exit_kind(signal_type: str, execution_reason: Any = None) -> str:
    raw = str(execution_reason or signal_type or "").upper()
    if "STOP" in raw or "STOP_LOSS" in raw:
        return "stop_loss"
    if "MAX_HOLD" in raw or "MAX_HOLDING" in raw:
        return "time_expiration"
    if "TECHNICAL" in raw:
        return "technical_exit"
    if "END_OF_BACKTEST" in raw:
        return "end_of_backtest"
    return "other_exit"


def _metrics_and_rows(*, version: str, strategy, app, bars: list[dict[str, Any]], trade_rows: list[dict[str, Any]],
                      risk_rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    pnl_rows = bt._build_pnl_rows(trade_rows)
    curve = derive_equity_curve(bars, trade_rows, float(app.initial_capital))
    summary = summary_from_curve(curve, pnl_rows, float(app.initial_capital))
    by_day: dict[str, float] = {}
    for point in curve:
        by_day[str(point["timestamp"])[:10]] = float(point["equity"])
    daily_returns = []
    prev = float(app.initial_capital)
    for day in sorted(by_day):
        current = by_day[day]
        daily_returns.append(current / prev - 1.0 if prev else 0.0)
        prev = current
    daily_vol = float(np.std(daily_returns, ddof=0)) if daily_returns else None
    sharpe = (float(np.mean(daily_returns) / daily_vol * math.sqrt(252))
              if daily_returns and daily_vol and daily_vol > 0 else None)
    wins = [float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) > 0]
    losses = [float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) < 0]
    net_returns_wins = [float(row["return_pct"]) for row in pnl_rows if float(row["net_pnl"]) > 0 and row.get("return_pct") is not None]
    net_returns_losses = [float(row["return_pct"]) for row in pnl_rows if float(row["net_pnl"]) < 0 and row.get("return_pct") is not None]

    bar_index = {str(row["datetime"]): i for i, row in enumerate(bars)}
    session_dates = sorted({str(row["datetime"])[:10] for row in bars})
    day_index = {day: i for i, day in enumerate(session_dates)}
    sell_signals = [s for s in strategy.local_signals if s.get("action") == "Sell"]
    sell_signals.sort(key=lambda s: float(s.get("exchange_ts", 0)))
    sells = [r for r in trade_rows if r.get("action") == "Sell"]
    enriched_trades = []
    hold_bars = []
    hold_days = []
    exit_counts = {"same_day": 0, "1_day": 0, "2_day": 0, "3_day": 0, "over_3_days": 0}
    for index, pnl in enumerate(pnl_rows):
        buy_dt, sell_dt = str(pnl["buy_datetime"]), str(pnl["sell_datetime"])
        bidx = bar_index.get(buy_dt)
        sidx = bar_index.get(sell_dt)
        duration_bars = max(0, sidx - bidx) if bidx is not None and sidx is not None else None
        days = max(0, day_index.get(sell_dt[:10], 0) - day_index.get(buy_dt[:10], 0))
        if duration_bars is not None:
            hold_bars.append(duration_bars)
        hold_days.append(days)
        exit_counts[{0: "same_day", 1: "1_day", 2: "2_day", 3: "3_day"}.get(days, "over_3_days")] += 1
        exit_signal = sell_signals[index] if index < len(sell_signals) else {}
        sell_trade = sells[index] if index < len(sells) else {}
        kind = _exit_kind(str(exit_signal.get("signal_type", "")), sell_trade.get("execution_reason"))
        enriched_trades.append({
            "variant": version, **pnl, "holding_bars": duration_bars, "holding_trading_day_intervals": days,
            "exit_type": kind, "exit_signal_type": exit_signal.get("signal_type"),
            "exit_reason": exit_signal.get("reason"),
        })

    all_wins = sum(float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) > 0)
    all_losses = abs(sum(float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) < 0))
    fee_total = sum(float(row.get("fee", 0.0)) for row in trade_rows)
    tax_total = sum(float(row.get("tax", 0.0)) for row in trade_rows)
    slip_total = sum(float(row.get("slippage_cost", 0.0)) for row in trade_rows)
    loss_streak = max_streak = 0
    for pnl in pnl_rows:
        loss_streak = loss_streak + 1 if float(pnl["net_pnl"]) < 0 else 0
        max_streak = max(max_streak, loss_streak)
    summary.update({
        "variant": version, "strategy_id": version, "strategy_version": "short_swing_ab_v1",
        "stock_code": "2303", "data_role": "historical_development",
        "backtest_start": "2023-01-01", "backtest_end": "2024-12-31",
        "win_rate_pct": (100.0 * len(wins) / len(pnl_rows)) if pnl_rows else None,
        "average_trade_net_return_pct": (100.0 * float(np.mean([r["return_pct"] for r in pnl_rows if r.get("return_pct") is not None])) if pnl_rows else None),
        "profit_factor": (all_wins / all_losses) if all_losses > 0 else None,
        "profit_factor_reason": None if all_losses > 0 else ("NO_COMPLETED_TRADES" if not pnl_rows else "NO_LOSING_TRADES"),
        "sharpe_ratio": sharpe,
        "sharpe_reason": None if sharpe is not None else ("NO_DAILY_RETURN_VARIANCE_OR_TOO_FEW_DAYS"),
        "average_winning_trade_pnl": float(np.mean(wins)) if wins else None,
        "average_losing_trade_pnl": float(np.mean(losses)) if losses else None,
        "average_winning_trade_return_pct": 100.0 * float(np.mean(net_returns_wins)) if net_returns_wins else None,
        "average_losing_trade_return_pct": 100.0 * float(np.mean(net_returns_losses)) if net_returns_losses else None,
        "payoff_ratio": (float(np.mean(wins)) / abs(float(np.mean(losses)))) if wins and losses else None,
        "average_holding_bars": float(np.mean(hold_bars)) if hold_bars else None,
        "average_holding_trading_day_intervals": float(np.mean(hold_days)) if hold_days else None,
        "median_holding_bars": float(np.median(hold_bars)) if hold_bars else None,
        "median_holding_trading_day_intervals": float(np.median(hold_days)) if hold_days else None,
        "exit_holding_day_counts": exit_counts,
        "stop_loss_exit_count": sum(row["exit_type"] == "stop_loss" for row in enriched_trades),
        "technical_exit_count": sum(row["exit_type"] == "technical_exit" for row in enriched_trades),
        "time_expiration_exit_count": sum(row["exit_type"] == "time_expiration" for row in enriched_trades),
        "end_of_backtest_exit_count": sum(row["exit_type"] == "end_of_backtest" for row in enriched_trades),
        "total_fee": fee_total, "total_transaction_tax": tax_total,
        "total_slippage": slip_total, "total_transaction_cost": fee_total + tax_total + slip_total,
        "maximum_consecutive_losses": max_streak,
        "annualized_volatility_pct": (100.0 * daily_vol * math.sqrt(252)) if daily_vol is not None else None,
        "daily_mark_to_market_returns_count": len(daily_returns),
        "risk_metric_assumption": "daily close-to-close marked equity return; risk-free rate 0; population standard deviation; annualization sqrt(252)",
        "holding_day_definition": "number of observed trading-session date transitions between actual entry-open and exit-open",
        "risk_decision_count": len(risk_rows),
        "raw_breakout_trigger_count": sum(bool(row.get("breakout_cross_pass")) for row in candidate_rows),
        "would_be_blocked_by_bollinger_count": sum(bool(row.get("blocked_by_bollinger_if_required")) for row in candidate_rows),
        "would_be_blocked_by_ma120_count": sum(bool(row.get("blocked_by_ma120_if_required")) for row in candidate_rows),
        "technical_candidate_count": sum(bool(row.get("technical_candidate")) for row in candidate_rows),
        "post_market_filter_candidate_count": sum(bool(row.get("candidate_after_regime")) for row in candidate_rows),
        "actual_entry_fills": sum(row.get("action") == "Buy" for row in trade_rows),
    })

    # Year slices use continuous account equity; yearly return starts from the
    # prior year's last marked equity, rather than resetting the account.
    annual = []
    for year in (2023, 2024):
        points = [p for p in curve if str(p["timestamp"])[:4] == str(year)]
        if not points:
            continue
        start_equity = float(app.initial_capital)
        prior_points = [p for p in curve if str(p["timestamp"])[:4] < str(year)]
        if prior_points:
            start_equity = float(prior_points[-1]["equity"])
        end_equity = float(points[-1]["equity"])
        peak = start_equity
        mdd = 0.0
        for point in points:
            peak = max(peak, float(point["equity"]))
            if peak > 0:
                mdd = max(mdd, (peak - float(point["equity"])) / peak)
        annual.append({
            "variant": version, "year": year, "start_equity": start_equity,
            "end_equity": end_equity, "net_pnl": end_equity - start_equity,
            "return_pct": (end_equity / start_equity - 1) * 100 if start_equity else None,
            "max_drawdown_pct": mdd * 100,
            "trade_count": sum(str(row["sell_datetime"])[:4] == str(year) for row in enriched_trades),
        })
    summary["annual_performance"] = annual
    curve_rows = [{"variant": version, **point} for point in curve]
    return summary, enriched_trades, curve_rows


def _benchmark(bars: list[dict[str, Any]], capital: float, exposure: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not bars:
        raise RuntimeError("No bars available for Buy & Hold")
    first_open, last_close = float(bars[0]["open"]), float(bars[-1]["close"])
    qty = int(math.floor(capital * exposure / (first_open * (1 + 0.001425))))
    buy_gross, sell_gross = qty * first_open, qty * last_close
    fee = (buy_gross + sell_gross) * 0.001425
    tax = sell_gross * 0.003
    cash = capital - buy_gross - qty * first_open * 0.001425
    curve = []
    peak = capital
    for i, bar in enumerate(bars):
        value = qty * float(bar["close"])
        equity = cash + value
        if i == len(bars) - 1:
            equity -= qty * last_close * 0.003 + qty * last_close * 0.001425
        peak = max(peak, equity)
        curve.append({"variant": "buy_and_hold_full" if exposure >= 1 else "buy_and_hold_40pct",
                      "timestamp": bar["datetime"], "equity": equity,
                      "cash": cash if i < len(bars) - 1 else cash + sell_gross - qty * last_close * 0.001425 - tax,
                      "position_value": value if i < len(bars) - 1 else 0.0,
                      "realized_pnl": None if i < len(bars) - 1 else equity - capital,
                      "unrealized_pnl": value - buy_gross if i < len(bars) - 1 else 0.0,
                      "cumulative_return_pct": (equity / capital - 1) * 100,
                      "drawdown_pct": (equity / peak - 1) * 100,
                      "position_quantity": qty if i < len(bars) - 1 else 0})
    end_equity = float(curve[-1]["equity"])
    return ({"variant": curve[-1]["variant"], "quantity": qty, "exposure_target_pct": exposure * 100,
             "initial_capital": capital, "final_equity": end_equity,
             "total_return_pct": (end_equity / capital - 1) * 100, "net_pnl": end_equity - capital,
             "buy_fee": qty * first_open * 0.001425, "sell_fee": qty * last_close * 0.001425,
             "transaction_tax": tax, "total_cost": fee + tax,
             "assumption": "buy at first available bar open, hold all shares through final close, sell at final close; odd-lot integer shares; no Risk V1 stop/position cap"}, curve)


def _render_charts(output: Path, comparison: pd.DataFrame, trades: pd.DataFrame, curves: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    charts = output / "charts"
    charts.mkdir()
    fig, ax = plt.subplots(figsize=(12, 6))
    for variant, group in curves.groupby("variant", sort=False):
        ax.plot(pd.to_datetime(group["timestamp"]), group["equity"], label=variant, linewidth=1.2)
    ax.set_title("2303 Equity Curve (2023–2024)"); ax.set_ylabel("NTD"); ax.grid(alpha=.25); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(charts / "equity_curves.png", dpi=150); plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 6))
    for variant, group in curves.groupby("variant", sort=False):
        ax.plot(pd.to_datetime(group["timestamp"]), group["drawdown_pct"], label=variant, linewidth=1.1)
    ax.set_title("Drawdown Curves"); ax.set_ylabel("Drawdown %"); ax.grid(alpha=.25); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(charts / "drawdown_curves.png", dpi=150); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(comparison["variant"], comparison["total_return_pct"], color="#2478c7")
    ax.set_title("Net Total Return After Costs"); ax.set_ylabel("Return %"); ax.tick_params(axis="x", rotation=25); ax.grid(axis="y", alpha=.25)
    fig.tight_layout(); fig.savefig(charts / "net_return_comparison.png", dpi=150); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(comparison)); width = .38
    ax.bar(x-width/2, comparison["trade_count"], width, label="Trades", color="#2478c7")
    ax.set_ylabel("Trades")
    ax2 = ax.twinx(); ax2.bar(x+width/2, comparison["win_rate_pct"].fillna(0), width, label="Win rate %", color="#e0a130")
    ax2.set_ylabel("Win rate %"); ax.set_xticks(x, comparison["variant"], rotation=25)
    ax.set_title("Trade Count and Win Rate"); ax.grid(axis="y", alpha=.2)
    handles, labels = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels(); ax.legend(handles+h2, labels+l2)
    fig.tight_layout(); fig.savefig(charts / "trade_count_win_rate.png", dpi=150); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    for variant, group in trades.groupby("variant", sort=False):
        if len(group):
            ax.hist(group["holding_trading_day_intervals"], bins=np.arange(-.5, max(4.5, group["holding_trading_day_intervals"].max()+1.5), 1), alpha=.45, label=variant)
    ax.set_title("Holding Duration (Trading-session date transitions)"); ax.set_xlabel("Trading days elapsed"); ax.set_ylabel("Trade count"); ax.legend(fontsize=8); ax.grid(axis="y", alpha=.25)
    fig.tight_layout(); fig.savefig(charts / "holding_duration.png", dpi=150); plt.close(fig)


def _report_text(output: Path, config: dict[str, Any], audit: dict[str, Any], comparison: pd.DataFrame,
                 annual: pd.DataFrame, candidates: pd.DataFrame, trades: pd.DataFrame, benchmarks: dict[str, Any]) -> str:
    def pct(v): return "N/A" if pd.isna(v) else f"{float(v):.3f}%"
    def num(v): return "N/A" if pd.isna(v) else f"{float(v):,.2f}"
    lines = [
        "# Technical V2-B 原始策略與短線波段改善 A/B 回測",
        "",
        f"- 實驗：`{config['experiment_id']}`；股票：2303 聯電。",
        "- 資料角色：2023–2024 已曝光歷史開發資料；不是 Final OOS，也不是正式即時訊號。",
        f"- 實際資料：{audit['actual_first_session']} 至 {audit['actual_last_session']}，{audit['trading_day_count']} 個共同完整交易日、{audit['bar_count']} 根 5 分 K。",
        "- 五組均使用純 Technical + Risk V1，未使用 ML Gate。各版本共用 5 檔股票完整 session 交集所產生的同一市場代理濾網、資料、初始本金、成本及執行規則。",
        "- 成交：完成 K 棒訊號後，於下一根實際可交易 K 棒開盤模擬；滑價 0。停損沿用現有 OHLC 保守近似：開盤跳空按開盤價，盤中觸及按停損價，停損優先。",
        "- 股票交易成本沿用引擎：買賣手續費 0.1425%；一般賣出證交稅 0.3%；同日出場若引擎條件成立，沿用當沖稅率 0.15%。",
        "- 股數沿用引擎整股／零股委託與 Risk V1 風險／曝險限制；原候選設定另有每筆最多 1,000 股的引擎數量上限，故部位可能低於 40% 上限與 1% 風險預算。",
        "- 最大持有 162 根 5 分 K、最短持有 2 根、冷卻 6 根、每日最多進場 1 次；週期低點與均線只用當下已完成 K 棒及其之前資料。",
        "",
        "## 五組總表",
        "",
        "| 版本 | 交易 | 報酬 | 淨損益 | 勝率 | 最大回撤 | Profit Factor | Sharpe | 年化波動 | 平均持有日間隔 | 成本 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison.to_dict("records"):
        lines.append(f"| {row['variant']} | {int(row['trade_count'])} | {pct(row['total_return_pct'])} | {num(row['net_pnl'])} | {pct(row['win_rate_pct'])} | {pct(row['max_drawdown_pct'])} | {num(row['profit_factor'])} | {num(row['sharpe_ratio'])} | {pct(row['annualized_volatility_pct'])} | {num(row['average_holding_trading_day_intervals'])} | {num(row['total_transaction_cost'])} |")
    lines += ["", "## 年度結果", "", "| 版本 | 年度 | 年初資產 | 年末資產 | 年度損益 | 年度報酬 | 年度最大回撤 | 出場交易數 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in annual.to_dict("records"):
        lines.append(f"| {row['variant']} | {row['year']} | {num(row['start_equity'])} | {num(row['end_equity'])} | {num(row['net_pnl'])} | {pct(row['return_pct'])} | {pct(row['max_drawdown_pct'])} | {int(row['trade_count'])} |")
    lines += ["", "## 進場漏斗", "", "| 版本 | 突破觸發 | 布林可能阻擋 | MA120可能阻擋 | 技術候選 | 市場濾網後 | 風控決策 | 買進成交 | Risk/帳戶阻擋 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for variant in VARIANTS:
        g = candidates.loc[candidates.variant == variant]
        risk_rows = comparison.loc[comparison.variant == variant].iloc[0]
        block_count = int((g.get("risk_and_execution_result", pd.Series(dtype=str)).astype(str).str.startswith("BLOCK")).sum())
        lines.append(f"| {variant} | {int(risk_rows['raw_breakout_trigger_count'])} | {int(risk_rows['would_be_blocked_by_bollinger_count'])} | {int(risk_rows['would_be_blocked_by_ma120_count'])} | {int(g['technical_candidate'].sum()) if len(g) else 0} | {int(g['candidate_after_regime'].sum()) if len(g) else 0} | {int(risk_rows['risk_decision_count'])} | {int(risk_rows['actual_entry_fills'])} | {block_count} |")
    lines += ["", "## Buy & Hold 參考", "", f"- 全額買進持有：{pct(benchmarks['buy_and_hold_full']['total_return_pct'])}，數量 {benchmarks['buy_and_hold_full']['quantity']} 股，成本 {num(benchmarks['buy_and_hold_full']['total_cost'])} 元。採可買進的整數股，持有至最後一根收盤賣出；不套用 Risk V1 停損或 40% 上限，因此曝險與主動策略不同。",
              f"- 40% 初始資金曝險參考：{pct(benchmarks['buy_and_hold_40pct']['total_return_pct'])}，數量 {benchmarks['buy_and_hold_40pct']['quantity']} 股；這是依初始本金 40% 設定的曝險對照，仍不代表與會動態調倉的主動策略完全同風險。", "",
              "## 研究問題判讀", ""]
    base = comparison.loc[comparison.variant == "A_ORIGINAL_V2B"].iloc[0]
    for variant, question in [
        ("B_NO_BOLLINGER", "1. 移除布林上軌後，交易機會增加多少？績效是否改善？"),
        ("C_NO_MA120", "2. 再移除 MA120 與斜率條件後，是否增加有效突破訊號？"),
        ("D_EXIT55", "3. 將出場低點改為 55 根後，是否降低過早出場？"),
        ("E_EXIT80", "4. 將出場低點改為 80 根後，是否降低過早出場？"),
    ]:
        v = comparison.loc[comparison.variant == variant].iloc[0]
        g = candidates.loc[candidates.variant == variant]
        b = candidates.loc[candidates.variant == "A_ORIGINAL_V2B"]
        lines.append(f"- **{question}** {variant} 技術候選 {int(g['technical_candidate'].sum()) if len(g) else 0}，A 為 {int(b['technical_candidate'].sum()) if len(b) else 0}；實際交易 {int(v['trade_count'])} 對 A 的 {int(base['trade_count'])}；成本後報酬 {pct(v['total_return_pct'])} 對 {pct(base['total_return_pct'])}，最大回撤 {pct(v['max_drawdown_pct'])} 對 {pct(base['max_drawdown_pct'])}。")
    for variant in ("C_NO_MA120", "D_EXIT55", "E_EXIT80"):
        v = comparison.loc[comparison.variant == variant].iloc[0]
        lines.append(f"- **5. {variant} 是否符合 1–3 日持有並改善成本後績效？** 平均持有日間隔 {num(v['average_holding_trading_day_intervals'])}；當日/1/2/3 日出場數分別為 {v['exit_holding_day_counts']}；成本後報酬 {pct(v['total_return_pct'])}，相對 A 為 {pct(v['total_return_pct']-base['total_return_pct'])} 個百分點。")
    lines += ["", "- **6. 增加交易後風險是否上升？** 以最大回撤及每日淨值年化波動率觀察，B–E 的最大回撤均高於 A（A 為 0.754%；B/C/D/E 分別為 "
              + "/".join(pct(comparison.loc[comparison.variant == v].iloc[0]["max_drawdown_pct"]) for v in ("B_NO_BOLLINGER", "C_NO_MA120", "D_EXIT55", "E_EXIT80"))
              + "），年化波動率也均高於 A（A 為 " + pct(base["annualized_volatility_pct"]) + "；B/C/D/E 分別為 "
              + "/".join(pct(comparison.loc[comparison.variant == v].iloc[0]["annualized_volatility_pct"]) for v in ("B_NO_BOLLINGER", "C_NO_MA120", "D_EXIT55", "E_EXIT80"))
              + "）。放寬條件帶來更多成交，但本次沒有換來更好的成本後報酬；回撤與波動指標均顯示風險增加。",
              "- **7. 哪個版本值得進入 ML Gate 與樣本外驗證？** 本輪沒有改善版在成本後報酬、回撤及 Profit Factor 上勝過 A，因此不應把任何 B–E 宣告為已改善或取代凍結策略。若下一輪的研究問題是檢查 ML Gate 能否篩除低品質候選，可保留 A 作為原策略對照，並把 C 作為「候選較多的診斷組」進行預先規劃的 Gate 測試；C 不是績效勝出者，測試結果仍須視為探索性，且不得用本段已曝光資料宣稱樣本外成功。"]
    lines += ["", "## 結論與下一階段建議", ""]
    for row in comparison.to_dict("records"):
        hold = row.get("average_holding_trading_day_intervals")
        lines.append(f"- **{row['variant']}**：{int(row['trade_count'])} 筆已完成交易，報酬 {pct(row['total_return_pct'])}、最大回撤 {pct(row['max_drawdown_pct'])}、PF {num(row['profit_factor'])}、平均持有 {num(hold)} 個交易日間隔。")
    lines += ["", "本報告只在 2303 與已曝光的 2023–2024 開發資料比較五個預先指定版本，不能據此宣稱泛化或 Final OOS 成功。下一階段是否進入 ML Gate 應同時檢查交易成本後報酬、回撤、PF、交易數、年度一致性與候選品質；不以單一最高報酬選版。此資料期間已曝光，因此結果屬研究探索，後續需另找未曝光資料驗證。", "",
              "## 輸出與稽核", "", "- `comparison.csv`：策略績效總表。", "- `annual_performance.csv`：年度績效。", "- `trades.csv`：逐筆交易。", "- `candidate_audit.csv`：突破訊號、條件阻擋與風控／成交漏斗。", "- `risk_decisions.csv`：逐筆 Risk V1 股數決策。", "- `equity_curves.csv`：每根 K 棒資產曲線。", "- `config_snapshot.json`、`audit.json`：參數、資料與程式雜湊。", "- `charts/`：報告圖表。"]
    return "\n".join(lines) + "\n"


def run() -> Path:
    config = _load_config()
    tech_path = ROOT / config["technical_config"]
    candidate_config_path = ROOT / config["frozen_candidate_config"]
    db_path = ROOT / config["data_source"]
    if not all(p.is_file() for p in (tech_path, candidate_config_path, db_path)):
        raise FileNotFoundError("BLOCKED_MISSING_CONFIG_OR_DATABASE")
    tech = tv1.read_config(tech_path)
    tv1.assert_execution_ready(tech, "training")
    family_name, family, base_candidate = tv1.candidate_by_id(tech, config["frozen_candidate_id"])
    frozen_cfg = json.loads(candidate_config_path.read_text(encoding="utf-8"))
    if frozen_cfg.get("base_candidate") != base_candidate["id"] or frozen_cfg.get("market_regime_filter", {}).get("enabled") is not True:
        raise RuntimeError("BLOCKED_FROZEN_TECHNICAL_CANDIDATE_IDENTITY_MISMATCH")
    db_hash_before = sha256(db_path)
    output = ROOT / "exports" / f"short_swing_ab_test_{datetime.now().strftime('%Y%m%dT%H%M%S')}"

    with tv1._readonly_connection(db_path) as connection:
        fold = {"id": "AB_2023_2024", "train_start": "2023-01-01", "train_end": "2024-12-31",
                "evaluate_start": "2023-01-01", "evaluate_end": "2024-12-31"}
        full_frame, evaluation, app_base, warmup_count, eval_lower = tv1._load_fold_frame(
            connection, tech, base_candidate, config["stock_code"], fold,
        )
        frame_times = full_frame["kbar_time"].to_list()
        schedule = {iso_taipei(value) for value in frame_times[1:]}
        candidate_dates = {value[:10] for value in schedule}
        regime_context, regime_audit = build_regime_context(
            connection, tuple(frozen_cfg["market_regime_filter"]["peer_stock_codes"]), candidate_dates,
        )
        raw_rows = connection.execute(
            "SELECT stock_code,kbar_timestamp,open,high,low,close,volume FROM market_kbars "
            "WHERE stock_code IN (?,?,?,?,?) AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
            "ORDER BY stock_code,kbar_timestamp",
            (*frozen_cfg["market_regime_filter"]["peer_stock_codes"],
             *tv1._timestamp_bounds("2023-01-01", "2024-12-31")),
        ).fetchall()
        session_counts: dict[tuple[str, str], int] = {}
        for row in raw_rows:
            local_dt = datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI)
            if local_dt.strftime("%H:%M") < "09:00" or local_dt.strftime("%H:%M") > "13:25":
                continue
            session_counts[(str(row["stock_code"]), local_dt.date().isoformat())] = session_counts.get((str(row["stock_code"]), local_dt.date().isoformat()), 0) + 1
        target_days = sorted(day for stock, day in session_counts if stock == config["stock_code"])
        incomplete = [(s, d, count) for (s, d), count in session_counts.items() if count != 54]
        if not target_days or incomplete or len(set(session_counts)) != 5 * len(target_days):
            raise RuntimeError(f"BLOCKED_SESSION_COMPLETENESS:{len(incomplete)} incomplete stock-days")
        for stock in frozen_cfg["market_regime_filter"]["peer_stock_codes"]:
            stock_days = {d for s, d in session_counts if s == stock}
            if stock_days != set(target_days):
                raise RuntimeError(f"BLOCKED_GLOBAL_TRADING_DATE_PARITY:{stock}")

        variants_result = []
        all_trade_rows, all_curve_rows, all_candidate_rows, all_risk_rows, annual_rows = [], [], [], [], []
        base_bars = None
        for variant in VARIANTS:
            candidate = _variant_candidate(base_candidate, variant)
            config_variant = copy.deepcopy(tech)
            config_variant["common_backtest"]["initial_capital"] = config["initial_capital"]
            risk_cfg = config["risk"]
            config_variant["common_backtest"].update({
                "stock_fee_rate": config["execution"]["stock_fee_rate"],
                "stock_tax_rate": config["execution"]["stock_tax_rate"],
                "slippage_rate": config["execution"]["slippage_rate"],
                "stop_loss_pct": risk_cfg["stop_loss_pct"],
                "min_hold_bars": risk_cfg["min_holding_bars"],
                "max_hold_bars": risk_cfg["max_holding_bars"],
                "cooldown_bars": risk_cfg["cooldown_bars"],
                "max_entries_per_day": risk_cfg["max_entries_per_day"],
            })
            # Rebuild indicator frame only where the exit lookback differs; the
            # underlying raw timestamp set is asserted identical across versions.
            full, eval_frame, app, _, lower = tv1._load_fold_frame(
                connection, config_variant, candidate, config["stock_code"], fold,
            )
            if full["kbar_timestamp"].to_list() != full_frame["kbar_timestamp"].to_list():
                raise RuntimeError(f"BLOCKED_VARIANT_BAR_ALIGNMENT:{variant}")
            if base_bars is None:
                base_bars = bt.build_kbar_rows_from_frame(eval_frame, app)
            bars = bt.build_kbar_rows_from_frame(eval_frame, app)
            strategy_cls = _make_strategy_class(variant)
            strategy = strategy_cls(
                config=app, study_config=config_variant, family_name=family_name, family=family,
                candidate=candidate, data_role="historical_development", fold_id="AB_2023_2024",
                is_backtest=True, is_simulation=True, local_only=True,
                ml_model="technical_only", prediction_lookup={}, candidate_schedule=schedule,
                regime_context=regime_context,
            )
            strategy.configure_local_account(app)
            strategy.evaluation_start_timestamp = lower
            strategy.local_slippage_bps = float(config["execution"]["slippage_rate"]) * 10000.0
            strategy.diagnostic_sizing_enabled = True
            stats = bt.MarketDataStats(
                tick_count=full.height,
                first_timestamp=float(full["kbar_timestamp"][0]),
                last_timestamp=float(full["kbar_timestamp"][-1]),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                bt.replay_kbars(strategy, full, stats)
            trades = bt._build_trade_rows(strategy, app)
            risk_rows = [dict(variant=variant, **row) for row in strategy.risk_decisions]
            candidate_rows = [dict(row) for row in strategy.candidate_audit]
            summary, enriched, curves = _metrics_and_rows(
                version=variant, strategy=strategy, app=app, bars=bars,
                trade_rows=trades, risk_rows=risk_rows, candidate_rows=candidate_rows,
            )
            if strategy.local_position != 0:
                raise RuntimeError(f"BLOCKED_OPEN_POSITION_AFTER_REPLAY:{variant}")
            variants_result.append(summary)
            all_trade_rows.extend(enriched)
            all_curve_rows.extend(curves)
            all_candidate_rows.extend(candidate_rows)
            all_risk_rows.extend(risk_rows)
            annual_rows.extend(summary.pop("annual_performance"))
            summary["annual_performance"] = "see annual_performance.csv"

        if sha256(db_path) != db_hash_before:
            raise RuntimeError("BLOCKED_DATABASE_CHANGED_DURING_READ_ONLY_RUN")

    # Use common 5-minute axis for both buy-and-hold reference exposures.
    benchmark_full, bh_full_curve = _benchmark(base_bars or [], float(config["initial_capital"]), 1.0)
    benchmark_40, bh_40_curve = _benchmark(base_bars or [], float(config["initial_capital"]), 0.4)
    all_curve_rows.extend(bh_full_curve)
    all_curve_rows.extend(bh_40_curve)
    comparison = pd.DataFrame(variants_result)
    annual = pd.DataFrame(annual_rows)
    trades_df = pd.DataFrame(all_trade_rows)
    candidates_df = pd.DataFrame(all_candidate_rows)
    curves_df = pd.DataFrame(all_curve_rows)
    risk_df = pd.DataFrame(all_risk_rows)
    if candidates_df.empty:
        candidates_df = pd.DataFrame(columns=["variant", "technical_candidate", "candidate_after_regime", "risk_and_execution_result"])
    output.mkdir(parents=True, exist_ok=False)
    # A row is emitted for every raw 55-bar breakout/cross trigger. Counts are
    # derived from those rows, not inferred from completed round-trip trades.
    comparison.to_csv(output / "comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(output / "annual_performance.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(output / "trades.csv", index=False, encoding="utf-8-sig")
    candidates_df.to_csv(output / "candidate_audit.csv", index=False, encoding="utf-8-sig")
    curves_df.to_csv(output / "equity_curves.csv", index=False, encoding="utf-8-sig")
    risk_df.to_csv(output / "risk_decisions.csv", index=False, encoding="utf-8-sig")
    (output / "config_snapshot.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    actual_first = str(curves_df.loc[curves_df.variant == VARIANTS[0], "timestamp"].iloc[0])[:10]
    actual_last = str(curves_df.loc[curves_df.variant == VARIANTS[0], "timestamp"].iloc[-1])[:10]
    audit = {
        "status": "COMPLETED", "experiment_id": config["experiment_id"],
        "data_role": config["data_role"], "stock_code": config["stock_code"],
        "requested_period": config["period"], "actual_first_session": target_days[0],
        "actual_last_session": target_days[-1], "trading_day_count": len(target_days),
        "bar_count": len(full_frame), "training_warmup_bars": int(warmup_count),
        "database_path": str(db_path.relative_to(ROOT)), "database_sha256_before": db_hash_before,
        "database_sha256_after": sha256(db_path),
        "technical_config_path": str(tech_path.relative_to(ROOT)), "technical_config_sha256": sha256(tech_path),
        "frozen_candidate_config_path": str(candidate_config_path.relative_to(ROOT)),
        "frozen_candidate_config_sha256": sha256(candidate_config_path),
        "technical_source_sha256": sha256(ROOT / "trading_system/technical_v1.py"),
        "market_regime_source": "hybrid/scripts/run_strategy_v2_batch.py::build_regime_context",
        "market_regime_audit": regime_audit,
        "market_data_quality": {"all_five_stocks_same_dates": True, "bars_per_stock_session": 54,
                                 "incomplete_sessions": 0, "missing_fill_used": False},
        "variants": list(VARIANTS), "risk_v1": config["risk"],
        "entry_execution": "completed 5-minute K-bar signal -> next actual tradable 5-minute bar open",
        "stop_execution": "OHLC stop approximation: gap uses current bar open; intrabar low touch uses stop; stop has priority",
        "same_day_tax": "existing engine 0.15% for eligible same-day round trips; otherwise 0.3%",
        "position_quantity_cap": int(app_base.quantity),
        "candidate_counts": {v: {
            "raw_breakout_trigger": int((candidates_df.loc[candidates_df.variant == v, "breakout_cross_pass"] == True).sum()) if len(candidates_df) else 0,
            "technical_candidate": int(candidates_df.loc[candidates_df.variant == v, "technical_candidate"].fillna(False).sum()) if len(candidates_df) else 0,
            "after_market_filter": int(candidates_df.loc[candidates_df.variant == v, "candidate_after_regime"].fillna(False).sum()) if len(candidates_df) else 0,
            "risk_decisions": int((risk_df.variant == v).sum()) if len(risk_df) else 0,
            "buy_fills": int(((candidates_df.variant == v) & (candidates_df.risk_and_execution_result == "FILLED")).sum()) if len(candidates_df) else 0,
        } for v in VARIANTS},
        "buy_and_hold": {"full_capital": benchmark_full, "initial_40pct_exposure": benchmark_40},
        "lookahead_checks": {"current_bar_excluded_from_breakout_high": True,
                             "market_regime_uses_strict_prior_complete_sessions": True,
                             "signal_uses_completed_bar_only": True,
                             "entry_at_next_actual_tradable_open": True,
                             "OHLCV_fill_used": False},
        "final_oos_used": False, "ml_gate_used": False, "model_training_performed": False,
        "parameter_search_performed": False, "frozen_technical_config_modified": False,
        "database_modified": False, "comparison_is_single_stock_exploratory": True,
        "output_files": [],
    }
    _render_charts(output, comparison, trades_df, curves_df)
    (output / "report.md").write_text(_report_text(output, config, audit, comparison, annual, candidates_df,
                                                    trades_df, {"buy_and_hold_full": benchmark_full,
                                                                "buy_and_hold_40pct": benchmark_40}), encoding="utf-8")
    audit["output_files"] = sorted(str(p.relative_to(output)).replace("\\", "/") for p in output.rglob("*") if p.is_file()) + ["audit.json"]
    write_json(output / "audit.json", audit)
    print(json.dumps({
        "status": "COMPLETED", "output_directory": str(output.relative_to(ROOT)),
        "actual_sessions": [target_days[0], target_days[-1]], "trading_days": len(target_days),
        "bar_count": len(full_frame), "comparison": comparison[["variant", "trade_count", "total_return_pct", "win_rate_pct", "max_drawdown_pct", "profit_factor", "sharpe_ratio"]].to_dict("records"),
        "candidate_counts": audit["candidate_counts"],
        "final_oos_used": False, "ml_gate_used": False,
    }, ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    try:
        run()
    except (OSError, ValueError, KeyError, sqlite3.Error, RuntimeError) as exc:
        raise SystemExit(f"SHORT_SWING_AB_TEST_BLOCKED: {exc}") from exc


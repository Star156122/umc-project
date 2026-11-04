from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.expected_edge_filter_exp001 import (  # noqa: E402
    estimate_expected_net_edge, validate_no_lookahead,
)
from hybrid.core.risk_v1 import calculate_risk_size  # noqa: E402
from hybrid.scripts.run_ml_v2_exp001 import TechnicalV1Strategy, TechnicalV2AloneStrategy  # noqa: E402
from hybrid.scripts.run_short_swing_ab_test import (  # noqa: E402
    _make_strategy_class, _metrics_and_rows, iso_taipei, json_safe, sha256, write_json,
)
from hybrid.scripts.run_strategy_v2_batch import build_regime_context  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
CONFIG_PATH = ROOT / "hybrid/config/expected_edge_filter_exp001.json"
TECH_CONFIG_PATH = ROOT / "configs/technical_v1_research.json"
FROZEN_CONFIG_PATH = ROOT / "configs/technical_v2_candidate.json"
ARM_A = "A_FROZEN_V2B"
ARM_B = "B_EXPECTED_EDGE_FILTER"
MIN_HISTORY = 5
ESTIMATION_SLIPPAGE_BPS = 5.0
SENSITIVITY_SLIPPAGE_BPS = (0.0, 5.0, 10.0)


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if (config.get("status") != "PREREGISTERED"
            or config.get("data_role") != "historical_development"
            or config.get("period") != {"start": "2023-01-01", "end": "2024-12-31"}
            or config.get("stock_code") != "2303"
            or config.get("parameter_search_performed") is not False
            or config.get("final_oos_used") is not False
            or config.get("additional_holdout_used") is not False):
        raise RuntimeError("BLOCKED_EXPERIMENT_CONFIG_OR_DATA_ROLE_MISMATCH")
    tv1.assert_development_period("2023-01-01", "2024-12-31")
    return config


def _ts_from_datetime(value: str) -> int:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI)
    return int(parsed.timestamp())


def _baseline_outcomes(pnl_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcomes = []
    for row in pnl_rows:
        buy_amount = float(row.get("buy_amount") or 0.0)
        if buy_amount <= 0:
            continue
        outcomes.append({
            "exit_timestamp": _ts_from_datetime(str(row["sell_datetime"])),
            "gross_return": float(row["gross_pnl"]) / buy_amount,
            "baseline_buy_datetime": str(row["buy_datetime"]),
            "baseline_sell_datetime": str(row["sell_datetime"]),
        })
    return outcomes


def _trade_time_key(value: str) -> str:
    parsed = datetime.fromisoformat(str(value))
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _make_edge_strategy(baseline_outcomes: list[dict[str, Any]], fee_rate: float, tax_rate: float):
    Parent = _make_strategy_class("A_ORIGINAL_V2B")

    class ExpectedEdgeStrategy(Parent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.baseline_outcomes = baseline_outcomes
            self.edge_decisions: list[dict[str, Any]] = []
            self._current_edge_decision: dict[str, Any] | None = None

        def _estimate_for_bar(self, code, decision_time, completed_kbar):
            close = completed_kbar.get("Close")
            snapshot = bt.indicator_snapshot(completed_kbar)
            if close is None:
                return None
            close = float(close)
            reasons = self._entry_reasons(close, snapshot)
            if not reasons:
                return None
            signal_time = decision_time if decision_time.tzinfo else decision_time.replace(tzinfo=TAIPEI)
            signal_time = signal_time.astimezone(TAIPEI)
            if signal_time.isoformat(timespec="microseconds") not in self.candidate_schedule:
                return None
            context = self.regime_context.get((str(code), signal_time.date().isoformat()))
            if (not context or context.get("eligible") is not True
                    or len(context.get("last_20_proxy_closes", [])) != 20
                    or float(context["last_20_proxy_closes"][-1]) <= float(np.mean(context["last_20_proxy_closes"]))):
                return None

            signal_timestamp = int(completed_kbar["kbar_timestamp"]) + 299
            current_equity = float(self.local_cash or 0.0) + int(self.local_position) * close
            base_quantity = int(TechnicalV1Strategy._order_quantity(self, close))
            sizing = calculate_risk_size(
                current_equity=current_equity,
                entry_price=close * (1.0 + ESTIMATION_SLIPPAGE_BPS / 10_000.0),
                original_quantity=base_quantity,
                stop_loss_pct=float(self.config.stop_loss_pct),
            )
            estimate = estimate_expected_net_edge(
                completed_trades=self.baseline_outcomes,
                candidate_timestamp=signal_timestamp,
                entry_price=close,
                quantity=int(sizing["final_quantity"]),
                min_matured_trades=MIN_HISTORY,
                commission_rate=fee_rate,
                tax_rate=tax_rate,
                slippage_bps_per_side=ESTIMATION_SLIPPAGE_BPS,
            )
            row = {
                "arm": ARM_B,
                "stock_code": str(code),
                "signal_bar_timestamp": int(completed_kbar["kbar_timestamp"]),
                "completed_signal_timestamp": signal_timestamp,
                "signal_bar_end_time": datetime.fromtimestamp(signal_timestamp, TAIPEI).isoformat(),
                "candidate_time": signal_time.isoformat(timespec="microseconds"),
                "expected_edge_status": estimate.status,
                "matured_baseline_trade_count": estimate.matured_trade_count,
                "latest_training_exit_timestamp": estimate.latest_training_exit_timestamp,
                "estimated_gross_return_pct": None if estimate.estimated_gross_return is None else estimate.estimated_gross_return * 100.0,
                "estimated_gross_pnl": estimate.estimated_gross_pnl,
                "estimated_cost": estimate.estimated_cost,
                "estimated_net_pnl": estimate.estimated_net_pnl,
                "estimated_net_return_pct": None if estimate.estimated_net_return is None else estimate.estimated_net_return * 100.0,
                "estimated_quantity": estimate.estimated_quantity,
                "estimation_entry_reference_price": close,
                "commission_rate": fee_rate,
                "ordinary_sell_tax_rate": tax_rate,
                "estimation_slippage_bps_per_side": ESTIMATION_SLIPPAGE_BPS,
                "history_source": "prior_completed_A_FROZEN_V2B_trades_only",
            }
            self.edge_decisions.append(row)
            return row

        def _try_entry(self, code, tick_time, close, snapshot):
            decision = self._current_edge_decision
            if decision is None or decision["expected_edge_status"] != "ALLOW_EXPECTED_NET_POSITIVE":
                return
            return super()._try_entry(code, tick_time, close, snapshot)

        def process_completed_kbar(self, code, decision_time, completed_kbar):
            edge_decision = self._estimate_for_bar(code, decision_time, completed_kbar)
            self._current_edge_decision = edge_decision
            before = len(self.candidate_audit)
            try:
                super().process_completed_kbar(code, decision_time, completed_kbar)
            finally:
                self._current_edge_decision = None
            if len(self.candidate_audit) > before:
                candidate = self.candidate_audit[-1]
                if edge_decision is not None:
                    candidate.update(edge_decision)
                elif candidate.get("candidate_after_regime"):
                    # This path should not be needed for an eligible candidate; retain a
                    # visible status instead of silently treating it as allowed.
                    candidate["expected_edge_status"] = "NOT_EVALUATED_ENGINE_PATH"
                else:
                    candidate["expected_edge_status"] = "NOT_APPLICABLE_CANDIDATE_OR_REGIME"
                if (candidate.get("candidate_after_regime")
                        and candidate.get("expected_edge_status") != "ALLOW_EXPECTED_NET_POSITIVE"):
                    status = candidate.get("expected_edge_status")
                    candidate["risk_and_execution_result"] = (
                        "BLOCK_EXPECTED_EDGE_INSUFFICIENT_HISTORY" if status == "INSUFFICIENT_MATURED_BASELINE_TRADES"
                        else "BLOCK_EXPECTED_EDGE_NO_ESTIMABLE_QUANTITY" if status == "NO_ESTIMABLE_QUANTITY"
                        else "BLOCK_EXPECTED_EDGE_NOT_EVALUATED" if status == "NOT_EVALUATED_ENGINE_PATH"
                        else "BLOCK_EXPECTED_EDGE_NOT_POSITIVE"
                    )

    return ExpectedEdgeStrategy


def _run_one(*, arm: str, slippage_bps: float, frame, schedule, regime_context,
             tech: dict[str, Any], family_name: str, family: dict[str, Any], candidate: dict[str, Any],
             config: dict[str, Any], eval_lower: int, baseline_outcomes: list[dict[str, Any]] | None = None):
    config_variant = copy.deepcopy(tech)
    config_variant["common_backtest"]["initial_capital"] = config["initial_capital"]
    config_variant["common_backtest"].update({
        "stock_fee_rate": config["execution"]["commission_rate"],
        "stock_tax_rate": config["execution"]["stock_tax_rate"],
        "slippage_rate": float(slippage_bps) / 10_000.0,
        "stop_loss_pct": config["risk"]["stop_loss_pct"],
        "min_hold_bars": config["risk"]["min_holding_bars"],
        "max_hold_bars": config["risk"]["max_holding_bars"],
        "cooldown_bars": config["risk"]["cooldown_bars"],
        "max_entries_per_day": config["risk"]["max_entries_per_day"],
    })
    app = tv1.build_app_config(config_variant, candidate, config["stock_code"], config["period"]["start"], config["period"]["end"])
    if arm == ARM_A:
        strategy_cls = _make_strategy_class("A_ORIGINAL_V2B")
    else:
        strategy_cls = _make_edge_strategy(
            baseline_outcomes or [], config["execution"]["commission_rate"], config["execution"]["stock_tax_rate"],
        )
    strategy = strategy_cls(
        config=app, study_config=config_variant, family_name=family_name, family=family,
        candidate=candidate, data_role="historical_development", fold_id="EXP001_2023_2024",
        is_backtest=True, is_simulation=True, local_only=True,
        ml_model="technical_only", prediction_lookup={}, candidate_schedule=schedule,
        regime_context=regime_context,
    )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = eval_lower
    strategy.local_slippage_bps = float(slippage_bps)
    strategy.diagnostic_sizing_enabled = True
    stats = bt.MarketDataStats(
        tick_count=frame.height,
        first_timestamp=float(frame["kbar_timestamp"][0]),
        last_timestamp=float(frame["kbar_timestamp"][-1]),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, frame, stats)
    if strategy.local_position != 0:
        raise RuntimeError(f"BLOCKED_OPEN_POSITION_AFTER_REPLAY:{arm}/{slippage_bps}")
    trade_rows = bt._build_trade_rows(strategy, app)
    pnl_rows = bt._build_pnl_rows(trade_rows)
    bar_rows = bt.build_kbar_rows_from_frame(frame, app)
    summary, enriched, curve = _metrics_and_rows(
        version=arm, strategy=strategy, app=app, bars=bar_rows, trade_rows=trade_rows,
        risk_rows=[{"arm": arm, **row} for row in strategy.risk_decisions],
        candidate_rows=[{"arm": arm, **row} for row in strategy.candidate_audit],
    )
    candidate_rows = [{"arm": arm, "simulated_slippage_bps_per_side": slippage_bps, **row} for row in strategy.candidate_audit]
    trade_rows_enriched = [{"arm": arm, "simulated_slippage_bps_per_side": slippage_bps, **row} for row in enriched]
    curve_rows = [{"arm": arm, "simulated_slippage_bps_per_side": slippage_bps, **row} for row in curve]
    risk_rows = [{"arm": arm, "simulated_slippage_bps_per_side": slippage_bps, **row} for row in strategy.risk_decisions]
    summary.update({
        "arm": arm,
        "simulated_slippage_bps_per_side": slippage_bps,
        "gross_pnl": float(sum(float(row["gross_pnl"]) for row in pnl_rows)),
        "net_pnl": float(sum(float(row["net_pnl"]) for row in pnl_rows)),
        "trade_count": len(pnl_rows),
        "candidate_count": sum(bool(row.get("candidate_after_regime")) for row in candidate_rows),
        "filled_entries": sum(row.get("action") == "Buy" for row in trade_rows),
        "commission_total": sum(float(row.get("fee", 0.0)) for row in trade_rows),
        "transaction_tax_total": sum(float(row.get("tax", 0.0)) for row in trade_rows),
        "slippage_total": sum(float(row.get("slippage_cost", 0.0)) for row in trade_rows),
        "total_transaction_cost": sum(float(row.get("fee", 0.0)) + float(row.get("tax", 0.0)) + float(row.get("slippage_cost", 0.0)) for row in trade_rows),
        "risk_decision_count": len(risk_rows),
        "technical_exit_lookback_bars": 30,
        "average_exposure_fraction": _average_exposure(curve),
        "return_per_average_exposure_pct": _return_per_average_exposure(summary, curve),
    })
    return {
        "summary": summary, "trades": trade_rows_enriched, "curves": curve_rows,
        "candidates": candidate_rows, "risk": risk_rows,
        "pnl_rows": pnl_rows,
        "edge_decisions": list(getattr(strategy, "edge_decisions", [])),
        "bar_count": frame.height, "strategy": strategy,
    }


def _average_exposure(curve: list[dict[str, Any]]) -> float | None:
    if not curve:
        return None
    equities = [float(row.get("equity", 0.0)) for row in curve if float(row.get("equity", 0.0)) > 0]
    if not equities:
        return None
    return float(np.mean([float(row.get("position_value", 0.0)) / float(row["equity"]) for row in curve if float(row.get("equity", 0.0)) > 0]))


def _return_per_average_exposure(summary: dict[str, Any], curve: list[dict[str, Any]]) -> float | None:
    exposure = _average_exposure(curve)
    result = summary.get("total_return_pct")
    if exposure is None or exposure <= 0 or result is None:
        return None
    return float(result) / exposure


def _candidate_rows_for_arm(arm_result, arm: str):
    return [row for row in arm_result["candidates"] if row.get("arm") == arm]


def _augment_counterfactuals(a_result, b_result):
    a_by_buy = {_trade_time_key(row["buy_datetime"]): row for row in a_result["trades"]}
    b_by_buy = {_trade_time_key(row["buy_datetime"]): row for row in b_result["trades"]}
    blocked, prediction_eval = [], []
    for row in _candidate_rows_for_arm(b_result, ARM_B):
        if not row.get("candidate_after_regime"):
            continue
        entry = _trade_time_key(row.get("earliest_entry_time", ""))
        is_blocked = row.get("expected_edge_status") != "ALLOW_EXPECTED_NET_POSITIVE"
        actual = a_by_buy.get(entry) if is_blocked else b_by_buy.get(entry)
        blocked.append({
            **row,
            "counterfactual_source": "A_FROZEN_V2B" if is_blocked else "B_EXPECTED_EDGE_FILTER",
            "counterfactual_match_status": "MATCHED_BASELINE_TRADE" if actual and is_blocked else (
                "MATCHED_B_TRADE" if actual else "NO_REALIZED_TRADE_AT_THIS_CANDIDATE"),
            "actual_gross_return_pct": None if not actual else (float(actual.get("gross_pnl", 0.0)) / float(actual.get("buy_amount", 0.0)) * 100.0 if actual.get("buy_amount") else None),
            "actual_net_return_pct": None if not actual or actual.get("return_pct") is None else 100.0 * float(actual.get("return_pct")),
            "actual_net_pnl": None if not actual else actual.get("net_pnl"),
        })
        if actual and row.get("estimated_net_return_pct") is not None:
            prediction_eval.append({
                "candidate_time": row.get("earliest_entry_time"),
                "predicted_net_return_pct": row.get("estimated_net_return_pct"),
                "actual_net_return_pct": 100.0 * float(actual.get("return_pct", 0.0)),
                "prediction_error_percentage_points": 100.0 * float(actual.get("return_pct", 0.0)) - float(row["estimated_net_return_pct"]),
                "counterfactual_source": "A_FROZEN_V2B" if is_blocked else "B_EXPECTED_EDGE_FILTER",
            })
    return blocked, prediction_eval


def _time_segment_rows(results: list[dict[str, Any]], sensitivity_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for slip, pair in sensitivity_results:
        for arm, result in ((ARM_A, pair[0]), (ARM_B, pair[1])):
            for start, end, label in (("2023-01-01", "2023-06-30", "2023_H1"), ("2023-07-01", "2023-12-31", "2023_H2"),
                                      ("2024-01-01", "2024-06-30", "2024_H1"), ("2024-07-01", "2024-12-31", "2024_H2")):
                trades = [t for t in result["trades"] if start <= str(t.get("sell_datetime", ""))[:10] <= end]
                candidates = [c for c in result["candidates"] if start <= str(c.get("signal_bar_end_time", ""))[:10] <= end and c.get("candidate_after_regime")]
                rows.append({
                    "arm": arm, "simulated_slippage_bps_per_side": slip, "period_segment": label,
                    "candidate_count": len(candidates), "trade_count": len(trades),
                    "gross_pnl": sum(float(t.get("gross_pnl", 0.0)) for t in trades),
                    "net_pnl": sum(float(t.get("net_pnl", 0.0)) for t in trades),
                    "fees": sum(float(t.get("fee", 0.0)) for t in trades),
                    "tax": sum(float(t.get("tax", 0.0)) for t in trades),
                    "slippage_cost": sum(float(t.get("slippage", 0.0)) for t in trades),
                    "mean_matured_training_trades": (float(np.mean([float(c.get("matured_baseline_trade_count", 0)) for c in candidates if c.get("matured_baseline_trade_count") is not None])) if any(c.get("matured_baseline_trade_count") is not None for c in candidates) else None),
                    "insufficient_history_count": sum(c.get("expected_edge_status") == "INSUFFICIENT_MATURED_BASELINE_TRADES" for c in candidates),
                })
    return rows


def _write_chart(output: Path, comparison: pd.DataFrame, curves: list[dict[str, Any]], blocked: list[dict[str, Any]]):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    charts = output / "charts"
    charts.mkdir(parents=True, exist_ok=False)
    main = [r for r in comparison if r["simulated_slippage_bps_per_side"] == 0]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar([r["arm"] for r in main], [r.get("total_return_pct") or 0.0 for r in main], color=["#587b9d", "#28a681"])
    ax.set_ylabel("Net return (%)"); ax.set_title("Expected Edge Filter Exp001 — 0 bps execution slippage")
    ax.grid(axis="y", alpha=.25); fig.tight_layout(); fig.savefig(charts / "net_return_comparison.png", dpi=150); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    for arm in (ARM_A, ARM_B):
        selected = [r for r in curves if r["arm"] == arm and r["simulated_slippage_bps_per_side"] == 0]
        ax.plot(pd.to_datetime([r["timestamp"] for r in selected]), [r["equity"] for r in selected], label=arm, linewidth=1)
    ax.set_title("Equity Curves — Primary Scenario"); ax.set_ylabel("NTD"); ax.grid(alpha=.25); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(charts / "equity_curves.png", dpi=150); plt.close(fig)

    matched = [r for r in blocked if r.get("counterfactual_match_status") == "MATCHED_BASELINE_TRADE"]
    labels = ["Allowed B trades", "Blocked but A traded"]
    allowed = [r for r in blocked if r.get("expected_edge_status") == "ALLOW_EXPECTED_NET_POSITIVE" and r.get("counterfactual_match_status") == "MATCHED_B_TRADE"]
    means = [float(np.mean([float(r["actual_net_return_pct"]) for r in allowed])) if allowed else 0.0,
             float(np.mean([float(r["actual_net_return_pct"]) for r in matched])) if matched else 0.0]
    fig, ax = plt.subplots(figsize=(8, 5)); ax.bar(labels, means, color=["#28a681", "#d88947"])
    ax.set_ylabel("Mean net return (%)"); ax.set_title("Matched candidates only; descriptive, not independent test")
    ax.grid(axis="y", alpha=.25); fig.tight_layout(); fig.savefig(charts / "matched_candidate_outcomes.png", dpi=150); plt.close(fig)


def _report(config, audit, comparison, blocked, prediction_eval, time_segments, out_path):
    primary = [row for row in comparison if row["simulated_slippage_bps_per_side"] == 0]
    a = next(row for row in primary if row["arm"] == ARM_A)
    b = next(row for row in primary if row["arm"] == ARM_B)
    primary_blocked = [r for r in blocked if float(r.get("simulated_slippage_bps_per_side", 0.0)) == 0.0]
    primary_errors = [r for r in prediction_eval if float(r.get("simulated_slippage_bps_per_side", 0.0)) == 0.0]
    matched_a = [r for r in primary_blocked if r.get("expected_edge_status") == "BLOCK_EXPECTED_NET_NOT_POSITIVE" and r.get("counterfactual_match_status") == "MATCHED_BASELINE_TRADE"]
    matched_insufficient = [r for r in primary_blocked if r.get("expected_edge_status") == "INSUFFICIENT_MATURED_BASELINE_TRADES" and r.get("counterfactual_match_status") == "MATCHED_BASELINE_TRADE"]
    matched_b = [r for r in primary_blocked if r.get("expected_edge_status") == "ALLOW_EXPECTED_NET_POSITIVE" and r.get("counterfactual_match_status") == "MATCHED_B_TRADE"]
    blocked_mean = float(np.mean([float(r["actual_net_return_pct"]) for r in matched_a])) if matched_a else None
    allowed_mean = float(np.mean([float(r["actual_net_return_pct"]) for r in matched_b])) if matched_b else None
    insufficient_mean = float(np.mean([float(r["actual_net_return_pct"]) for r in matched_insufficient])) if matched_insufficient else None
    primary_error_values = [float(r["prediction_error_percentage_points"]) for r in primary_errors]
    error_mae = float(np.mean(np.abs(primary_error_values))) if primary_error_values else None
    error_bias = float(np.mean(primary_error_values)) if primary_error_values else None
    enough = len(matched_a) >= 5 and len(matched_b) >= 5 and len(primary_errors) >= 5
    if not enough:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif allowed_mean is not None and blocked_mean is not None and allowed_mean > blocked_mean:
        verdict = "DIRECTIONALLY_SELECTIVE_BUT_DEVELOPMENT_ONLY"
    else:
        verdict = "NO_CLEAR_SELECTION_EVIDENCE"
    pct = lambda value: "N/A" if value is None else f"{float(value):.3f}%"
    num = lambda value: "N/A" if value is None else f"{float(value):,.2f}"
    lines = [
        "# Expected Edge Filter A/B 實驗報告", "",
        f"- Experiment ID：`{config['experiment_id']}`；狀態：`COMPLETED`；研究判讀：`{verdict}`。",
        "- 標的：2303 聯電；期間：2023-01-01～2024-12-31；資料角色：已曝光 `historical_development`。非 Final OOS、非即時訊號。",
        "- A 是凍結 Technical V2-B（BR_SENS_RANGE55_BB20_2）+ Risk V1；B 唯一增加 Expected Edge Filter；兩組都沒有 ML Gate。",
        "- 正式資料資格：Technical V2-B 全域共同完整交易日；五檔 09:00–13:25 各 54 根真實 K 棒，任何缺日依 EXCLUDE_NO_FILL。行情 SQLite 以唯讀模式開啟。",
        "- 主結果沿用凍結滑價 0 bps；Expected Edge 成本估計另預留買賣各 5 bps。另以 A/B 配對方式重跑實際成交滑價 0/5/10 bps，Filter 的估計成本假設仍固定 5 bps/邊。",
        "- 訊號使用完成 K 棒，下一根可交易 K 棒開盤成交。Risk V1：風險預算 1%、曝險上限 40%、固定停損 2.5%；其他停損、30 根低點出場、持有上限、冷卻和每日進場限制沿用凍結設定。",
        "- 估計方式是累積平均「先前 A 組已完成交易」的毛報酬；最少 5 筆成熟交易。成熟條件為該筆 A 交易的出場時間嚴格早於訊號 K 棒完成時間。樣本不足時標成無法估計並阻擋，不等同預期淨報酬為負。",
        "- 預估成本包含雙邊手續費、一般賣出稅 0.3% 及雙邊 5 bps 滑價預留。未來實際成交可能使用當沖稅折減；預測不假定能享有折減，因此偏保守。",
        "", "## 主結果（0 bps 實際滑價）", "",
        "| 組別 | 候選數 | 成交數 | 毛損益 | 手續費 | 證交稅 | 滑價 | 淨損益 | 淨報酬 | 勝率 | PF | 期望值/筆 | 最大回撤 | Sharpe | 平均曝險 | 曝險正規化報酬* |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in primary:
        lines.append(f"| {row['arm']} | {row['candidate_count']} | {row['trade_count']} | {num(row['gross_pnl'])} | {num(row['commission_total'])} | {num(row['transaction_tax_total'])} | {num(row['slippage_total'])} | {num(row['net_pnl'])} | {pct(row.get('total_return_pct'))} | {pct(row.get('win_rate_pct'))} | {num(row.get('profit_factor'))} | {num(row.get('average_trade_net_return_pct'))} | {pct(row.get('max_drawdown_pct'))} | {num(row.get('sharpe_ratio'))} | {pct((row.get('average_exposure_fraction') or 0)*100)} | {pct(row.get('return_per_average_exposure_pct'))} |")
    blocked_count = sum(r.get("expected_edge_status") == "BLOCK_EXPECTED_NET_NOT_POSITIVE" for r in primary_blocked)
    insufficient_count = sum(r.get("expected_edge_status") == "INSUFFICIENT_MATURED_BASELINE_TRADES" for r in primary_blocked)
    allow_count = sum(r.get("expected_edge_status") == "ALLOW_EXPECTED_NET_POSITIVE" for r in primary_blocked)
    candidate_count = sum(bool(r.get("candidate_after_regime")) for r in primary_blocked)
    lines += [
        "", "*曝險正規化報酬 = 淨報酬率 ÷ 平均資產曝險比例，只是曝險差異診斷，不是以相同部位重新模擬的精確等曝險回測。Sharpe 與最大回撤仍提供資金曲線的風險調整比較。",
        "", "## Filter 判斷與候選事後表現", "",
        f"- B 的市場濾網後候選：{candidate_count}；估計為正並允許：{allow_count}；預估淨報酬不為正而阻擋：{blocked_count}；成熟歷史交易少於 5 筆、無法估計而阻擋：{insufficient_count}。含樣本不足在內的預期邊際阻擋率為 {pct(100.0 * (candidate_count - allow_count) / candidate_count) if candidate_count else 'N/A'}。",
        f"- 因估計淨報酬不為正而阻擋：{blocked_count} 個候選，其中 {len(matched_a)} 個能與 A 的實際交易一對一對照；這些對照交易平均事後淨報酬 {pct(blocked_mean)}。",
        f"- 因成熟歷史樣本不足而阻擋：{insufficient_count} 個候選，其中 {len(matched_insufficient)} 個能與 A 的實際交易對照；該組事後平均淨報酬 {pct(insufficient_mean)}。樣本不足不能解讀為模型預測它們會虧損。",
        f"- B 放行且實際成交、可計算結果者：{len(matched_b)}；平均事後成本後報酬：{pct(allowed_mean)}。候選未能對應已成交交易者不冒充交易績效。",
        f"- 主情境預測誤差樣本 {len(primary_errors)} 筆；MAE {pct(error_mae)}、平均誤差（實際減預測）{pct(error_bias)}；見 `prediction_error.csv`。樣本與交易均非獨立樣本外資料，僅作開發診斷。",
        "- 被阻擋交易的事後報酬取自 A 的同一候選日期且 A 實際成交的交易，僅作反事實診斷；沒有 A 對應成交者列為無匹配，不推算未發生交易的收益。",
        "", "## 滑價敏感度", "",
        "| 實際滑價（每邊 bps） | 組別 | 成交 | 淨損益 | 淨報酬 | PF | 最大回撤 | Sharpe |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison:
        lines.append(f"| {row['simulated_slippage_bps_per_side']:.0f} | {row['arm']} | {row['trade_count']} | {num(row['net_pnl'])} | {pct(row.get('total_return_pct'))} | {num(row.get('profit_factor'))} | {pct(row.get('max_drawdown_pct'))} | {num(row.get('sharpe_ratio'))} |")
    lines += ["", "## 各時間區段（主情境 0 bps）", "", "| 時段 | 組別 | 候選 | 成交 | 淨損益 | 成熟歷史樣本平均 | 樣本不足候選 |", "|---|---|---:|---:|---:|---:|---:|"]
    for row in time_segments:
        if float(row["simulated_slippage_bps_per_side"]) == 0.0:
            lines.append(f"| {row['period_segment']} | {row['arm']} | {row['candidate_count']} | {row['trade_count']} | {num(row['net_pnl'])} | {num(row.get('mean_matured_training_trades'))} | {row['insufficient_history_count']} |")
    lines += ["", "分段交易與淨損益來自同一條連續資金路徑；分段資料用於觀察時間差異，不將各段視為獨立重置帳戶報酬。", "", "## 公平性、洩漏與風險檢查", "",
              f"- A/B 使用相同 {audit['target_bar_count']:,} 根 2303 K 棒、相同日期、相同凍結指標／市場代理、Risk V1、成本與交易執行。三種滑價情境中，每個情境內 A/B 使用同一滑價。",
              f"- 目標交易日數：{audit['target_session_count']}；全域共同完整日：{audit['global_complete_session_count']}；市場代理只取目標以外四檔的前 20 個共同完整日收盤，嚴格早於候選日期。",
              f"- 洩漏檢查：{audit['lookahead_check']}；每筆歷史估計最新訓練出場時間均早於當前完成訊號時間。估計沒有使用自身候選的未來結果。",
              "- 交易成本與成交價格均由原引擎 `_create_local_order`、`_build_trade_rows`、`_build_pnl_rows` 產生；主結果滑價 0，滑價敏感度改由同一引擎重播。",
              "- 正式策略設定、Risk V1、資料庫、ML Gate 均未修改；沒有參數搜尋，也沒有使用 2025/2026、Additional Holdout 或 Final OOS。",
              "", "## 結論", "",
              f"本次事前固定的簡單統計 Filter 判讀為 **{verdict}**。A 淨報酬 {pct(a.get('total_return_pct'))}，B 淨報酬 {pct(b.get('total_return_pct'))}；但不能只由虧損變小判定訊號品質改善。必須同時看放行與被阻擋候選的可匹配事後結果、預測誤差、交易數、平均曝險、PF、回撤與 Sharpe。若可匹配樣本不足，答案就是目前證據不足，不能宣稱它辨識出成本後優勢。",
              "這是已看過的 2023–2024 開發資料上的研究實驗；不取代凍結 V2-B，也不代表跨股票、未來期間或 Final OOS 有效。", "", "## 檔案", "",
              "- `performance_comparison.csv`：主情境與滑價敏感度績效。", "- `candidate_audit.csv`：候選資格、Filter 狀態與風控／成交結果。", "- `edge_predictions.csv`：歷史估計與每筆所用成熟樣本證據。", "- `trades.csv`、`equity_curves.csv`、`risk_decisions.csv`：原引擎回測明細。", "- `blocked_candidate_outcomes.csv`、`prediction_error.csv`：候選反事實診斷與預測誤差。", "- `time_segments.csv`：半年區段結果。", "- `config_snapshot.json`、`audit.json`：事前設定及資料／程式雜湊。", "- `charts/`：績效曲線與配對候選比較圖。", ""]
    return "\n".join(lines)


def run() -> Path:
    config = _load_config()
    db_path = ROOT / config["data_source"]
    if not db_path.is_file():
        raise RuntimeError("BLOCKED_MISSING_FROZEN_CLEAN_DATABASE")
    tech = tv1.read_config(TECH_CONFIG_PATH)
    tv1.assert_execution_ready(tech, "training")
    family_name, family, candidate = tv1.candidate_by_id(tech, config["candidate_id"])
    frozen = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    if frozen.get("base_candidate") != candidate["id"] or frozen.get("market_regime_filter", {}).get("enabled") is not True:
        raise RuntimeError("BLOCKED_FROZEN_CANDIDATE_IDENTITY_MISMATCH")
    if candidate["id"] != "BR_SENS_RANGE55_BB20_2":
        raise RuntimeError("BLOCKED_UNEXPECTED_CANDIDATE")
    source_hash_before = sha256(db_path)
    config_hash = sha256(TECH_CONFIG_PATH)
    frozen_hash = sha256(FROZEN_CONFIG_PATH)

    with contextlib.closing(_readonly_connection(db_path)) as connection:
        fold = {"id": "EXPECTED_EDGE_EXP001", "train_start": "2023-01-01", "train_end": "2024-12-31",
                "evaluate_start": "2023-01-01", "evaluate_end": "2024-12-31"}
        full_frame, _, base_app, warmup_count, eval_lower = tv1._load_fold_frame(
            connection, tech, candidate, config["stock_code"], fold,
        )
        frame_times = [int(value) for value in full_frame["kbar_timestamp"].to_list()]
        schedule = {iso_taipei(datetime.fromtimestamp(value, TAIPEI)) for value in frame_times[1:]}
        candidate_dates = {value[:10] for value in schedule}
        regime_context, regime_audit = build_regime_context(
            connection, tuple(frozen["market_regime_filter"]["peer_stock_codes"]), candidate_dates,
        )
        placeholders = ",".join("?" for _ in frozen["market_regime_filter"]["peer_stock_codes"])
        lower, upper = tv1._timestamp_bounds("2023-01-01", "2024-12-31")
        session_rows = connection.execute(
            f"SELECT stock_code,kbar_timestamp FROM market_kbars WHERE stock_code IN ({placeholders}) "
            "AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY stock_code,kbar_timestamp",
            (*frozen["market_regime_filter"]["peer_stock_codes"], lower, upper),
        ).fetchall()
        sessions: dict[tuple[str, str], int] = {}
        for row in session_rows:
            dt = datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI)
            if "09:00" <= dt.strftime("%H:%M") <= "13:25":
                key = (str(row["stock_code"]), dt.date().isoformat())
                sessions[key] = sessions.get(key, 0) + 1
        all_dates = sorted({day for _, day in sessions})
        target_days = sorted(day for stock, day in sessions if stock == config["stock_code"])
        stocks = tuple(frozen["market_regime_filter"]["peer_stock_codes"])
        incomplete = [(stock, day, count) for (stock, day), count in sessions.items() if count != 54]
        globally_complete = [day for day in all_dates if all(sessions.get((stock, day)) == 54 for stock in stocks)]
        if incomplete or set(target_days) != set(globally_complete):
            raise RuntimeError(f"BLOCKED_DATASET_NOT_FROZEN_GLOBAL_COMPLETE:{len(incomplete)}")
        for stock in stocks:
            days = {day for s, day in sessions if s == stock}
            if days != set(target_days):
                raise RuntimeError(f"BLOCKED_GLOBAL_DATE_PARITY:{stock}")

        sensitivity_results = []
        comparison_rows: list[dict[str, Any]] = []
        all_candidate_rows, all_trade_rows, all_curve_rows, all_risk_rows = [], [], [], []
        all_edge_rows, all_blocked_rows, all_prediction_error_rows = [], [], []
        for slip in SENSITIVITY_SLIPPAGE_BPS:
            a_result = _run_one(
                arm=ARM_A, slippage_bps=slip, frame=full_frame, schedule=schedule,
                regime_context=regime_context, tech=tech, family_name=family_name,
                family=family, candidate=candidate, config=config, eval_lower=eval_lower,
            )
            baseline_outcomes = _baseline_outcomes(a_result["pnl_rows"])
            b_result = _run_one(
                arm=ARM_B, slippage_bps=slip, frame=full_frame, schedule=schedule,
                regime_context=regime_context, tech=tech, family_name=family_name,
                family=family, candidate=candidate, config=config, eval_lower=eval_lower,
                baseline_outcomes=baseline_outcomes,
            )
            sensitivity_results.append((slip, (a_result, b_result)))
            for result in (a_result, b_result):
                summary = dict(result["summary"])
                summary["total_return_pct"] = float(summary.get("total_return_pct", 0.0))
                comparison_rows.append(summary)
                all_candidate_rows.extend(result["candidates"])
                all_trade_rows.extend(result["trades"])
                all_curve_rows.extend(result["curves"])
                all_risk_rows.extend(result["risk"])
            b_result["edge_decisions"] = [dict(row, simulated_slippage_bps_per_side=slip) for row in b_result["edge_decisions"]]
            all_edge_rows.extend(b_result["edge_decisions"])
            blocked, error_rows = _augment_counterfactuals(a_result, b_result)
            all_blocked_rows.extend([dict(row, simulated_slippage_bps_per_side=slip) for row in blocked])
            all_prediction_error_rows.extend([dict(row, simulated_slippage_bps_per_side=slip) for row in error_rows])

        if sha256(db_path) != source_hash_before:
            raise RuntimeError("BLOCKED_SOURCE_DATABASE_CHANGED_DURING_READ_ONLY_RUN")

    validate_no_lookahead(all_edge_rows)
    output = ROOT / "exports" / f"expected_edge_filter_exp001_{datetime.now().astimezone(TAIPEI).strftime('%Y%m%dT%H%M%S%z')}"
    output.mkdir(parents=True, exist_ok=False)
    comparison_df = pd.DataFrame(comparison_rows)
    candidates_df = pd.DataFrame(all_candidate_rows)
    trades_df = pd.DataFrame(all_trade_rows)
    curves_df = pd.DataFrame(all_curve_rows)
    risks_df = pd.DataFrame(all_risk_rows)
    edge_df = pd.DataFrame(all_edge_rows)
    blocked_df = pd.DataFrame(all_blocked_rows)
    errors_df = pd.DataFrame(all_prediction_error_rows)
    segments = _time_segment_rows(comparison_rows, sensitivity_results)
    comparison_df.to_csv(output / "performance_comparison.csv", index=False, encoding="utf-8-sig")
    candidates_df.to_csv(output / "candidate_audit.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(output / "trades.csv", index=False, encoding="utf-8-sig")
    curves_df.to_csv(output / "equity_curves.csv", index=False, encoding="utf-8-sig")
    risks_df.to_csv(output / "risk_decisions.csv", index=False, encoding="utf-8-sig")
    edge_df.to_csv(output / "edge_predictions.csv", index=False, encoding="utf-8-sig")
    blocked_df.to_csv(output / "blocked_candidate_outcomes.csv", index=False, encoding="utf-8-sig")
    errors_df.to_csv(output / "prediction_error.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(segments).to_csv(output / "time_segments.csv", index=False, encoding="utf-8-sig")
    write_json(output / "config_snapshot.json", config)
    audit = {
        "status": "COMPLETED", "experiment_id": config["experiment_id"],
        "data_role": config["data_role"], "stock_code": config["stock_code"],
        "period": config["period"], "database_path": config["data_source"],
        "database_sha256_before": source_hash_before, "database_sha256_after": sha256(db_path),
        "technical_config_sha256": config_hash, "frozen_candidate_config_sha256": frozen_hash,
        "technical_source_sha256": sha256(ROOT / "trading_system/technical_v1.py"),
        "backtest_source_sha256": sha256(ROOT / "trading_system/backtest.py"),
        "candidate_id": candidate["id"], "technical_params": candidate["params"],
        "target_bar_count": full_frame.height, "target_session_count": len(target_days),
        "global_complete_session_count": len(globally_complete), "warmup_count": warmup_count,
        "market_regime_audit": regime_audit,
        "all_source_sessions_complete": True,
        "lookahead_check": "PASS_STRICT_PRIOR_COMPLETED_BASELINE_TRADE_EXIT",
        "lookahead_assertion_count": len(all_edge_rows),
        "baseline_history_method": "all baseline arm results are filtered per candidate; only exits strictly earlier than completed signal timestamp contribute",
        "expected_cost_assumptions": {"commission_rate": config["execution"]["commission_rate"], "ordinary_sell_tax_rate": config["execution"]["stock_tax_rate"], "slippage_bps_per_side": ESTIMATION_SLIPPAGE_BPS},
        "simulated_slippage_sensitivity_bps_per_side": list(SENSITIVITY_SLIPPAGE_BPS),
        "official_strategy_modified": False, "risk_v1_modified": False,
        "ml_gate_used": False, "parameter_search_performed": False,
        "market_database_modified": False, "additional_holdout_used": False, "final_oos_used": False,
        "forbidden_roles_used": False,
    }
    write_json(output / "audit.json", audit)
    report = _report(config, audit, comparison_rows, all_blocked_rows, all_prediction_error_rows, segments, output)
    (output / "report.md").write_text(report + "\n", encoding="utf-8")
    _write_chart(output, comparison_rows, all_curve_rows, all_blocked_rows)
    # A compact user-facing HTML is derived only from the report and local CSVs.
    escaped = report.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = "<!doctype html><meta charset='utf-8'><title>Expected Edge Filter Exp001</title><style>body{font:16px/1.65 system-ui;max-width:1100px;margin:32px auto;padding:0 18px;color:#172033}pre{white-space:pre-wrap;background:#f5f7fa;padding:22px;border-radius:12px}img{max-width:100%}</style><h1>Expected Edge Filter A/B 實驗</h1><p>2303 聯電｜2023–2024 Historical Development；非 Final OOS。</p><img src='charts/equity_curves.png'><img src='charts/net_return_comparison.png'><pre>" + escaped + "</pre>"
    (output / "user_analysis.html").write_text(html, encoding="utf-8")
    return output


if __name__ == "__main__":
    print(run())

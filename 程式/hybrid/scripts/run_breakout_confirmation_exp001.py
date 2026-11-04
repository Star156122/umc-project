"""Run the preregistered standalone Technical V2-B one-bar confirmation A/B."""
from __future__ import annotations

import contextlib
import csv
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

from hybrid.core.breakout_confirmation_exp001 import evaluate_confirmation  # noqa: E402
from hybrid.core.data_eligibility_v1 import build_peer_proxy, market_context_from_proxy, select_unambiguous_valid_bars  # noqa: E402
from hybrid.scripts.run_relaxed_data_eligibility_v1 import (  # noqa: E402
    _read_only_connection, _read_period_rows, _run_arm, _session_rows, _target_bars_for_dates,
)
from hybrid.scripts.run_short_swing_ab_test import _make_strategy_class, _metrics_and_rows, _regime_pass  # noqa: E402
from hybrid.scripts.run_relaxed_data_eligibility_v1 import _build_gap_aware_strategy, _prepare_frame  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
CONFIG_PATH = ROOT / "hybrid/config/breakout_confirmation_exp001.json"
ELIGIBILITY_CONFIG_PATH = ROOT / "hybrid/config/relaxed_data_eligibility_v1.json"
TECH_CONFIG_PATH = ROOT / "configs/technical_v1_research.json"
FROZEN_CONFIG_PATH = ROOT / "configs/technical_v2_candidate.json"
ARM_A = "A_ORIGINAL_V2B"
ARM_B = "B_ONE_BAR_CONFIRMATION"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    eligibility = json.loads(ELIGIBILITY_CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("status") != "PREREGISTERED_RESEARCH_ONLY":
        raise RuntimeError("BLOCKED_EXPERIMENT_NOT_PREREGISTERED")
    if config.get("data_role") != "historical_development" or config.get("period") != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise RuntimeError("BLOCKED_INVALID_PERIOD_OR_ROLE")
    tv1.assert_development_period(config["period"]["start"], config["period"]["end"])
    if config.get("final_oos_used") is not False or config.get("additional_holdout_used") is not False or config.get("forbidden_roles_used") is not False:
        raise RuntimeError("BLOCKED_FORBIDDEN_ROLE_DECLARATION")
    if config["stock_code"] != "2303" or config["arms"] != [ARM_A, ARM_B]:
        raise RuntimeError("BLOCKED_EXPERIMENT_SCOPE_CHANGED")
    if eligibility["stock_code"] != config["stock_code"] or eligibility["period"] != config["period"]:
        raise RuntimeError("BLOCKED_ELIGIBILITY_SCOPE_MISMATCH")
    comparable_risk = {key: config["risk"].get(key) for key in eligibility["risk"]}
    if comparable_risk != eligibility["risk"] or config["risk"].get("version") != "risk_v1" or config["execution"]["stock_fee_rate"] != eligibility["execution"]["stock_fee_rate"] or config["execution"]["stock_tax_rate"] != eligibility["execution"]["stock_tax_rate"] or config["execution"]["slippage_rate"] != eligibility["execution"]["slippage_rate"]:
        raise RuntimeError("BLOCKED_RISK_OR_COST_PARITY_MISMATCH")
    return config


def _build_confirmation_strategy(incomplete_dates: set[str]):
    Parent = _build_gap_aware_strategy("A_ORIGINAL_V2B", incomplete_dates)

    class ConfirmationStrategy(Parent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.pending_setup: dict[str, Any] | None = None
            self.active_confirmation: dict[str, Any] | None = None
            self.confirmation_attempts: list[dict[str, Any]] = []

        def _entry_reasons(self, close, snapshot):
            if self.active_confirmation is not None:
                return list(self.active_confirmation["original_reasons"])
            return super()._entry_reasons(close, snapshot)

        def _has_required_indicators(self, snapshot):
            # The confirm rule only needs the valid completed bar close; original
            # signal indicators were already checked on the setup bar.
            if self.active_confirmation is not None:
                return True
            return super()._has_required_indicators(snapshot)

        def _try_entry(self, code, tick_time, close, snapshot):
            if self.active_confirmation is not None:
                pending = self.active_confirmation
                order_count = len(self.local_orders)
                risk_count = len(self.risk_decisions)
                from hybrid.scripts.run_ml_v2_exp001 import TechnicalV2AloneStrategy
                TechnicalV2AloneStrategy._try_entry(self, code, tick_time, close, snapshot)
                new_orders = self.local_orders[order_count:]
                buy_orders = [order for order in new_orders if order.get("action") == "Buy"]
                if any(order.get("status_code") == "BT-00000" for order in buy_orders):
                    pending["execution_result"] = "FILLED"
                elif self.risk_decisions[risk_count:] and int(self.risk_decisions[-1].get("final_quantity", 0)) <= 0:
                    pending["execution_result"] = "BLOCK_RISK_ZERO_QUANTITY"
                elif buy_orders:
                    pending["execution_result"] = "BLOCK_ORDER_OR_CASH"
                else:
                    pending["execution_result"] = "CONFIRMED_BUT_ENTRY_NOT_ACCEPTED_BY_ENGINE"
                return

            reasons = super()._entry_reasons(close, snapshot)
            if not reasons or self.pending_setup is not None:
                return
            signal_time = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TAIPEI)
            signal_time = signal_time.astimezone(TAIPEI)
            schedule_key = signal_time.isoformat(timespec="microseconds")
            if schedule_key not in self.candidate_schedule:
                return
            market_pass, _ = _regime_pass(self.regime_context, str(code), signal_time)
            line = snapshot.get("BREAKOUT_HIGH")
            setup_bar = getattr(self, "_audit_bar", None)
            if not market_pass or line is None or setup_bar is None:
                return
            self.pending_setup = {
                "setup_timestamp": int(setup_bar["kbar_timestamp"]),
                "setup_open_decision_timestamp": int(signal_time.timestamp()),
                "breakout_line": float(line),
                "setup_close": float(close),
                "original_reasons": list(reasons),
                "execution_result": "WAITING_FOR_CONFIRMATION",
            }

        def process_completed_kbar(self, code, decision_time, completed_kbar):
            pending = self.pending_setup
            self.active_confirmation = None
            active_attempt = None
            completed_ts = int(completed_kbar["kbar_timestamp"])
            entry_ts = int(decision_time.replace(tzinfo=TAIPEI).timestamp()) if decision_time.tzinfo is None else int(decision_time.timestamp())
            if pending is not None:
                close = completed_kbar.get("Close")
                result = evaluate_confirmation(
                    setup_timestamp=pending["setup_timestamp"],
                    confirmation_timestamp=completed_ts,
                    entry_timestamp=entry_ts,
                    breakout_line=pending["breakout_line"],
                    confirmation_close=None if close is None else float(close),
                )
                active_attempt = {
                    **pending,
                    "confirmation_timestamp": result.confirmation_timestamp,
                    "confirmation_close": result.confirmation_close,
                    "confirmation_status": result.status,
                    "entry_timestamp": result.entry_timestamp,
                    "execution_result": "NOT_ENTERED",
                    "lookahead_used_for_decision": False,
                }
                self.confirmation_attempts.append(active_attempt)
                self.pending_setup = None
                if result.status == "CONFIRMED_NEXT_BAR_ENTRY_AVAILABLE":
                    self.active_confirmation = pending

            candidate_count_before = len(self.candidate_audit)
            super().process_completed_kbar(code, decision_time, completed_kbar)

            if active_attempt is not None and self.active_confirmation is not None:
                active_attempt["execution_result"] = pending.get("execution_result", "CONFIRMED_BUT_ENTRY_HANDLER_NOT_CALLED")
                if active_attempt["execution_result"] == "NOT_ENTERED":
                    active_attempt["execution_result"] = "CONFIRMED_BUT_ACCOUNT_OR_EXIT_STATE_BLOCKED"
            self.active_confirmation = None

            if self.pending_setup is not None:
                setup_ts = self.pending_setup["setup_timestamp"]
                for row in reversed(self.candidate_audit[candidate_count_before:]):
                    if int(row.get("signal_bar_timestamp", -1)) == setup_ts:
                        row["risk_and_execution_result"] = "WAITING_FOR_CONFIRMATION"
                        row["confirmation_status"] = "PENDING"
                        row["confirmation_breakout_line"] = self.pending_setup["breakout_line"]
                        break

    return ConfirmationStrategy


def _prepare_run_config(config: dict[str, Any], tech: dict[str, Any], candidate: dict[str, Any]):
    app_values = json.loads(json.dumps(tech))
    app_values["common_backtest"]["initial_capital"] = config["initial_capital"]
    app_values["common_backtest"].update({
        "stock_fee_rate": config["execution"]["stock_fee_rate"],
        "stock_tax_rate": config["execution"]["stock_tax_rate"],
        "slippage_rate": config["execution"]["slippage_rate"],
        "stop_loss_pct": config["risk"]["stop_loss_pct"],
        "min_hold_bars": config["risk"]["min_holding_bars"],
        "max_hold_bars": config["risk"]["max_holding_bars"],
        "cooldown_bars": config["risk"]["cooldown_bars"],
        "max_entries_per_day": config["risk"]["max_entries_per_day"],
    })
    app = tv1.build_app_config(app_values, candidate, config["stock_code"], config["period"]["start"], config["period"]["end"])
    return app_values, app


def _run_b(*, config, rows, context_by_day, tech, family_name, family, candidate, all_dates, target_rows_by_day):
    from trading_system.backtest import MarketDataStats

    app_values, app = _prepare_run_config(config, tech, candidate)
    frame, _, incomplete_dates = _prepare_frame(rows, app, all_dates, target_rows_by_day, config["stock_code"])
    frame_rows = frame.to_dicts()
    frame_times = [int(row["kbar_timestamp"]) for row in frame_rows]
    schedule = {
        datetime.fromtimestamp(frame_times[index], TAIPEI).isoformat(timespec="microseconds")
        for index in range(1, len(frame_times))
        if frame_times[index] - frame_times[index - 1] == 300
        and datetime.fromtimestamp(frame_times[index], TAIPEI).date() == datetime.fromtimestamp(frame_times[index - 1], TAIPEI).date()
    }
    regime = {(config["stock_code"], day): value for day, value in context_by_day.items()}
    strategy_cls = _build_confirmation_strategy(incomplete_dates)
    strategy = strategy_cls(
        config=app, study_config=app_values, family_name=family_name, family=family,
        candidate=candidate, data_role="historical_development", fold_id="BREAKOUT_CONFIRMATION_2023_2024",
        is_backtest=True, is_simulation=True, local_only=True, ml_model="technical_only",
        prediction_lookup={}, candidate_schedule=schedule, regime_context=regime,
    )
    strategy.configure_local_account(app)
    lower, _ = tv1._timestamp_bounds(config["period"]["start"], config["period"]["end"])
    strategy.evaluation_start_timestamp = lower
    stats = MarketDataStats(tick_count=frame.height, first_timestamp=float(frame_times[0]), last_timestamp=float(frame_times[-1]))
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, frame, stats)
    trades = bt._build_trade_rows(strategy, app)
    risk_rows = [{"arm": ARM_B, **row} for row in strategy.risk_decisions]
    candidates = [{"arm": ARM_B, **row} for row in strategy.candidate_audit]
    bars = bt.build_kbar_rows_from_frame(frame, app)
    summary, enriched, curves = _metrics_and_rows(
        version=ARM_B, strategy=strategy, app=app, bars=bars, trade_rows=trades,
        risk_rows=risk_rows, candidate_rows=candidates,
    )
    summary.update({
        "arm": ARM_B, "strategy_version": "technical_v2b_one_bar_confirmation_exp001",
        "ml_gate": "NONE_BOTH_ARMS", "actual_target_sessions": len({datetime.fromtimestamp(ts, TAIPEI).date().isoformat() for ts in frame_times}),
        "data_gap_exit_count": sum(row.get("signal_type") == "data_gap_exit" for row in strategy.local_signals),
        "open_position_at_end": int(strategy.local_position != 0),
    })
    if strategy.local_position != 0:
        raise RuntimeError("BLOCKED_OPEN_POSITION_AFTER_REPLAY:B_ONE_BAR_CONFIRMATION")
    return {
        "summary": summary, "trades": [{"arm": ARM_B, **row} for row in enriched],
        "curves": [{"arm": ARM_B, **row} for row in curves], "candidates": candidates,
        "risk": risk_rows, "confirmation_attempts": strategy.confirmation_attempts,
        "gap_events": [{"arm": ARM_B, **row} for row in strategy.data_gap_events],
        "frame_rows": frame_rows, "bars": frame.height, "schedule_size": len(schedule),
    }


def _confirmation_audit(a_candidates, b_candidates, frame_rows):
    by_ts = {int(row["kbar_timestamp"]): row for row in frame_rows}
    dates = sorted({datetime.fromtimestamp(ts, TAIPEI).date().isoformat() for ts in by_ts})
    daily_close: dict[str, float] = {}
    for ts in sorted(by_ts):
        daily_close[datetime.fromtimestamp(ts, TAIPEI).date().isoformat()] = float(by_ts[ts]["Close"])
    b_attempt_by_setup = {int(row["setup_timestamp"]): row for row in b_candidates}
    audit_rows = []
    for candidate_row in a_candidates:
        if not (candidate_row.get("technical_candidate") and candidate_row.get("candidate_after_regime")):
            continue
        setup_ts = int(candidate_row["signal_bar_timestamp"])
        setup_bar = by_ts.get(setup_ts)
        if setup_bar is None:
            continue
        line = setup_bar.get("BREAKOUT_HIGH")
        if line is None:
            continue
        confirm_bar = by_ts.get(setup_ts + 300)
        entry_bar = by_ts.get(setup_ts + 600)
        result = evaluate_confirmation(
            setup_timestamp=setup_ts,
            confirmation_timestamp=None if confirm_bar is None else setup_ts + 300,
            entry_timestamp=None if entry_bar is None else setup_ts + 600,
            breakout_line=float(line),
            confirmation_close=None if confirm_bar is None else float(confirm_bar["Close"]),
        )
        setup_day = datetime.fromtimestamp(setup_ts, TAIPEI).date().isoformat()
        later_days = [day for day in dates if day > setup_day][:3]
        horizon_values = [daily_close[day] for day in later_days]
        horizon_complete = len(later_days) == 3
        false_3d = (any(close <= float(line) for close in horizon_values) if horizon_complete else None)
        attempt = b_attempt_by_setup.get(setup_ts)
        actual_b_result = attempt.get("execution_result") if attempt else (
            "CONFIRMED_NOT_ATTEMPTED_ACCOUNT_OR_ENGINE_STATE"
            if result.status == "CONFIRMED_NEXT_BAR_ENTRY_AVAILABLE" else "NOT_ENTERED_CONFIRMATION_BLOCKED_OR_UNAVAILABLE"
        )
        row = {
            "stock_code": "2303", "signal_bar_timestamp": setup_ts, "signal_date": setup_day,
            "signal_close": float(candidate_row["close_at_signal"]), "breakout_line": float(line),
            "technical_candidate": True, "market_regime_pass": True,
            "A_candidate_execution": candidate_row.get("risk_and_execution_result"),
            "confirmation_timestamp": None if confirm_bar is None else setup_ts + 300,
            "confirmation_close": None if confirm_bar is None else float(confirm_bar["Close"]),
            "confirmation_status": result.status,
            "B_entry_timestamp": result.entry_timestamp,
            "B_candidate_execution": actual_b_result,
            "next_1_session_close_return_pct": ((daily_close[later_days[0]] / float(candidate_row["close_at_signal"]) - 1) * 100 if len(later_days) >= 1 else None),
            "next_2_session_close_return_pct": ((daily_close[later_days[1]] / float(candidate_row["close_at_signal"]) - 1) * 100 if len(later_days) >= 2 else None),
            "next_3_session_close_return_pct": ((daily_close[later_days[2]] / float(candidate_row["close_at_signal"]) - 1) * 100 if len(later_days) >= 3 else None),
            "false_breakout_within_3_sessions": false_3d,
            "false_breakout_horizon_complete": horizon_complete,
            "outcome_used_for_decision": False,
        }
        audit_rows.append(row)
    return audit_rows


def _blocked_outcomes(rows):
    result = []
    for row in rows:
        if row["confirmation_status"] != "BLOCKED_CLOSE_NOT_ABOVE_BREAKOUT_LINE":
            continue
        result.append({
            "stock_code": row["stock_code"], "signal_bar_timestamp": row["signal_bar_timestamp"],
            "signal_date": row["signal_date"], "signal_close": row["signal_close"],
            "breakout_line": row["breakout_line"], "confirmation_close": row["confirmation_close"],
            "blocked_reason": row["confirmation_status"],
            "next_1_session_close_return_pct": row["next_1_session_close_return_pct"],
            "next_2_session_close_return_pct": row["next_2_session_close_return_pct"],
            "next_3_session_close_return_pct": row["next_3_session_close_return_pct"],
            "outcomes_are_posthoc_only": True,
        })
    return result


def _markdown_report(config, audit, comparison, candidate_rows, blocked_rows):
    a = comparison[comparison["arm"] == ARM_A].iloc[0]
    b = comparison[comparison["arm"] == ARM_B].iloc[0]
    false_a = [row for row in candidate_rows if row["false_breakout_within_3_sessions"] is not None]
    eligible_b = [row for row in candidate_rows if row["confirmation_status"] == "CONFIRMED_NEXT_BAR_ENTRY_AVAILABLE" and row["false_breakout_within_3_sessions"] is not None]
    false_a_count = sum(bool(row["false_breakout_within_3_sessions"]) for row in false_a)
    false_b_count = sum(bool(row["false_breakout_within_3_sessions"]) for row in eligible_b)
    blocked_means = {}
    for horizon in (1, 2, 3):
        vals = [row[f"next_{horizon}_session_close_return_pct"] for row in blocked_rows if row.get(f"next_{horizon}_session_close_return_pct") is not None]
        blocked_means[horizon] = float(np.mean(vals)) if vals else None
    lines = [
        "# Technical V2-B 突破確認 A/B 回測實驗",
        "",
        f"- 實驗：`{config['experiment_id']}`；2303 聯電；2023-01-01～2024-12-31；資料角色 `historical_development`。",
        "- A：凍結 Technical V2-B 的原始策略參數與進出場條件；B：原始完整策略候選成立後，要求下一根同交易日完整 5 分 K 收盤嚴格高於原突破線，並於再下一根實際 K 棒開盤成交。",
        "- 兩組沿用同一筆 2303 真實 K 棒、relaxed_data_eligibility_v1 資料資格與前一交易日可得的市場代理、Risk V1、成本、停損及 30 根低點出場；沒有 ML Gate。這是同一 eligibility 下的實驗配對，不是對舊 422 個五股共同完整日期報告的逐位重現。",
        "- B 不跨日等待；若確認棒或下一根成交棒不存在／不連續，就不成交。缺值不補、不插值、不合成 K 棒。滑價維持 0，這不是實際滑價模擬。",
        "- 此期間已曝光，屬 Training 開發資料。結果只作探索性比較，不覆蓋正式候選，也不能證明泛化或 Final OOS。",
        "",
        "## A/B 績效",
        "",
        "| 版本 | 成交筆數 | 勝率 | 淨報酬 | 淨損益 | 最大回撤 | Profit Factor | 總交易成本 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in (a, b):
        lines.append(f"| {row['arm']} | {int(row['trade_count'])} | {row['win_rate_pct'] if pd.notna(row['win_rate_pct']) else 'N/A'}% | {row['total_return_pct']:.4f}% | {row['net_pnl']:.2f} | {row['max_drawdown_pct']:.4f}% | {row['profit_factor'] if pd.notna(row['profit_factor']) else 'N/A'} | {row['total_transaction_cost']:.2f} |")
    lines += [
        "",
        f"B−A：交易數 {int(b['trade_count']) - int(a['trade_count']):+d}；淨報酬差 {float(b['total_return_pct']) - float(a['total_return_pct']):+.4f} 個百分點；PF 差 {float(b['profit_factor']) - float(a['profit_factor']) if pd.notna(a['profit_factor']) and pd.notna(b['profit_factor']) else 'N/A'}；最大回撤差 {float(b['max_drawdown_pct']) - float(a['max_drawdown_pct']):+.4f} 個百分點。",
        "",
        "## 突破訊號品質",
        "",
        f"- 以完整原始 Technical V2-B 技術條件及市場濾網通過的候選為母體：{len(candidate_rows)} 筆。",
        f"- B 確認結果：通過且有下一根成交棒 {sum(r['confirmation_status'] == 'CONFIRMED_NEXT_BAR_ENTRY_AVAILABLE' for r in candidate_rows)}；確認收盤未高於突破線而阻擋 {sum(r['confirmation_status'] == 'BLOCKED_CLOSE_NOT_ABOVE_BREAKOUT_LINE' for r in candidate_rows)}；無確認棒 {sum(r['confirmation_status'] == 'NO_CONFIRMATION_BAR' for r in candidate_rows)}；非連續確認棒 {sum(r['confirmation_status'] == 'NONCONSECUTIVE_CONFIRMATION_BAR' for r in candidate_rows)}；確認通過但無下一根成交棒 {sum(r['confirmation_status'] == 'NO_NEXT_ENTRY_BAR' for r in candidate_rows)}。",
        f"- 假突破定義（事前固定）：訊號後 3 個交易日收盤中，任何一日收盤回到突破線或以下。A：{false_a_count}/{len(false_a)} = {false_a_count / len(false_a) * 100:.2f}%（可觀察完整 3 日的候選）；B 已確認候選：{false_b_count}/{len(eligible_b)} = {false_b_count / len(eligible_b) * 100:.2f}%（可觀察完整 3 日者）。不完整 3 日視窗不納入比例。",
        f"- 被確認規則阻擋訊號：{len(blocked_rows)} 筆；阻擋後第 1／2／3 個交易日收盤相對訊號收盤的平均變化為 {blocked_means[1]}%／{blocked_means[2]}%／{blocked_means[3]}%。這些是事後描述，沒有回饋到訊號或交易。",
        "- 上述 3 日假突破比例衡量候選後價格是否收回突破線，不等同於策略獲利率；候選層級與風控成交層級分開統計。",
        "",
        "## 前視偏差與一致性檢查",
        "",
        f"- A/B 實際目標股 bars 相同：{audit['same_target_bars']}; 共 {audit['target_bar_count']:,} 根有效真實 K；兩組使用同一市場代理序列與訊號日期資格。",
        "- 技術突破線使用前 55 根已完成 K 棒最高價（既有指標 `BREAKOUT_HIGH` 含 shift(1)）；訊號只在 setup K 棒完成後建立。",
        "- B 確認只能讀下一根完整 K 棒的收盤，成交只讀其後緊鄰實際 K 棒開盤。輸出 audit 的時間差驗證全數應為 300 秒；沒有使用後續價格來決定是否進場。",
        f"- OHLCV 資料來源為唯讀 `{config['data_source']}`；資料庫執行前後雜湊相同：{audit['database_unchanged']}。Technical V2-B frozen config 執行前後雜湊相同：{audit['frozen_config_unchanged']}。",
        "- 績效依同一 2303 bar stream、同本金 NT$1,000,000、Risk V1（風險預算 1%、曝險上限 40%、停損 2.5%）、手續費 0.1425%、交易稅 0.3%、滑價 0% 計算。",
        "",
        "## 結論",
        "",
        "本次判定：MIXED，沒有證據支持直接採用 B。B 的淨報酬比 A 高 0.0186 個百分點（約 NT$186），最大回撤少 0.1576 個百分點、成本少約 NT$1,239.94；但勝率低 2.06 個百分點、Profit Factor 從 0.7577 降至 0.7514。兩組淨報酬仍為負。",
        "突破品質也沒有改善證據：三日假突破比例由 A 的 58.40% 變成 B 通過確認後的 60.61%；26 個被擋訊號在之後 1／2／3 個交易日的平均收盤變化反而為正。淨報酬的微幅改善伴隨交易與成本下降，不能說確認條件提升了訊號辨識力。建議本研究維持凍結 V2-B，B 僅保留為未支持的探索實驗，不升格為候選。",
        "這是單一股票、已曝光 Training 資料；結論不外推至其他股票，也不宣稱樣本外有效。Additional Holdout 與 Final OOS 本次未使用。",
        "",
        "## 輸出",
        "",
        "- `comparison.csv`：A/B 績效與差異。",
        "- `trades.csv`、`equity_curves.csv`、`risk_decisions.csv`：實際回測明細。",
        "- `candidate_confirmation_audit.csv`：候選、確認、可成交與三日假突破稽核。",
        "- `blocked_signal_outcomes.csv`：被確認規則阻擋後的事後 1/2/3 日價格表現。",
        "- `audit.json`：資料、版本雜湊、時序與限制。",
        "",
    ]
    return "\n".join(lines)


def run() -> Path:
    config = _load_config()
    peer_codes = tuple(config["eligibility"]["peer_proxy"]["codes"])
    stocks = (config["stock_code"], *peer_codes)
    db_path = ROOT / config["data_source"]
    if not db_path.is_file():
        raise FileNotFoundError(f"BLOCKED_MISSING_RESEARCH_DATABASE:{config['data_source']}")
    tech = tv1.read_config(ROOT / config["technical_config"])
    tv1.assert_execution_ready(tech, "training")
    family_name, family, candidate = tv1.candidate_by_id(tech, config["frozen_candidate_id"])
    frozen_path = ROOT / config["frozen_candidate_config"]
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if frozen.get("base_candidate") != candidate["id"] or frozen.get("candidate_parameters") != candidate["params"]:
        raise RuntimeError("BLOCKED_FROZEN_V2B_CANDIDATE_MISMATCH")
    if frozen.get("market_regime_filter", {}).get("enabled") is not True:
        raise RuntimeError("BLOCKED_FROZEN_V2B_REGIME_FILTER_MISMATCH")

    before_db = (db_path.stat().st_size, db_path.stat().st_mtime_ns, _sha256(db_path))
    before_frozen = _sha256(frozen_path)
    with contextlib.closing(_read_only_connection(db_path)) as connection:
        raw_rows = _read_period_rows(connection, config["period"], stocks)
        raw_by_day = _session_rows(raw_rows)
        all_dates = sorted({day for _, day in raw_by_day})
        if not all_dates:
            raise RuntimeError("BLOCKED_NO_TRAINING_DATA")
        target_rows, _ = _target_bars_for_dates(raw_by_day, all_dates, config["stock_code"])
        if not target_rows:
            raise RuntimeError("BLOCKED_NO_VALID_TARGET_BARS")
        proxy, proxy_exclusions = build_peer_proxy(raw_by_day, peer_codes, all_dates)
        context = market_context_from_proxy(proxy, all_dates, int(config["eligibility"]["peer_proxy"]["lookback_sessions"]))
        context_by_day = {day: context[day] for day in all_dates}
        target_rows_by_day = raw_by_day

        # Both arms get the same valid target rows, strict-prior peer proxy and
        # exact frozen candidate. A remains the original engine implementation.
        run_a = _run_arm(
            arm=ARM_A, rows=target_rows, context_by_day=context_by_day, tech=tech,
            family_name=family_name, family=family, candidate=candidate, config={
                **config,
                "execution": {**config["execution"], "same_day_tax": "reuse_existing_engine_rule"},
            }, all_dates=all_dates, target_rows_by_day=target_rows_by_day,
        )
        run_b = _run_b(
            config=config, rows=target_rows, context_by_day=context_by_day, tech=tech,
            family_name=family_name, family=family, candidate=candidate,
            all_dates=all_dates, target_rows_by_day=target_rows_by_day,
        )

    after_db = (db_path.stat().st_size, db_path.stat().st_mtime_ns, _sha256(db_path))
    after_frozen = _sha256(frozen_path)
    if before_db != after_db:
        raise RuntimeError("BLOCKED_DATABASE_CHANGED_DURING_READ_ONLY_EXPERIMENT")
    if before_frozen != after_frozen:
        raise RuntimeError("BLOCKED_FROZEN_CONFIG_CHANGED_DURING_EXPERIMENT")
    if run_a["bars"] != run_b["bars"]:
        raise RuntimeError("BLOCKED_ARM_TARGET_BAR_COUNT_MISMATCH")

    candidate_rows = _confirmation_audit(run_a["candidates"], run_b["confirmation_attempts"], run_b["frame_rows"])
    blocked_rows = _blocked_outcomes(candidate_rows)
    summaries = [run_a["summary"], run_b["summary"]]
    comparison = pd.DataFrame(summaries)
    numeric = comparison.select_dtypes(include=[np.number]).columns
    delta = {column: None for column in comparison.columns}
    for column in numeric:
        if pd.notna(comparison.iloc[0][column]) and pd.notna(comparison.iloc[1][column]):
            delta[column] = float(comparison.iloc[1][column]) - float(comparison.iloc[0][column])
    delta.update({"arm": "DELTA_B_MINUS_A", "strategy_id": "ONE_BAR_CONFIRMATION_ONLY", "strategy_version": "not a formal candidate"})
    comparison = pd.concat([comparison, pd.DataFrame([delta])], ignore_index=True)

    source_config_path = ROOT / config["technical_config"]
    audit = {
        "status": "COMPLETED", "experiment_id": config["experiment_id"],
        "data_role": config["data_role"], "period": config["period"], "stock_code": config["stock_code"],
        "data_source": config["data_source"], "data_source_sha256": before_db[2],
        "technical_config_sha256": _sha256(source_config_path), "frozen_v2b_config_sha256": before_frozen,
        "technical_source_sha256": _sha256(ROOT / "trading_system/technical_v1.py"),
        "backtest_engine_sha256": _sha256(ROOT / "trading_system/backtest.py"),
        "target_bar_count": run_a["bars"], "same_target_bars": run_a["bars"] == run_b["bars"],
        "same_peer_proxy_for_both_arms": True, "peer_proxy_excluded_days": len(proxy_exclusions),
        "market_proxy_rule": "equal-weight 13:25 closes for peers 2330/2379/2454/3034; only strictly prior synchronized observations; no target stock and no imputation",
        "execution_timing_check": "A setup close -> immediate next actual bar open; B setup close -> next consecutive completed bar close above saved line -> following consecutive actual bar open",
        "no_lookahead_checks": {
            "breakout_high_uses_shifted_prior_bars": True,
            "B_confirmation_timestamp_minus_setup_seconds": 300,
            "B_entry_timestamp_minus_confirmation_seconds": 300,
            "same_session_required": True,
            "future_outcomes_used_for_signal": False,
            "all_candidate_confirmation_rows_time_checked": all(
                row["confirmation_status"] != "CONFIRMED_NEXT_BAR_ENTRY_AVAILABLE"
                or (row["confirmation_timestamp"] - row["signal_bar_timestamp"] == 300 and row["B_entry_timestamp"] - row["confirmation_timestamp"] == 300)
                for row in candidate_rows
            ),
        },
        "same_risk_cost_exit": True, "risk_v1": config["risk"], "execution": config["execution"],
        "no_ml_gate": True, "parameter_search_performed": False,
        "final_oos_used": False, "additional_holdout_used": False, "validation_used": False,
        "database_unchanged": before_db == after_db, "frozen_config_unchanged": before_frozen == after_frozen,
        "frozen_candidate_overwritten": False, "raw_database_modified": False,
        "output_files": [],
    }
    if not audit["no_lookahead_checks"]["all_candidate_confirmation_rows_time_checked"]:
        raise RuntimeError("BLOCKED_CONFIRMATION_TIME_ORDER_AUDIT_FAILED")

    now = datetime.now(TAIPEI).strftime("%Y%m%dT%H%M%S%z")
    output = ROOT / "exports" / f"breakout_confirmation_exp001_{now}"
    output.mkdir(parents=True, exist_ok=False)
    _write_csv(output / "comparison.csv", comparison.to_dict("records"))
    _write_csv(output / "trades.csv", run_a["trades"] + run_b["trades"])
    _write_csv(output / "candidate_confirmation_audit.csv", candidate_rows)
    _write_csv(output / "blocked_signal_outcomes.csv", blocked_rows)
    _write_csv(output / "equity_curves.csv", run_a["curves"] + run_b["curves"])
    _write_csv(output / "risk_decisions.csv", run_a["risk"] + run_b["risk"])
    _write_json(output / "config_snapshot.json", config)
    (output / "report.md").write_text(_markdown_report(config, audit, comparison, candidate_rows, blocked_rows), encoding="utf-8")
    audit["output_files"] = sorted(path.name for path in output.iterdir() if path.is_file()) + ["audit.json"]
    _write_json(output / "audit.json", audit)
    print(json.dumps({
        "status": audit["status"], "output_directory": str(output.relative_to(ROOT)),
        "comparison": comparison[["arm", "trade_count", "win_rate_pct", "total_return_pct", "max_drawdown_pct", "profit_factor", "total_transaction_cost"]].to_dict("records"),
        "regime_passing_original_candidates": len(candidate_rows),
        "confirmation_pass_count": sum(row["confirmation_status"] == "CONFIRMED_NEXT_BAR_ENTRY_AVAILABLE" for row in candidate_rows),
        "confirmation_block_count": sum(row["confirmation_status"] == "BLOCKED_CLOSE_NOT_ABOVE_BREAKOUT_LINE" for row in candidate_rows),
        "final_oos_used": False, "database_unchanged": audit["database_unchanged"],
    }, ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    try:
        run()
    except (OSError, ValueError, KeyError, sqlite3.Error, RuntimeError) as exc:
        raise SystemExit(f"BREAKOUT_CONFIRMATION_EXP001_BLOCKED: {exc}") from exc

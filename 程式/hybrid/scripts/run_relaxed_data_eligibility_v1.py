"""Compare strict global-session eligibility with target/peer-close eligibility.

This is a research-only, read-only run for 2303 and 2023–2024.  It reuses the
frozen Technical V2-B candidate, Technical V1 indicator/backtest engine and Risk
V1 sizing.  No database is modified and no market bars are synthesized.
"""
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
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.core.data_eligibility_v1 import (  # noqa: E402
    EXPECTED_BARS, TAIPEI, audit_session, build_peer_proxy,
    expected_session_timestamps, indicator_window_clear, market_context_from_proxy,
    is_newly_eligible, select_unambiguous_valid_bars, signal_and_fill_are_contiguous, valid_ohlcv,
)
from hybrid.scripts.run_short_swing_ab_test import _make_strategy_class, _metrics_and_rows, _regime_pass  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

CONFIG_PATH = ROOT / "hybrid/config/relaxed_data_eligibility_v1.json"
TECH_CONFIG_PATH = ROOT / "configs/technical_v1_research.json"
FROZEN_CONFIG_PATH = ROOT / "configs/technical_v2_candidate.json"
ARM_A = "A_ORIGINAL_GLOBAL_COMPLETE"
ARM_B = "B_TARGET_SESSION_AND_PEER_CLOSE"
BAR_COLUMNS = ("MA_FAST", "MA_MID", "MA_SLOW", "TREND_MA", "TREND_SLOPE", "BB_MIDDLE", "BB_UPPER", "BB_LOWER", "BREAKOUT_HIGH", "BREAKOUT_LOW")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _restricted_rows_hash(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("data_role") != "historical_development" or config.get("period") != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise ValueError("BLOCKED_INVALID_DATA_ROLE_OR_PERIOD")
    tv1.assert_development_period(config["period"]["start"], config["period"]["end"])
    if config.get("final_oos_used") is not False or config.get("forbidden_roles_used") is not False:
        raise ValueError("BLOCKED_FORBIDDEN_ROLE_DECLARATION")
    if config.get("arms") != [ARM_A, ARM_B] or config.get("parameter_search_performed") is not False:
        raise ValueError("BLOCKED_EXPERIMENT_DEFINITION_CHANGED")
    return config


def _session_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        dt = datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI)
        if dt.strftime("%H:%M") < "09:00" or dt.strftime("%H:%M") > "13:25":
            continue
        grouped.setdefault((str(row["stock_code"]), dt.date().isoformat()), []).append(row)
    for values in grouped.values():
        values.sort(key=lambda row: int(row["kbar_timestamp"]))
    return grouped


def _read_period_rows(connection: sqlite3.Connection, period: dict[str, str], stocks: tuple[str, ...]) -> list[dict[str, Any]]:
    lower, upper = tv1._timestamp_bounds(period["start"], period["end"])
    placeholders = ",".join("?" for _ in stocks)
    query = (
        "SELECT stock_code,kbar_timestamp,open,high,low,close,volume "
        f"FROM market_kbars WHERE stock_code IN ({placeholders}) AND freq_minutes=5 "
        "AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY stock_code,kbar_timestamp"
    )
    return [dict(row) for row in connection.execute(query, (*stocks, lower, upper)).fetchall()]


def _row_key(row: dict[str, Any]) -> tuple[str, int]:
    return str(row["stock_code"]), int(row["kbar_timestamp"])


def _ohlcv_parity(clean_rows: list[dict[str, Any]], raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    left: dict[tuple[str, int], dict[str, Any]] = {}
    right: dict[tuple[str, int], dict[str, Any]] = {}
    for target, rows in ((left, clean_rows), (right, raw_rows)):
        for row in rows:
            if 9 * 3600 <= datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI).hour * 3600 + datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI).minute * 60 <= 13 * 3600 + 25 * 60:
                key = _row_key(row)
                if key in target:
                    raise RuntimeError(f"BLOCKED_DUPLICATE_SOURCE_TIMESTAMP:{key}")
                target[key] = row
    shared = sorted(set(left) & set(right))
    columns = ("open", "high", "low", "close", "volume")
    mismatches: list[dict[str, Any]] = []
    by_column = {name: 0 for name in columns}
    for key in shared:
        for column in columns:
            a, b = left[key][column], right[key][column]
            equal = (int(a) == int(b)) if column == "volume" else math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-8)
            if not equal:
                by_column[column] += 1
                if len(mismatches) < 20:
                    mismatches.append({"stock_code": key[0], "kbar_timestamp": key[1], "column": column, "clean_value": a, "raw_value": b, "difference": float(b) - float(a)})
    return {"shared_rows_compared": len(shared), "mismatch_count": sum(by_column.values()), "mismatch_by_column": by_column, "tolerance": "OHLC absolute 1e-8; volume exact", "samples": mismatches}


def _audit_missing_bars(rows_by_stock_day: dict[tuple[str, str], list[dict[str, Any]]], all_dates: list[str], stocks: tuple[str, ...]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    missing_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for stock in stocks:
        missing_count = duplicate_count = invalid_count = incomplete_sessions = 0
        for day in all_dates:
            materialized = rows_by_stock_day.get((stock, day), [])
            audit = audit_session(materialized, day)
            incomplete_sessions += int(not audit["session_complete"])
            duplicate_count += len(audit["duplicate_timestamps"])
            invalid_count += len(audit["invalid_ohlcv_timestamps"])
            for stamp in audit["missing_timestamps"]:
                missing_count += 1
                missing_rows.append({"stock_code": stock, "date": day, "missing_timestamp": stamp,
                                     "missing_time": datetime.fromtimestamp(stamp, TAIPEI).strftime("%H:%M"),
                                     "classification": "EXPECTED_MISSING_NO_FILL"})
            for stamp in audit["duplicate_timestamps"]:
                missing_rows.append({"stock_code": stock, "date": day, "missing_timestamp": stamp,
                                     "missing_time": datetime.fromtimestamp(stamp, TAIPEI).strftime("%H:%M"),
                                     "classification": "DUPLICATE_TIMESTAMP_BLOCK_SESSION"})
            for stamp in audit["invalid_ohlcv_timestamps"]:
                missing_rows.append({"stock_code": stock, "date": day, "missing_timestamp": stamp,
                                     "missing_time": datetime.fromtimestamp(stamp, TAIPEI).strftime("%H:%M"),
                                     "classification": "INVALID_OHLCV_BLOCK_SESSION"})
            for stamp in audit["unexpected_timestamps"]:
                missing_rows.append({"stock_code": stock, "date": day, "missing_timestamp": stamp,
                                     "missing_time": datetime.fromtimestamp(stamp, TAIPEI).strftime("%H:%M"),
                                     "classification": "UNEXPECTED_OR_MISALIGNED_TIMESTAMP_BLOCK_SESSION"})
        summary[stock] = {"incomplete_sessions": incomplete_sessions, "missing_expected_bars": missing_count,
                          "duplicate_timestamp_count": duplicate_count, "invalid_ohlcv_count": invalid_count}
    return missing_rows, summary


def _target_bars_for_dates(rows_by_stock_day: dict[tuple[str, str], list[dict[str, Any]]], dates: list[str], target_stock: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    bars: list[dict[str, Any]] = []
    audits: dict[str, dict[str, Any]] = {}
    for day in dates:
        rows = rows_by_stock_day.get((target_stock, day), [])
        audit = audit_session(rows, day)
        audits[day] = audit
        bars.extend(select_unambiguous_valid_bars(rows, day))
    return bars, audits


def _global_slot_map(all_dates: list[str]) -> tuple[dict[int, int], set[int]]:
    """Expected-bar ordinal map plus no-fill missing slots for target 2303."""
    timestamp_to_slot: dict[int, int] = {}
    missing_slots: set[int] = set()
    for day_index, day in enumerate(all_dates):
        expected = expected_session_timestamps(day)
        for slot, stamp in enumerate(expected):
            timestamp_to_slot[stamp] = day_index * EXPECTED_BARS + slot
    return timestamp_to_slot, missing_slots


def _prepare_frame(bars: list[dict[str, Any]], app: Any, all_dates: list[str], target_rows_by_day: dict[tuple[str, str], list[dict[str, Any]]], target_stock: str) -> tuple[pl.DataFrame, int, set[str]]:
    if not bars:
        raise RuntimeError("BLOCKED_NO_COMPLETE_TARGET_SESSIONS")
    observed = {int(row["kbar_timestamp"]) for row in bars}
    timestamp_to_slot, _ = _global_slot_map(all_dates)
    missing_slots: set[int] = set()
    incomplete_target_dates: set[str] = set()
    for day_index, day in enumerate(all_dates):
        rows = target_rows_by_day.get((target_stock, day), [])
        audit = audit_session(rows, day)
        usable_stamps = {int(row["kbar_timestamp"]) for row in select_unambiguous_valid_bars(rows, day)}
        if not audit["session_complete"]:
            incomplete_target_dates.add(day)
            for slot, stamp in enumerate(expected_session_timestamps(day)):
                if stamp not in usable_stamps:
                    missing_slots.add(day_index * EXPECTED_BARS + slot)

    raw_frame = pl.DataFrame([{
        "kbar_timestamp": int(row["kbar_timestamp"]), "Open": float(row["open"]), "High": float(row["high"]),
        "Low": float(row["low"]), "Close": float(row["close"]), "Volume": int(row["volume"]),
    } for row in bars]).sort("kbar_timestamp").with_columns(
        pl.col("kbar_timestamp").map_elements(bt.taipei_datetime_from_timestamp, return_dtype=pl.Datetime).alias("kbar_time")
    )
    frame = bt.add_indicators(raw_frame, app)
    records = frame.to_dicts()
    for record in records:
        slot = timestamp_to_slot.get(int(record["kbar_timestamp"]))
        if slot is None:
            raise RuntimeError("BLOCKED_TARGET_TIMESTAMP_OUTSIDE_EXPECTED_SESSION_GRID")
        if not indicator_window_clear(slot, missing_slots, int(app.ma_slow_period)):
            for column in BAR_COLUMNS:
                record[column] = None
    frame = pl.DataFrame(records, infer_schema_length=None).sort("kbar_timestamp")
    if frame.height != len(observed):
        raise RuntimeError("BLOCKED_SYNTHETIC_OR_DUPLICATE_TARGET_ROW")
    return frame, len(incomplete_target_dates), incomplete_target_dates


def _build_gap_aware_strategy(variant: str, incomplete_target_dates: set[str]):
    Parent = _make_strategy_class(variant)

    class GapAwareStrategy(Parent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._last_completed_timestamp: int | None = None
            self._last_completed_date: str | None = None
            self.data_gap_events: list[dict[str, Any]] = []

        def process_completed_kbar(self, code, decision_time, completed_kbar):
            completed = int(completed_kbar["kbar_timestamp"])
            current = int(decision_time.replace(tzinfo=TAIPEI).timestamp()) if decision_time.tzinfo is None else int(decision_time.timestamp())
            completed_date = datetime.fromtimestamp(completed, TAIPEI).date().isoformat()
            current_date = datetime.fromtimestamp(current, TAIPEI).date().isoformat()
            intraday_gap = completed_date == current_date and current - completed != 300
            prior_incomplete_session = bool(
                self._last_completed_date
                and any(self._last_completed_date < day <= current_date for day in incomplete_target_dates)
            )
            if intraday_gap or prior_incomplete_session:
                self.previous_snapshot = None
                event = {"signal_bar_timestamp": completed, "decision_open_timestamp": current,
                         "reason": "MISSING_EXPECTED_TARGET_KBAR", "position_open": bool(self.is_bought and not self.is_sold)}
                self.data_gap_events.append(event)
                if self.is_bought and not self.is_sold and self.last_tick:
                    opening = float(self.last_tick.get("close", completed_kbar.get("Open", completed_kbar.get("Close"))))
                    self.is_exit = True
                    self.is_entry = False
                    self.local_fill_price_override = None
                    self.local_fill_reason_override = None
                    self.local_fill_timestamp_override = None
                    self.record_signal(code=code, action="Sell", signal_type="data_gap_exit",
                                       signal_time=decision_time, price=opening,
                                       reason="DATA_GAP_EXIT_AT_NEXT_OBSERVED_OPEN", indicators=bt.indicator_snapshot(completed_kbar))
                    self._sell_market(code, opening)
            self._audit_bar = completed_kbar
            try:
                super().process_completed_kbar(code, decision_time, completed_kbar)
            finally:
                self._audit_bar = None
            self._last_completed_timestamp = completed
            self._last_completed_date = completed_date

    return GapAwareStrategy


def _run_arm(*, arm: str, rows: list[dict[str, Any]], context_by_day: dict[str, dict[str, Any]], tech: dict[str, Any], family_name: str, family: dict[str, Any], candidate: dict[str, Any], config: dict[str, Any], all_dates: list[str], target_rows_by_day: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, Any]:
    from trading_system.backtest import MarketDataStats
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
    frame, _, incomplete_dates = _prepare_frame(rows, app, all_dates, target_rows_by_day, config["stock_code"])
    frame_times = [int(value) for value in frame["kbar_timestamp"].to_list()]
    schedule = {
        datetime.fromtimestamp(frame_times[index], TAIPEI).isoformat(timespec="microseconds")
        for index in range(1, len(frame_times))
        if signal_and_fill_are_contiguous(frame_times[index - 1], frame_times[index])
    }
    regime = {(config["stock_code"], day): value for day, value in context_by_day.items()}
    strategy_cls = _build_gap_aware_strategy("A_ORIGINAL_V2B", incomplete_dates)
    strategy = strategy_cls(
        config=app, study_config=app_values, family_name=family_name, family=family,
        candidate=candidate, data_role="historical_development", fold_id="ELIGIBILITY_2023_2024",
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
    risk = [{"arm": arm, **row} for row in strategy.risk_decisions]
    candidates = [{"arm": arm, **row} for row in strategy.candidate_audit]
    bars = bt.build_kbar_rows_from_frame(frame, app)
    summary, enriched, curve = _metrics_and_rows(version=arm, strategy=strategy, app=app, bars=bars,
                                                   trade_rows=trades, risk_rows=risk, candidate_rows=candidates)
    summary.update({"arm": arm, "strategy_version": "technical_v2b_data_eligibility_v1", "ml_gate": "NONE_BOTH_ARMS",
                    "actual_target_sessions": len({datetime.fromtimestamp(t, TAIPEI).date().isoformat() for t in frame_times}),
                    "data_gap_exit_count": sum(row.get("signal_type") == "data_gap_exit" for row in strategy.local_signals),
                    "open_position_at_end": int(strategy.local_position != 0)})
    if strategy.local_position != 0:
        raise RuntimeError(f"BLOCKED_OPEN_POSITION_AFTER_REPLAY:{arm}")
    return {"summary": summary, "trades": [{"arm": arm, **row} for row in enriched],
            "curves": [{"arm": arm, **row} for row in curve], "candidates": candidates,
            "risk": risk, "gap_events": [{"arm": arm, **row} for row in strategy.data_gap_events],
            "bars": frame.height, "schedule_size": len(schedule)}


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _markdown_report(config: dict[str, Any], audit: dict[str, Any], comparison: pd.DataFrame,
                     date_rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]]) -> str:
    a = comparison.loc[comparison.arm == ARM_A].iloc[0]
    b = comparison.loc[comparison.arm == ARM_B].iloc[0]
    added = int(audit["newly_eligible_session_count"])
    new_candidates = sum(bool(row.get("candidate_after_regime")) for row in candidate_rows if row.get("arm") == ARM_B and not row.get("baseline_signal_date_eligible"))
    new_fills = sum(bool(row.get("risk_and_execution_result") == "FILLED") for row in candidate_rows if row.get("arm") == ARM_B and not row.get("baseline_signal_date_eligible"))
    return "\n".join([
        "# Technical V2-B 資料資格放寬實驗",
        "",
        f"- Experiment: `{config['experiment_id']}`；股票 2303 聯電；資料角色 `historical_development`。",
        "- 期間：2023-01-01～2024-12-31。只查詢 Training exposed 資料；沒有讀取 2025/2026、Holdout 或 Final OOS。",
        "- A/B 均使用凍結 Technical V2-B 進出場、Risk V1、相同本金與成本；兩邊都不使用 ML Gate。",
        "- 唯一實驗變因是資料資格：A 依五檔完整 54 根共同日決定當日是否允許進場；B 保留 2303 所有唯一、合法、時間正確的真實 K 棒，只在訊號回看區間或下一根成交棒缺漏時阻擋。市場代理排除 2303、四檔等權、SMA20，使用訊號日前已同步的代理收盤；當日代理收盤只供後續日期使用。",
        "- 沒有補值、合成 K 棒或用未來收盤回填。2303 不完整 session 中仍保留唯一、合法、時間對齊的真實 K 棒；缺口會使往後 240 個預期 bar 的指標訊號失效。缺少訊號確認棒或下一根成交棒就不產生／不成交；持倉遇到缺口則在下一個可觀測開盤處理並記錄。",
        "",
        "## 交易日資格",
        "",
        f"- A 原始全域完整日期：{audit['strict_global_complete_sessions']} 日。",
        f"- B 目標股完整日期：{audit['target_complete_sessions']} 日；另有 {audit['incomplete_target_sessions_with_usable_bars_retained_in_trade_replay']} 個不完整日期仍保留可用真實 K 棒。",
        f"- B 代理可同日同步的 13:25 收盤日期：{audit['synchronized_peer_close_sessions']} 日。",
        f"- B 新增可用日期：{added} 日。此數字代表可用於指標／候選評估的日期，不等於新增交易數。",
        f"- 新增日期上的市場濾網通過候選：{new_candidates}；風控後實際成交：{new_fills}。",
        f"- 全部日期的市場濾網通過候選：A {int(a['post_market_filter_candidate_count'])}、B {int(b['post_market_filter_candidate_count'])}。新增日期貢獻 {new_candidates} 個；其餘差異來自代理 SMA20 使用較完整的同步收盤日期序列。",
        f"- 扣除 20 日代理暖機後，進場資格日數：A {int(a['data_eligible_session_count'])} 日、B {int(b['data_eligible_session_count'])} 日。兩組回放使用相同的 2303 真實且可判讀 K 棒時間軸，以保留一致的停損與出場觀察。",
        "",
        "## A/B 實際績效",
        "",
        "| 組別 | 資料資格日 | 交易數 | 報酬 | 淨損益 | 勝率 | 最大回撤 | Profit Factor | Sharpe | 成本 | 目標股實際觀察日 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *[f"| {row['arm']} | {int(row['data_eligible_session_count'])} | {int(row['trade_count'])} | {row['total_return_pct']:.3f}% | {row['net_pnl']:,.2f} | {row['win_rate_pct'] if pd.notna(row['win_rate_pct']) else 'N/A'} | {row['max_drawdown_pct']:.3f}% | {row['profit_factor'] if pd.notna(row['profit_factor']) else 'N/A'} | {row['sharpe_ratio'] if pd.notna(row['sharpe_ratio']) else 'N/A'} | {row['total_transaction_cost']:,.2f} | {int(row['actual_target_sessions'])} |" for row in (a, b)],
        "",
        f"報酬差（B−A）={b.total_return_pct-a.total_return_pct:.3f} 個百分點；這是已曝光開發期間的探索性結果，不是乾淨樣本外結論，也不會據此取代凍結 V2-B。",
        "",
        "## 資料與訊號安全檢查",
        "",
        f"- 共同時間戳 OHLCV 比對：{audit['ohlcv_parity']['shared_rows_compared']:,} 列；欄位容差內不一致 {audit['ohlcv_parity']['mismatch_count']}。",
        f"- 非法 OHLCV 列：{audit['data_quality_totals']['invalid_ohlcv_count']}；重複 timestamp：{audit['data_quality_totals']['duplicate_timestamp_count']}。",
        f"- 缺少預期 K 棒：{audit['data_quality_totals']['missing_expected_bars']} 根。均保留缺漏紀錄，未補值。",
        f"- A/B 進場訊號都要求訊號 bar 後恰有下一根實際 5 分 K；成交時間戳不連續者不建立候選成交。",
        f"- 指標窗口缺口遮蔽：最長必要窗口 {audit['indicator_gap_guard_bars']} 根。代理只採相同日期四檔有效 13:25 close；缺一檔即排除該代理日期，不混用不同日期。",
        "- 當日候選、風控決策、實際成交分開記錄。新增日的候選或成交若為 0，表示日期增加不保證訊號增加。",
        "",
        "## 判讀與限制",
        "",
        "- 如果 B 的有效日增加但候選／成交沒有增加，問題可能仍在技術條件或市場濾網，而不是共同日期交集。",
        "- 如果 B 候選增加，仍需看完整交易紀錄、成本後報酬與回撤；交易數增加本身不代表訊號品質改善。",
        "- 目標股不完整 session 不補值：保留有效棒，但指標窗口中任何預期槽位缺失都會遮蔽訊號，下一根實際 K 不連續時不成交。若持倉跨過資料缺口，下一個可觀測開盤強制處理，無法還原缺口期間的盤中停損路徑，該限制已逐筆記錄。",
        "- Risk V1 使用既有 1% 風險預算、40% 曝險上限、2.5% 停損；策略仍有既有整股／最大數量限制。滑價為 0。",
        "- 這是資料資格實驗，績效差異不是策略改善證據。不得因本次結果在同一期間回頭調參。",
        "",
        "## 檔案",
        "",
        "- `data_eligibility_comparison.csv`：日期級 A/B 資格與排除原因。",
        "- `missing_kbar_audit.csv`：各股票缺棒、重複與非法資料 audit。",
        "- `newly_eligible_days.csv`：B 相對 A 新增日期與代理／Warm-up 狀態。",
        "- `candidate_trade_diff.csv`：候選訊號與成交逐筆對照。",
        "- `performance_comparison.csv`：A/B 指標及差值。",
        "- `audit.json`：資料範圍、來源、檢查與限制。",
        "",
    ])


def run() -> Path:
    config = _load_config()
    peer_codes = tuple(str(code) for code in config["market_proxy"]["peer_codes"])
    stock_codes = (str(config["stock_code"]), *peer_codes)
    if len(set(stock_codes)) != len(stock_codes):
        raise ValueError("BLOCKED_DUPLICATE_TARGET_OR_PROXY_STOCK")
    data_paths = {name: ROOT / config[name] for name in ("baseline_data_source", "candidate_data_source")}
    if any(not path.is_file() for path in data_paths.values()):
        raise FileNotFoundError("BLOCKED_MISSING_RESEARCH_DATABASE")
    tech_path, frozen_path = ROOT / config["technical_config"], ROOT / config["frozen_candidate_config"]
    if not tech_path.is_file() or not frozen_path.is_file():
        raise FileNotFoundError("BLOCKED_MISSING_TECHNICAL_CONFIG")
    tech = tv1.read_config(tech_path)
    tv1.assert_execution_ready(tech, "training")
    family_name, family, candidate = tv1.candidate_by_id(tech, config["frozen_candidate_id"])
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if frozen.get("base_candidate") != candidate["id"] or frozen.get("market_regime_filter", {}).get("enabled") is not True:
        raise RuntimeError("BLOCKED_FROZEN_CANDIDATE_MISMATCH")

    before_stats = {name: (p.stat().st_size, p.stat().st_mtime_ns) for name, p in data_paths.items()}
    with contextlib.closing(_read_only_connection(data_paths["baseline_data_source"])) as clean_conn, \
            contextlib.closing(_read_only_connection(data_paths["candidate_data_source"])) as raw_conn:
        clean_rows = _read_period_rows(clean_conn, config["period"], stock_codes)
        raw_rows = _read_period_rows(raw_conn, config["period"], stock_codes)
        parity = _ohlcv_parity(clean_rows, raw_rows)
        if parity["mismatch_count"]:
            raise RuntimeError(f"BLOCKED_SHARED_OHLCV_MISMATCH:{parity['mismatch_count']}")
        clean_by_day, raw_by_day = _session_rows(clean_rows), _session_rows(raw_rows)
        all_dates = sorted({day for _, day in raw_by_day})
        if not all_dates:
            raise RuntimeError("BLOCKED_NO_TRAINING_SESSION_DATES")
        missing_rows, quality = _audit_missing_bars(raw_by_day, all_dates, stock_codes)
        clean_target_bars, clean_audits = _target_bars_for_dates(clean_by_day, all_dates, config["stock_code"])
        raw_target_bars, raw_audits = _target_bars_for_dates(raw_by_day, all_dates, config["stock_code"])
        target_complete_days = sorted(day for day, audit in raw_audits.items() if audit["session_complete"])
        strict_days = [day for day in all_dates if all(
            audit_session(raw_by_day.get((stock, day), []), day)["session_complete"] for stock in stock_codes
        )]
        clean_target_days = sorted({datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI).date().isoformat() for row in clean_target_bars})
        clean_complete_days = sorted(day for day in clean_target_days if clean_audits[day]["session_complete"])
        if set(clean_complete_days) != set(strict_days):
            raise RuntimeError(f"BLOCKED_CLEAN_DATABASE_NOT_EQUAL_TO_GLOBAL_INTERSECTION:clean={len(clean_target_days)},strict={len(strict_days)}")
        if sum(clean_audits[day]["bar_count"] for day in strict_days) != len(strict_days) * EXPECTED_BARS:
            raise RuntimeError("BLOCKED_BASELINE_TARGET_SESSION_BAR_COUNT")
        target_observed_days = sorted({datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI).date().isoformat() for row in raw_target_bars})
        proxy, proxy_exclusions = build_peer_proxy(raw_by_day, peer_codes, all_dates)
        # Strict baseline proxy is rebuilt from exactly the global complete days.
        strict_proxy = {}
        for day in strict_days:
            closes = []
            for peer in peer_codes:
                close = next((float(row["close"]) for row in raw_by_day.get((peer, day), [])
                              if int(row["kbar_timestamp"]) == expected_session_timestamps(day)[-1]
                              and valid_ohlcv(row)), None)
                if close is None:
                    raise RuntimeError(f"BLOCKED_STRICT_PROXY_CLOSE_MISSING:{peer}:{day}")
                closes.append(close)
            strict_proxy[day] = float(np.mean(closes))
        strict_context = market_context_from_proxy(strict_proxy, target_observed_days, 20)
        # A and B replay the exact same real target-stock bars.  A reproduces
        # the original *eligibility decision* by denying entries on dates that
        # were removed by the five-stock global intersection; it still observes
        # 2303 bars for exits, stops, and mark-to-market.  This avoids confounding
        # the comparison with a different target-price time axis.
        for day in target_observed_days:
            if day not in strict_days:
                strict_context[day] = {
                    "eligible": False,
                    "reason": "CURRENT_DAY_NOT_GLOBAL_COMPLETE",
                    "target_stock_excluded": True,
                    "latest_common_prior_date": None,
                    "last_20_proxy_closes": [],
                    "proxy_sma20": None,
                    "history_count": 0,
                }
        relaxed_context = market_context_from_proxy(proxy, target_observed_days, 20)
        date_rows = []
        new_dates = []
        for day in all_dates:
            target_audit = raw_audits.get(day, audit_session([], day))
            peer_close_ok = day in proxy
            strict_eligible = day in strict_days and len([d for d in strict_days if d < day]) >= 20
            target_has_usable_bars = bool(select_unambiguous_valid_bars(raw_by_day.get((config["stock_code"], day), []), day))
            relaxed_eligible = target_has_usable_bars and len([d for d in proxy if d < day]) >= 20
            reason = ""
            if not target_has_usable_bars:
                reason = "NO_UNAMBIGUOUS_VALID_TARGET_BARS"
            elif not relaxed_eligible:
                reason = "INSUFFICIENT_PRIOR_SYNCHRONIZED_PROXY_HISTORY"
            elif not strict_eligible:
                if not target_audit["session_complete"]:
                    reason = "TARGET_SESSION_INCOMPLETE_VALID_REAL_BARS_RETAINED"
                else:
                    reason = ("PEER_INTRADAY_KBARS_INCOMPLETE_BUT_13_25_CLOSES_ALIGNED"
                              if peer_close_ok else "CURRENT_PEER_CLOSE_MISSING_NOT_REQUIRED_FOR_TODAY_SIGNAL")
            row = {"date": day, "target_stock": config["stock_code"], "target_session_complete": target_audit["session_complete"],
                   "target_bar_count": target_audit["bar_count"], "strict_global_complete": day in strict_days,
                   "peer_same_date_13_25_closes_complete": peer_close_ok,
                   "target_has_usable_real_bars": target_has_usable_bars,
                   "peer_close_used_only_for_future_proxy_observations": True,
                   "strict_prior_20_sessions_ready": len([d for d in strict_days if d < day]) >= 20,
                   "relaxed_prior_20_synchronized_closes_ready": len([d for d in proxy if d < day]) >= 20,
                   "baseline_data_eligible": strict_eligible, "candidate_data_eligible": relaxed_eligible,
                   "newly_eligible": bool(relaxed_eligible and not strict_eligible), "reason": reason}
            date_rows.append(row)
            if row["newly_eligible"]:
                new_dates.append(row)

        clean_indicators, raw_indicators = None, None
        # Both arms use the same unique, valid, time-aligned real 2303 bars,
        # including usable bars from incomplete sessions.  Only data
        # eligibility and prior synchronized proxy history differ.
        runs = [
            _run_arm(arm=ARM_A, rows=raw_target_bars, context_by_day=strict_context, tech=tech,
                     family_name=family_name, family=family, candidate=candidate, config=config,
                     all_dates=all_dates, target_rows_by_day=raw_by_day),
            _run_arm(arm=ARM_B, rows=raw_target_bars, context_by_day=relaxed_context, tech=tech,
                     family_name=family_name, family=family, candidate=candidate, config=config,
                     all_dates=all_dates, target_rows_by_day=raw_by_day),
        ]
        eligible_day_counts = {
            ARM_A: sum(bool(row["baseline_data_eligible"]) for row in date_rows),
            ARM_B: sum(bool(row["candidate_data_eligible"]) for row in date_rows),
        }
        for item in runs:
            item["summary"]["data_eligible_session_count"] = eligible_day_counts[item["summary"]["arm"]]
        after_stats = {name: (p.stat().st_size, p.stat().st_mtime_ns) for name, p in data_paths.items()}
        if before_stats != after_stats:
            raise RuntimeError("BLOCKED_DATABASE_FILE_CHANGED_DURING_READ_ONLY_RUN")

    now = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%dT%H%M%S%z")
    output = ROOT / "exports" / f"relaxed_data_eligibility_v1_{now}"
    output.mkdir(parents=True, exist_ok=False)
    candidate_rows: list[dict[str, Any]] = []
    trade_diff: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []
    for run in runs:
        filled_candidates = {}
        for row in run["candidates"]:
            day = str(row.get("signal_bar_end_time", ""))[:10]
            date_match = next((x for x in date_rows if x["date"] == day), {})
            item = {**row, "baseline_signal_date_eligible": bool(date_match.get("baseline_data_eligible")),
                    "candidate_signal_date_eligible": bool(date_match.get("candidate_data_eligible"))}
            candidate_rows.append(item)
            trade_diff.append({"record_type": "CANDIDATE", **item})
            if row.get("risk_and_execution_result") == "FILLED":
                key = (str(row.get("arm")), str(row.get("earliest_entry_time", ""))[:19].replace("T", " "))
                filled_candidates[key] = item
        for row in run["trades"]:
            candidate = filled_candidates.get((str(run["summary"]["arm"]), str(row.get("buy_datetime", ""))[:19]))
            trade_diff.append({"record_type": "TRADE", **row,
                "matched_candidate": candidate is not None,
                "matched_candidate_data_eligible": bool(candidate.get("candidate_signal_date_eligible")) if candidate else None,
                "matched_candidate_newly_eligible": is_newly_eligible(candidate.get("baseline_signal_date_eligible"), candidate.get("candidate_signal_date_eligible")) if candidate else None,
                "matched_candidate_reason": candidate.get("market_regime_reason") if candidate else None,
            })
        risk_rows.extend(run["risk"])
        curve_rows.extend(run["curves"])

    comparison = pd.DataFrame([run["summary"] for run in runs])
    a = comparison.loc[comparison.arm == ARM_A].iloc[0]
    b = comparison.loc[comparison.arm == ARM_B].iloc[0]
    delta = {column: (float(b[column]) - float(a[column])) if column in comparison.columns and pd.api.types.is_numeric_dtype(comparison[column]) and pd.notna(a[column]) and pd.notna(b[column]) else None for column in comparison.columns}
    delta.update({"arm": "DELTA_B_MINUS_A", "strategy_id": "DATA_ELIGIBILITY_ONLY", "strategy_version": "not a strategy version"})
    comparison = pd.concat([comparison, pd.DataFrame([delta])], ignore_index=True)
    candidate_counts = {run["summary"]["arm"]: {
        "raw_breakout_triggers": sum(bool(row.get("breakout_cross_pass")) for row in run["candidates"]),
        "technical_candidates": sum(bool(row.get("technical_candidate")) for row in run["candidates"]),
        "market_filter_pass_candidates": sum(bool(row.get("candidate_after_regime")) for row in run["candidates"]),
        "risk_decisions": len(run["risk"]), "actual_fills": sum(row.get("action") == "Buy" for row in run["trades"]),
    } for run in runs}
    # The completed-trade rows store a round-trip result, not separate Buy
    # order rows. Count candidate-level FILLED outcomes as the entry-fill total.
    for run in runs:
        candidate_counts[run["summary"]["arm"]]["actual_fills"] = sum(
            row.get("risk_and_execution_result") == "FILLED" for row in run["candidates"]
        )
    data_totals = {key: sum(value[key] for value in quality.values()) for key in ("missing_expected_bars", "duplicate_timestamp_count", "invalid_ohlcv_count")}
    audit = {
        "status": "COMPLETED", "experiment_id": config["experiment_id"], "data_role": config["data_role"],
        "stock_code": config["stock_code"], "period": config["period"],
        "baseline_source": config["baseline_data_source"], "candidate_source": config["candidate_data_source"],
        "restricted_period_baseline_rows_sha256": _restricted_rows_hash(clean_rows),
        "restricted_period_candidate_rows_sha256": _restricted_rows_hash(raw_rows),
        "ohlcv_parity": parity, "global_calendar_days": len(all_dates),
        "strict_global_complete_sessions": len(strict_days), "target_complete_sessions": len(target_complete_days),
        "synchronized_peer_close_sessions": len(proxy), "newly_eligible_session_count": len(new_dates),
        "incomplete_target_sessions_with_usable_bars_retained_in_trade_replay": sum(
            bool(select_unambiguous_valid_bars(raw_by_day.get((config["stock_code"], day), []), day))
            and not raw_audits.get(day, {}).get("session_complete", False) for day in all_dates
        ),
        "incomplete_target_sessions_without_usable_bars": sum(
            not bool(select_unambiguous_valid_bars(raw_by_day.get((config["stock_code"], day), []), day)) for day in all_dates
        ),
        "per_stock_quality": quality, "data_quality_totals": data_totals,
        "indicator_gap_guard_bars": 240,
        "candidate_counts": candidate_counts,
        "gap_exit_events": {run["summary"]["arm"]: len(run["gap_events"]) for run in runs},
        "strict_proxy_rule": "all five stock sessions exactly 54 bars for current-date eligibility; target excluded; equal-weight peer 13:25 closes; strict prior 20 globally complete common sessions",
        "relaxed_proxy_rule": "target excluded; a proxy observation is formed only when all four peer 13:25 closes for that date are unique and valid; equal-weight; candidate date uses only the strict prior 20 synchronized proxy dates; current-date peer close is not used to authorize earlier target signals; no imputation",
        "target_session_rule": "retain every unique, valid, expected-time target bar; never fill missing bars. Mask all indicator outputs if the current or prior 239 expected target-bar slots contain a gap. Missing signal/next-fill bars block that trade; open positions encountering data gaps exit at the next observed open.",
        "comparison_design": "A/B use the same unique, valid, expected-time real target-stock bars, including usable bars from incomplete sessions. A blocks entries unless all stocks have complete sessions that day; B blocks only bars whose actual indicator lookback contains a missing slot and fills that lack the exact next bar. Market proxy uses only strictly prior synchronized peer-close dates; current-date peer close is not required for current target signals. A is not a byte-for-byte rerun of the historical cleaned-DB report.",
        "newly_eligible_completed_trade_count": sum(row.get("record_type") == "TRADE" and row.get("arm") == ARM_B and row.get("matched_candidate_newly_eligible") is True for row in trade_diff),
        "execution_rule": "completed 5-minute bar; next scheduled actual bar open only; candidate fill schedule requires exactly +300 seconds",
        "risk_v1": config["risk"], "costs": config["execution"], "ml_gate_used": False,
        "final_oos_used": False, "additional_holdout_used": False, "validation_used": False,
        "parameter_search_performed": False, "technical_parameters_changed": False,
        "frozen_candidate_changed": False, "raw_database_modified": False,
        "data_source_metadata_before_after_unchanged": True,
        "output_files": [],
    }
    _write_csv(output / "data_eligibility_comparison.csv", date_rows)
    _write_csv(output / "missing_kbar_audit.csv", missing_rows)
    _write_csv(output / "newly_eligible_days.csv", new_dates)
    _write_csv(output / "candidate_trade_diff.csv", trade_diff)
    _write_csv(output / "performance_comparison.csv", comparison.to_dict("records"))
    (output / "config_snapshot.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "data_quality_report.md").write_text(_markdown_report(config, audit, comparison, date_rows, candidate_rows), encoding="utf-8")
    audit["output_files"] = sorted(p.name for p in output.iterdir() if p.is_file()) + ["audit.json"]
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "output_directory": str(output.relative_to(ROOT)),
                      "strict_global_complete_sessions": len(strict_days), "target_complete_sessions": len(target_complete_days),
                      "newly_eligible_session_count": len(new_dates), "candidate_counts": candidate_counts,
                      "comparison": comparison[["arm", "trade_count", "total_return_pct", "win_rate_pct", "max_drawdown_pct", "profit_factor", "sharpe_ratio"]].to_dict("records"),
                      "final_oos_used": False, "raw_database_modified": False}, ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    try:
        run()
    except (OSError, ValueError, KeyError, sqlite3.Error, RuntimeError) as exc:
        raise SystemExit(f"RELAXED_DATA_ELIGIBILITY_BLOCKED: {exc}") from exc

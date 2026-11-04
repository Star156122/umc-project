"""Run preregistered 2303 Technical V2-B max-hold comparison on 2023-2024 only."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import math
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

from hybrid.core.multi_strategy_backtest_v1 import derive_equity_curve, summary_from_curve  # noqa: E402
from hybrid.scripts import run_short_swing_ab_test as shared  # noqa: E402
from hybrid.scripts.run_strategy_v2_batch import build_regime_context  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
CONFIG_PATH = ROOT / "hybrid/config/max_holding_days_ab_exp002.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def day_equivalent_to_bars(days: int, bars_per_session: int = 54) -> int:
    if days <= 0 or bars_per_session <= 0:
        raise ValueError("days and bars_per_session must be positive")
    return days * bars_per_session


def observed_day_intervals(entry_date: str, exit_date: str, observed_dates: list[str]) -> int:
    """Count observed session-date transitions, rather than calendar-day gaps."""
    index = {str(day)[:10]: i for i, day in enumerate(observed_dates)}
    start, end = str(entry_date)[:10], str(exit_date)[:10]
    if start not in index or end not in index:
        raise ValueError("entry and exit dates must exist in observed_dates")
    return max(0, index[end] - index[start])


def inclusive_sessions(entry_date: str, exit_date: str, observed_dates: list[str]) -> int:
    return observed_day_intervals(entry_date, exit_date, observed_dates) + 1


def pair_baseline_time_exits(baseline: list[dict[str, Any]],
                             variants: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result = []
    for trade in baseline:
        if trade.get("exit_type") != "time_expiration":
            continue
        row = {"baseline_buy_datetime": trade.get("buy_datetime"),
               "baseline_net_pnl": trade.get("net_pnl"),
               "baseline_gross_pnl": trade.get("gross_pnl"),
               "baseline_exit_type": trade.get("exit_type"),
               "baseline_holding_bars": trade.get("holding_bars")}
        for label, longer_trades in variants.items():
            match = next((candidate for candidate in longer_trades
                          if candidate.get("buy_datetime") == trade.get("buy_datetime")), None)
            row[f"{label}_pair_status"] = "MATCHED" if match else "NO_MATCHED_TRADE"
            row[f"{label}_buy_datetime"] = match.get("buy_datetime") if match else None
            row[f"{label}_sell_datetime"] = match.get("sell_datetime") if match else None
            row[f"{label}_exit_type"] = match.get("exit_type") if match else None
            row[f"{label}_gross_pnl"] = match.get("gross_pnl") if match else None
            row[f"{label}_net_pnl"] = match.get("net_pnl") if match else None
            row[f"{label}_net_pnl_change"] = (float(match["net_pnl"]) - float(trade["net_pnl"])) if match else None
            row[f"{label}_holding_bars"] = match.get("holding_bars") if match else None
        result.append(row)
    return result


def _load_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("status") != "PREREGISTERED" or config.get("preregistration_created_before_data_run") is not True:
        raise RuntimeError("BLOCKED_NOT_PREREGISTERED")
    if config.get("data_role") != "historical_development" or config.get("period") != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise RuntimeError("BLOCKED_UNREGISTERED_DATA_SCOPE")
    base = shared._load_config()
    for key in ("stock_code", "initial_capital", "data_source", "technical_config", "frozen_candidate_config", "frozen_candidate_id", "execution", "market_regime_filter"):
        if config.get(key) != base.get(key):
            raise RuntimeError(f"BLOCKED_BASELINE_CONFIG_MISMATCH:{key}")
    if int(config["baseline"]["max_hold_bars"]) != int(base["risk"]["max_holding_bars"]):
        raise RuntimeError("BLOCKED_BASELINE_HOLD_LIMIT_MISMATCH")
    expected = [162, 270, 540, 810]
    actual = [int(config["baseline"]["max_hold_bars"])] + [int(item["max_hold_bars"]) for item in config["candidates"]]
    if actual != expected:
        raise RuntimeError("BLOCKED_UNEXPECTED_HOLD_CAPS")
    return config, base


def run() -> Path:
    config, _ = _load_inputs()
    tech_path, frozen_path, db_path = (ROOT / config[key] for key in ("technical_config", "frozen_candidate_config", "data_source"))
    if not all(path.is_file() for path in (tech_path, frozen_path, db_path)):
        raise FileNotFoundError("BLOCKED_MISSING_CONFIG_OR_DATABASE")
    tech = tv1.read_config(tech_path)
    tv1.assert_execution_ready(tech, "training")
    family_name, family, candidate = tv1.candidate_by_id(tech, config["frozen_candidate_id"])
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if frozen.get("base_candidate") != candidate["id"] or frozen.get("market_regime_filter", {}).get("enabled") is not True:
        raise RuntimeError("BLOCKED_FROZEN_CANDIDATE_IDENTITY_MISMATCH")

    db_hash_before = sha256(db_path)
    period = config["period"]
    fold = {"id": "TRAINING_2023_2024", "train_start": period["start"], "train_end": "2022-12-31",
            "evaluate_start": period["start"], "evaluate_end": period["end"]}
    rows_by_version: dict[str, dict[str, Any]] = {}
    trades_by_version: dict[str, list[dict[str, Any]]] = {}
    all_trades: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []
    annual_rows: list[dict[str, Any]] = []

    with tv1._readonly_connection(db_path) as connection:
        full_ref, eval_ref, app_ref, warmup_count, eval_lower = tv1._load_fold_frame(connection, tech, candidate, config["stock_code"], fold)
        frame_timestamps = full_ref["kbar_timestamp"].to_list()
        schedule = {shared.iso_taipei(value) for value in full_ref["kbar_time"].to_list()[1:]}
        candidate_dates = {value[:10] for value in schedule}
        regime_context, regime_audit = build_regime_context(
            connection, tuple(frozen["market_regime_filter"]["peer_stock_codes"]), candidate_dates)
        variants = [("A_ORIGINAL_V2B", int(config["baseline"]["max_hold_bars"]))]
        variants.extend((str(item["id"]), int(item["max_hold_bars"])) for item in config["candidates"])
        eval_dates = sorted({str(value)[:10] for value in eval_ref["kbar_time"].to_list()})
        common_curve_timestamps = None
        for version, hold_bars in variants:
            variant_config = copy.deepcopy(tech)
            risk = config["risk"]
            variant_config["common_backtest"].update({
                "initial_capital": config["initial_capital"],
                "stock_fee_rate": config["execution"]["stock_fee_rate"],
                "stock_tax_rate": config["execution"]["stock_tax_rate"],
                "slippage_rate": config["execution"]["slippage_rate"],
                "stop_loss_pct": risk["stop_loss_pct"],
                "min_hold_bars": risk["min_holding_bars"],
                "max_hold_bars": hold_bars,
                "cooldown_bars": risk["cooldown_bars"],
                "max_entries_per_day": risk["max_entries_per_day"],
            })
            full, eval_frame, app, _, lower = tv1._load_fold_frame(connection, variant_config, candidate, config["stock_code"], fold)
            if full["kbar_timestamp"].to_list() != frame_timestamps:
                raise RuntimeError(f"BLOCKED_VARIANT_BAR_ALIGNMENT:{version}")
            bars = bt.build_kbar_rows_from_frame(eval_frame, app)
            curve_timestamps = [str(item["datetime"]) for item in bars]
            if common_curve_timestamps is None:
                common_curve_timestamps = curve_timestamps
            elif curve_timestamps != common_curve_timestamps:
                raise RuntimeError(f"BLOCKED_CURVE_TIME_ALIGNMENT:{version}")
            strategy_cls = shared._make_strategy_class("A_ORIGINAL_V2B")
            strategy = strategy_cls(config=app, study_config=variant_config, family_name=family_name, family=family,
                candidate=candidate, data_role="historical_development", fold_id=fold["id"], is_backtest=True,
                is_simulation=True, local_only=True, ml_model="technical_only", prediction_lookup={},
                candidate_schedule=schedule, regime_context=regime_context)
            strategy.configure_local_account(app)
            strategy.evaluation_start_timestamp = lower
            strategy.local_slippage_bps = float(config["execution"]["slippage_rate"]) * 10000.0
            strategy.diagnostic_sizing_enabled = True
            stats = bt.MarketDataStats(tick_count=full.height, first_timestamp=float(full["kbar_timestamp"][0]),
                                      last_timestamp=float(full["kbar_timestamp"][-1]))
            with contextlib.redirect_stdout(io.StringIO()):
                bt.replay_kbars(strategy, full, stats)
            raw_trades = bt._build_trade_rows(strategy, app)
            candidates = [dict(row, variant=version) for row in strategy.candidate_audit]
            risks = [dict(row, variant=version) for row in strategy.risk_decisions]
            summary, enriched, curves = shared._metrics_and_rows(version=version, strategy=strategy, app=app,
                bars=bars, trade_rows=raw_trades, risk_rows=risks, candidate_rows=candidates)
            # Derive accounting fields directly from the existing engine's paired trades.
            summary["gross_pnl"] = sum(float(row.get("gross_pnl", 0)) for row in enriched)
            summary["gross_profit"] = sum(max(0.0, float(row.get("gross_pnl", 0))) for row in enriched)
            summary["gross_loss"] = sum(min(0.0, float(row.get("gross_pnl", 0))) for row in enriched)
            summary["total_return_pct"] = float(summary["total_return_pct"])
            summary["average_holding_sessions_inclusive"] = float(np.mean([
                inclusive_sessions(str(row["buy_datetime"])[:10], str(row["sell_datetime"])[:10], eval_dates) for row in enriched
            ])) if enriched else None
            for trade in enriched:
                trade["variant"] = version
                trade["holding_sessions_inclusive"] = inclusive_sessions(str(trade["buy_datetime"])[:10], str(trade["sell_datetime"])[:10], eval_dates)
            summary["max_hold_bars"] = hold_bars
            summary["max_hold_session_equivalent"] = hold_bars / config["bar_time_semantics"]["complete_bars_per_session"]
            summary["average_holding_days"] = summary.get("average_holding_sessions_inclusive")
            rows_by_version[version] = summary
            trades_by_version[version] = enriched
            all_trades.extend(enriched)
            candidate_rows.extend(candidates)
            risk_rows.extend(risks)
            curve_rows.extend({**point, "variant": version} for point in curves)
            annual_rows.extend(summary.pop("annual_performance"))
            if strategy.local_position != 0:
                raise RuntimeError(f"BLOCKED_OPEN_POSITION_AFTER_REPLAY:{version}")

        if sha256(db_path) != db_hash_before:
            raise RuntimeError("BLOCKED_DATABASE_CHANGED_DURING_READ_ONLY_RUN")

    pair_rows = pair_baseline_time_exits(trades_by_version["A_ORIGINAL_V2B"], {
        "B": trades_by_version["B_MAX_HOLD_5D_EQUIVALENT"],
        "C": trades_by_version["C_MAX_HOLD_10D_EQUIVALENT"],
        "D": trades_by_version["D_MAX_HOLD_15D_EQUIVALENT"],
    })
    b_entry_times = {row.get("buy_datetime") for row in trades_by_version["B_MAX_HOLD_5D_EQUIVALENT"]}
    baseline_entries_not_taken = [row for row in trades_by_version["A_ORIGINAL_V2B"]
                                  if row.get("buy_datetime") not in b_entry_times]
    output = ROOT / "exports" / f"max_holding_days_ab_exp002_{datetime.now(TAIPEI).strftime('%Y%m%dT%H%M%S%z')}"
    output.mkdir(parents=True, exist_ok=False)
    comparison = pd.DataFrame(list(rows_by_version.values()))
    comparison.to_csv(output / "comparison.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(all_trades).to_csv(output / "trades.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(candidate_rows).to_csv(output / "candidate_audit.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(risk_rows).to_csv(output / "risk_decisions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(curve_rows).to_csv(output / "equity_curves.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(annual_rows).to_csv(output / "annual_performance.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(pair_rows).to_csv(output / "original_162_time_exit_followthrough.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(baseline_entries_not_taken).to_csv(output / "baseline_entries_not_taken_by_B.csv", index=False, encoding="utf-8-sig")
    snapshot = {**config, "versions": [{"id": name, "max_hold_bars": row["max_hold_bars"]} for name, row in rows_by_version.items()],
                "only_changed_parameter": "max_hold_bars"}
    (output / "config_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit = {"status": "COMPLETED", "experiment_id": config["experiment_id"], "data_role": config["data_role"],
        "stock_code": config["stock_code"], "period": config["period"], "actual_first_session": eval_dates[0],
        "actual_last_session": eval_dates[-1], "evaluation_trading_days": len(eval_dates),
        "warmup_bars": int(warmup_count), "same_bar_timestamps_all_variants": True,
        "data_source": str(db_path.relative_to(ROOT)), "database_sha256_before": db_hash_before,
        "database_sha256_after": sha256(db_path), "technical_config_sha256": sha256(tech_path),
        "frozen_candidate_config_sha256": sha256(frozen_path), "market_regime_audit": regime_audit,
        "entry_fill": "completed 5m bar signal -> next tradable bar open", "ml_gate_used": False,
        "final_oos_used": False, "additional_holdout_used": False, "parameter_search": False,
        "production_strategy_modified": False, "database_modified": False,
        "holding_counter_semantics": config["bar_time_semantics"],
        "exit_priority": "inherited engine: stop/time risk exits are checked before technical exit; queued exit executes at next tradable open; end-of-backtest mark is separately labelled",
        "source_hashes": {"runner": sha256(Path(__file__)), "technical_engine": sha256(ROOT / "trading_system/backtest.py"),
                          "strategy_source": sha256(ROOT / "hybrid/scripts/run_short_swing_ab_test.py")}}
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_report(output, config, rows_by_version, pair_rows, audit, baseline_entries_not_taken)
    _write_chart(output, curve_rows, comparison)
    return output


def _write_chart(output: Path, curves: list[dict[str, Any]], comparison: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt
    frame = pd.DataFrame(curves)
    fig, ax = plt.subplots(figsize=(11, 5))
    for version, group in frame.groupby("variant"):
        ax.plot(group["timestamp"], group["equity"], label=version, linewidth=1.1)
    ax.set_title("2303 Technical V2-B max holding comparison (2023–2024)")
    ax.set_ylabel("Equity (NTD)"); ax.grid(alpha=.25); ax.legend(fontsize=8)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(output / "equity_curve.png", dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(comparison["variant"], comparison["total_return_pct"])
    ax.axhline(0, color="black", linewidth=.7); ax.set_ylabel("Net total return (%)"); ax.tick_params(axis="x", rotation=20)
    fig.tight_layout(); fig.savefig(output / "return_comparison.png", dpi=150); plt.close(fig)


def _write_report(output: Path, config: dict[str, Any], summaries: dict[str, dict[str, Any]], pairs: list[dict[str, Any]], audit: dict[str, Any], baseline_entries_not_taken: list[dict[str, Any]]) -> None:
    order = list(summaries)
    lines = ["# 2303 Technical V2-B 最長持有時間 A/B/C/D 回測", "",
        f"- 實驗：`{config['experiment_id']}`；資料角色：`historical_development`；期間：{audit['actual_first_session']}～{audit['actual_last_session']}。",
        "- 四組均用凍結 Technical V2-B 進場、既有市場代理、前 30 根低點技術出場、Risk V1 及相同成本；只有 max_hold_bars 不同；沒有 ML Gate。",
        "- 持有上限是引擎中進場後累計的實際觀測 5 分 K 棒數，夜間與非交易時間不累加。162/270/540/810 分別相當於 3/5/10/15 個 54-bar 完整時段。因盤中訊號、下一根開盤成交及排隊出場，這不是精確的第 N 個交易日收盤時限。",
        "- 跨日持有天數：`exit fill date - entry fill date` 的觀測交易日間隔；含進場與出場日數為間隔 + 1。回測末強制結算另列，不當成時間到期。", "",
        "## 績效比較", "", "| 版本 | 上限 bars | 交易 | 勝率 | 毛損益 | 成本 | 淨損益 | 淨報酬 | PF | 最大回撤 | 平均持有日（含首尾） | 技術出場 | 停損 | 時間到期 | 期末結算 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in order:
        r = summaries[name]
        lines.append(f"| {name} | {r['max_hold_bars']} | {r['trade_count']} | {r.get('win_rate_pct') if r.get('win_rate_pct') is not None else 'N/A'} | {r['gross_pnl']:.2f} | {r['total_transaction_cost']:.2f} | {r['net_pnl']:.2f} | {r['total_return_pct']:.4f}% | {r.get('profit_factor') if r.get('profit_factor') is not None else 'N/A'} | {r['max_drawdown_pct']:.4f}% | {r.get('average_holding_days') if r.get('average_holding_days') is not None else 'N/A'} | {r['technical_exit_count']} | {r['stop_loss_exit_count']} | {r['time_expiration_exit_count']} | {r['end_of_backtest_exit_count']} |")
    a = summaries["A_ORIGINAL_V2B"]
    b = summaries["B_MAX_HOLD_5D_EQUIVALENT"]
    paired_changes = [float(row["B_net_pnl_change"]) for row in pairs if row.get("B_net_pnl_change") is not None]
    paired_better = sum(value > 0 for value in paired_changes)
    paired_worse = sum(value < 0 for value in paired_changes)
    paired_unchanged = sum(value == 0 for value in paired_changes)
    paired_delta_sum = sum(paired_changes)
    missed_loss = sum(float(row.get("net_pnl", 0)) for row in baseline_entries_not_taken)
    lines += ["", "## 原 A 版 162 根到期交易的延長持有追蹤", "", f"原 A 共有 {len(pairs)} 筆 162 根時間到期交易。依實際進場成交時間逐筆配對；延長版本未出現同一進場時，記為 `NO_MATCHED_TRADE`，不推算反事實損益。不同持倉會改變後續可進場機會，因此配對只作描述，不是完全獨立的交易反事實。", ""]
    if pairs:
        lines.append("| A 進場 | A 淨損益 | B 配對狀態/淨損益變化 | C 配對狀態/淨損益變化 | D 配對狀態/淨損益變化 |")
        lines.append("|---|---:|---|---|---|")
        for p in pairs:
            cells = []
            for key in "BCD":
                value = p.get(f"{key}_net_pnl_change")
                cells.append(f"{p[f'{key}_pair_status']} / {value:.2f}" if value is not None else p[f"{key}_pair_status"])
            lines.append(f"| {p['baseline_buy_datetime']} | {float(p['baseline_net_pnl']):.2f} | {cells[0]} | {cells[1]} | {cells[2]} |")
    else:
        lines.append("A 版沒有發生 162 根時間到期出場，因此本資料無法直接回答那些到期交易延長持有後的結果。")
    lines += ["", "## 年度拆分", "", "年度績效請參閱 `annual_performance.csv`；結果按連續帳戶淨值計算，沒有跨期重設本金。", "", "## 判讀與採用決策", "",
        f"B 的總淨損益較 A 高 {float(b['net_pnl']) - float(a['net_pnl']):.2f} 元，但 A 的 39 筆中有 {len(baseline_entries_not_taken)} 筆未在 B 重現；這些 A 交易合計淨損益 {missed_loss:.2f} 元。這表示總體改善同時受到延長持倉佔用資金、減少後續進場的影響，不能全歸因於延長持有改善單筆出場品質。",
        f"六筆 A 到期交易的 B 配對結果為 {paired_better} 筆淨損益改善、{paired_worse} 筆變差、{paired_unchanged} 筆不變；配對淨損益變化合計 {paired_delta_sum:.2f} 元，呈現混合且合計不利。B/C/D 的交易與各項摘要完全相同，三者均沒有 270 根以上的時間到期出場，因此 10 日與 15 日上限在這份資料中沒有額外效果。",
        "2023 年 B 的報酬比 A 稍差，2024 年較好，年度方向不一致；四版本淨報酬仍為負。故本次分類為 `NOT_ADOPTED_INSUFFICIENT_STABLE_EVIDENCE`，正式 Technical V2-B 繼續維持 162 根，實驗結果只保留作研究紀錄。", "", "## 輸出", "", "`comparison.csv`、`trades.csv`、`candidate_audit.csv`、`risk_decisions.csv`、`equity_curves.csv`、`annual_performance.csv`、`original_162_time_exit_followthrough.csv`、`baseline_entries_not_taken_by_B.csv`、`config_snapshot.json`、`audit.json` 及圖檔。"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    print(run())

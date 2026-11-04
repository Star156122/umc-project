"""Diagnose existing short-swing A–E trades using 2023–2024 bars only.

This is a read-only analysis of an already completed development backtest. It
does not execute strategies, adjust parameters, or open protected data periods.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = "exports/short_swing_ab_test_20261104T123811"
DB_PATH = ROOT / "data/technical_v1_mltech_clean.sqlite3"
TAIPEI = ZoneInfo("Asia/Taipei")
VARIANTS = ["A_ORIGINAL_V2B", "B_NO_BOLLINGER", "C_NO_MA120", "D_EXIT55", "E_EXIT80"]
START, END = "2023-01-01", "2024-12-31"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def taipei_epoch(value: str) -> int:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(TAIPEI)
    else:
        ts = ts.tz_convert(TAIPEI)
    return int(ts.timestamp())


def load_bars() -> pd.DataFrame:
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        rows = pd.read_sql_query(
            "SELECT kbar_timestamp, range_start, range_end, open, high, low, close, volume "
            "FROM market_kbars WHERE stock_code=? AND freq_minutes=? "
            "AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp",
            con, params=("2303", 5, taipei_epoch(START), taipei_epoch("2025-01-01")),
        )
    if rows.empty:
        raise RuntimeError("BLOCKED_NO_2303_TRAINING_BARS")
    rows["timestamp"] = pd.to_datetime(rows.kbar_timestamp, unit="s", utc=True).dt.tz_convert(TAIPEI)
    rows["session"] = rows.timestamp.dt.strftime("%Y-%m-%d")
    rows = rows[(rows.session >= START) & (rows.session <= END)].copy().reset_index(drop=True)
    rows["prior55_high"] = rows.high.shift(1).rolling(55, min_periods=55).max()
    if rows.kbar_timestamp.duplicated().any() or not rows.kbar_timestamp.is_monotonic_increasing:
        raise RuntimeError("BLOCKED_DUPLICATE_OR_OUT_OF_ORDER_BAR")
    if (rows.high < rows.low).any() or (rows.low <= 0).any() or (rows.close <= 0).any():
        raise RuntimeError("BLOCKED_INVALID_OHLC")
    return rows


def summarize_num(series: pd.Series) -> dict:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if not len(s):
        return {k: None for k in ("count", "mean", "median", "std", "q10", "q25", "q75", "q90", "min", "max")}
    return {"count": int(len(s)), "mean": float(s.mean()), "median": float(s.median()),
            "std": float(s.std(ddof=1)) if len(s) > 1 else None,
            "q10": float(s.quantile(.10)), "q25": float(s.quantile(.25)),
            "q75": float(s.quantile(.75)), "q90": float(s.quantile(.90)),
            "min": float(s.min()), "max": float(s.max())}


def attach_trade_diagnostics(trades: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    by_ts = bars.set_index("kbar_timestamp", drop=False)
    sessions = sorted(bars.session.unique())
    session_last = bars.groupby("session", sort=True).tail(1).set_index("session")
    session_order = {d: i for i, d in enumerate(sessions)}
    out = []
    for row in trades.to_dict("records"):
        entry_ts, exit_ts = taipei_epoch(row["buy_datetime"]), taipei_epoch(row["sell_datetime"])
        if entry_ts not in by_ts.index or exit_ts not in by_ts.index:
            raise RuntimeError(f"BLOCKED_TRADE_TIMESTAMP_NOT_IN_SOURCE_BARS:{row['variant']}:{row['buy_datetime']}:{row['sell_datetime']}")
        entry_bar, exit_bar = by_ts.loc[entry_ts], by_ts.loc[exit_ts]
        if isinstance(entry_bar, pd.DataFrame) or isinstance(exit_bar, pd.DataFrame):
            raise RuntimeError("BLOCKED_AMBIGUOUS_TRADE_TIMESTAMP")
        entry_price, exit_price = float(row["buy_price"]), float(row["sell_price"])
        entry_open_match = abs(entry_price - float(entry_bar.open)) < 1e-7
        exit_type = str(row.get("exit_type", "")).lower()
        exit_bar_is_execution_bar = exit_type in {"stop_loss", "stop_gap_open", "stop_intrabar"}
        held = bars[(bars.kbar_timestamp >= entry_ts) & (bars.kbar_timestamp < exit_ts)].copy()
        if held.empty:
            held = pd.DataFrame([entry_bar])
        excursion_high = float(held.high.max())
        excursion_low = float(held.low.min())
        # At a stop bar, OHLC cannot order high vs low. Exclude the execution
        # bar's full range and include only the observed stop fill in MAE.
        if exit_bar_is_execution_bar:
            excursion_low = min(excursion_low, exit_price)
        mfe = (excursion_high / entry_price - 1.0) * 100.0
        mae = (excursion_low / entry_price - 1.0) * 100.0
        prior = bars[bars.kbar_timestamp < exit_ts]
        prev_close = float(prior.iloc[-1].close) if len(prior) else np.nan
        stop_price = entry_price * .975
        is_stop_exit = "stop" in exit_type or "stop" in str(row.get("exit_reason", "")).lower()
        stop_gap = is_stop_exit and float(exit_bar.open) <= stop_price + 1e-7 and abs(exit_price - float(exit_bar.open)) < 1e-7
        exit_open_match = abs(exit_price - float(exit_bar.open)) < 1e-7
        if is_stop_exit:
            expected = float(exit_bar.open) if float(exit_bar.open) <= stop_price else stop_price
            exit_price_match = abs(exit_price - expected) < 1e-7
        else:
            exit_price_match = exit_open_match
        row.update({
            "entry_timestamp_epoch": entry_ts, "exit_timestamp_epoch": exit_ts,
            "entry_bar_open": float(entry_bar.open), "entry_bar_close": float(entry_bar.close),
            "entry_open_price_match": entry_open_match,
            "exit_bar_open": float(exit_bar.open), "exit_bar_low": float(exit_bar.low),
            "previous_close_before_exit": prev_close, "exit_open_price_match": exit_open_match,
            "exit_fill_rule_price_match": exit_price_match, "stop_gap_exit": bool(stop_gap),
            "mfe_pct_gross_price_excursion": mfe, "mae_pct_gross_price_excursion": mae,
            "mfe_minus_mae_pct": mfe - abs(mae),
            "gross_return_pct_on_entry_notional": float(row["gross_pnl"]) / float(row["buy_amount"]) * 100.0,
            "fee_pct_on_entry_notional": float(row["fee"]) / float(row["buy_amount"]) * 100.0,
            "tax_pct_on_entry_notional": float(row["tax"]) / float(row["buy_amount"]) * 100.0,
            "net_return_pct_on_entry_notional": float(row["net_pnl"]) / float(row["buy_amount"]) * 100.0,
            "entry_session": str(entry_bar.session), "exit_session": str(exit_bar.session),
        })
        eidx = session_order[str(entry_bar.session)]
        for horizon in (1, 2, 3):
            hidx = eidx + horizon
            if hidx < len(sessions):
                hdate = sessions[hidx]
                hclose = float(session_last.loc[hdate, "close"])
                hts = int(session_last.loc[hdate, "kbar_timestamp"])
                ret = (hclose / entry_price - 1.0) * 100.0
                row[f"day{horizon}_date"] = hdate
                row[f"day{horizon}_close_return_pct"] = ret
                row[f"day{horizon}_still_holding"] = bool(exit_ts > hts)
                row[f"day{horizon}_close_timestamp_epoch"] = hts
            else:
                row[f"day{horizon}_date"] = None
                row[f"day{horizon}_close_return_pct"] = None
                row[f"day{horizon}_still_holding"] = None
                row[f"day{horizon}_close_timestamp_epoch"] = None
        # Stop exits are recorded at the stop bar's timestamp; technical/time
        # exits execute at next tradable bar open. The source audit documents it.
        row["exit_reason_category"] = (
            "fixed_stop" if "stop" in exit_type else
            "technical_exit" if "technical" in exit_type else
            "time_expiration" if "hold" in exit_type or "time" in exit_type else
            "other"
        )
        row["bars_in_position_from_record"] = int(row["holding_bars"])
        row["cross_day_exit"] = str(entry_bar.session) != str(exit_bar.session)
        row["exit_open_gap_pct_vs_previous_close"] = ((exit_price / prev_close - 1.0) * 100.0) if pd.notna(prev_close) and prev_close else None
        out.append(row)
    return pd.DataFrame(out)


def candidate_outcomes(candidates: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    by_ts = bars.set_index("kbar_timestamp", drop=False)
    sessions = sorted(bars.session.unique())
    session_order = {d: i for i, d in enumerate(sessions)}
    session_closes = bars.groupby("session", sort=True).tail(1).set_index("session")
    out = []
    for row in candidates.to_dict("records"):
        sig_ts = int(row["signal_bar_timestamp"])
        if sig_ts not in by_ts.index:
            raise RuntimeError(f"BLOCKED_CANDIDATE_TIMESTAMP_NOT_IN_SOURCE_BARS:{sig_ts}")
        sig = by_ts.loc[sig_ts]
        entry_ts = taipei_epoch(row["earliest_entry_time"])
        if entry_ts not in by_ts.index:
            raise RuntimeError(f"BLOCKED_NEXT_BAR_MISSING:{row['earliest_entry_time']}")
        entry = by_ts.loc[entry_ts]
        prior_high = float(sig.prior55_high) if pd.notna(sig.prior55_high) else None
        item = dict(row)
        item["prior_55_bar_high"] = prior_high
        item["entry_open"] = float(entry.open)
        item["entry_open_matches_timestamp"] = True
        sidx = session_order[str(entry.session)]
        returns = []
        breach_day = None
        for horizon in (1, 2, 3):
            idx = sidx + horizon
            if idx < len(sessions):
                day = sessions[idx]
                close = float(session_closes.loc[day, "close"])
                ret = (close / float(entry.open) - 1.0) * 100.0
                item[f"day{horizon}_date"] = day
                item[f"day{horizon}_close_return_pct"] = ret
                item[f"day{horizon}_close"] = close
                returns.append(ret)
                if breach_day is None and prior_high is not None and close <= prior_high:
                    breach_day = horizon
            else:
                item[f"day{horizon}_date"] = None
                item[f"day{horizon}_close_return_pct"] = None
                item[f"day{horizon}_close"] = None
        complete = len(returns) == 3
        item["three_following_sessions_complete"] = complete
        item["post_entry_3d_max_close_return_pct"] = max(returns) if returns else None
        item["false_breakout_within_3_following_sessions"] = bool(complete and breach_day is not None)
        item["false_breakout_breach_day"] = breach_day if complete else None
        out.append(item)
    return pd.DataFrame(out)


def grouped_exit(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (trades.groupby(["variant", "exit_reason_category"], dropna=False)
            .agg(trade_count=("net_pnl", "size"), wins=("net_pnl", lambda s: int((s > 0).sum())),
                 gross_pnl=("gross_pnl", "sum"), fees=("fee", "sum"), tax=("tax", "sum"),
                 total_cost=("total_cost", "sum"), net_pnl=("net_pnl", "sum"),
                 mean_net_pnl=("net_pnl", "mean"), mean_mfe_pct=("mfe_pct_gross_price_excursion", "mean"),
                 mean_mae_pct=("mae_pct_gross_price_excursion", "mean"))
            .reset_index())


def quality_summary(trades: pd.DataFrame, candidates: pd.DataFrame, outcomes: pd.DataFrame, backtest_comparison: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for variant in VARIANTS:
        t = trades[trades.variant == variant]
        c = candidates[candidates.variant == variant]
        gross = pd.to_numeric(t.gross_pnl).sum()
        cost = pd.to_numeric(t.total_cost).sum()
        net = pd.to_numeric(t.net_pnl).sum()
        wins = pd.to_numeric(t.net_pnl)
        pos, neg = wins[wins > 0], wins[wins < 0]
        cpost = c[c.candidate_after_regime.astype(str).str.lower() == "true"]
        outcome = outcomes[outcomes.variant == variant]
        technical_outcome = outcome[outcome.technical_candidate.astype(str).str.lower() == "true"]
        completed_raw = outcome[outcome.three_following_sessions_complete.astype(bool)]
        completed_technical = technical_outcome[technical_outcome.three_following_sessions_complete.astype(bool)]
        completed_market = outcome[(outcome.candidate_after_regime.astype(str).str.lower() == "true") & outcome.three_following_sessions_complete.astype(bool)]
        comparison_row = backtest_comparison.loc[backtest_comparison.variant == variant].iloc[0]
        entry_counts = cpost.risk_and_execution_result.value_counts().to_dict()
        rows.append({
            "variant": variant, "raw_breakout_triggers": int(len(c)),
            "technical_candidates": int((c.technical_candidate.astype(str).str.lower() == "true").sum()),
            "post_market_filter_candidates": int(len(cpost)), "actual_trades": int(len(t)),
            "candidate_to_trade_conversion_pct": float(len(t) / len(cpost) * 100) if len(cpost) else None,
            "candidate_gate_outcome_counts_json": json.dumps({str(k): int(v) for k, v in entry_counts.items()}, ensure_ascii=False),
            "max_drawdown_pct": float(comparison_row.max_drawdown_pct),
            "sharpe_ratio": float(comparison_row.sharpe_ratio),
            "average_holding_trading_day_intervals": float(comparison_row.average_holding_trading_day_intervals),
            "gross_pnl": float(gross), "fees": float(pd.to_numeric(t.fee).sum()),
            "tax": float(pd.to_numeric(t.tax).sum()), "total_cost": float(cost), "net_pnl": float(net),
            "gross_return_pct_on_initial_capital": float(gross / 1_000_000 * 100),
            "net_return_pct_on_initial_capital": float(net / 1_000_000 * 100),
            "cost_as_pct_of_positive_gross_pnl": float(cost / gross * 100) if gross > 0 else None,
            "win_rate_pct": float((wins > 0).mean() * 100) if len(wins) else None,
            "avg_winning_trade_net_pnl": float(pos.mean()) if len(pos) else None,
            "avg_losing_trade_net_pnl": float(neg.mean()) if len(neg) else None,
            "payoff_ratio_net": float(pos.mean() / abs(neg.mean())) if len(pos) and len(neg) else None,
            "profit_factor_net": float(pos.sum() / abs(neg.sum())) if len(pos) and len(neg) and neg.sum() else None,
            "expectancy_net_pnl_per_trade": float(wins.mean()) if len(wins) else None,
            "mfe_pct_summary_json": json.dumps(summarize_num(t.mfe_pct_gross_price_excursion), ensure_ascii=False),
            "mae_pct_summary_json": json.dumps(summarize_num(t.mae_pct_gross_price_excursion), ensure_ascii=False),
            "raw_breakout_3session_n": int(len(completed_raw)),
            "raw_breakout_3session_false_count": int(completed_raw.false_breakout_within_3_following_sessions.sum()) if len(completed_raw) else 0,
            "raw_breakout_3session_false_rate_pct": float(completed_raw.false_breakout_within_3_following_sessions.mean() * 100) if len(completed_raw) else None,
            "technical_candidate_3session_n": int(len(completed_technical)),
            "technical_candidate_3session_false_count": int(completed_technical.false_breakout_within_3_following_sessions.sum()) if len(completed_technical) else 0,
            "technical_candidate_3session_false_rate_pct": float(completed_technical.false_breakout_within_3_following_sessions.mean() * 100) if len(completed_technical) else None,
            "post_market_candidate_3session_n": int(len(completed_market)),
            "post_market_candidate_3session_false_count": int(completed_market.false_breakout_within_3_following_sessions.sum()) if len(completed_market) else 0,
            "post_market_candidate_3session_false_rate_pct": float(completed_market.false_breakout_within_3_following_sessions.mean() * 100) if len(completed_market) else None,
        })
    return pd.DataFrame(rows)


def horizon_summary(outcomes: pd.DataFrame, trade_diags: pd.DataFrame) -> pd.DataFrame:
    rows = []
    cohort_types = [
        ("all_post_market_candidates", outcomes[outcomes.candidate_after_regime.astype(str).str.lower() == "true"]),
        ("actual_filled_trades_fixed_horizon", trade_diags),
    ]
    for cohort_name, df in cohort_types:
        for variant in VARIANTS:
            subset = df[df.variant == variant]
            for day in (1, 2, 3):
                col = f"day{day}_close_return_pct"
                valid = pd.to_numeric(subset[col], errors="coerce").dropna()
                held_col = f"day{day}_still_holding"
                is_actual = cohort_name.startswith("actual")
                still = subset[subset.get(held_col, pd.Series(index=subset.index, dtype=object)).astype(str).str.lower() == "true"] if is_actual else pd.DataFrame()
                held_values = pd.to_numeric(still[col], errors="coerce").dropna() if len(still) else pd.Series(dtype=float)
                rows.append({
                    "cohort": cohort_name, "variant": variant, "following_trading_day": day,
                    "n_fixed_horizon": int(len(valid)), "mean_close_return_pct": float(valid.mean()) if len(valid) else None,
                    "median_close_return_pct": float(valid.median()) if len(valid) else None,
                    "positive_close_return_pct": float((valid > 0).mean() * 100) if len(valid) else None,
                    "n_still_holding_at_day_close": int(len(held_values)) if is_actual else None,
                    "mean_return_still_holding_pct": float(held_values.mean()) if len(held_values) else None,
                    "median_return_still_holding_pct": float(held_values.median()) if len(held_values) else None,
                })
    return pd.DataFrame(rows)


def make_charts(out: Path, trades: pd.DataFrame, outcomes: pd.DataFrame, exit_summary: pd.DataFrame, quality: pd.DataFrame, horizons: pd.DataFrame) -> None:
    chart = out / "charts"
    chart.mkdir(parents=True, exist_ok=True)
    colors = ["#1479b8", "#ef8a17", "#55a868", "#c44e52", "#8172b2"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    data_mfe = [trades.loc[trades.variant == v, "mfe_pct_gross_price_excursion"].dropna() for v in VARIANTS]
    data_mae = [trades.loc[trades.variant == v, "mae_pct_gross_price_excursion"].dropna() for v in VARIANTS]
    axes[0].boxplot(data_mfe, tick_labels=["A", "B", "C", "D", "E"], showfliers=False)
    axes[0].set_title("MFE by variant"); axes[0].set_ylabel("% from entry (gross price excursion)"); axes[0].grid(alpha=.2)
    axes[1].boxplot(data_mae, tick_labels=["A", "B", "C", "D", "E"], showfliers=False)
    axes[1].set_title("MAE by variant"); axes[1].set_ylabel("% from entry (gross price excursion)"); axes[1].grid(alpha=.2)
    fig.suptitle("Trade excursion distribution (execution bar ambiguity handled conservatively)")
    fig.tight_layout(); fig.savefig(chart / "mfe_mae_distribution.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    allc = outcomes[outcomes.candidate_after_regime.astype(str).str.lower() == "true"]
    for v, color in zip(VARIANTS, colors):
        g = allc[allc.variant == v]
        means = [pd.to_numeric(g[f"day{d}_close_return_pct"], errors="coerce").mean() for d in (1, 2, 3)]
        ax.plot([1, 2, 3], means, marker="o", label=f"{v} candidates", color=color, alpha=.48, linestyle="--")
        tg = trades[(trades.variant == v)]
        heldmeans = []
        for d in (1, 2, 3):
            mask = tg[f"day{d}_still_holding"].astype(str).str.lower() == "true"
            heldmeans.append(pd.to_numeric(tg.loc[mask, f"day{d}_close_return_pct"], errors="coerce").mean())
        ax.plot([1, 2, 3], heldmeans, marker="s", label=f"{v} actual still-held", color=color)
    ax.axhline(0, color="black", linewidth=.8); ax.set_xticks([1, 2, 3]); ax.set_xlabel("Completed trading sessions after entry session")
    ax.set_ylabel("Mean close return from next-bar entry open (%)"); ax.set_title("Post-entry price path: candidates vs positions still held")
    ax.grid(alpha=.2); ax.legend(fontsize=7, ncol=2); fig.tight_layout(); fig.savefig(chart / "entry_horizon_returns.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 5))
    piv = exit_summary.pivot(index="variant", columns="exit_reason_category", values="trade_count").fillna(0).reindex(VARIANTS)
    piv.plot(kind="bar", stacked=True, ax=ax, color=["#c44e52", "#4c72b0", "#dd8452", "#999999"][:len(piv.columns)])
    ax.set_title("Exit reason: trade count by variant"); ax.set_ylabel("Completed trades"); ax.set_xlabel(""); ax.grid(axis="y", alpha=.2)
    fig.tight_layout(); fig.savefig(chart / "exit_reason_counts.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 5))
    pnl_piv = exit_summary.pivot(index="variant", columns="exit_reason_category", values="net_pnl").fillna(0).reindex(VARIANTS)
    pnl_piv.plot(kind="bar", ax=ax)
    ax.axhline(0, color="black", linewidth=.8); ax.set_title("Net PnL by exit reason and variant"); ax.set_ylabel("NTD"); ax.set_xlabel("")
    ax.grid(axis="y", alpha=.2); ax.legend(title="Exit reason")
    fig.tight_layout(); fig.savefig(chart / "exit_reason_net_pnl.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(VARIANTS)); width = .24
    q = quality.set_index("variant").reindex(VARIANTS)
    ax.bar(x-width, q.gross_pnl, width, label="Gross PnL")
    ax.bar(x, -q.total_cost, width, label="Trading costs (shown negative)")
    ax.bar(x+width, q.net_pnl, width, label="Net PnL")
    ax.axhline(0, color="black", linewidth=.8); ax.set_xticks(x, ["A", "B", "C", "D", "E"])
    ax.set_ylabel("NTD"); ax.set_title("Gross PnL, fees/tax and net PnL"); ax.legend(); ax.grid(axis="y", alpha=.2)
    fig.tight_layout(); fig.savefig(chart / "cost_before_after.png", dpi=160); plt.close(fig)


def write_report(out: Path, trades: pd.DataFrame, quality: pd.DataFrame, exits: pd.DataFrame, horizons: pd.DataFrame,
                 candidates: pd.DataFrame, outcomes: pd.DataFrame, bars: pd.DataFrame, source_run: Path, source_audit: dict) -> None:
    a = quality.set_index("variant").loc["A_ORIGINAL_V2B"]
    a_trades = trades[trades.variant == "A_ORIGINAL_V2B"]
    a_exit = exits[exits.variant == "A_ORIGINAL_V2B"]
    def p(x): return "N/A" if pd.isna(x) else f"{float(x):.3f}%"
    def n(x): return "N/A" if pd.isna(x) else f"{float(x):,.2f}"
    lines = [
        "# Technical V2-B 虧損原因與交易品質診斷", "",
        f"- 核心標的：2303 聯電；資料角色：`historical_development`（2023–2024 已曝光 Training）；資料實際日期 {bars.session.min()}～{bars.session.max()}，共 {bars.session.nunique()} 個交易日、{len(bars):,} 根 5 分 K。",
        f"- 來源回測：`{source_run.as_posix()}`；A–E 共 {len(trades)} 筆已完成交易；本報告是對既有輸出的事後診斷，不是新策略評估或 Final OOS。",
        f"- 價格資料：唯讀 `{DB_PATH.relative_to(ROOT).as_posix()}`；SHA256 `{sha256(DB_PATH)}`。不得據此推論其他股票或未曝光期間。",
        "- MFE/MAE 口徑：以進場成交價為基準，計算持倉期間完整 K 棒 High/Low 的最大有利／不利百分比。出場停損 K 棒的整根區間不納入，MAE 只加入實際停損成交價，避免 OHLC 無法判斷同根高低點先後而高估有利波動。屬毛價格路徑，不扣成本。",
        "- 固定期間價格研究：每筆候選／成交以實際下一根 K 開盤作為價格起點，計算進場交易日之後第 1、2、3 個交易日收盤報酬；另對實際成交交易標出該日收盤仍持倉的子集合。已提前出場的交易仍保留在固定期間價格研究，用來看假設持有路徑，不代表策略實際持倉收益。",
        "- 假突破事前診斷定義：對已通過 Technical 與市場濾網的候選，以訊號時點前 55 根已完成 K 棒最高價為突破線；若進場後三個完整後續交易日中，任一日收盤回到突破線或以下，記為假突破。須有完整三日資料才進分母；此為描述性定義，不是新的交易規則。",
        "- 成交與偏誤檢查：沿用原回測記錄及程式的 completed K signal → next tradable bar open；停損以 OHLC 模擬，開盤低於停損價用開盤價，否則盤中 Low 觸及用停損價。滑價為 0。5 分 K OHLC 不能確認停損觸及前同根是否先到過更高價；這項路徑順序不確定性保留為限制。",
        "", "## A 原始 V2-B：39 筆逐筆交易", "",
        f"A 合計毛損益 {n(a.gross_pnl)} 元，手續費 {n(a.fees)} 元，證交稅 {n(a.tax)} 元，成本共 {n(a.total_cost)} 元，成本後淨損益 {n(a.net_pnl)} 元。毛損益為正但不足以支付成本，成本占正毛損益 {p(a.cost_as_pct_of_positive_gross_pnl)}；因此交易成本是 A 由毛利轉為淨虧的直接原因，但毛利緩衝很薄，不能據此宣稱已有穩健優勢。",
        f"勝率 {p(a.win_rate_pct)}；平均獲利 {n(a.avg_winning_trade_net_pnl)} 元、平均虧損 {n(a.avg_losing_trade_net_pnl)} 元；賺賠比 {n(a.payoff_ratio_net)}；Profit Factor {n(a.profit_factor_net)}；平均每筆期望值 {n(a.expectancy_net_pnl_per_trade)} 元。",
        f"A 的 MFE 分布：{json.dumps(json.loads(a.mfe_pct_summary_json), ensure_ascii=False)}；MAE 分布：{json.dumps(json.loads(a.mae_pct_summary_json), ensure_ascii=False)}。逐筆診斷見 `trade_diagnostics.csv`。",
        "", "### A 出場原因", "", "| 出場原因 | 筆數 | 毛損益 | 手續費 | 證交稅 | 淨損益 |", "|---|---:|---:|---:|---:|---:|",
    ]
    for r in a_exit.to_dict("records"):
        lines.append(f"| {r['exit_reason_category']} | {int(r['trade_count'])} | {n(r['gross_pnl'])} | {n(r['fees'])} | {n(r['tax'])} | {n(r['net_pnl'])} |")
    lines += ["", "## A–E 比較", "", "| 版本 | 技術候選 | 市場濾網後 | 成交 | 毛損益 | 成本 | 淨損益 | 勝率 | PF | 最大回撤 | 平均持有日 | 3日假突破率（濾網後） |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in quality.to_dict("records"):
        lines.append(f"| {r['variant']} | {r['technical_candidates']} | {r['post_market_filter_candidates']} | {r['actual_trades']} | {n(r['gross_pnl'])} | {n(r['total_cost'])} | {n(r['net_pnl'])} | {p(r['win_rate_pct'])} | {n(r['profit_factor_net'])} | {p(r['max_drawdown_pct'])} | {n(r['average_holding_trading_day_intervals'])} | {p(r['post_market_candidate_3session_false_rate_pct'])} ({r['post_market_candidate_3session_false_count']}/{r['post_market_candidate_3session_n']}) |")
    a_q = quality.loc[quality.variant == "A_ORIGINAL_V2B"].iloc[0]
    lines += [f"", f"對 A 的 55 根突破觸發，三日假突破為 {int(a_q.raw_breakout_3session_false_count)}/{int(a_q.raw_breakout_3session_n)}（{p(a_q.raw_breakout_3session_false_rate_pct)}）；限縮到 A 技術候選後為 {int(a_q.technical_candidate_3session_false_count)}/{int(a_q.technical_candidate_3session_n)}（{p(a_q.technical_candidate_3session_false_rate_pct)}）。市場濾網後的比例見表。",
              "", "A 的 B–E 對照顯示，移除條件能增加候選與成交，但需同時看每筆平均淨損益、毛損益是否足以覆蓋成本、最大回撤及出場分布；交易數增加本身不代表候選品質提高。完整數值在 `variant_quality.csv`、`candidate_outcomes.csv`、`exit_reason_summary.csv`。",
              "", "## 進場後第 1～3 個交易日", "",
              "`entry_horizon_summary.csv` 分開列出全部通過市場濾網候選的固定持有價格路徑，以及已成交交易的固定期間價格路徑與當日收盤仍持倉子集合。這些固定持有報酬是事後診斷，不是策略實際報酬；部分候選／交易因落在期間尾端而沒有完整第 1～3 日資料，會以較少樣本數呈現，不補值。",
              "", "## 突破失敗與候選轉換", "",
              "假突破採上方定義。`candidate_outcomes.csv` 對原始突破事件、各版本 Technical 候選與市場濾網後候選保留逐筆結果；`variant_quality.csv` 列出市場濾網後候選的三日假突破率。這是單一股票、單一已曝光期間的條件式統計，不能視為一般假突破機率。",
              "", "## 回測執行核對", "",
              f"- 成交紀錄中的買進價與該 timestamp 的來源 K 棒 Open 相符：{int(trades.entry_open_price_match.sum())}/{len(trades)}。",
              f"- 成交交易與同版本 candidate_audit 的 FILLED 訊號／下一根開盤時間相符：{int(trades.matched_filled_candidate.sum())}/{len(trades)}。",
              f"- 出場成交規則價格與來源 K 棒／停損公式相符：{int(trades.exit_fill_rule_price_match.sum())}/{len(trades)}。",
              f"- A 停損交易：{int((a_trades.exit_reason_category == 'fixed_stop').sum())} 筆；其中開盤跳空越過停損：{int(a_trades.stop_gap_exit.sum())} 筆。",
              "- 未完成 K 棒：回測引擎的 `process_completed_kbar` 由已封閉 K 棒事件呼叫；訊號建立後使用下一根可成交開盤。指標突破高點與出場低點由 rolling 計算後 shift(1)，不含當前 K 棒。研究輸出 audit 也記錄此檢查通過。",
              "- 跨日跳空：所有出場成交價均可按逐筆記錄與 OHLC／停損規則對上；一般技術／到期出場於下一根可交易開盤，停損缺口使用該根開盤價，盤中觸發使用停損價。資料是 5 分 K，無法驗證 K 棒內更細的實際成交順序；滑價 0 也可能低估真實交易摩擦。",
              "", "## 結論：主要虧損來源", "",
              "就 A 而言，成本是淨虧損的直接轉折因素：成本前毛損益小幅為正，但手續費與證交稅合計高於毛利，最後淨損。這反映進場／出場形成的毛利優勢不足以承受實際成本。B–E 對照中若毛損益已為負，代表不能只靠降低成本解釋，需同時檢視訊號品質、持有期間及出場方式。",
              "因此不宜簡化成「全是買點錯」或「全是出場太早」：本次數據對 A 支持的是「訊號組合只有微弱毛利，成本侵蝕使其轉負」；B–E 放寬／延長後的交易品質若未提升，也說明單純增加訊號或延長持有並未建立穩定優勢。單一股票、422 個交易日的結果不足以確認策略長期沒有優勢，還需跨股或未曝光資料才能判斷泛化。",
              "", "## 輸出檔案", "",
              "- `trade_diagnostics.csv`：A–E 逐筆交易、成本、MFE/MAE、第 1～3 日價格變動、出場類別與成交核對。",
              "- `variant_quality.csv`：A–E 交易品質、毛利／成本／淨利與候選轉換總表。",
              "- `candidate_outcomes.csv`：原始 55 根突破事件、版本候選、下一根開盤及三日假突破判定。",
              "- `entry_horizon_summary.csv`：候選與實際成交固定期間、仍持倉子集合統計。",
              "- `exit_reason_summary.csv`：出場原因筆數及毛利、成本、淨損益。",
              "- `audit.json`：來源、期間、資料 hash、成交與規則稽核。",
              "- `charts/`：MFE/MAE、進場後價格路徑、出場原因筆數／淨損益與成本前後圖。",
              "", "**研究限制：** 不使用 Final OOS；沒有修改策略或參數；沒有重新執行回測；MFE/MAE與固定持有期分析為 5 分 K OHLC 的近似；回測單股部位仍受每筆最多 1,000 股限制。此報告只用來提出後續假設，不足以宣稱任何版本已改善或具備可交易優勢。"]
    (out / "diagnosis_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", default=DEFAULT_RUN)
    args = parser.parse_args()
    source = ROOT / args.source_run
    required = ["trades.csv", "candidate_audit.csv", "audit.json", "comparison.csv"]
    if not all((source / name).is_file() for name in required):
        raise FileNotFoundError("BLOCKED_MISSING_SOURCE_RUN_FILES")
    source_audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    if source_audit.get("stock_code") != "2303" or source_audit.get("data_role") != "historical_development" or source_audit.get("final_oos_used") is not False:
        raise RuntimeError("BLOCKED_SOURCE_RUN_ROLE_OR_STOCK_MISMATCH")
    trades = pd.read_csv(source / "trades.csv")
    candidates = pd.read_csv(source / "candidate_audit.csv")
    backtest_comparison = pd.read_csv(source / "comparison.csv")
    if not set(trades.variant.unique()).issubset(set(VARIANTS)) or len(trades[trades.variant == VARIANTS[0]]) != 39:
        raise RuntimeError("BLOCKED_UNEXPECTED_AB_TRADE_SET")
    bars_hash_before = sha256(DB_PATH)
    bars = load_bars()
    trade_diagnostics = attach_trade_diagnostics(trades, bars)
    filled_candidate_keys = {
        (str(r.variant), taipei_epoch(str(r.earliest_entry_time)))
        for r in candidates.itertuples()
        if str(r.risk_and_execution_result) == "FILLED"
    }
    trade_diagnostics["matched_filled_candidate"] = [
        (str(r.variant), int(r.entry_timestamp_epoch)) in filled_candidate_keys
        for r in trade_diagnostics.itertuples()
    ]
    candidate_results = candidate_outcomes(candidates, bars)
    exits = grouped_exit(trade_diagnostics)
    quality = quality_summary(trade_diagnostics, candidates, candidate_results, backtest_comparison)
    horizons = horizon_summary(candidate_results, trade_diagnostics)
    if sha256(DB_PATH) != bars_hash_before:
        raise RuntimeError("BLOCKED_SOURCE_DATABASE_CHANGED_DURING_READ_ONLY_AUDIT")
    if not trade_diagnostics.entry_open_price_match.all() or not trade_diagnostics.exit_fill_rule_price_match.all() or not trade_diagnostics.matched_filled_candidate.all():
        raise RuntimeError("BLOCKED_TRADE_TO_SIGNAL_OR_EXECUTION_RECONCILIATION_FAILED")
    out = ROOT / "exports" / f"technical_v2b_loss_diagnosis_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    out.mkdir(parents=True, exist_ok=False)
    trade_diagnostics.to_csv(out / "trade_diagnostics.csv", index=False, encoding="utf-8-sig")
    quality.to_csv(out / "variant_quality.csv", index=False, encoding="utf-8-sig")
    candidate_results.to_csv(out / "candidate_outcomes.csv", index=False, encoding="utf-8-sig")
    horizons.to_csv(out / "entry_horizon_summary.csv", index=False, encoding="utf-8-sig")
    exits.to_csv(out / "exit_reason_summary.csv", index=False, encoding="utf-8-sig")
    make_charts(out, trade_diagnostics, candidate_results, exits, quality, horizons)
    audit = {
        "status": "COMPLETED", "experiment_id": "technical-v2b-loss-diagnosis-2303-20261104",
        "stock_code": "2303", "data_role": "training (historical_development source run)",
        "period": {"requested_start": START, "requested_end": END,
                   "actual_first_session": bars.session.min(), "actual_last_session": bars.session.max()},
        "source_run": str(source.relative_to(ROOT)), "source_experiment_id": source_audit.get("experiment_id"),
        "database_path": str(DB_PATH.relative_to(ROOT)), "database_sha256_before": bars_hash_before,
        "database_sha256_after": sha256(DB_PATH), "bar_count": int(len(bars)),
        "trading_day_count": int(bars.session.nunique()), "trade_rows_analyzed": int(len(trades)),
        "A_trade_count": int((trades.variant == "A_ORIGINAL_V2B").sum()),
        "entry_open_price_match_count": int(trade_diagnostics.entry_open_price_match.sum()),
        "exit_fill_rule_price_match_count": int(trade_diagnostics.exit_fill_rule_price_match.sum()),
        "filled_candidate_alignment_count": int(trade_diagnostics.matched_filled_candidate.sum()),
        "final_oos_used": False, "additional_holdout_used": False,
        "strategy_replayed": False, "parameter_search_performed": False,
        "frozen_strategy_changed": False, "database_modified": False,
        "false_breakout_definition": "Among post-market-filter candidates with 3 complete following sessions, false if any of the next 3 session closes is <= prior 55 completed-bar high at signal time.",
        "mfe_mae_definition": "Entry price to max high/min low over bars from entry timestamp inclusive to exit timestamp exclusive; for stop exits exclude the stop execution bar range and include actual stop fill in MAE.",
        "limitations": ["5-minute OHLC does not reveal intrabar high/low order", "single stock and exposed 2023–2024 only", "zero slippage assumption inherited", "1,000-share quantity cap inherited", "fixed-horizon close paths are diagnostic and may continue after actual exit"],
        "outputs": ["trade_diagnostics.csv", "variant_quality.csv", "candidate_outcomes.csv", "entry_horizon_summary.csv", "exit_reason_summary.csv", "diagnosis_report.md", "charts/mfe_mae_distribution.png", "charts/entry_horizon_returns.png", "charts/exit_reason_counts.png", "charts/exit_reason_net_pnl.png", "charts/cost_before_after.png"],
    }
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(out, trade_diagnostics, quality, exits, horizons, candidates, candidate_results, bars, source, source_audit)
    print(json.dumps({"status": "COMPLETED", "output_directory": str(out.relative_to(ROOT)),
                      "A_trades": audit["A_trade_count"], "all_trade_rows": len(trades),
                      "bar_count": len(bars), "trading_day_count": int(bars.session.nunique()),
                      "database_modified": False, "final_oos_used": False}, ensure_ascii=False, indent=2))
    return out


if __name__ == "__main__":
    run()

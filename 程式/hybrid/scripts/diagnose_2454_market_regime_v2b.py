"""Post-hoc market-regime and trade-quality diagnosis for 2454, 2023-2024 only.

Reads a completed frozen Technical V2-B export and the canonical SQLite database
in read-only mode. It does not replay or modify the strategy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.scripts import run_technical_v2b_cross_stock_exp001 as cross  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

SOURCE = ROOT / "exports/technical_v2b_cross_stock_exp001_20261104T201723+0800"
PERIOD_SOURCE = ROOT / "exports/technical_v2b_2454_period_stability_20261104T210500"
DB_PATH = ROOT / "data/technical_v1_mltech_clean.sqlite3"
TECH_CONFIG = ROOT / "configs/technical_v1_research.json"
FROZEN_CONFIG = ROOT / "configs/technical_v2_candidate.json"
TAIPEI = ZoneInfo("Asia/Taipei")
PERIODS = [
    ("2023_H1", "2023-01-01", "2023-06-30"),
    ("2023_H2", "2023-07-01", "2023-12-31"),
    ("2024_H1", "2024-01-01", "2024-06-30"),
    ("2024_H2", "2024-07-01", "2024-12-31"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def period_id(value) -> str | None:
    date = pd.Timestamp(value).date()
    for name, start, end in PERIODS:
        if pd.Timestamp(start).date() <= date <= pd.Timestamp(end).date():
            return name
    return None


def bool_col(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "yes"])


def calc_path_excursions(bars: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    timestamps = bars["kbar_timestamp"].to_numpy(dtype=np.int64)
    highs = bars["High"].to_numpy(dtype=float)
    lows = bars["Low"].to_numpy(dtype=float)
    for trade in trades.itertuples(index=False):
        buy = pd.Timestamp(trade.buy_datetime).tz_localize(TAIPEI).timestamp()
        sell = pd.Timestamp(trade.sell_datetime).tz_localize(TAIPEI).timestamp()
        left = int(np.searchsorted(timestamps, buy, side="left"))
        right = int(np.searchsorted(timestamps, sell, side="left"))
        path_high = highs[left:right]
        path_low = lows[left:right]
        if len(path_high) == 0:
            rows.append({"mfe_pct": np.nan, "mae_pct": np.nan, "bars_in_excursion_window": 0})
            continue
        entry = float(trade.buy_price)
        rows.append({
            "mfe_pct": (float(path_high.max()) / entry - 1) * 100,
            "mae_pct": (float(path_low.min()) / entry - 1) * 100,
            "bars_in_excursion_window": int(len(path_high)),
        })
    return pd.DataFrame(rows, index=trades.index)


def make_features(indicators: pd.DataFrame, candidates: pd.DataFrame, context_by_day: dict) -> pd.DataFrame:
    ind = indicators.copy().sort_values("kbar_timestamp").reset_index(drop=True)
    close = ind["Close"].astype(float)
    high = ind["High"].astype(float)
    low = ind["Low"].astype(float)
    opn = ind["Open"].astype(float)
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    ind["ATR14_5m"] = tr.rolling(14, min_periods=14).mean()
    ind["ATR14_pct"] = ind["ATR14_5m"] / close * 100
    ind["HV20_5m_pct"] = close.pct_change().rolling(20, min_periods=20).std(ddof=0) * 100
    ma_cols = {20: "MA_FAST", 60: "MA_MID", 120: "TREND_MA", 240: "MA_SLOW"}
    for window, col in ma_cols.items():
        ind[f"close_vs_ma{window}_pct"] = (close / ind[col].astype(float) - 1) * 100
        ind[f"ma{window}_slope12_pct"] = (ind[col].astype(float) / ind[col].astype(float).shift(12) - 1) * 100
    ind["ma_bull_stack_20_60_120_240"] = (
        (ind["MA_FAST"] > ind["MA_MID"]) & (ind["MA_MID"] > ind["TREND_MA"]) & (ind["TREND_MA"] > ind["MA_SLOW"])
    )
    ind["volume_ratio20"] = ind["Volume"].astype(float) / ind["VOLUME_MA"].astype(float)
    candle_range = (high - low).replace(0, np.nan)
    ind["upper_wick_ratio"] = (high - pd.concat([opn, close], axis=1).max(axis=1)) / candle_range
    ind["close_position_in_range"] = (close - low) / candle_range
    map_rows = ind.set_index("kbar_timestamp").to_dict(orient="index")
    records = []
    for row in candidates.to_dict(orient="records"):
        ts = int(row["signal_bar_timestamp"])
        values = map_rows.get(ts)
        if values is None:
            continue
        line = pd.to_numeric(row.get("breakout_line"), errors="coerce")
        close_at_signal = float(row["close_at_signal"])
        day = pd.Timestamp(row["signal_bar_end_time"]).date().isoformat()
        context = context_by_day.get(day, {})
        out = dict(row)
        out.update({k: values.get(k) for k in [
            "MA_FAST", "MA_MID", "TREND_MA", "MA_SLOW", "ATR14_pct", "HV20_5m_pct",
            "volume_ratio20", "upper_wick_ratio", "close_position_in_range",
            "ma_bull_stack_20_60_120_240", "close_vs_ma20_pct", "close_vs_ma60_pct",
            "close_vs_ma120_pct", "close_vs_ma240_pct", "ma20_slope12_pct", "ma60_slope12_pct",
            "ma120_slope12_pct", "ma240_slope12_pct",
        ]})
        out["breakout_excess_pct"] = (close_at_signal / line - 1) * 100 if pd.notna(line) and line else np.nan
        proxy_closes = context.get("last_20_proxy_closes", [])
        proxy_latest = context.get("latest_proxy_close")
        proxy_sma = context.get("proxy_sma20")
        out["proxy_vs_sma20_pct"] = (
            (float(proxy_latest) / float(proxy_sma) - 1) * 100
            if proxy_latest is not None and proxy_sma not in (None, 0) else np.nan
        )
        out["proxy_return_20d_pct"] = (
            (float(proxy_closes[-1]) / float(proxy_closes[0]) - 1) * 100
            if len(proxy_closes) == 20 and float(proxy_closes[0]) else np.nan
        )
        out["proxy_volatility_20d_pct"] = (
            float(np.std(np.diff(np.log(np.asarray(proxy_closes, dtype=float))), ddof=0) * 100)
            if len(proxy_closes) == 20 and all(float(v) > 0 for v in proxy_closes) else np.nan
        )
        out.update(context)
        out["period"] = period_id(day)
        records.append(out)
    return pd.DataFrame(records)


def aggregate_features(frame: pd.DataFrame, group_cols: list[str], features: list[str]) -> pd.DataFrame:
    rows = []
    for keys, group in frame.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        base = dict(zip(group_cols, keys))
        base["n"] = int(len(group))
        for col in features:
            values = pd.to_numeric(group[col], errors="coerce").dropna()
            base[f"{col}_mean"] = float(values.mean()) if len(values) else np.nan
            base[f"{col}_median"] = float(values.median()) if len(values) else np.nan
        rows.append(base)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {output}")
    audit = json.loads((SOURCE / "audit.json").read_text(encoding="utf-8"))
    config = json.loads((SOURCE / "config_snapshot.json").read_text(encoding="utf-8"))
    if audit.get("requested_period") != {"start": "2023-01-01", "end": "2024-12-31"} or audit.get("forbidden_periods_used") is not False:
        raise SystemExit("BLOCKED_SOURCE_PERIOD_OR_POLICY_MISMATCH")
    if config.get("strategy") != "FROZEN_TECHNICAL_V2B_BR_SENS_RANGE55_BB20_2" or config.get("ml_gate_used") is not False:
        raise SystemExit("BLOCKED_SOURCE_STRATEGY_MISMATCH")
    if not DB_PATH.is_file():
        raise SystemExit("BLOCKED_MISSING_HISTORICAL_SQLITE")

    candidates_all = pd.read_csv(SOURCE / "candidate_audit.csv", dtype={"stock_code": str})
    candidates = candidates_all.loc[
        (candidates_all["stock_code"] == "2454") & (candidates_all["year_run"].astype(str) == "FULL")
    ].copy()
    trades_all = pd.read_csv(SOURCE / "trades.csv", dtype={"stock_code": str})
    trades = trades_all.loc[
        (trades_all["stock_code"] == "2454")
        & (trades_all["variant"] == "TECH_V2B_RISK_V1")
        & (trades_all["year_run"].astype(str) == "FULL")
    ].copy()
    trades["buy_datetime"] = pd.to_datetime(trades["buy_datetime"])
    trades["sell_datetime"] = pd.to_datetime(trades["sell_datetime"])
    trades["period"] = trades["buy_datetime"].map(period_id)
    trades["profit_group"] = np.where(trades["net_pnl"].astype(float) > 0, "PROFIT", "LOSS_OR_FLAT")

    tech = tv1.read_config(TECH_CONFIG)
    family_name, family, candidate = tv1.candidate_by_id(tech, "BR_SENS_RANGE55_BB20_2")
    if family_name != "breakout" or candidate.get("id") != "BR_SENS_RANGE55_BB20_2":
        raise SystemExit("BLOCKED_FROZEN_CANDIDATE_LOOKUP_MISMATCH")

    db_hash_before = sha256(DB_PATH)
    with tv1._readonly_connection(DB_PATH) as conn:
        complete, incomplete, quality, _ = cross.audit_and_build_sessions(conn, cross.ALL_REQUIRED)
        proxy_days = set.intersection(*(complete[s] for s in cross.PEERS))
        common_days = set.intersection(*(complete[s] for s in cross.ALL_REQUIRED))
        contexts, proxy_audit = cross.build_frozen_proxy_context(conn, proxy_days, common_days)
        contexts_2454 = {day: value for (stock, day), value in contexts.items() if stock == "2454"}
        # Build the technical feature frame via the same production indicator code,
        # with a strict 2023-01-01..2024-12-31 query bound.
        full, _, app, _, _ = cross._filtered_frame(
            conn, tech, candidate, "2454", tech, "2023-01-01", "2024-12-31"
        )
        indicators = full.to_pandas()
        # The cross-stock run only schedules signals on the common complete sessions.
        candidates = candidates.loc[candidates["signal_bar_end_time"].str[:10].isin(common_days)].copy()
        feature_rows = make_features(indicators, candidates, contexts_2454)

        # Obtain only the target's actual 5-minute OHLCV bars in the permitted period
        # for post-hoc MFE/MAE; no row outside the explicit UTC timestamp bounds is queried.
        low, high = tv1._timestamp_bounds("2023-01-01", "2024-12-31")
        raw = pd.read_sql_query(
            "SELECT kbar_timestamp, open AS Open, high AS High, low AS Low, close AS Close, volume AS Volume "
            "FROM market_kbars WHERE stock_code=? AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
            "ORDER BY kbar_timestamp",
            conn,
            params=("2454", low, high),
        )
    db_hash_after = sha256(DB_PATH)
    if db_hash_before != db_hash_after:
        raise SystemExit("BLOCKED_DATABASE_HASH_CHANGED")
    if not common_days or len(common_days) != int(audit["common_eligible_sessions"]):
        raise SystemExit("BLOCKED_COMMON_SESSION_SET_MISMATCH")
    if len(trades) != 43 or len(feature_rows) == 0:
        raise SystemExit(f"BLOCKED_UNEXPECTED_SOURCE_ROWS trades={len(trades)} features={len(feature_rows)}")

    # Attach actual entry/exit outcomes to the signal feature record. The signal
    # timestamp is the previous completed bar; the fill is the next tradable bar.
    trade_keys = trades[["buy_datetime", "sell_datetime", "net_pnl", "gross_pnl", "total_cost", "exit_type", "exit_reason", "return_pct", "holding_bars"]].copy()
    trade_keys["entry_key"] = trade_keys["buy_datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    feature_rows["entry_key"] = pd.to_datetime(feature_rows["earliest_entry_time"], utc=True).dt.tz_convert(TAIPEI).dt.tz_localize(None).dt.strftime("%Y-%m-%d %H:%M:%S")
    feature_rows = feature_rows.merge(trade_keys, on="entry_key", how="left", validate="many_to_one")
    feature_rows["trade_filled"] = feature_rows["net_pnl"].notna()
    feature_rows["trade_outcome"] = np.where(
        ~feature_rows["trade_filled"], "NOT_FILLED",
        np.where(feature_rows["net_pnl"].astype(float) > 0, "PROFIT", "LOSS_OR_FLAT"),
    )
    excursions = calc_path_excursions(raw, trades)
    trades = pd.concat([trades.reset_index(drop=True), excursions.reset_index(drop=True)], axis=1)
    trades["mfe_minus_realized_gross_pct"] = trades["mfe_pct"] - trades["return_pct"].astype(float) * 100
    trades["mfe_capture_ratio_pct"] = np.where(
        trades["mfe_pct"] > 0, trades["return_pct"].astype(float) * 100 / trades["mfe_pct"] * 100, np.nan
    )

    periods = pd.read_csv(PERIOD_SOURCE / "half_year_comparison.csv")
    exit_summary = trades.groupby(["period", "exit_type"], dropna=False).agg(
        trade_count=("net_pnl", "size"),
        winners=("net_pnl", lambda x: int((x.astype(float) > 0).sum())),
        gross_pnl=("gross_pnl", "sum"),
        fees=("fee", "sum"),
        tax=("transaction_tax", "sum"),
        total_cost=("total_cost", "sum"),
        net_pnl=("net_pnl", "sum"),
        median_net_pnl=("net_pnl", "median"),
    ).reset_index()
    exit_summary["win_rate_pct"] = exit_summary["winners"] / exit_summary["trade_count"] * 100

    trade_features = feature_rows.loc[feature_rows["trade_filled"]].copy()
    trade_features["profit_group"] = np.where(
        trade_features["net_pnl"].astype(float) > 0, "PROFIT", "LOSS_OR_FLAT"
    )
    feature_cols = [
        "breakout_excess_pct", "volume_ratio20", "upper_wick_ratio", "ATR14_pct", "HV20_5m_pct",
        "close_vs_ma20_pct", "close_vs_ma60_pct", "close_vs_ma120_pct", "close_vs_ma240_pct",
        "ma20_slope12_pct", "ma60_slope12_pct", "ma120_slope12_pct", "ma240_slope12_pct",
        "proxy_vs_sma20_pct", "proxy_return_20d_pct", "proxy_volatility_20d_pct",
    ]
    for col in feature_cols:
        if col not in trade_features:
            trade_features[col] = np.nan
    win_loss_summary = aggregate_features(trade_features, ["period", "trade_outcome"], feature_cols)

    period_performance = periods.copy()
    entry_summary = trades.groupby("period", as_index=False).agg(
        trades=("net_pnl", "size"),
        winning_trades=("net_pnl", lambda s: int((s.astype(float) > 0).sum())),
        gross_pnl=("gross_pnl", "sum"),
        total_cost=("total_cost", "sum"),
        net_pnl=("net_pnl", "sum"),
        stop_loss_count=("exit_type", lambda s: int((s == "stop_loss").sum())),
        technical_exit_count=("exit_type", lambda s: int((s == "technical_exit").sum())),
        time_expiration_count=("exit_type", lambda s: int((s == "time_expiration").sum())),
        median_mfe_pct=("mfe_pct", "median"),
        median_mae_pct=("mae_pct", "median"),
        mean_mfe_pct=("mfe_pct", "mean"),
        mean_mae_pct=("mae_pct", "mean"),
    )
    period_performance = period_performance.merge(entry_summary, on="period", how="left", validate="one_to_one")
    period_performance["win_rate_trade_pct"] = period_performance["winning_trades"] / period_performance["trades"] * 100

    signal_summary = feature_rows.groupby("period", as_index=False).agg(
        raw_breakout_triggers=("signal_bar_timestamp", "size"),
        technical_candidates=("technical_candidate", lambda s: int(bool_col(s).sum())),
        market_pass_candidates=("candidate_after_regime", lambda s: int(bool_col(s).sum())),
        filled_signals=("trade_filled", "sum"),
        median_close_vs_ma120_pct=("close_vs_ma120_pct", "median"),
        median_close_vs_ma240_pct=("close_vs_ma240_pct", "median"),
        median_ma20_slope12_pct=("ma20_slope12_pct", "median"),
        median_ma60_slope12_pct=("ma60_slope12_pct", "median"),
        median_ma120_slope12_pct=("ma120_slope12_pct", "median"),
        median_ma240_slope12_pct=("ma240_slope12_pct", "median"),
        median_breakout_excess_pct=("breakout_excess_pct", "median"),
        median_volume_ratio20=("volume_ratio20", "median"),
        median_upper_wick_ratio=("upper_wick_ratio", "median"),
        median_atr14_pct=("ATR14_pct", "median"),
        median_hv20_5m_pct=("HV20_5m_pct", "median"),
        median_proxy_vs_sma20_pct=("proxy_vs_sma20_pct", "median"),
        median_proxy_return_20d_pct=("proxy_return_20d_pct", "median"),
        median_proxy_volatility_20d_pct=("proxy_volatility_20d_pct", "median"),
    )
    period_performance = period_performance.merge(signal_summary, on="period", how="left", validate="one_to_one")

    market = pd.DataFrame([
        {"session_date": day, "period": period_id(day), **value}
        for day, value in contexts_2454.items()
    ]).sort_values("session_date")
    if not market.empty:
        market["proxy_vs_sma20_pct"] = (market["latest_proxy_close"] / market["proxy_sma20"] - 1) * 100
        market["proxy_return_20d_pct"] = market["last_20_proxy_closes"].map(
            lambda values: (values[-1] / values[0] - 1) * 100 if len(values) == 20 and values[0] else np.nan
        )
        market["proxy_volatility_20d_pct"] = market["last_20_proxy_closes"].map(
            lambda values: float(np.std(np.diff(np.log(np.asarray(values, dtype=float))), ddof=0) * 100)
            if len(values) == 20 and all(float(v) > 0 for v in values) else np.nan
        )
    monthly_market = market.assign(month=pd.to_datetime(market["session_date"]).dt.to_period("M").astype(str)).groupby("month", as_index=False).agg(
        proxy_vs_sma20_pct_median=("proxy_vs_sma20_pct", "median"),
        proxy_return_20d_pct_median=("proxy_return_20d_pct", "median"),
        proxy_volatility_20d_pct_median=("proxy_volatility_20d_pct", "median"),
        proxy_bull_share_pct=("eligible", lambda s: 100 * sum(bool(x) for x in s) / len(s)),
        session_count=("session_date", "size"),
    )

    output.mkdir(parents=True, exist_ok=False)
    charts = output / "charts"
    charts.mkdir()
    period_performance.to_csv(output / "period_performance.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(output / "trade_mfe_mae.csv", index=False, encoding="utf-8-sig")
    exit_summary.to_csv(output / "exit_reason_comparison.csv", index=False, encoding="utf-8-sig")
    win_loss_summary.to_csv(output / "winner_loser_feature_comparison.csv", index=False, encoding="utf-8-sig")
    feature_rows.to_csv(output / "candidate_signal_features.csv", index=False, encoding="utf-8-sig")
    monthly_market.to_csv(output / "market_regime_monthly.csv", index=False, encoding="utf-8-sig")

    # Plot MFE/MAE distribution and trade-level outcome placement.
    colors = {"2023_H1": "#7d8ca3", "2023_H2": "#7d8ca3", "2024_H1": "#168a67", "2024_H2": "#c84a4a"}
    fig, axes = plt.subplots(2, 2, figsize=(11, 9), sharex=True, sharey=True)
    for ax, (pid, _, _) in zip(axes.flat, PERIODS):
        g = trades[trades.period == pid]
        win = g[g.net_pnl.astype(float) > 0]
        loss = g[g.net_pnl.astype(float) <= 0]
        ax.scatter(win.mae_pct, win.mfe_pct, color="#168a67", label=f"Profit n={len(win)}", alpha=.8)
        ax.scatter(loss.mae_pct, loss.mfe_pct, color="#c84a4a", marker="x", label=f"Loss/flat n={len(loss)}", alpha=.8)
        ax.axvline(0, color="black", lw=.6); ax.axhline(0, color="black", lw=.6)
        ax.set_title(pid); ax.grid(alpha=.25); ax.legend(fontsize=8)
    fig.supxlabel("MAE (% from entry; more negative = worse adverse move)")
    fig.supylabel("MFE (% from entry; higher = more favorable move)")
    fig.suptitle("2454 Technical V2-B trade MFE / MAE (post-hoc path diagnostics)")
    fig.tight_layout(); fig.savefig(charts / "mfe_mae_by_period.png", dpi=160); plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    dates = pd.to_datetime(market["session_date"])
    axes[0].plot(dates, market["proxy_vs_sma20_pct"], color="#205f96", lw=1)
    axes[0].axhline(0, color="black", lw=.8); axes[0].set_ylabel("Proxy vs prior SMA20 (%)")
    axes[0].set_title("Frozen equal-weight peer proxy: trend spread and 20-session volatility")
    axes[1].plot(dates, market["proxy_volatility_20d_pct"], color="#be7a21", lw=1)
    axes[1].set_ylabel("20-session proxy return std (%)"); axes[1].set_xlabel("Date")
    for ax in axes:
        ax.grid(alpha=.25)
        for cut in [pd.Timestamp("2023-07-01"), pd.Timestamp("2024-01-01"), pd.Timestamp("2024-07-01")]:
            ax.axvline(cut, color="#777777", lw=.7, ls="--")
    fig.tight_layout(); fig.savefig(charts / "market_proxy_trend_volatility.png", dpi=160); plt.close(fig)

    # Feature medians for profitable vs losing filled trades in 2024 halves.
    selected = trade_features[trade_features.period.isin(["2024_H1", "2024_H2"])].copy()
    plot_cols = ["breakout_excess_pct", "volume_ratio20", "upper_wick_ratio", "ATR14_pct", "close_vs_ma240_pct", "proxy_vs_sma20_pct"]
    plot_rows = []
    for col in plot_cols:
        for pid in ["2024_H1", "2024_H2"]:
            for outcome in ["PROFIT", "LOSS_OR_FLAT"]:
                v = pd.to_numeric(selected.loc[(selected.period == pid) & (selected.profit_group == outcome), col], errors="coerce").dropna()
                plot_rows.append({"feature": col, "period": pid, "outcome": outcome, "n": len(v), "median": v.median() if len(v) else np.nan})
    plot_df = pd.DataFrame(plot_rows)
    plot_df.to_csv(output / "winner_loser_feature_plot_data.csv", index=False, encoding="utf-8-sig")
    fig, axes = plt.subplots(2, 3, figsize=(13, 7))
    for ax, col in zip(axes.flat, plot_cols):
        sub = plot_df[plot_df.feature == col]
        labels = [f"{r.period[-2:]} {r.outcome.replace('_OR_FLAT','')}\nn={r.n}" for r in sub.itertuples()]
        vals = sub["median"].to_numpy(dtype=float)
        ax.bar(np.arange(len(vals)), np.nan_to_num(vals, nan=0), color=["#168a67" if "PROFIT" in l else "#c84a4a" for l in labels])
        ax.set_xticks(np.arange(len(labels)), labels, rotation=20, ha="right", fontsize=7)
        ax.set_title(col); ax.grid(axis="y", alpha=.2)
    fig.suptitle("Median entry features: profitable vs losing/flat trades (small samples)")
    fig.tight_layout(); fig.savefig(charts / "winner_loser_signal_features.png", dpi=160); plt.close(fig)

    period_md = period_performance[[
        "period", "trades", "win_rate_trade_pct", "gross_pnl", "total_cost", "net_pnl",
        "profit_factor_net_entry_cohort", "max_drawdown_pct_reset_at_period_start", "median_mfe_pct", "median_mae_pct",
        "mean_mfe_pct", "mean_mae_pct",
        "stop_loss_count", "technical_exit_count", "time_expiration_count",
    ]].to_markdown(index=False, floatfmt=".2f")
    exit_md = exit_summary.to_markdown(index=False, floatfmt=".2f")
    sig_md = signal_summary.to_markdown(index=False, floatfmt=".2f")
    feat_md = win_loss_summary[(win_loss_summary.period.isin(["2024_H1", "2024_H2"]))].to_markdown(index=False, floatfmt=".3f")
    top_24 = trades.loc[trades.period == "2024_H1"].sort_values("net_pnl", ascending=False).head(3)
    top_24h2_losses = trades.loc[trades.period == "2024_H2"].sort_values("net_pnl").head(3)
    h1 = period_performance.loc[period_performance.period == "2024_H1"].iloc[0]
    h2 = period_performance.loc[period_performance.period == "2024_H2"].iloc[0]
    h1sig = signal_summary.loc[signal_summary.period == "2024_H1"].iloc[0]
    h2sig = signal_summary.loc[signal_summary.period == "2024_H2"].iloc[0]
    h2_stop = exit_summary.loc[(exit_summary.period == "2024_H2") & (exit_summary.exit_type == "stop_loss")]
    h2_stop_net = float(h2_stop.net_pnl.iloc[0]) if not h2_stop.empty else 0.0
    h2_stop_loss_share = abs(h2_stop_net) / abs(float(h2.net_pnl)) * 100 if float(h2.net_pnl) else np.nan
    report = f"""# 2454 聯發科：Technical V2-B 市場狀態與交易績效診斷

## 範圍與方法

- 策略：凍結候選 `BR_SENS_RANGE55_BB20_2` + Risk V1；無 ML Gate。
- 期間：只使用 2023-01-01 至 2024-12-31 historical development；四段為 2023 H1/H2、2024 H1/H2。
- 來源回測：`{SOURCE.relative_to(ROOT)}`；股票交易明細及候選訊號取自既有輸出。5 分 K 由 `{DB_PATH.relative_to(ROOT)}` 以 read-only 方式讀取，SQL 嚴格限定 2023-01-01 至 2024-12-31；沒有讀取其他年份。
- Technical MA、布林及突破線使用現行凍結程式的 `backtest.add_indicators` 計算；ATR14、20 根 5 分 K 波動率、突破幅度、成交量比、上影線比例是事後診斷特徵，沒有進入交易決策。
- 市場代理沿用凍結版本定義：同儕 5 檔等權重絕對收盤價，排除目標股，使用前一個完整共同交易日與前 20 個已完成交易日。此值只作環境比較。
- MFE/MAE 用實際進場至出場成交前的 5 分 K 高低價事後計算；沒有用於建立進場特徵。
- 資料資格沿用來源輸出：Exact 54 real bars、無補值、共同完整日交集。來源 audit 所載共同日期數：{audit['common_eligible_sessions']}，範圍 {audit['first_common_session']} 至 {audit['last_common_session']}。

## 分段績效與交易狀態

績效表中的交易數、成本前後損益及出場數按進場日期分組；半年淨值報酬與最大回撤由既有 equity curve 計算，包含浮動損益。跨半年交易可能造成兩種口徑差異。

{period_md}

2024 H1 的獲利交易前三筆：
{top_24[['buy_datetime','sell_datetime','net_pnl','exit_type']].to_markdown(index=False, floatfmt='.2f')}

2024 H2 的虧損交易前三筆：
{top_24h2_losses[['buy_datetime','sell_datetime','net_pnl','exit_type']].to_markdown(index=False, floatfmt='.2f')}

## 訊號漏斗與突破形態

{sig_md}

候選訊號特徵是在訊號 K 收盤後可得。MA 斜率用最近 12 根 5 分 K 的相對變化；ATR14 是最近 14 根 5 分 K 真實波幅簡單平均；HV20 是最近 20 根 5 分 K 收盤報酬標準差。它們是描述性量，不是新增策略條件。

## H1 / H2 的進場時點特徵對照

上表涵蓋各期原始候選（含之後未成交者）的中位數。市場代理趨勢與波動使用訊號日前已完成的代理資料。

## 獲利與虧損交易特徵比較

以下僅列 2024 H1/H2 的成交交易，樣本數每組很小；均值/中位數差異不能視為統計顯著，也不代表因果。

{feat_md}

## 出場原因

{exit_md}

## MFE / MAE

![各半年 MFE / MAE 散點圖](charts/mfe_mae_by_period.png)

![贏家與輸家訊號特徵中位數](charts/winner_loser_signal_features.png)

MFE 是持倉期間最高價相對進場價的最大有利幅度；MAE 是最低價相對進場價的最大不利幅度。若 MFE 高但實際獲利低，表示持倉期間曾出現較佳浮盈但最終未完全保留；這不能單獨證明應延長持有或更改出場。

## 市場代理趨勢與波動

![代理趨勢與波動率](charts/market_proxy_trend_volatility.png)

月份資料：`market_regime_monthly.csv`。代理是原策略的等權重絕對股價代理，不是市場指數報酬率；不同股票名目價格可能影響代理尺度，解讀需保留此限制。

## 已有數據支持的發現

1. **結果方向反轉。**2024 H1 有 {int(h1.trades)} 筆、勝率 {float(h1.win_rate_trade_pct):.1f}%、淨損益 NT${float(h1.net_pnl):,.0f}；H2 有 {int(h2.trades)} 筆、勝率 {float(h2.win_rate_trade_pct):.1f}%、淨損益 NT${float(h2.net_pnl):,.0f}。H1 成本前毛損益 NT${float(h1.gross_pnl):,.0f}、成本 NT${float(h1.total_cost):,.0f}；H2 毛損益已是 NT${float(h2.gross_pnl):,.0f}，再扣 NT${float(h2.total_cost):,.0f}。
2. **H2 虧損集中在兩筆停損。**H2 有 {int(h2.stop_loss_count)} 筆停損，合計淨損益 NT${h2_stop_net:,.0f}，約占該段淨虧損金額 {h2_stop_loss_share:.1f}%；6 筆技術出場合計 NT${float(exit_summary.loc[(exit_summary.period=='2024_H2') & (exit_summary.exit_type=='technical_exit'),'net_pnl'].sum()):,.0f}。H1 有 3 筆停損合計 NT${float(exit_summary.loc[(exit_summary.period=='2024_H1') & (exit_summary.exit_type=='stop_loss'),'net_pnl'].sum()):,.0f}，但技術出場 10 筆合計 +NT${float(exit_summary.loc[(exit_summary.period=='2024_H1') & (exit_summary.exit_type=='technical_exit'),'net_pnl'].sum()):,.0f}，另 1 筆時間到期交易 +NT${float(exit_summary.loc[(exit_summary.period=='2024_H1') & (exit_summary.exit_type=='time_expiration'),'net_pnl'].sum()):,.0f}。
3. **突破幅度與量比沒有顯示 H2 明顯較弱。**候選突破超越幅度中位數 H1 {float(h1sig.median_breakout_excess_pct):.3f}%、H2 {float(h2sig.median_breakout_excess_pct):.3f}%；量比 {float(h1sig.median_volume_ratio20):.2f} 對 {float(h2sig.median_volume_ratio20):.2f}。但 H2 的 ATR% {float(h2sig.median_atr14_pct):.3f}% 高於 H1 {float(h1sig.median_atr14_pct):.3f}%，HV20 {float(h2sig.median_hv20_5m_pct):.3f}% 高於 H1 {float(h1sig.median_hv20_5m_pct):.3f}%。這或許代表較躁動，但需更多資料驗證。
4. **長期趨勢與代理市場環境在 H2 較弱。**候選收盤價高於 MA240 的中位幅度由 H1 {float(h1sig.median_close_vs_ma240_pct):.2f}% 降至 H2 {float(h2sig.median_close_vs_ma240_pct):.2f}%；MA120 斜率由 {float(h1sig.median_ma120_slope12_pct):.3f}% 降至 {float(h2sig.median_ma120_slope12_pct):.3f}%（仍為正）。凍結代理相對 SMA20 由 {float(h1sig.median_proxy_vs_sma20_pct):.2f}% 降到 {float(h2sig.median_proxy_vs_sma20_pct):.2f}%；20 日代理變化由 {float(h1sig.median_proxy_return_20d_pct):.2f}% 降到 {float(h2sig.median_proxy_return_20d_pct):.2f}%，代理波動度則由 {float(h1sig.median_proxy_volatility_20d_pct):.2f}% 升至 {float(h2sig.median_proxy_volatility_20d_pct):.2f}%。這些與結果同時變化，不證明因果。
5. **進場後延續性偏弱。**實際成交的 MFE 中位數 H1 {float(h1.median_mfe_pct):.2f}%、H2 {float(h2.median_mfe_pct):.2f}%；H2 平均 MAE {float(h2.mean_mae_pct):.2f}%，H1 為 {float(h1.mean_mae_pct):.2f}%。H2 僅 8 筆交易，這是樣本描述，不足以推論普遍規律。
6. **可用候選也變少。**H1 有 {int(h1sig.technical_candidates)} 筆技術候選、{int(h1sig.market_pass_candidates)} 筆通過市場代理；H2 分別 {int(h2sig.technical_candidates)} 與 {int(h2sig.market_pass_candidates)} 筆。這說明 H2 機會較少且既有濾網擋下更多候選，不代表濾網造成虧損。
7. 來源回測使用完成 K 棒後、下一根實際可交易 K 棒開盤成交；市場代理只取前一完整共同交易日。MFE/MAE 是事後行情路徑，不參與訊號特徵。

## 可能原因與尚待驗證的假設

- **可能原因：**H2 的部分突破可能在較差的市場代理/波動條件下出現，或突破後延續不足；若 H2 虧損交易在突破幅度、上影線、ATR/波動、代理趨勢或 MFE/MAE 上與 H1 呈現一致差異，這些可作後續研究假設。
- **尚待驗證：**四個半年及 2024 H2 的 8 筆交易太少，無法證明某一特徵造成虧損；年度市場報酬與策略報酬的同步/反向也不是因果證據。市場代理的絕對價格等權規則本身可能限制其經濟解釋。
- **不支持的結論：**不能把本次歷史中較賺錢的特徵門檻直接改成進場規則，也不能因 2024 H1 表現好就宣稱策略已穩定。

## 總結回答

V2-B 在 2024 H1 獲利、H2 虧損，直接可確認的是成交樣本數、勝率、成本前損益、交易成本、出場組成和各項訊號/市場特徵描述不同。H2 成本前已虧損，故交易成本加重結果但不是唯一原因。若特徵比較顯示 H2 的突破品質或市場代理狀態較弱，合理說法是「與較差交易結果相關的候選解釋」，不能說已證明因果。下一步應在不改規則下，擴大其他股票與預先規劃的前瞻期間驗證，並用真實執行滑價重做風險敏感度。

## 治理旗標

`data_period = 2023-01-01..2024-12-31`  
`data_role = historical_development_exposed`  
`2025_used = false`  
`2026_used = false`  
`holdout_or_final_oos_used = false`  
`backtest_rerun = false`  
`strategy_or_risk_changed = false`  
`parameter_search = false`
"""
    (output / "market_regime_diagnosis.md").write_text(report, encoding="utf-8")
    manifest = {
        "status": "COMPLETED_POST_HOC_DIAGNOSIS",
        "experiment_id": "technical-v2b-2454-market-regime-diagnosis-2023-2024",
        "source_export": str(SOURCE.relative_to(ROOT)),
        "source_experiment_id": audit["experiment_id"],
        "stock_code": "2454",
        "period": {"start": "2023-01-01", "end": "2024-12-31"},
        "data_role": "historical_development_exposed",
        "db_path": str(DB_PATH.relative_to(ROOT)),
        "db_sha256_before": db_hash_before,
        "db_sha256_after": db_hash_after,
        "complete_common_sessions": len(common_days),
        "proxy_audit": proxy_audit,
        "frozen_candidate": "BR_SENS_RANGE55_BB20_2",
        "risk_version": "risk_v1",
        "ml_gate_used": False,
        "market_data_read_only": True,
        "market_data_query_bound": ["2023-01-01", "2024-12-31"],
        "backtest_rerun": False,
        "parameter_search": False,
        "strategy_or_risk_changed": False,
        "2025_used": False,
        "2026_used": False,
        "holdout_used": False,
        "final_oos_used": False,
        "features_from_future_for_signal": False,
        "mfe_mae_are_post_hoc_only": True,
        "files": [
            "market_regime_diagnosis.md", "period_performance.csv", "trade_mfe_mae.csv",
            "exit_reason_comparison.csv", "winner_loser_feature_comparison.csv",
            "candidate_signal_features.csv", "market_regime_monthly.csv", "charts/mfe_mae_by_period.png",
            "charts/market_proxy_trend_volatility.png", "charts/winner_loser_signal_features.png",
        ],
    }
    (output / "audit.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DIAGNOSIS_OUTPUT={output}")


if __name__ == "__main__":
    main()

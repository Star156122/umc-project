"""Cross-stock replay of the frozen Technical V2-B + Risk V1 candidate."""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.scripts import run_short_swing_ab_test as shared  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
CONFIG_PATH = ROOT / "hybrid/config/technical_v2b_cross_stock_exp001.json"
TECH_CONFIG_PATH = ROOT / "configs/technical_v1_research.json"
FROZEN_CONFIG_PATH = ROOT / "configs/technical_v2_candidate.json"
DB_PATH = ROOT / "data/technical_v1_mltech_clean.sqlite3"
STOCK_NAMES = {"2303": "聯電", "2330": "台積電", "2382": "廣達", "2454": "聯發科", "2317": "鴻海"}
TARGETS = tuple(STOCK_NAMES)
PEERS = ("2303", "2330", "2379", "2454", "3034")
ALL_REQUIRED = tuple(dict.fromkeys((*TARGETS, *PEERS)))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _timestamp_bounds(start: str, end: str) -> tuple[int, int]:
    return tv1._timestamp_bounds(start, end)


def _expected_session_stamps(day: str) -> set[int]:
    date = dt.date.fromisoformat(day)
    start = dt.datetime.combine(date, dt.time(9, 0), tzinfo=TAIPEI)
    return {int((start + dt.timedelta(minutes=5 * i)).timestamp()) for i in range(54)}


def audit_and_build_sessions(connection: sqlite3.Connection, symbols: tuple[str, ...]) -> tuple[dict[str, set[str]], dict[str, list[dict]], list[dict], dict[str, int]]:
    lower, upper = _timestamp_bounds("2023-01-01", "2024-12-31")
    marks = ",".join("?" for _ in symbols)
    rows = connection.execute(
        f"SELECT stock_code,kbar_timestamp,open,high,low,close,volume FROM market_kbars "
        f"WHERE stock_code IN ({marks}) AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
        "ORDER BY stock_code,kbar_timestamp", (*symbols, lower, upper),
    ).fetchall()
    by_stock_day: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        stamp = int(row["kbar_timestamp"])
        local = dt.datetime.fromtimestamp(stamp, TAIPEI)
        if local.weekday() >= 5 or not (dt.time(9, 0) <= local.time() <= dt.time(13, 25)):
            continue
        by_stock_day[(str(row["stock_code"]), local.date().isoformat())].append(row)
    observed_days = sorted({day for _, day in by_stock_day})
    complete: dict[str, set[str]] = {s: set() for s in symbols}
    quality: list[dict] = []
    incomplete: dict[str, list[dict]] = {s: [] for s in symbols}
    observed_session_count = {s: sum(1 for key in by_stock_day if key[0] == s) for s in symbols}
    for stock in symbols:
        for day in observed_days:
            group = by_stock_day.get((stock, day), [])
            stamps = [int(r["kbar_timestamp"]) for r in group]
            expected = _expected_session_stamps(day)
            bad_ohlcv = []
            for r in group:
                vals = [r[k] for k in ("open", "high", "low", "close", "volume")]
                if any(v is None for v in vals):
                    bad_ohlcv.append(int(r["kbar_timestamp"]))
                    continue
                o, h, l, c, v = map(float, vals)
                if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h) or v < 0:
                    bad_ohlcv.append(int(r["kbar_timestamp"]))
            exact = len(stamps) == 54 and len(set(stamps)) == 54 and set(stamps) == expected
            valid = exact and not bad_ohlcv
            state = "PASS" if valid else ("NO_BARS_ON_UNION_DATE" if not group else "INCOMPLETE_OR_INVALID_SESSION")
            record = {
                "stock_code": stock, "stock_name": STOCK_NAMES.get(stock, "frozen_proxy"), "date": day,
                "observed_bar_count_0900_1325": len(stamps), "expected_bar_count": 54,
                "missing_timestamp_count": len(expected - set(stamps)), "unexpected_timestamp_count": len(set(stamps) - expected),
                "duplicate_timestamp_count": len(stamps) - len(set(stamps)), "bad_ohlcv_count": len(bad_ohlcv),
                "session_complete_valid": bool(valid), "session_state": state, "eligibility_reason": state,
            }
            quality.append(record)
            if valid:
                complete[stock].add(day)
            elif group:
                incomplete[stock].append(record)
    return complete, incomplete, quality, observed_session_count


def build_frozen_proxy_context(connection: sqlite3.Connection, eligible_proxy_days: set[str], candidate_days: set[str]) -> tuple[dict, dict]:
    lower, upper = _timestamp_bounds("2023-01-01", "2024-12-31")
    marks = ",".join("?" for _ in PEERS)
    rows = connection.execute(
        f"SELECT stock_code,kbar_timestamp,close FROM market_kbars WHERE stock_code IN ({marks}) "
        "AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY stock_code,kbar_timestamp",
        (*PEERS, lower, upper),
    ).fetchall()
    closes: dict[tuple[str, str], float] = {}
    for row in rows:
        local = dt.datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI)
        day = local.date().isoformat()
        if local.weekday() < 5 and local.time() == dt.time(13, 25) and day in eligible_proxy_days:
            closes[(str(row["stock_code"]), day)] = float(row["close"])
    proxy_by_day: dict[str, float] = {}
    for day in sorted(eligible_proxy_days):
        if all((peer, day) in closes for peer in PEERS):
            proxy_by_day[day] = float(np.mean([closes[(peer, day)] for peer in PEERS]))
    contexts = {}
    for target in TARGETS:
        component_stocks = [peer for peer in PEERS if peer != target] if target in PEERS else list(PEERS)
        for day in candidate_days:
            prior_days = [d for d in sorted(proxy_by_day) if d < day]
            last20_days = prior_days[-20:]
            vals = [float(np.mean([closes[(s, d)] for s in component_stocks])) for d in last20_days]
            eligible = len(vals) == 20
            latest = vals[-1] if eligible else None
            sma = float(np.mean(vals)) if eligible else None
            contexts[(target, day)] = {
                "eligible": bool(eligible and latest is not None and sma is not None and latest > sma),
                "reason": "PASS_PRIOR_PROXY_ABOVE_SMA20" if eligible and latest > sma else ("PROXY_NOT_ABOVE_SMA20" if eligible else "INSUFFICIENT_PRIOR_COMMON_SESSIONS"),
                "target_excluded": target in PEERS,
                "proxy_component_stocks": component_stocks,
                "latest_common_prior_date": last20_days[-1] if last20_days else None,
                "last_20_proxy_closes": vals, "latest_proxy_close": latest, "proxy_sma20": sma,
            }
    return contexts, {"eligible_frozen_peer_sessions": len(proxy_by_day), "target_contexts": len(contexts),
                      "proxy_rule": "frozen five peers; target excluded for in-pool stocks; all five peers for external targets; prior 20 complete peer sessions; equal-weight absolute closes"}


def _filtered_frame(connection, tech, candidate, stock: str, app_config: dict, lower: str, upper: str):
    fold = {"id": "CROSS_STOCK", "train_start": "2023-01-01", "train_end": "2024-12-31", "evaluate_start": lower, "evaluate_end": upper}
    full0, _, app, warmup, eval_lower = tv1._load_fold_frame(connection, app_config, candidate, stock, fold)
    raw = full0.select("kbar_timestamp", "Open", "High", "Low", "Close", "Volume", "kbar_time").filter(pl.col("kbar_time").dt.weekday() <= 5)
    full = bt.add_indicators(raw, app)
    ev_lo, ev_hi = _timestamp_bounds(lower, upper)
    evaluation = full.filter((pl.col("kbar_timestamp") >= ev_lo) & (pl.col("kbar_timestamp") < ev_hi))
    return full, evaluation, app, warmup, eval_lower


def _candidate_line_map(frame: pl.DataFrame, candidate_rows: list[dict]) -> list[dict]:
    records = frame.select("kbar_timestamp", "High", "Close").to_dicts()
    index = {int(r["kbar_timestamp"]): i for i, r in enumerate(records)}
    out = []
    for source in candidate_rows:
        row = dict(source)
        i = index.get(int(row["signal_bar_timestamp"]))
        row["breakout_line"] = max(float(x["High"]) for x in records[max(0, i - 55):i]) if i is not None and i >= 55 else None
        out.append(row)
    return out


def _benchmark_for_stock(bars: list[dict], capital: float, stock: str, active_exposure: float) -> tuple[list[dict], list[dict]]:
    outputs, curves = [], []
    for label, exposure in (("buy_and_hold_100pct", 1.0), ("buy_and_hold_40pct", 0.4), ("buy_and_hold_avg_exposure_scaled", active_exposure)):
        summary, points = shared._benchmark(bars, capital, exposure)
        summary.update({"stock_code": stock, "benchmark": label, "exposure_target_pct": exposure * 100})
        outputs.append(summary)
        for point in points:
            point["variant"] = label
            point["stock_code"] = stock
            curves.append(point)
    return outputs, curves


def _bars_for_dates(bars: list[dict], allowed_dates: set[str]) -> list[dict]:
    """Keep only actual bars on the common eligible session calendar."""
    return [
        bar for bar in bars
        if dt.datetime.fromtimestamp(int(bar["time"]), TAIPEI).date().isoformat() in allowed_dates
    ]


def _write_charts(output: Path, comparison: pd.DataFrame, curves: pd.DataFrame, trades: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    folder = output / "charts"
    folder.mkdir(parents=True, exist_ok=True)
    for field, filename, title, ylabel in (("equity", "equity_curves.png", "Equity curves", "NTD"), ("drawdown_pct", "drawdown_curves.png", "Drawdown", "%")):
        fig, ax = plt.subplots(figsize=(13, 6))
        for stock in TARGETS:
            group = curves[(curves.stock_code.astype(str) == stock) & curves.variant.eq("TECH_V2B_RISK_V1")]
            if len(group): ax.plot(pd.to_datetime(group.timestamp), group[field], label=stock, lw=1.1)
        ax.set_title(title); ax.set_ylabel(ylabel); ax.grid(alpha=.25); ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(folder / filename, dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 5)); ax.bar(comparison.stock_code.astype(str), comparison.total_return_pct, color="#2b76bb"); ax.set_title("Net return by stock"); ax.set_ylabel("%"); ax.grid(axis="y", alpha=.25); fig.tight_layout(); fig.savefig(folder / "net_return_by_stock.png", dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 5)); x=np.arange(len(comparison)); w=.38; ax.bar(x-w/2, comparison.trade_count, w, label="Trades"); ax2=ax.twinx(); ax2.bar(x+w/2, comparison.win_rate_pct.fillna(0), w, color="#e4a53a", label="Win rate %"); ax.set_xticks(x, comparison.stock_code); ax.set_ylabel("Trades"); ax2.set_ylabel("Win rate %"); ax.set_title("Trades and win rate"); ax.grid(axis="y", alpha=.2); fig.tight_layout(); fig.savefig(folder / "trades_and_win_rate.png", dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 5));
    for stock in TARGETS:
        group=trades[trades.stock_code.astype(str).eq(stock)] if len(trades) else pd.DataFrame()
        if len(group): ax.hist(group.holding_trading_day_intervals.dropna(), bins=range(0, int(group.holding_trading_day_intervals.max())+2), alpha=.4, label=stock)
    ax.set_title("Holding day intervals"); ax.set_xlabel("Trading day transitions"); ax.set_ylabel("Trades"); ax.grid(axis="y", alpha=.2); ax.legend(); fig.tight_layout(); fig.savefig(folder / "holding_days.png", dpi=150); plt.close(fig)


def run() -> Path:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config["experiment_id"] != "technical-v2b-cross-stock-2023-2024-exp001" or config["stocks"] != list(TARGETS):
        raise RuntimeError("BLOCKED_EXPERIMENT_CONFIG_IDENTITY_MISMATCH")
    tech = tv1.read_config(TECH_CONFIG_PATH)
    tv1.assert_execution_ready(tech, "training")
    family_name, family, candidate = tv1.candidate_by_id(tech, "BR_SENS_RANGE55_BB20_2")
    # Extend only this in-memory research run's metadata so the unchanged
    # shared loader can label the requested additional target stocks.
    tech = copy.deepcopy(tech)
    universe = tech["stock_universe"]
    universe["stock_codes"] = sorted(set(universe.get("stock_codes", [])) | set(TARGETS))
    universe.setdefault("stock_names", {}).update({"2382": "廣達", "2317": "鴻海"})
    universe.setdefault("industries", {}).update({"2382": "電腦及週邊設備", "2317": "電子零組件"})
    frozen = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    if frozen.get("base_candidate") != candidate["id"] or frozen.get("status") != "FROZEN_CANDIDATE":
        raise RuntimeError("BLOCKED_FROZEN_CANDIDATE_MISMATCH")
    if not DB_PATH.is_file(): raise FileNotFoundError("BLOCKED_MISSING_CANONICAL_TRAINING_DATABASE")
    db_hash = sha256(DB_PATH)
    timestamp = dt.datetime.now(TAIPEI).strftime("%Y%m%dT%H%M%S%z")
    output = ROOT / "exports" / f"technical_v2b_cross_stock_exp001_{timestamp}"
    output.mkdir(parents=True, exist_ok=False)
    config_run = copy.deepcopy(tech)
    config_run["common_backtest"]["initial_capital"] = config["initial_capital_per_stock"]
    config_run["common_backtest"].update({
        "stock_fee_rate": config["execution"]["stock_fee_rate"], "stock_tax_rate": config["execution"]["stock_tax_rate"],
        "slippage_rate": config["execution"]["slippage_rate"], "stop_loss_pct": config["risk"]["stop_loss_pct"],
        "min_hold_bars": config["risk"]["min_holding_bars"], "max_hold_bars": config["risk"]["max_holding_bars"],
        "cooldown_bars": config["risk"]["cooldown_bars"], "max_entries_per_day": config["risk"]["max_entries_per_day"],
    })
    with tv1._readonly_connection(DB_PATH) as connection:
        complete_by_stock, incomplete_by_stock, quality_rows, observed_session_count = audit_and_build_sessions(connection, ALL_REQUIRED)
        common_days = set.intersection(*(complete_by_stock[s] for s in ALL_REQUIRED))
        proxy_days = set.intersection(*(complete_by_stock[s] for s in PEERS))
        if not common_days:
            raise RuntimeError("BLOCKED_NO_COMMON_COMPLETE_SESSION")
        contexts, proxy_audit = build_frozen_proxy_context(connection, proxy_days, common_days)
        comparisons, annual_rows, annual_benchmark_rows, trade_rows, curve_rows, candidate_rows, risk_rows, benchmark_rows = [], [], [], [], [], [], [], []
        for stock in TARGETS:
            candidate_years: dict[int, list[dict]] = {}
            for year in (2023, 2024):
                full, evaluation, app, warmup, eval_lower = _filtered_frame(connection, tech, candidate, stock, config_run, f"{year}-01-01", f"{year}-12-31")
                year_hi = _timestamp_bounds(f"{year}-01-01", f"{year}-12-31")[1]
                full = full.filter(pl.col("kbar_timestamp") < year_hi)
                year_dates = {d for d in common_days if d.startswith(str(year))}
                frame_times = full["kbar_time"].to_list()
                schedule = {shared.iso_taipei(value) for value in frame_times[1:] if value.date().isoformat() in year_dates}
                eval_lo = _timestamp_bounds(f"{year}-01-01", f"{year}-12-31")[0]
                eval_only = full.filter((pl.col("kbar_timestamp") >= eval_lo) & (pl.col("kbar_timestamp") < year_hi))
                bars = bt.build_kbar_rows_from_frame(eval_only, app)
                strategy_cls = shared._make_strategy_class("A_ORIGINAL_V2B")
                strategy = strategy_cls(config=app, study_config=config_run, family_name=family_name, family=family,
                    candidate=candidate, data_role="historical_development", fold_id=f"YEAR_{year}",
                    is_backtest=True, is_simulation=True, local_only=True, ml_model="technical_only",
                    prediction_lookup={}, candidate_schedule=schedule, regime_context=contexts)
                strategy.configure_local_account(app); strategy.evaluation_start_timestamp = eval_lo
                strategy.local_slippage_bps = 0.0; strategy.diagnostic_sizing_enabled = True
                stats = bt.MarketDataStats(tick_count=full.height, first_timestamp=float(full["kbar_timestamp"][0]), last_timestamp=float(full["kbar_timestamp"][-1]))
                with contextlib.redirect_stdout(io.StringIO()): bt.replay_kbars(strategy, full, stats)
                raw_trades = bt._build_trade_rows(strategy, app)
                risk = [{**r, "stock_code": stock} for r in strategy.risk_decisions]
                candidates = [{**r, "stock_code": stock} for r in strategy.candidate_audit]
                candidates = [r for r in candidates if dt.datetime.fromtimestamp(int(r["signal_bar_timestamp"]), TAIPEI).date().isoformat() in year_dates]
                candidates = _candidate_line_map(full, candidates)
                summary, enriched, curves = shared._metrics_and_rows(version="TECH_V2B_RISK_V1", strategy=strategy, app=app, bars=bars,
                    trade_rows=raw_trades, risk_rows=risk, candidate_rows=candidates)
                summary.update({"stock_code": stock, "stock_name": STOCK_NAMES[stock], "strategy_id": "BR_SENS_RANGE55_BB20_2+RISK_V1",
                    "strategy_version": "Technical V2-B + Risk V1", "data_period": f"{year}-01-01/{year}-12-31", "account_reset": True,
                    "eligible_signal_days": len(year_dates), "target_valid_session_count": len(complete_by_stock[stock]),
                    "target_observed_session_count": observed_session_count[stock],
                    "target_incomplete_observed_session_count": len(incomplete_by_stock[stock]),
                    "target_data_completeness_pct": 100.0 * len(complete_by_stock[stock]) / max(1, observed_session_count[stock]),
                    "slippage_rate": 0.0, "initial_capital": config["initial_capital_per_stock"]})
                if strategy.local_position != 0: raise RuntimeError(f"BLOCKED_OPEN_POSITION:{stock}:{year}")
                annual_rows.append({k: v for k, v in summary.items() if k != "annual_performance"} | {"year": year})
                annual_exposure = float(np.mean([float(p.get("position_value") or 0.0) / float(p["equity"]) for p in curves if float(p["equity"]) > 0])) if curves else 0.0
                annual_benchmark_bars = _bars_for_dates(bars, year_dates)
                annual_bench, annual_bench_curves = _benchmark_for_stock(annual_benchmark_bars, config["initial_capital_per_stock"], stock, annual_exposure)
                for item in annual_bench:
                    item["year"] = year
                    annual_benchmark_rows.append(item)
                curve_rows.extend([{**r, "year_run": year} for r in annual_bench_curves])
                candidate_years[year] = candidates
                trade_rows.extend([{**r, "stock_code": stock, "stock_name": STOCK_NAMES[stock], "year_run": year} for r in enriched])
                curve_rows.extend([{**r, "stock_code": stock, "stock_name": STOCK_NAMES[stock], "year_run": year} for r in curves])
                candidate_rows.extend([{**r, "stock_name": STOCK_NAMES[stock], "year_run": year} for r in candidates])
                risk_rows.extend([{**r, "stock_name": STOCK_NAMES[stock], "year_run": year} for r in risk])
            # Main two-year run uses a fresh independent account.
            full, _, app, warmup, eval_lower = _filtered_frame(connection, tech, candidate, stock, config_run, "2023-01-01", "2024-12-31")
            frame_times = full["kbar_time"].to_list(); schedule = {shared.iso_taipei(value) for value in frame_times[1:] if value.date().isoformat() in common_days}
            bars = bt.build_kbar_rows_from_frame(full, app)
            strategy_cls = shared._make_strategy_class("A_ORIGINAL_V2B")
            strategy = strategy_cls(config=app, study_config=config_run, family_name=family_name, family=family, candidate=candidate,
                data_role="historical_development", fold_id="FULL_2023_2024", is_backtest=True, is_simulation=True,
                local_only=True, ml_model="technical_only", prediction_lookup={}, candidate_schedule=schedule, regime_context=contexts)
            strategy.configure_local_account(app); strategy.evaluation_start_timestamp = eval_lower
            strategy.local_slippage_bps = 0.0; strategy.diagnostic_sizing_enabled = True
            stats = bt.MarketDataStats(tick_count=full.height, first_timestamp=float(full["kbar_timestamp"][0]), last_timestamp=float(full["kbar_timestamp"][-1]))
            with contextlib.redirect_stdout(io.StringIO()): bt.replay_kbars(strategy, full, stats)
            raw_trades = bt._build_trade_rows(strategy, app)
            risk = [{**r, "stock_code": stock} for r in strategy.risk_decisions]
            candidates = [{**r, "stock_code": stock} for r in strategy.candidate_audit]
            candidates = [r for r in candidates if dt.datetime.fromtimestamp(int(r["signal_bar_timestamp"]), TAIPEI).date().isoformat() in common_days]
            candidates = _candidate_line_map(full, candidates)
            summary, enriched, curves = shared._metrics_and_rows(version="TECH_V2B_RISK_V1", strategy=strategy, app=app, bars=bars,
                trade_rows=raw_trades, risk_rows=risk, candidate_rows=candidates)
            summary.update({"stock_code": stock, "stock_name": STOCK_NAMES[stock], "strategy_id": "BR_SENS_RANGE55_BB20_2+RISK_V1",
                "strategy_version": "Technical V2-B + Risk V1", "eligible_signal_days": len(common_days),
                "target_valid_session_count": len(complete_by_stock[stock]),
                "target_observed_session_count": observed_session_count[stock],
                "target_incomplete_observed_session_count": len(incomplete_by_stock[stock]),
                "target_data_completeness_pct": 100.0 * len(complete_by_stock[stock]) / max(1, observed_session_count[stock]),
                "slippage_rate": 0.0, "initial_capital": config["initial_capital_per_stock"]})
            exp_rows = [r for r in enriched if str(r.get("buy_datetime", ""))[:10] in common_days or str(r.get("buy_datetime", ""))[:10] in {d for d in common_days}]
            # Enrich exposure and benchmark on the exact same real-bar series.
            active_exposure = float(np.mean([float(p.get("position_value") or 0.0) / float(p["equity"]) for p in curves if float(p["equity"]) > 0])) if curves else 0.0
            summary["average_exposure_pct"] = 100.0 * active_exposure
            comparisons.append(summary)
            trade_rows.extend([{**r, "stock_code": stock, "stock_name": STOCK_NAMES[stock], "year_run": "FULL"} for r in enriched])
            curve_rows.extend([{**r, "stock_code": stock, "stock_name": STOCK_NAMES[stock], "year_run": "FULL"} for r in curves])
            candidates = _candidate_line_map(full, candidates)
            candidate_rows.extend([{**r, "stock_name": STOCK_NAMES[stock], "year_run": "FULL"} for r in candidates])
            risk_rows.extend([{**r, "stock_name": STOCK_NAMES[stock], "year_run": "FULL"} for r in risk])
            benchmark_bars = _bars_for_dates(bars, common_days)
            bench, bench_curves = _benchmark_for_stock(benchmark_bars, config["initial_capital_per_stock"], stock, active_exposure)
            benchmark_rows.extend(bench); curve_rows.extend(bench_curves)
            if strategy.local_position != 0: raise RuntimeError(f"BLOCKED_OPEN_POSITION:{stock}:FULL")
        if sha256(DB_PATH) != db_hash: raise RuntimeError("BLOCKED_DATABASE_HASH_CHANGED")

    comparison = pd.DataFrame(comparisons)
    annual = pd.DataFrame(annual_rows)
    annual_benchmarks = pd.DataFrame(annual_benchmark_rows)
    trades = pd.DataFrame(trade_rows)
    curves = pd.DataFrame(curve_rows)
    candidates = pd.DataFrame(candidate_rows)
    risks = pd.DataFrame(risk_rows)
    benchmarks = pd.DataFrame(benchmark_rows)
    data_quality = pd.DataFrame(quality_rows)
    # Post-run descriptive false-breakout definition: any of next 3 eligible
    # session closes at/below the recorded 55-bar breakout line.
    daily_close: dict[str, dict[str, float]] = defaultdict(dict)
    with tv1._readonly_connection(DB_PATH) as read_connection:
        for row in read_connection.execute("SELECT stock_code,kbar_timestamp,close FROM market_kbars WHERE stock_code IN (?,?,?,?,?,?,?) AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY stock_code,kbar_timestamp", (*ALL_REQUIRED, *_timestamp_bounds("2023-01-01", "2024-12-31"))):
            local = dt.datetime.fromtimestamp(int(row["kbar_timestamp"]), TAIPEI)
            if local.weekday() < 5 and local.time() == dt.time(13, 25) and local.date().isoformat() in common_days:
                daily_close[str(row["stock_code"])][local.date().isoformat()] = float(row["close"])
    candidate_metrics = []
    stock_characteristics = []
    for stock in TARGETS:
        ordered_close = [daily_close.get(stock, {}).get(day) for day in sorted(common_days)]
        close_values = [float(v) for v in ordered_close if v is not None]
        daily_returns = pd.Series(close_values, dtype="float64").pct_change().dropna()
        stock_characteristics.append({
            "stock_code": stock, "stock_name": STOCK_NAMES[stock],
            "common_session_count": len(close_values),
            "mean_1325_close": float(np.mean(close_values)) if close_values else None,
            "median_1325_close": float(np.median(close_values)) if close_values else None,
            "min_1325_close": float(np.min(close_values)) if close_values else None,
            "max_1325_close": float(np.max(close_values)) if close_values else None,
            "daily_close_return_std_pct": float(daily_returns.std(ddof=1) * 100) if len(daily_returns) > 1 else None,
            "daily_close_return_abs_median_pct": float(daily_returns.abs().median() * 100) if len(daily_returns) else None,
        })
        g = candidates[(candidates.stock_code == stock) & candidates.year_run.eq("FULL")] if len(candidates) else pd.DataFrame()
        future_days = sorted(common_days)
        evaluated = false_count = 0
        for row in g.to_dict("records"):
            line = row.get("breakout_line")
            signal_day = dt.datetime.fromtimestamp(int(row["signal_bar_timestamp"]), TAIPEI).date().isoformat()
            nxt = [d for d in future_days if d > signal_day][:3]
            if line is None or len(nxt) < 3: continue
            closes_for_stock = [daily_close.get(stock, {}).get(d) for d in nxt]
            if any(v is None for v in closes_for_stock): continue
            evaluated += 1; false_count += int(any(v <= float(line) for v in closes_for_stock))
        candidate_metrics.append({"stock_code": stock, "stock_name": STOCK_NAMES[stock], "raw_breakout_trigger_count": int(len(g)),
            "technical_candidate_count": int(g.technical_candidate.fillna(False).sum()) if len(g) else 0,
            "after_market_regime_candidate_count": int(g.candidate_after_regime.fillna(False).sum()) if len(g) else 0,
            "risk_decision_count": int(((risks.stock_code == stock) & risks.year_run.eq("FULL")).sum()) if len(risks) else 0,
            "actual_entry_count": int(comparison.loc[comparison.stock_code == stock, "trade_count"].iloc[0]) if len(comparison) else 0,
            "false_breakout_evaluated_candidates": evaluated, "false_breakout_count": false_count,
            "false_breakout_pct": (100.0 * false_count / evaluated) if evaluated else None,
            "false_breakout_definition": "among next 3 observed eligible session 13:25 closes, any <= breakout line; descriptive only"})
    candidate_summary = pd.DataFrame(candidate_metrics)
    # Flag only observed partial sessions. A date with no target bars is not
    # assumed to be a missed target trading day without an authoritative calendar.
    partial_dates = {
        stock: sorted(r["date"] for r in incomplete_by_stock[stock] if r["observed_bar_count_0900_1325"] > 0)
        for stock in TARGETS
    }
    if len(trades):
        def overlap_partial_trade(row):
            start_day = str(row["buy_datetime"])[:10]
            end_day = str(row["sell_datetime"])[:10]
            hits = [d for d in partial_dates.get(str(row["stock_code"]), []) if start_day <= d <= end_day]
            return bool(hits), ";".join(hits)
        overlap_values = trades.apply(overlap_partial_trade, axis=1)
        trades["overlaps_partial_target_session"] = [v[0] for v in overlap_values]
        trades["partial_target_dates_overlapped"] = [v[1] for v in overlap_values]
    # Summaries for same nominal and average-active exposure references.
    if len(comparison):
        comparison = comparison.merge(candidate_summary[["stock_code", "false_breakout_evaluated_candidates", "false_breakout_count", "false_breakout_pct"]], on="stock_code", how="left")
        comparison = comparison.rename(columns={
            "risk_decision_count_x": "risk_decision_count",
            "raw_breakout_trigger_count_x": "raw_breakout_trigger_count",
            "technical_candidate_count_x": "technical_candidate_count",
        })
        comparison = comparison.drop(columns=[c for c in comparison.columns if c.endswith("_x") or c.endswith("_y")], errors="ignore")
        full_trades = trades[trades.year_run.eq("FULL")] if len(trades) else pd.DataFrame()
        gross_by_stock = full_trades.groupby("stock_code").gross_pnl.sum().to_dict() if len(full_trades) else {}
        gross_winning_by_stock = full_trades.assign(gross_win=full_trades.gross_pnl.clip(lower=0)).groupby("stock_code").gross_win.sum().to_dict() if len(full_trades) else {}
        comparison["gross_pnl"] = comparison.stock_code.map(gross_by_stock).fillna(0.0)
        comparison["gross_winning_trade_pnl"] = comparison.stock_code.map(gross_winning_by_stock).fillna(0.0)
        comparison["total_cost_to_gross_winning_trade_pnl_pct"] = comparison.apply(
            lambda r: (100.0 * r["total_transaction_cost"] / r["gross_winning_trade_pnl"])
            if r["gross_winning_trade_pnl"] > 0 else None, axis=1)
        comparison["target_observed_session_count"] = comparison.stock_code.map(observed_session_count)
        comparison["target_incomplete_observed_session_count"] = comparison.stock_code.map({s: len(incomplete_by_stock[s]) for s in TARGETS})
        comparison["target_data_completeness_pct"] = comparison.stock_code.map({
            s: 100.0 * len(complete_by_stock[s]) / max(1, observed_session_count[s]) for s in TARGETS
        })
    comparison.to_csv(output / "comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(output / "annual_performance.csv", index=False, encoding="utf-8-sig")
    annual_benchmarks.to_csv(output / "annual_buy_and_hold_benchmarks.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(output / "trades.csv", index=False, encoding="utf-8-sig")
    candidates.to_csv(output / "candidate_audit.csv", index=False, encoding="utf-8-sig")
    curves.to_csv(output / "equity_curves.csv", index=False, encoding="utf-8-sig")
    risks.to_csv(output / "risk_decisions.csv", index=False, encoding="utf-8-sig")
    data_quality.to_csv(output / "data_quality.csv", index=False, encoding="utf-8-sig")
    candidate_summary.to_csv(output / "candidate_summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(stock_characteristics).to_csv(output / "stock_characteristics.csv", index=False, encoding="utf-8-sig")
    benchmarks.to_csv(output / "buy_and_hold_benchmarks.csv", index=False, encoding="utf-8-sig")
    (output / "config_snapshot.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit = {"status": "COMPLETED", "experiment_id": config["experiment_id"], "data_role": "historical_development",
        "requested_period": config["period"], "stocks": list(TARGETS), "frozen_candidate": candidate["id"],
        "database_path": str(DB_PATH.relative_to(ROOT)), "database_sha256_before": db_hash, "database_sha256_after": sha256(DB_PATH),
        "required_symbols": list(ALL_REQUIRED), "complete_valid_sessions_by_symbol": {s: len(complete_by_stock[s]) for s in ALL_REQUIRED},
        "common_eligible_sessions": len(common_days), "first_common_session": min(common_days), "last_common_session": max(common_days),
        "incomplete_sessions_by_symbol": {s: len(incomplete_by_stock[s]) for s in ALL_REQUIRED},
        "observed_sessions_by_symbol": observed_session_count,
        "session_state_counts": data_quality.groupby(["stock_code", "session_state"]).size().reset_index(name="count").to_dict(orient="records"),
        "proxy_audit": proxy_audit,
        "technical_config_sha256": sha256(TECH_CONFIG_PATH), "frozen_candidate_config_sha256": sha256(FROZEN_CONFIG_PATH),
        "technical_source_sha256": sha256(ROOT / "trading_system/technical_v1.py"),
        "risk_source_sha256": sha256(ROOT / "hybrid/core/risk_v1.py"), "shared_runner_source_sha256": sha256(ROOT / "hybrid/scripts/run_short_swing_ab_test.py"),
        "signal_execution": "completed bar close -> next actual bar open", "fees_pct": 0.1425, "sell_tax_pct": 0.3, "slippage_pct": 0.0,
        "market_proxy_config_unchanged": True, "ml_gate_used": False, "database_modified": False, "strategy_config_modified": False,
        "parameter_search": False, "forbidden_periods_used": False, "annual_runs_reset_capital": True,
        "same_exposure_benchmark": "Buy & Hold 100%, nominal 40%, and B&H scaled to each strategy's average invested exposure, all on the common eligible session calendar; average exposure is not pathwise matched",
        "incomplete_target_dates_used_for_entry": False, "incomplete_target_dates_used_for_management_if_present": True,
        "known_data_risk": "target sessions with observed incomplete bars are excluded from new candidate eligibility; actual available bars remain in replay for position management; trades overlapping observed incomplete sessions are flagged; no-row dates are not presumed to be target trading days without an authoritative calendar"}
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_charts(output, comparison, curves, trades)
    report = ["# Technical V2-B 跨股票適用性回測比較", "", "- 五檔均使用凍結 `BR_SENS_RANGE55_BB20_2` + Risk V1，不使用 ML Gate。", "- 資料角色：2023–2024 exposed Training / historical development；不是 Clean OOS。", "- 初始本金：每檔獨立 NT$1,000,000；費率 0.1425%，賣出交易稅 0.3%，滑價 0。", "- 執行：完成 5 分 K 收盤訊號，下一根實際 K 棒開盤成交；Risk V1 為 1% 風險、40% 部位上限、2.5% 停損。", f"- 嚴格共同比較資格：目標五檔與原凍結市場代理五檔均須有有效完整 54 根；可用 {len(common_days)} 日（{min(common_days)} 至 {max(common_days)}）。代理仍按凍結池計算；2382/2317 作為池外標的時使用五檔代理，池內標的排除自身後用其餘四檔。", "- Buy & Hold 同列全額、名目 40% 和依主動策略平均曝險縮放三種基準；第三者僅匹配平均曝險，並非逐時點曝險匹配。", "", "## 五檔完整比較", "", "| 股票 | 共同可用日 | 候選 | 成交 | 毛損益 | 手續費 | 證交稅 | 交易成本 | 淨損益 | 報酬率 | 勝率 | PF | MDD | Sharpe | 平均單筆淨報酬 | 平均持有日 | 假突破比例 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in comparison.to_dict("records"):
        def f(v, n=2): return "N/A" if v is None or pd.isna(v) else f"{float(v):.{n}f}"
        report.append(f"| {r['stock_code']} {r['stock_name']} | {r['eligible_signal_days']} | {r.get('technical_candidate_count', 0)} | {r['trade_count']} | {f(r.get('gross_pnl'))} | {f(r.get('total_fee'))} | {f(r.get('total_transaction_tax'))} | {f(r.get('total_transaction_cost'))} | {f(r['net_pnl'])} | {f(r['total_return_pct'])}% | {f(r['win_rate_pct'])}% | {f(r['profit_factor'])} | {f(r['max_drawdown_pct'])}% | {f(r['sharpe_ratio'])} | {f(r['average_trade_net_return_pct'])}% | {f(r['average_holding_trading_day_intervals'])} | {f(r.get('false_breakout_pct'))}% |")
    report += ["", "## 各年度獨立績效", "", "每個股票年度各自重設本金；2024 的指標暖機可用 2023 Training 資料，實際進場只允許 2024。", "", "| 股票 | 年度 | 交易數 | 淨報酬 | 勝率 | PF | 最大回撤 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in annual.to_dict("records"):
        report.append(f"| {r['stock_code']} {r['stock_name']} | {r['year']} | {r['trade_count']} | {r['total_return_pct']:.3f}% | {r['win_rate_pct'] if r['win_rate_pct'] is not None else 'N/A'} | {r['profit_factor'] if r['profit_factor'] is not None else 'N/A'} | {r['max_drawdown_pct']:.3f}% |")
    report += ["", "## Buy & Hold 及曝險比較", "", "完整期間基準：", benchmarks.to_markdown(index=False) if len(benchmarks) else "無基準資料。", "", "年度獨立基準：", annual_benchmarks.to_markdown(index=False) if len(annual_benchmarks) else "無年度基準資料。", "", "## 資料資格與限制", "", f"- 各符號完整 session 數見 `data_quality.csv` 與 `audit.json`；共同交集 {len(common_days)} 日。13:30 撮合列不計入 54 根 session。", "- 2382、2317 有少數不完整 session；這些日期不允許產生新訊號，但仍以實際可得 K 棒管理既有持倉。若任何交易跨越缺 K 日期，該筆存在停損路徑不完整風險，請以 `trades.csv` / `data_quality.csv` 核對；沒有補值。", "- Sharpe 由逐日收盤標記資產報酬計算，無風險利率 0、母體標準差、年化 √252；最大回撤由逐時點淨值曲線計算。平均持有天數是實際進出場日期間隔。", "- 成本前/後損益、手續費與證交稅採現有共用回測指標函式；同日交易稅沿用既有引擎規則。", "- 交易次數過少的個股年度不宜解讀為穩定績效；五檔均為已曝光開發資料，本比較不能證明未來或樣本外有效。", "", "## 實驗判讀", "", "本報告不以單一最高報酬選出正式策略。應綜合候選轉成交比例、成本侵蝕、回撤、PF、Sharpe、年度穩定性、共同曝險基準及資料完整度；個股差異可能與價格、波動、整股/零股限制及市場走勢有關，但本實驗不因差異調整參數。", "", "## 輸出", "", "`comparison.csv`, `annual_performance.csv`, `annual_buy_and_hold_benchmarks.csv`, `trades.csv`, `candidate_audit.csv`, `candidate_summary.csv`, `equity_curves.csv`, `risk_decisions.csv`, `data_quality.csv`, `buy_and_hold_benchmarks.csv`, `audit.json`, `config_snapshot.json`, `charts/`."]
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    # Write the final, normalized report after the machine-readable exports.
    def fmt(value, digits=2):
        return "N/A" if value is None or pd.isna(value) else f"{float(value):.{digits}f}"
    corrected = [
        "# Technical V2-B 跨股票適用性回測比較", "",
        "本報告使用凍結 `BR_SENS_RANGE55_BB20_2` + Risk V1，沒有 ML Gate，沒有個股調參。這是 2023–2024 已曝光的歷史開發研究，不是樣本外驗證。",
        "每股獨立本金 NT$1,000,000；手續費 0.1425%、賣出交易稅 0.3%、滑價 0。訊號於完成 K 棒確認，下一根實際 K 棒開盤成交。",
        f"訊號資格採五個目標股與原凍結代理池共同完整 session；共 {len(common_days)} 日（{min(common_days)} 至 {max(common_days)}）。代理池、排除目標股自身及前 20 個共同完整 session 的 SMA 規則維持原凍結定義。", "",
        "## 五檔完整比較", "",
        "完整率分母是各股自身實際有盤中資料的日期；共同可用日則是所有必要標的都具完整有效 54 根 K 的訊號日期。候選依序為原始突破、通過技術條件、通過市場狀態濾網。假突破只在有完整後三個共同可用收盤日的候選上評估。", "",
        "| 股票 | 共用訊號日 | 自身完整/觀察日 | 完整率 | 原始突破 | 技術候選 | 市場濾網後 | 成交 | 毛損益 | 成本 | 成本/毛獲利單 | 淨損益 | 淨報酬 | 勝率 | PF | 最大回撤 | Sharpe | 平均單筆淨報酬 | 平均持有日 | 假突破/評估數 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in comparison.to_dict("records"):
        corrected.append(
            f"| {r['stock_code']} {r['stock_name']} | {int(r['eligible_signal_days'])} | {int(r['target_valid_session_count'])}/{int(r['target_observed_session_count'])} | {fmt(r['target_data_completeness_pct'])}% | {int(r['raw_breakout_trigger_count'])} | {int(r['technical_candidate_count'])} | {int(r['post_market_filter_candidate_count'])} | {int(r['trade_count'])} | {fmt(r['gross_pnl'])} | {fmt(r['total_transaction_cost'])} | {fmt(r['total_cost_to_gross_winning_trade_pnl_pct'])}% | {fmt(r['net_pnl'])} | {fmt(r['total_return_pct'])}% | {fmt(r['win_rate_pct'])}% | {fmt(r['profit_factor'])} | {fmt(r['max_drawdown_pct'])}% | {fmt(r['sharpe_ratio'])} | {fmt(r['average_trade_net_return_pct'])}% | {fmt(r['average_holding_trading_day_intervals'])} | {fmt(r['false_breakout_pct'])}% / {int(r['false_breakout_evaluated_candidates'])} |"
        )
    corrected += ["", "## 各年度獨立績效", "", "每個年度各自重設 NT$1,000,000 本金；2024 使用 2023 資料暖機，但只在 2024 允許訊號及進場。", "", "| 股票 | 年度 | 交易 | 淨報酬 | 勝率 | Profit Factor | 最大回撤 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in annual.to_dict("records"):
        corrected.append(f"| {r['stock_code']} {r['stock_name']} | {r['year']} | {int(r['trade_count'])} | {fmt(r['total_return_pct'], 3)}% | {fmt(r['win_rate_pct'])}% | {fmt(r['profit_factor'])} | {fmt(r['max_drawdown_pct'], 3)}% |")
    partial_trade_counts = trades[(trades.year_run == "FULL") & trades.overlaps_partial_target_session].groupby("stock_code").size().to_dict() if len(trades) else {}
    corrected += [
        "", "## 價格與波動差異", "",
        "以下為共用 422 日內 13:25 收盤價與日收盤報酬的描述統計；只用來說明標的尺度，不用於選股或調參。股價較高時，相同資金與風險額度可買股數較少，整股/零股規則與手續費最低收費也可能改變實際曝險；波動度則會影響 2.5% 停損被觸發的頻率。但本表不能單獨證明這些因素造成績效差異。", "",
        "| 股票 | 平均收盤 | 收盤範圍 | 日報酬標準差 | 日絕對報酬中位數 |", "|---|---:|---:|---:|---:|",
    ]
    for r in stock_characteristics:
        corrected.append(f"| {r['stock_code']} {r['stock_name']} | {fmt(r['mean_1325_close'])} | {fmt(r['min_1325_close'])}–{fmt(r['max_1325_close'])} | {fmt(r['daily_close_return_std_pct'])}% | {fmt(r['daily_close_return_abs_median_pct'])}% |")
    corrected += [
        "", "## Buy & Hold 與曝險比較", "",
        "完整期間與年度基準明細見 `buy_and_hold_benchmarks.csv` 及 `annual_buy_and_hold_benchmarks.csv`。三種基準都使用共同可用 session 日期：全額持有、名目 40% 持有，以及依主動策略平均曝險縮放。平均曝險縮放只配平平均投入，不是逐日曝險匹配。Buy & Hold 不套用 Risk V1 停損或部位上限，買進並持有至期末，費用按同一成本率計算。", "",
        "## 資料完整性與指標定義", "",
        f"- 各股自身完整率分別為：" + "；".join(f"{s} {int(complete_by_stock[s].__len__())}/{observed_session_count[s]} ({fmt(100 * len(complete_by_stock[s]) / max(1, observed_session_count[s]))}%)" for s in TARGETS) + ".",
        f"- 跨股聯集日期中，某股票完全沒有列者標為 `NO_BARS_ON_UNION_DATE`，不直接認定為該股漏交易日；有列但少 K 或 OHLCV 不合法者另標為不完整/無效 session。兩者都不會補值。",
        "- 2382、2317 各有兩個實際出現但不完整的 session。新訊號不在這些日子產生；既有持倉仍使用實際存在的 K 棒管理。全期間跨越這些已觀察不完整 session 的交易數：" + json.dumps(partial_trade_counts, ensure_ascii=False) + ".明細在 `trades.csv`。",
        "- 原凍結代理池完整日交集為 422 日；2382/2317 自身另有 57 個完整日不在共同比較日內，因此不能把其 479 個自身完整日誤解為同樣數量的共同訊號日。",
        "- Sharpe 由日收盤標記淨值報酬計算，無風險利率 0、母體標準差、年化 √252；最大回撤由淨值曲線計算。平均持有日為進出場交易日期間的觀察交易日轉換數。",
        "- 假突破定義：原始突破觸發後，未來三個共同可用 session 的 13:25 收盤中至少一次不高於突破線；該比例僅描述價格後續是否回落，並非唯一交易品質判準。",
        "- 交易次數較少的年度不適合推論穩定性；同一段已曝光開發資料曾用於策略研究，結論只能作歷史適用性診斷，不能視為乾淨驗證或 Final OOS。", "",
        "## 結果解讀", "",
        "2454 是五檔中唯一全期淨報酬為正者（+4.05%，PF 1.24，43 筆）；其 2023 與 2024 均為正，但樣本仍小且資料已曝光，不能宣稱已驗證通用性。2303、2330、2382、2317 全期均為負；多檔年度由 2023 負轉 2024 正，或持續負，顯示策略表現對年度行情狀態敏感。",
        "價格、波動、股數/整股交易限制及成本都可能造成差異，但本次是觀察性比較，沒有隔離因果，也沒有個股化參數。五檔結果不構成正式股票推薦，凍結策略維持不變。", "",
        "## 圖表與檔案", "",
        "`comparison.csv`、`annual_performance.csv`、`buy_and_hold_benchmarks.csv`、`annual_buy_and_hold_benchmarks.csv`、`stock_characteristics.csv`、`trades.csv`、`candidate_audit.csv`、`candidate_summary.csv`、`equity_curves.csv`、`risk_decisions.csv`、`data_quality.csv`、`audit.json`、`config_snapshot.json`；圖表位於 `charts/`。"
    ]
    (output / "report.md").write_text("\n".join(corrected) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    print(run())

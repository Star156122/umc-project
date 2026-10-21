"""Execute preregistered Entry Sensitivity Audit Exp001 (2023-2024 only)."""
from __future__ import annotations

import contextlib
import copy
import csv
import hashlib
import html
import io
import json
import math
import re
import subprocess
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

from hybrid.core.hybrid_poc_v1 import (  # noqa: E402
    HybridPOCError, build_prediction_lookup, sha256_file,
    validate_oof_predictions, validate_training_only_dates,
)
from hybrid.core.risk_v2_exp001 import execution_fill_check  # noqa: E402
from hybrid.core.strategy_v2_batch import regime_confirmation_passes  # noqa: E402
from hybrid.scripts.run_hybrid_common_coverage import (  # noqa: E402
    POC_CONFIG_PATH, directory_hashes, latest_ready_preflight, read_json, training_calendar,
)
from hybrid.scripts.run_hybrid_risk_v1 import validate_risk_config  # noqa: E402
from hybrid.scripts.run_representative_benchmark_exp001 import (  # noqa: E402
    CORE_UNIVERSE, FOLD_IDS, MODELS, POC_RUN, RISK_CONFIG, SUBJECTS,
    SUBJECT_NAMES, TECH_BATCH, TechnicalOnlyV2B, build_target_regime_context,
    metrics_row, read_subject_rows, trade_exposure,
)
from hybrid.scripts.run_strategy_v2_batch import simulate_one  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TZ = ZoneInfo("Asia/Taipei")
PREREG = ROOT / "research/strategy_entry_sensitivity_exp001_preregistration.md"
STATUS_PATH = ROOT / "research/current/strategy_status.json"
EXP_REGISTRY = ROOT / "research/registries/experiment_registry.csv"
DATA_REGISTRY = ROOT / "research/registries/dataset_registry.csv"
COMMON_RUN = ROOT / "exports/hybrid_common_coverage_20261018T121416162413"
OOF_PATH = POC_RUN / "ml_v1_baseline_oof_predictions.csv"
CONTROL_RESULTS = ROOT / "exports/representative_benchmark_exp001_20261020T111920272910/benchmark_per_fold.csv"
ENTRY_VALUES = (40, 55, 70)
STRATEGIES = ("TECHNICAL", "RF", "XGBOOST", "GRU")
STRATEGY_MODEL = {"TECHNICAL": "technical_only", "RF": "random_forest", "XGBOOST": "xgboost", "GRU": "gru"}


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, np.generic):
        return safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(safe(data), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_csv(path: Path, *, stock_col: str = "stock_code") -> pd.DataFrame:
    frames = [chunk.loc[chunk[stock_col].astype(str).isin(SUBJECTS)].copy()
              for chunk in pd.read_csv(path, dtype={stock_col: str}, chunksize=50_000)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def git_state() -> dict[str, Any]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain=v1"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return {"commit": commit, "working_tree_clean": not bool(status.strip()),
            "working_tree_status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
            "working_tree_status_lines": status.splitlines()}


def verify_prereg_hashes(text: str) -> dict[str, str]:
    expected = {}
    for line in text.splitlines():
        match = re.match(r"\|.*?`([^`]+)`.*?\| `([0-9a-f]{64})` \|", line)
        if match:
            expected[match.group(1)] = match.group(2)
    if len(expected) < 10:
        raise HybridPOCError("Preregistration is missing its captured input hash table")
    mismatches = {path: {"expected": digest, "actual": sha256_file(ROOT / path)}
                  for path, digest in expected.items()
                  if not (ROOT / path).is_file() or sha256_file(ROOT / path) != digest}
    if mismatches:
        raise HybridPOCError(f"Frozen preregistration input hash mismatch: {mismatches}")
    return expected


def candidate_clone(config: dict[str, Any], original: dict[str, Any], entry: int) -> dict[str, Any]:
    candidate = copy.deepcopy(original)
    candidate["params"]["breakout_entry_period"] = entry
    return candidate


def replay_candidate_scan(connection, config, family_name, family, candidate, stock, fold) -> list[dict[str, Any]]:
    full, _, app, _, eval_lower = tv1._load_fold_frame(connection, config, candidate, stock, fold)
    strategy = tv1.TechnicalV1Strategy(
        app, study_config=config, family_name=family_name, family=family, candidate=candidate,
        data_role="training", fold_id=fold["id"], is_backtest=True, is_simulation=True, local_only=True,
    )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = eval_lower
    stats = bt.MarketDataStats(tick_count=full.height,
        first_timestamp=float(full["kbar_timestamp"][0]), last_timestamp=float(full["kbar_timestamp"][-1]))
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full, stats)
    rows = list(strategy.candidate_signals)
    positions = {int(stamp): i for i, stamp in enumerate(full["kbar_timestamp"].to_list())}
    for row in rows:
        validate_training_only_dates([row["signal_time"], row["earliest_entry_time"]])
        signal_bar = int(pd.Timestamp(row["signal_time"]).timestamp()) - 299
        row.update({"stock_code": str(stock), "fold_id": fold["id"], "entry_value": candidate["params"]["breakout_entry_period"],
                    "_tradable_bar_index": positions.get(signal_bar)})
        if row["_tradable_bar_index"] is None:
            raise HybridPOCError(f"Signal timestamp did not map to an observed K bar: {stock}/{row['signal_time']}")
    return rows


def attach_coverage(candidates: list[dict[str, Any]], lookups: dict[str, dict], calendar: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    good, rejected = [], []
    for source in candidates:
        row = dict(source)
        day = str(row["signal_time"])[:10]
        prior_days = [date for date in calendar if date < day]
        expected_prior_day = prior_days[-1] if prior_days else None
        expected_signal_dates: set[str] = set()
        missing_models, invalid_models = [], []
        for model in MODELS:
            prediction = lookups[model].get((str(row["stock_code"]), day))
            if prediction is None:
                missing_models.append(model)
                continue
            signal_date, entry_date = str(prediction["signal_date"])[:10], str(prediction["entry_date"])[:10]
            expected_signal_dates.add(signal_date)
            if entry_date != day or signal_date != expected_prior_day or str(prediction["data_role"]) != "training" or str(prediction["is_oof"]).lower() not in {"true", "1"}:
                invalid_models.append(model)
        row["expected_previous_trading_day"] = expected_prior_day
        row["valid_oof_models"] = "|".join(m for m in MODELS if m not in missing_models and m not in invalid_models)
        row["missing_models"] = "|".join(missing_models)
        row["invalid_alignment_models"] = "|".join(invalid_models)
        for model in MODELS:
            prediction = lookups[model].get((str(row["stock_code"]), day))
            row[f"{model}_prediction"] = prediction.get("prediction") if prediction else None
            for key in ("prob_sell", "prob_hold", "prob_buy"):
                row[f"{model}_{key}"] = float(prediction[key]) if prediction else None
        if missing_models or invalid_models or len(expected_signal_dates) != 1:
            row["coverage_status"] = "DROPPED_MISSING_PREDICTION" if missing_models else "DROPPED_INVALID_ALIGNMENT"
            rejected.append(row)
        else:
            row["coverage_status"] = "COMMON_COVERAGE_PASS"
            good.append(row)
    return pd.DataFrame(good), pd.DataFrame(rejected)


def bar_number(signal_time: str) -> tuple[int, int]:
    ts = pd.Timestamp(signal_time).tz_convert(TZ)
    bar = int((ts.hour * 60 + ts.minute - 9 * 60) // 5)
    day = int(ts.strftime("%Y%m%d"))
    return day, bar


def timing_and_overlap(all_candidates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    timing_rows, overlap_rows = [], []
    for (stock, fold), group in all_candidates.groupby(["stock_code", "fold_id"], sort=True):
        sets: dict[int, dict[tuple[str, str], dict[str, Any]]] = {}
        records = []
        for row in group.to_dict("records"):
            signal = pd.Timestamp(row["signal_time"])
            fill = pd.Timestamp(row["earliest_entry_time"])
            identity = (str(row["signal_time"]), str(row["earliest_entry_time"]))
            value = int(row["entry_value"])
            sets.setdefault(value, {})[identity] = row
            day, intraday = bar_number(str(row["signal_time"]))
            records.append({"entry": value, "identity": identity, "signal": signal, "ordinal_key": (day, intraday), "row": row})
        # Exact overlap keys follow the registered identity: stock, fold, signal, fill.
        sigsets = {v: set(items) for v, items in sets.items()}
        s40, s55, s70 = (sigsets.get(v, set()) for v in ENTRY_VALUES)
        combos = [(40, 55, s40, s55), (55, 70, s55, s70), (40, 70, s40, s70)]
        for a, b, sa, sb in combos:
            intersection = sa & sb
            union = sa | sb
            overlap_rows.append({"stock_code": stock, "fold_id": fold, "entry_a": a, "entry_b": b,
                "intersection_count": len(intersection), "union_count": len(union),
                "overlap_ratio_jaccard": len(intersection) / len(union) if union else None,
                "a_only_count": len(sa - sb), "b_only_count": len(sb - sa)})
        overlap_rows.append({"stock_code": stock, "fold_id": fold, "entry_a": "40&55&70", "entry_b": "ALL",
            "intersection_count": len(s40 & s55 & s70), "union_count": len(s40 | s55 | s70),
            "overlap_ratio_jaccard": len(s40 & s55 & s70) / len(s40 | s55 | s70) if (s40 | s55 | s70) else None,
            "a_only_count": len(s40 - s55 - s70), "b_only_count": len(s55 - s40 - s70),
            "entry40_only_count": len(s40 - s55 - s70), "entry55_only_count": len(s55 - s40 - s70),
            "entry70_only_count": len(s70 - s40 - s55),
            "pairwise_40_55_count": len(s40 & s55), "pairwise_55_70_count": len(s55 & s70),
            "pairwise_40_70_count": len(s40 & s70)})
        # Time-only deterministic episode matching, no returns or outcomes.
        ordered = sorted(records, key=lambda x: (x["signal"], x["entry"]))
        episodes: list[list[dict[str, Any]]] = []
        for record in ordered:
            pos = int(record["row"]["_tradable_bar_index"])
            if not episodes:
                episodes.append([record]); continue
            prev = episodes[-1][-1]
            gap = pos - int(prev["row"]["_tradable_bar_index"])
            if gap <= 162:
                episodes[-1].append(record)
            else:
                episodes.append([record])
        for a, b in ((40, 55), (55, 70), (40, 70)):
            diffs, matched = [], 0
            for episode in episodes:
                first: dict[int, dict[str, Any]] = {}
                for record in episode:
                    first.setdefault(record["entry"], record)
                if a in first and b in first:
                    pa = int(first[a]["row"]["_tradable_bar_index"])
                    pb = int(first[b]["row"]["_tradable_bar_index"])
                    diffs.append(pb - pa)  # Positive means A signaled earlier than B.
                    matched += 1
            count_a = len(s40 if a == 40 else s55 if a == 55 else s70)
            count_b = len(s40 if b == 40 else s55 if b == 55 else s70)
            timing_rows.append({"stock_code": stock, "fold_id": fold, "comparison": f"{a}_vs_{b}",
                "matched_episode_count": matched, "unmatched_a_candidates": max(0, count_a - matched),
                "unmatched_b_candidates": max(0, count_b - matched), "mean_bars_a_earlier": float(np.mean(diffs)) if diffs else None,
                "median_bars_a_earlier": float(np.median(diffs)) if diffs else None,
                "matching_rule": "stock+fold; sort signal time; group consecutive signals <=162 observed tradable 5m bars apart; first signal per entry in episode; no outcomes used"})
    return pd.DataFrame(timing_rows), pd.DataFrame(overlap_rows)


def summarize_trade_metrics(result: dict[str, Any], trades: list[dict[str, Any]], bars: list[dict[str, Any]],
                            strategy: str, entry: int, candidate_count: int, regime_count: int,
                            ml_buy_count: int, stock: str, fold: dict[str, Any]) -> dict[str, Any]:
    pnl = bt._build_pnl_rows(trades)
    costs = sum(float(t.get("fee", 0) or 0) + float(t.get("tax", 0) or 0) for t in trades)
    gross_positive = sum(max(0.0, float(p["gross_pnl"])) for p in pnl)
    gross_pnl = sum(float(p["gross_pnl"]) for p in pnl)
    net_pnl = sum(float(p["net_pnl"]) for p in pnl)
    hold_bars = []
    for p in pnl:
        start = pd.Timestamp(p["buy_datetime"])
        end = pd.Timestamp(p["sell_datetime"])
        hold_bars.append(max(0.0, (end - start).total_seconds() / 300.0))
    exposure, max_exposure = trade_exposure(trades, bars, float(result["initial_capital"]))
    bar_positions = [int((pd.Timestamp(t["signal_time"]).hour * 60 + pd.Timestamp(t["signal_time"]).minute - 540) // 5)
                     for t in result.get("candidate_rows", [])]
    fill_times = [pd.Timestamp(t["earliest_entry_time"]).strftime("%H:%M:%S") for t in result.get("candidate_rows", [])]
    return {
        "entry_value": entry, "strategy": strategy, "model": STRATEGY_MODEL[strategy],
        "stock_code": stock, "stock_name": SUBJECT_NAMES[stock], "fold_id": fold["id"],
        "evaluate_start": fold["evaluate_start"], "evaluate_end": fold["evaluate_end"],
        "technical_candidate_count": candidate_count, "regime_pass_count": regime_count,
        "ml_buy_count": ml_buy_count if strategy != "TECHNICAL" else None,
        "trade_count": len(pnl), "return": float(result["total_return"]),
        "max_drawdown": float(result["max_drawdown"]), "sharpe": float(result["sharpe_ratio"]),
        "profit_factor": float(result["profit_factor"]) if result.get("profit_factor") is not None else None,
        "win_rate": float(result["win_rate"]) if result.get("win_rate") is not None else None,
        "average_capital_exposure": exposure, "maximum_capital_exposure": max_exposure,
        "gross_positive_pnl": gross_positive, "gross_pnl": gross_pnl,
        "transaction_cost": costs, "net_pnl": net_pnl,
        "net_edge_per_trade": net_pnl / len(pnl) if pnl else None,
        "cost_ratio": costs / gross_positive if gross_positive > 0 else None,
        "average_holding_bars": float(np.mean(hold_bars)) if hold_bars else None,
        "average_entry_timing_bars_after_0900": float(np.mean(bar_positions)) if bar_positions else None,
        "average_next_bar_fill_time": (pd.Series(fill_times).mode().iloc[0] if fill_times else None),
        "final_equity": float(result["final_equity"]), "initial_capital": float(result["initial_capital"]),
        "status": "COMPLETED" if len(pnl) >= 5 else "INSUFFICIENT",
    }


def aggregate(frame: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    rows = []
    for keys, g in frame.groupby(groups, dropna=False, sort=True):
        keys = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(groups, keys))
        for col in ("return", "max_drawdown", "sharpe", "profit_factor", "win_rate", "average_capital_exposure",
                    "maximum_capital_exposure", "net_edge_per_trade", "cost_ratio", "average_holding_bars",
                    "average_entry_timing_bars_after_0900"):
            if col in g:
                vals = pd.to_numeric(g[col], errors="coerce").dropna()
                row[col] = float(vals.mean()) if len(vals) else None
                if col == "return":
                    row["median_return"] = float(vals.median()) if len(vals) else None
                    row["return_std"] = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0 if len(vals) else None
            
        for col in ("technical_candidate_count", "regime_pass_count", "ml_buy_count", "trade_count", "gross_positive_pnl",
                    "gross_pnl", "transaction_cost", "net_pnl"):
            if col in g:
                vals = pd.to_numeric(g[col], errors="coerce")
                row[col] = int(vals.sum()) if col in {"technical_candidate_count", "regime_pass_count", "ml_buy_count", "trade_count"} else float(vals.sum())
        # Roll-up trade-level averages as pooled values so the displayed edge/cost
        # ratios reconcile with the summed trade counts and PnL/cost totals.
        row["net_edge_per_trade"] = (row["net_pnl"] / row["trade_count"]
                                      if row.get("trade_count", 0) else None)
        row["cost_ratio"] = (row["transaction_cost"] / row["gross_positive_pnl"]
                             if row.get("gross_positive_pnl", 0) > 0 else None)
        row["final_equity"] = float(pd.to_numeric(g["final_equity"], errors="coerce").mean())
        row["account_count"] = int(len(g))
        rows.append(row)
    return pd.DataFrame(rows)


def delta_vs_control(summary: pd.DataFrame) -> pd.DataFrame:
    metrics = ["return", "max_drawdown", "sharpe", "profit_factor", "win_rate", "trade_count",
               "average_capital_exposure", "net_edge_per_trade", "transaction_cost", "cost_ratio"]
    rows = []
    for strategy in STRATEGIES:
        base = summary.loc[(summary.entry_value == 55) & (summary.strategy == strategy)]
        for value in (40, 70):
            cur = summary.loc[(summary.entry_value == value) & (summary.strategy == strategy)]
            if base.empty or cur.empty:
                continue
            row = {"entry_value": value, "control": 55, "strategy": strategy}
            for metric in metrics:
                a = pd.to_numeric(base[metric], errors="coerce").mean()
                b = pd.to_numeric(cur[metric], errors="coerce").mean()
                row[f"delta_{metric}"] = float(b - a) if pd.notna(a) and pd.notna(b) else None
            rows.append(row)
    return pd.DataFrame(rows)


def classify_results(summary: pd.DataFrame, per_stock: pd.DataFrame, per_fold: pd.DataFrame) -> dict[str, Any]:
    tags: list[str] = []
    evidence = {}
    for strategy in STRATEGIES:
        sub = summary.loc[summary.strategy == strategy].set_index("entry_value")
        stock = per_stock.loc[per_stock.strategy == strategy]
        fold = per_fold.loc[per_fold.strategy == strategy]
        e40 = sub.loc[40] if 40 in sub.index else None
        e55 = sub.loc[55] if 55 in sub.index else None
        e70 = sub.loc[70] if 70 in sub.index else None
        if e40 is None or e55 is None or e70 is None:
            evidence[strategy] = {"classification": "MIXED", "reason": "required entry arm missing"}
            continue
        def consistent(value: int, metric: str, better: str) -> tuple[int, int]:
            v = per_stock.loc[(per_stock.strategy == strategy) & (per_stock.entry_value == value)]
            c = per_stock.loc[(per_stock.strategy == strategy) & (per_stock.entry_value == 55)]
            matched = v.merge(c, on="stock_code", suffixes=("_v", "_c"))
            lhs, rhs = pd.to_numeric(matched[f"{metric}_v"], errors="coerce"), pd.to_numeric(matched[f"{metric}_c"], errors="coerce")
            if better == "higher":
                count = int((lhs.notna() & rhs.notna() & (lhs >= rhs)).sum())
            else:
                count = int((lhs.notna() & rhs.notna() & (lhs <= rhs)).sum())
            vf = per_fold.loc[(per_fold.strategy == strategy) & (per_fold.entry_value == value)]
            cf = per_fold.loc[(per_fold.strategy == strategy) & (per_fold.entry_value == 55)]
            mf = vf.merge(cf, on="fold_id", suffixes=("_v", "_c"))
            fl, fr = pd.to_numeric(mf[f"{metric}_v"], errors="coerce"), pd.to_numeric(mf[f"{metric}_c"], errors="coerce")
            if better == "higher":
                fcount = int((fl.notna() & fr.notna() & (fl >= fr)).sum())
            else:
                fcount = int((fl.notna() & fr.notna() & (fl <= fr)).sum())
            return count, fcount
        edge_stocks, edge_folds = consistent(40, "net_edge_per_trade", "higher")
        exposure_stocks, exposure_folds = consistent(40, "average_capital_exposure", "higher")
        return_stocks, return_folds = consistent(40, "return", "higher")
        dd_stocks, dd_folds = consistent(40, "max_drawdown", "lower")
        cost_stocks, cost_folds = consistent(40, "cost_ratio", "lower")
        early_support = (e40["net_edge_per_trade"] is not None and e55["net_edge_per_trade"] is not None
            and e40["net_edge_per_trade"] > e55["net_edge_per_trade"]
            and e40["average_capital_exposure"] >= e55["average_capital_exposure"]
            and e40["return"] > e55["return"] and dd_stocks >= 2 and dd_folds >= 2
            and cost_stocks >= 2 and cost_folds >= 2 and edge_stocks >= 2 and edge_folds >= 2
            and exposure_stocks >= 2 and exposure_folds >= 2
            and return_stocks >= 2 and return_folds >= 2)
        early_adverse = [consistent(40, metric, "lower") for metric in ("net_edge_per_trade", "cost_ratio", "max_drawdown")]
        # Treat the direction as preregistered support only when it recurs across at least
        # two subjects and two folds; missing/insufficient cells do not count as evidence.
        early_not_supported = (e40["trade_count"] > e55["trade_count"] and any(
            stocks >= 2 and folds >= 2 for stocks, folds in early_adverse))
        strict_checks = [consistent(70, metric, direction) for metric, direction in (
            ("net_edge_per_trade", "higher"), ("profit_factor", "higher"),
            ("sharpe", "higher"), ("cost_ratio", "lower"))]
        strict_overall_direction = (
            e70["net_edge_per_trade"] is not None and e55["net_edge_per_trade"] is not None
            and e70["net_edge_per_trade"] > e55["net_edge_per_trade"]
            and pd.notna(e70["profit_factor"]) and pd.notna(e55["profit_factor"])
            and e70["profit_factor"] > e55["profit_factor"]
            and e70["sharpe"] > e55["sharpe"]
            and pd.notna(e70["cost_ratio"]) and pd.notna(e55["cost_ratio"])
            and e70["cost_ratio"] < e55["cost_ratio"])
        strict_supported = (e70["trade_count"] < e55["trade_count"] and strict_overall_direction and all(
            stocks >= 2 and folds >= 2 for stocks, folds in strict_checks))
        labels=[]
        if early_support: labels.append("EARLIER_ENTRY_SUPPORTED")
        if early_not_supported: labels.append("EARLIER_ENTRY_NOT_SUPPORTED")
        if strict_supported: labels.append("STRICTER_ENTRY_SUPPORTED")
        if not labels: labels.append("MIXED")
        tags.extend(labels)
        # The preregistration gives no numeric tolerance for "broadly similar" or
        # "materially worse". Keep ambiguous patterns MIXED instead of inventing a cutoff.
        value_stock_direction = {}
        for value in (40, 70):
            edge_s, edge_f = consistent(value, "net_edge_per_trade", "higher")
            ret_s, ret_f = consistent(value, "return", "higher")
            value_stock_direction[value] = {"edge_support_stocks": edge_s, "edge_support_folds": edge_f,
                                            "return_support_stocks": ret_s, "return_support_folds": ret_f}
        if ((value_stock_direction[40]["edge_support_stocks"] >= 2 and value_stock_direction[70]["edge_support_stocks"] < 2)
                or (value_stock_direction[70]["edge_support_stocks"] >= 2 and value_stock_direction[40]["edge_support_stocks"] < 2)):
            tags.append("POSSIBLE_PARAMETER_SENSITIVITY")
        for value in (40, 70):
            vrows=per_stock.loc[(per_stock.strategy==strategy)&(per_stock.entry_value==value)]
            crows=per_stock.loc[(per_stock.strategy==strategy)&(per_stock.entry_value==55)]
            merged=vrows.merge(crows,on="stock_code",suffixes=("_v","_c"))
            diffs=pd.to_numeric(merged["return_v"],errors="coerce")-pd.to_numeric(merged["return_c"],errors="coerce")
            if diffs.notna().sum()>=3 and (diffs>0).sum()>=2 and (diffs<0).sum()>=2:
                tags.append("MARKET_SPECIFIC_EFFECT")
        for v in ENTRY_VALUES:
            vf=per_fold.loc[(per_fold.strategy == strategy)&(per_fold.entry_value==v),"return"].mean()
            if pd.notna(vf) and pd.notna(e55["return"]) and (vf-e55["return"]) * (e40["return"]-e55["return"]) < 0:
                tags.append("FOLD_SENSITIVE")
        evidence[strategy] = {"classification": labels, "entry40_stock_edge_support_count": edge_stocks,
            "entry40_fold_edge_support_count": edge_folds, "entry40_stock_exposure_support_count": exposure_stocks,
            "entry40_fold_exposure_support_count": exposure_folds, "entry40_stock_return_support_count": return_stocks,
            "entry40_fold_return_support_count": return_folds, "entry40_stock_mdd_not_worse_count": dd_stocks,
            "entry40_fold_mdd_not_worse_count": dd_folds, "entry40_stock_cost_ratio_not_worse_count": cost_stocks,
            "entry40_fold_cost_ratio_not_worse_count": cost_folds, "entry70_reduced_trades": bool(e70["trade_count"] < e55["trade_count"]),
            "entry70_edge_pf_sharpe_cost_direction": [e70["net_edge_per_trade"], e70["profit_factor"], e70["sharpe"], e70["cost_ratio"]],
            "cross_stock_fold_direction": value_stock_direction}
    overall = "MIXED" if "MIXED" in tags or ("EARLIER_ENTRY_SUPPORTED" in tags and "EARLIER_ENTRY_NOT_SUPPORTED" in tags) else next((t for t in tags if t in {"ROBUST_REGION_EVIDENCE", "EARLIER_ENTRY_SUPPORTED", "EARLIER_ENTRY_NOT_SUPPORTED", "STRICTER_ENTRY_SUPPORTED", "POSSIBLE_PARAMETER_SENSITIVITY"}), "MIXED")
    return {"overall": overall, "tags": sorted(set(tags)), "by_strategy": evidence,
            "note": "No preregistered quantitative tolerance defines materially/broadly similar; ambiguous pattern remains MIXED. These labels diagnose and do not select a final Entry."}


def csv_write(path: Path, frame: pd.DataFrame) -> None:
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def render_full(audit: dict[str, Any], summary: pd.DataFrame, stock: pd.DataFrame, fold: pd.DataFrame,
                coverage: pd.DataFrame, timing: pd.DataFrame, overlap: pd.DataFrame, delta: pd.DataFrame) -> str:
    payload = json.dumps(safe({"audit":audit,"summary":summary.to_dict("records"),"stock":stock.to_dict("records"),
        "fold":fold.to_dict("records"),"coverage":coverage.to_dict("records"),"timing":timing.to_dict("records"),
        "overlap":overlap.to_dict("records"),"delta":delta.to_dict("records")}),ensure_ascii=False,allow_nan=False).replace("</","<\\/")
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Entry Sensitivity Audit Exp001</title><style>body{{font:15px/1.6 system-ui,"Microsoft JhengHei",sans-serif;background:#08131d;color:#e9f1f7;margin:0}}main{{max-width:1400px;margin:auto;padding:24px}}header,.card{{background:#102434;border:1px solid #284459;border-radius:14px;padding:20px;margin:16px 0}}h1,h2{{color:#fff}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:7px;border-bottom:1px solid #294458;text-align:left;white-space:nowrap}}pre{{white-space:pre-wrap}}.tag{{display:inline-block;padding:4px 8px;background:#164767;border-radius:20px;margin:3px}}</style><main><header><span class="tag">Training-only 2023–2024</span><span class="tag">Entry 40 / 55 Control / 70</span><h1>Entry Sensitivity Audit Exp001</h1><p>本報告依事前登錄比較 Entry 敏感度；結果不會自動選出或修改正式 Entry。</p><p>分類：{html.escape(str(audit["classification"]["overall"]))}</p></header><section class="card"><h2>Entry × Strategy</h2><div class="scroll">{summary.to_html(index=False,border=0)}</div></section><section class="card"><h2>Delta vs Control 55</h2><div class="scroll">{delta.to_html(index=False,border=0)}</div></section><section class="card"><h2>Per Stock</h2><div class="scroll">{stock.to_html(index=False,border=0)}</div></section><section class="card"><h2>Per Fold</h2><div class="scroll">{fold.to_html(index=False,border=0)}</div></section><section class="card"><h2>Common Coverage</h2><div class="scroll">{coverage.to_html(index=False,border=0)}</div></section><section class="card"><h2>Entry Timing</h2><div class="scroll">{timing.to_html(index=False,border=0)}</div></section><section class="card"><h2>Candidate Overlap</h2><div class="scroll">{overlap.to_html(index=False,border=0)}</div></section><section class="card"><h2>Audit</h2><pre>{html.escape(json.dumps(safe(audit),ensure_ascii=False,indent=2))}</pre></section></main></html>'''


def render_user(audit: dict[str, Any], summary: pd.DataFrame, stock: pd.DataFrame, fold: pd.DataFrame,
                timing: pd.DataFrame, overlap: pd.DataFrame, coverage: pd.DataFrame) -> str:
    payload=json.dumps(safe({"summary":summary.to_dict("records"),"stock":stock.to_dict("records"),"fold":fold.to_dict("records"),
        "timing":timing.to_dict("records"),"overlap":overlap.to_dict("records"),"coverage":coverage.to_dict("records"),"audit":audit}),ensure_ascii=False,allow_nan=False).replace("</","<\\/")
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Entry 敏感度比較</title><style>body{{font:16px/1.65 system-ui,"Microsoft JhengHei",sans-serif;background:#07141f;color:#edf5fb;margin:0}}main{{max-width:1080px;margin:auto;padding:22px}}header,.panel,.card{{background:#102536;border:1px solid #29485d;border-radius:16px;padding:20px;margin:14px 0}}header{{background:#0b354e}}.controls{{display:flex;gap:14px;flex-wrap:wrap}}select{{padding:10px;background:#071725;color:#fff;border:1px solid #45667a;border-radius:8px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}.card{{margin:0}}.num{{font-size:26px;font-weight:800;color:#56b0ff}}.muted{{color:#abc0d0}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:8px;border-bottom:1px solid #29485d;white-space:nowrap}}.scroll{{overflow:auto}}details{{padding:8px}}.warn{{color:#ffd98b}}@media(max-width:620px){{main{{padding:10px}}}}</style><main><header><h1>Breakout Entry 敏感度</h1><p>比較 40（較早／較敏感）、55（目前 Control）、70（較嚴格／較慢）。三檔股票、三個時間 Fold；沒有用最高報酬挑選參數。</p><p class="muted">期間：2023–2024 已曝光開發資料。這是研究比較，不代表未來績效。</p><div class="controls"><label>策略 <select id="strategy"><option value="TECHNICAL">Technical</option><option value="RF">RF</option><option value="XGBOOST">XGBoost</option><option value="GRU">GRU</option></select></label><label>股票 <select id="stockSel"><option value="ALL">三檔平均</option><option value="2330">2330 台積電</option><option value="2454">2454 聯發科</option><option value="2303">2303 聯電</option></select></label></div></header><section class="panel"><div class="grid" id="cards"></div><p id="interpret" class="muted"></p></section><section class="panel"><h2>三種 Entry 比較</h2><div id="table" class="scroll"></div></section><section class="panel"><details><summary>查看研究細節</summary><h3>Fold</h3><div id="fold" class="scroll"></div><h3>候選訊號時間差</h3><div id="timing" class="scroll"></div><h3>候選重疊</h3><div id="overlap" class="scroll"></div><h3>共同資料範圍</h3><div id="coverage" class="scroll"></div></details></section><section class="panel muted"><p>Entry 55 仍是 Control。本輪只提供診斷證據，最終 Entry 決策留給獨立 P2 步驟。</p><p>分類：{html.escape(str(audit["classification"]["overall"]))}</p></section></main><script>const D={payload};const pct=x=>x==null?'—':(Number(x)*100).toFixed(2)+'%';const num=x=>x==null?'—':Number(x).toFixed(2);function rows(id,arr,cols){{const el=document.querySelector(id);el.innerHTML=arr.length?'<table><tr>'+cols.map(x=>'<th>'+x[0]+'</th>').join('')+'</tr>'+arr.map(r=>'<tr>'+cols.map(x=>'<td>'+(x[1](r)??'—')+'</td>').join('')+'</tr>').join('')+'</table>':'<p>無資料</p>'}}function draw(){{const s=document.querySelector('#strategy').value,code=document.querySelector('#stockSel').value;let base=D.summary.filter(r=>r.strategy===s&&(code==='ALL'||r.stock_code===code));if(code==='ALL')base=base.filter(r=>r.scope==='overall');else base=base.filter(r=>r.scope==='per_stock');base.sort((a,b)=>a.entry_value-b.entry_value);const cards=base.map(r=>'<article class="card"><h2>Breakout '+r.entry_value+(r.entry_value===55?'｜Control':'')+'</h2><div class="num">'+pct(r.return)+'</div><div>平均報酬</div><p>最大回撤 '+pct(r.max_drawdown)+'<br>Sharpe '+num(r.sharpe)+'<br>交易數 '+r.trade_count+'<br>資金曝險 '+pct(r.average_capital_exposure)+'<br>每筆淨收益 '+(r.net_edge_per_trade==null?'—':'NT$'+Number(r.net_edge_per_trade).toFixed(0))+'<br>交易成本占比 '+pct(r.cost_ratio)+'</p></article>').join('');document.querySelector('#cards').innerHTML=cards;document.querySelector('#interpret').textContent='交易數、報酬、風險、每筆淨收益與成本需一起看；不同 Entry 會產生不同候選機會。';rows('#table',base,[['Entry',r=>r.entry_value],['平均報酬',r=>pct(r.return)],['最大回撤',r=>pct(r.max_drawdown)],['Sharpe',r=>num(r.sharpe)],['Profit Factor',r=>num(r.profit_factor)],['勝率',r=>pct(r.win_rate)],['交易數',r=>r.trade_count],['曝險',r=>pct(r.average_capital_exposure)],['Net PnL',r=>'NT$'+Number(r.net_pnl||0).toFixed(0)],['每筆淨收益',r=>r.net_edge_per_trade==null?'—':'NT$'+Number(r.net_edge_per_trade).toFixed(0)],['成本比例',r=>pct(r.cost_ratio)]]);const f=D.fold.filter(r=>r.strategy===s&&(code==='ALL'||r.stock_code===code));rows('#fold',f,[['Entry',r=>r.entry_value],['股票',r=>r.stock_code],['Fold',r=>r.fold_id],['報酬',r=>pct(r.return)],['回撤',r=>pct(r.max_drawdown)],['PF',r=>num(r.profit_factor)],['交易',r=>r.trade_count]]);rows('#timing',D.timing.filter(r=>code==='ALL'||r.stock_code===code),[['股票',r=>r.stock_code],['Fold',r=>r.fold_id],['比較',r=>r.comparison],['匹配事件',r=>r.matched_episode_count],['平均提前 bar',r=>r.mean_bars_a_earlier],['中位數',r=>r.median_bars_a_earlier]]);rows('#overlap',D.overlap.filter(r=>code==='ALL'||r.stock_code===code),[['股票',r=>r.stock_code],['Fold',r=>r.fold_id],['Entry A',r=>r.entry_a],['Entry B',r=>r.entry_b],['交集',r=>r.intersection_count],['Jaccard',r=>num(r.overlap_ratio_jaccard)]]);rows('#coverage',D.coverage.filter(r=>code==='ALL'||r.stock_code===code),[['Entry',r=>r.entry_value],['股票',r=>r.stock_code],['Fold',r=>r.fold_id],['Raw Candidate',r=>r.original_technical_candidate_count],['有效 OOF',r=>r.common_coverage_count],['缺預測',r=>r.dropped_missing_prediction],['錯位',r=>r.dropped_invalid_alignment]]);}}document.querySelector('#strategy').addEventListener('change',draw);document.querySelector('#stockSel').addEventListener('change',draw);draw();</script></html>'''


def update_status(audit: dict[str, Any], out: Path) -> None:
    state=json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    item=state["entry_sensitivity_exp001"]
    item.update({"status":"COMPLETED","export_directory":out.relative_to(ROOT).as_posix(),
                 "report":"research/strategy_entry_sensitivity_exp001.md","classification":audit["classification"],
                 "backtest_executed":True,"parameter_search_performed":False,"ml_retrained":False,
                 "2025_used":False,"2026_used":False,"final_oos_used":False,"strategy_changed":False})
    state["updated_at"]=datetime.now(TZ).date().isoformat()
    STATUS_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def update_registries(out: Path, classification: str) -> None:
    with EXP_REGISTRY.open("r",encoding="utf-8-sig",newline="") as f:
        rows=list(csv.DictReader(f)); fields=list(rows[0])
    for row in rows:
        if row.get("experiment_id")=="strategy-entry-sensitivity-exp001":
            row.update({"status":"completed","decision":classification,
                "report_file":(out/"entry_sensitivity_report.html").relative_to(ROOT).as_posix(),
                "result_file":(out/"entry_sensitivity_audit.json").relative_to(ROOT).as_posix(),
                "notes":"Preregistered Entry 40/55/70 audit completed; only Entry varied; no final Entry selected."})
    with EXP_REGISTRY.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    with DATA_REGISTRY.open("r",encoding="utf-8-sig",newline="") as f:
        data=list(csv.DictReader(f)); dfields=list(data[0])
    for row in data:
        if row.get("experiment_id","").find("strategy-entry-sensitivity-exp001")>=0:
            row["results_viewed"]="true"
            row["report_location"]=(out/"entry_sensitivity_user_analysis.html").relative_to(ROOT).as_posix()
            row["result_location"]=(out/"entry_sensitivity_audit.json").relative_to(ROOT).as_posix()
            row["notes"]="Entry Sensitivity Exp001 executed on 2023-2024 exposed development data; historical results viewed; no Entry adopted."
    with DATA_REGISTRY.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=dfields);writer.writeheader();writer.writerows(data)


def main() -> int:
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--preflight",action="store_true")
    args=parser.parse_args()
    if args.execute == args.preflight:
        parser.error("choose exactly one of --preflight or --execute")
    status=json.loads(STATUS_PATH.read_text(encoding="utf-8"))["entry_sensitivity_exp001"]
    prereg_text=PREREG.read_text(encoding="utf-8")
    if status.get("status")!="PREREGISTERED_NOT_EXECUTED" or "PREREGISTERED_NOT_EXECUTED" not in prereg_text:
        raise HybridPOCError("Preregistration is not in the expected not-executed state")
    if tuple(status.get("values",[]))!=ENTRY_VALUES or status.get("control")!=55 or status.get("exit_fixed")!=30:
        raise HybridPOCError("Entry, control, or exit conditions conflict with preregistration")
    if status.get("market_regime_fixed")!="TECH_V2_B_MARKET_REGIME_FILTER" or status.get("risk_fixed")!="RISK_V1":
        raise HybridPOCError("Market Regime or Risk differs from preregistration")
    if tuple(status.get("models",[]))!=MODELS or tuple(status.get("stocks",[]))!=SUBJECTS or tuple(status.get("folds",[]))!=FOLD_IDS:
        raise HybridPOCError("Model, stock, or Fold set conflicts with preregistration")
    if tuple(status.get("period",[]))!=("2023-01-01","2024-12-31"):
        raise HybridPOCError("Data period is not the registered 2023-2024 period")
    prereg_hashes=verify_prereg_hashes(prereg_text)
    risk_cfg=json.loads(RISK_CONFIG.read_text(encoding="utf-8"));validate_risk_config(risk_cfg)
    if (risk_cfg["risk_per_trade"],risk_cfg["stop_loss_pct"],risk_cfg["max_position_fraction"])!=(0.01,0.025,0.4):
        raise HybridPOCError("Risk V1 parameters differ from preregistration")
    poc=read_json(POC_CONFIG_PATH); config_path=ROOT/poc["technical"]["config"]
    config=tv1.read_config(config_path);tv1.assert_execution_ready(config,"training")
    folds=config["walk_forward_folds"]
    if tuple(f["id"] for f in folds)!=FOLD_IDS or any(f["evaluate_start"]<"2023-01-01" or f["evaluate_end"]>"2024-12-31" for f in folds):
        raise HybridPOCError("Fold schedule conflicts with preregistration")
    for f in folds:
        expected=next((x for x in re.findall(r"\| (WF[123]) \| ([^|]+) \| ([^|]+) \|",prereg_text) if x[0]==f["id"]),None)
        if expected is None or expected[1].strip()!=f["train_start"]+"–"+f["train_end"] or expected[2].strip()!=f["evaluate_start"]+"–"+f["evaluate_end"]:
            raise HybridPOCError(f"Registered fold dates mismatch: {f['id']}")
    _,preflight=latest_ready_preflight()
    if preflight.get("status")!="PREFLIGHT_READY" or preflight.get("forbidden_roles_queried"):
        raise HybridPOCError("Technical Training preflight is not ready")
    common_audit=read_json(COMMON_RUN/"common_coverage_audit.json")
    if common_audit.get("status")!="PASS" or common_audit.get("training_only") is not True:
        raise HybridPOCError("Frozen Common Coverage audit is not PASS/Training-only")
    common55=read_csv(COMMON_RUN/"common_coverage_candidates.csv")
    exclusions=read_csv(COMMON_RUN/"common_coverage_exclusions.csv")
    prior_stock_path=ROOT/"exports/representative_benchmark_exp001_20261020T111920272910/benchmark_per_stock.csv"
    prior_fold_path=ROOT/"exports/representative_benchmark_exp001_20261020T111920272910/benchmark_per_fold.csv"
    prior_summary_path=ROOT/"exports/representative_benchmark_exp001_20261020T111920272910/benchmark_summary.csv"
    prior_stock=pd.read_csv(prior_stock_path,dtype={"stock_code":str})
    prior_fold=pd.read_csv(prior_fold_path)
    prior_summary=pd.read_csv(prior_summary_path)
    required_benchmark_columns={"strategy","average_return","average_max_drawdown","average_sharpe",
        "total_trade_count","net_pnl_sum","transaction_cost_sum","average_final_equity"}
    for label,frame in (("per-stock",prior_stock),("per-fold",prior_fold),("overall",prior_summary)):
        if not required_benchmark_columns.issubset(frame.columns):
            raise HybridPOCError(f"Frozen benchmark {label} summary lacks required columns: {sorted(required_benchmark_columns-set(frame.columns))}")
    if not set(common55.stock_code.astype(str))<=set(SUBJECTS):
        raise HybridPOCError("Common Coverage source contains undeclared outcome subjects")
    oof=read_csv(OOF_PATH)
    oof_audit=validate_oof_predictions(oof,expected_models=MODELS,expected_stocks=SUBJECTS,period_start="2023-01-01",period_end="2024-12-31")
    if oof_audit.get("forbidden_roles_used") or oof_audit.get("in_sample_predictions_used"):
        raise HybridPOCError("OOF audit indicates forbidden/in-sample data")
    lookups={m:build_prediction_lookup(oof,m) for m in MODELS}
    fam_name,family,original=tv1.candidate_by_id(config,"BR_SENS_RANGE55_BB20_2")
    if fam_name!="breakout" or original["params"].get("breakout_entry_period")!=55 or original["params"].get("breakout_exit_period")!=30:
        raise HybridPOCError("Control candidate does not match registered Breakout 55 / Exit 30")
    db=tv1.resolve_market_data_path(ROOT,config); db_hash_before=sha256_file(db)
    source_dir=ROOT/poc["technical"]["source_run"]
    source_audit_path=source_dir/"technical_v1_audit.json"
    source_signals_path=source_dir/"technical_v1_signals.csv"
    source_audit=read_json(source_audit_path)
    source_hashes={p.name:sha256_file(p) for p in (source_audit_path,source_dir/"technical_v1_results.json",source_signals_path)}
    if source_hashes!=source_audit.get("source_hashes",source_audit.get("technical_source_hashes",source_hashes)) and source_audit.get("source_hashes"):
        raise HybridPOCError("Frozen Technical 55 source artifact hashes do not match its audit")
    source_signals=read_csv(source_signals_path)
    source_signals=source_signals.loc[(source_signals.candidate_id.astype(str)=="BR_SENS_RANGE55_BB20_2") &
        (source_signals.technical_signal.astype(str)=="BUY_CANDIDATE") & source_signals.fold_id.astype(str).isin(FOLD_IDS)].copy()
    validate_training_only_dates(source_signals.signal_time)
    git_before=git_state()
    with tv1._readonly_connection(db) as connection:
        calendar=training_calendar(connection,config,list(CORE_UNIVERSE))
        all_candidate_rows=[]
        for entry in ENTRY_VALUES:
            variant=candidate_clone(config,original,entry)
            for fold in folds:
                for stock in SUBJECTS:
                    if entry==55:
                        source=source_signals.loc[(source_signals.stock_code.astype(str)==stock)&(source_signals.fold_id.astype(str)==fold["id"])].to_dict("records")
                        # The registered Control uses the frozen Technical V1 signal artifact; its
                        # signal/fill identities must remain byte-for-byte aligned with Common Coverage.
                        full,_,_,_,_=tv1._load_fold_frame(connection,config,variant,stock,fold)
                        positions={int(ts):i for i,ts in enumerate(full["kbar_timestamp"].to_list())}
                        for row in source:
                            signal_time=pd.Timestamp(row["signal_time"])
                            fill_time=pd.Timestamp(row["earliest_entry_time"])
                            signal_start=int(signal_time.timestamp())-299
                            position=positions.get(signal_start)
                            if position is None:
                                fill_stamp=int(fill_time.timestamp())
                                if fill_stamp not in positions:
                                    raise HybridPOCError(f"Control fill timestamp did not map to a K bar: {stock}/{fill_time}")
                                position=positions[fill_stamp]-1
                            row.update({"entry_value":55,"_tradable_bar_index":position})
                            all_candidate_rows.append(row)
                    else:
                        all_candidate_rows.extend(replay_candidate_scan(connection,config,fam_name,family,variant,stock,fold))
        all_candidates=pd.DataFrame(all_candidate_rows)
        if all_candidates.empty: raise HybridPOCError("No Technical candidates were generated in the registered folds")
        all_candidates["entry_value"]=all_candidates.entry_value.astype(int)
        covered=[]; dropped=[]
        for entry in ENTRY_VALUES:
            subset=all_candidates.loc[all_candidates.entry_value==entry]
            good,bad=attach_coverage(subset.to_dict("records"),lookups,calendar)
            if len(good):covered.append(good)
            if len(bad):dropped.append(bad)
        coverage_candidates=pd.concat(covered,ignore_index=True) if covered else pd.DataFrame()
        coverage_dropped=pd.concat(dropped,ignore_index=True) if dropped else pd.DataFrame()
        # The frozen control's Common Coverage schedule must reproduce exactly before performance execution.
        keycols=["stock_code","fold_id","signal_time","earliest_entry_time"]
        old_keys=set(tuple(map(str,r)) for r in common55[keycols].itertuples(index=False,name=None))
        new55=coverage_candidates.loc[coverage_candidates.entry_value==55]
        new_keys=set(tuple(map(str,r)) for r in new55[keycols].itertuples(index=False,name=None))
        if old_keys!=new_keys:
            raise HybridPOCError(f"Control candidate/common coverage reproduction failed: prior={len(old_keys)} current={len(new_keys)} missing_examples={sorted(old_keys-new_keys)[:5]} extra_examples={sorted(new_keys-old_keys)[:12]}")
        if args.preflight:
            dropped_counts = [
                {"entry_value": int(entry), "coverage_status": str(status), "count": int(count)}
                for (entry, status), count in coverage_dropped.groupby(["entry_value", "coverage_status"]).size().items()
            ] if len(coverage_dropped) else []
            print(json.dumps({"status":"PREFLIGHT_PASS","performance_calculated":False,"entry_candidate_counts":all_candidates.groupby("entry_value").size().to_dict(),
                "common_coverage_counts":coverage_candidates.groupby("entry_value").size().to_dict(),"dropped_counts":dropped_counts,
                "control_schedule_reproduction":"PASS","oof_audit":oof_audit,"data_period":["2023-01-01","2024-12-31"],"forbidden_roles_used":False},ensure_ascii=False,indent=2,default=str))
            return 0
        stamp=datetime.now(TZ).strftime("%Y%m%dT%H%M%S%f%z")
        out=ROOT/"exports"/f"strategy_entry_sensitivity_exp001_{stamp}"
        out.mkdir(parents=True,exist_ok=False)
        regime_context,regime_audit=build_target_regime_context(connection,set(pd.to_datetime(all_candidates.signal_time).dt.strftime("%Y-%m-%d")))
        schedule_map={}
        for (entry,stock,fold_id),g in coverage_candidates.groupby(["entry_value","stock_code","fold_id"]):
            schedule_map[(int(entry),str(stock),str(fold_id))]={pd.Timestamp(x).tz_localize(TZ).isoformat(timespec="microseconds") if pd.Timestamp(x).tzinfo is None else pd.Timestamp(x).tz_convert(TZ).isoformat(timespec="microseconds") for x in g.earliest_entry_time}
        result_rows=[];trade_rows=[];gate_rows=[];fill_bars={}
        for fold in folds:
            for stock in SUBJECTS:
                for entry in ENTRY_VALUES:
                    variant=candidate_clone(config,original,entry)
                    schedule=schedule_map.get((entry,stock,fold["id"]),set())
                    raw_count=len(all_candidates.loc[(all_candidates.entry_value==entry)&(all_candidates.stock_code==stock)&(all_candidates.fold_id==fold["id"])])
                    common_rows=coverage_candidates.loc[(coverage_candidates.entry_value==entry)&(coverage_candidates.stock_code==stock)&(coverage_candidates.fold_id==fold["id"])].to_dict("records")
                    regime_count=sum(bool(regime_context.get((stock,str(r["signal_time"])[:10]),{}).get("eligible")) and regime_confirmation_passes(regime_context.get((stock,str(r["signal_time"])[:10]),{}).get("last_20_proxy_closes",[])) for r in common_rows)
                    buy_counts={m:sum(str(r.get(f"{m}_prediction")).upper()=="BUY" for r in common_rows) for m in MODELS}
                    full,evaluation,app,_,_=tv1._load_fold_frame(connection,config,variant,stock,fold)
                    bars=bt.build_kbar_rows_from_frame(evaluation,app)
                    for bar in bars:fill_bars[(stock,int(bar["time"]))]=float(bar["open"])
                    # Technical-alone control shares exactly the same OOF-eligible candidate schedule.
                    tech=TechnicalOnlyV2B(config=app,study_config=config,family_name=fam_name,family=family,candidate=variant,
                        data_role="training",fold_id=fold["id"],is_backtest=True,is_simulation=True,local_only=True,
                        candidate_schedule=schedule,regime_context=regime_context)
                    tech.configure_local_account(app);tech.evaluation_start_timestamp=tv1._timestamp_bounds(fold["evaluate_start"],fold["evaluate_end"])[0]
                    stats=bt.MarketDataStats(tick_count=full.height,first_timestamp=float(full["kbar_timestamp"][0]),last_timestamp=float(full["kbar_timestamp"][-1]))
                    with contextlib.redirect_stdout(io.StringIO()):bt.replay_kbars(tech,full,stats)
                    tech_trades=bt._build_trade_rows(tech,app)
                    tech_stats=bt.calculate_performance_metrics(bt._build_pnl_rows(tech_trades),app.initial_capital,trading_days=sorted({str(b["datetime"])[:10] for b in bars}))
                    tech_stats.update(bt.calculate_mark_to_market_risk(tech_trades,bars,app.initial_capital))
                    if tech.local_position!=0:raise HybridPOCError(f"Technical-only position remained open {stock}/{fold['id']}/E{entry}")
                    tech_result={"total_return":tech_stats["total_return"],"max_drawdown":tech_stats["max_drawdown"],"sharpe_ratio":tech_stats["sharpe_ratio"],
                        "profit_factor":tech_stats["profit_factor"] if tech_stats["completed_trades"] else None,"win_rate":tech_stats["win_rate"] if tech_stats["completed_trades"] else None,
                        "trade_count":tech_stats["completed_trades"],"net_pnl":tech_stats["net_pnl"],"gross_pnl":tech_stats["gross_pnl"],
                        "transaction_cost":tech_stats["transaction_cost"],"final_equity":tech_stats["ending_capital"],"initial_capital":app.initial_capital,
                        "candidate_rows":common_rows}
                    row=summarize_trade_metrics(tech_result,tech_trades,bars,"TECHNICAL",entry,raw_count,regime_count,0,stock,fold)
                    result_rows.append(row);trade_rows.extend({"entry_value":entry,"strategy":"TECHNICAL","stock_code":stock,"fold_id":fold["id"],**x} for x in tech_trades)
                    for model in MODELS:
                        model_key={"random_forest":"RF","xgboost":"XGBOOST","gru":"GRU"}[model]
                        res,trades,gates,risks,_=simulate_one(connection=connection,config=config,family_name=fam_name,family=family,candidate=variant,
                            stock=stock,fold=fold,version="TECH_V2_B",model=model,lookup=lookups[model],schedule=schedule,volume_context={},regime_context=regime_context)
                        res["candidate_rows"]=common_rows
                        row=summarize_trade_metrics(res,trades,bars,model_key,entry,raw_count,regime_count,buy_counts[model],stock,fold)
                        result_rows.append(row);trade_rows.extend({"entry_value":entry,"strategy":model_key,"stock_code":stock,"fold_id":fold["id"],**x} for x in trades)
                        gate_rows.extend({"entry_value":entry,"strategy":model_key,"stock_code":stock,"fold_id":fold["id"],**g} for g in gates)
        per_detail=pd.DataFrame(result_rows)
        if len(per_detail)!=ENTRY_VALUES.__len__()*len(STRATEGIES)*len(SUBJECTS)*len(FOLD_IDS):
            raise HybridPOCError(f"Incomplete metric matrix: {len(per_detail)} rows")
        if per_detail.duplicated(["entry_value","strategy","stock_code","fold_id"]).any():raise HybridPOCError("Duplicate strategy/account cell")
        overall=aggregate(per_detail,["entry_value","strategy"])
        overall["scope"]="overall"
        per_stock=aggregate(per_detail,["entry_value","strategy","stock_code"]);per_stock["scope"]="per_stock"
        per_fold=aggregate(per_detail,["entry_value","strategy","fold_id"]);per_fold["scope"]="per_fold"
        summary=pd.concat([overall,per_stock,per_fold],ignore_index=True,sort=False)
        timing,overlap=timing_and_overlap(all_candidates)
        delta=delta_vs_control(overall)
        coverage_rows=[]
        for entry in ENTRY_VALUES:
            for stock in SUBJECTS:
                for fold in folds:
                    raw=int(((all_candidates.entry_value==entry)&(all_candidates.stock_code==stock)&(all_candidates.fold_id==fold["id"])).sum())
                    good=int(((coverage_candidates.entry_value==entry)&(coverage_candidates.stock_code==stock)&(coverage_candidates.fold_id==fold["id"])).sum())
                    bad=coverage_dropped.loc[(coverage_dropped.entry_value==entry)&(coverage_dropped.stock_code==stock)&(coverage_dropped.fold_id==fold["id"])] if len(coverage_dropped) else pd.DataFrame()
                    miss=int((bad.coverage_status=="DROPPED_MISSING_PREDICTION").sum()) if len(bad) else 0
                    invalid=int((bad.coverage_status=="DROPPED_INVALID_ALIGNMENT").sum()) if len(bad) else 0
                    coverage_rows.append({"entry_value":entry,"stock_code":stock,"fold_id":fold["id"],"original_technical_candidate_count":raw,
                        "candidates_with_valid_rf_xgb_gru_oof":good,"dropped_missing_prediction":miss,"dropped_invalid_alignment":invalid,
                        "common_coverage_count":good,"common_coverage_fair":True})
        coverage_frame=pd.DataFrame(coverage_rows)
        classes=classify_results(overall,per_stock,per_fold)
        execution_audit=execution_fill_check(trade_rows,fill_bars)
        if execution_audit.get("status")!="PASS":raise HybridPOCError(f"Next-bar-open fill check failed: {execution_audit}")
        # Control cells must reproduce the existing fixed benchmark on exactly the same 55/common candidate set.
        prior_stock=pd.read_csv(ROOT/"exports/representative_benchmark_exp001_20261020T111920272910/benchmark_per_stock.csv",dtype={"stock_code":str})
        prior_fold=pd.read_csv(ROOT/"exports/representative_benchmark_exp001_20261020T111920272910/benchmark_per_fold.csv")
        prior_summary=pd.read_csv(ROOT/"exports/representative_benchmark_exp001_20261020T111920272910/benchmark_summary.csv")
        check=[]
        old_names={"TECHNICAL":"TECHNICAL_ONLY","RF":"HYBRID_RF","XGBOOST":"HYBRID_XGB","GRU":"HYBRID_GRU"}
        metric_pairs=(("return","average_return"),("max_drawdown","average_max_drawdown"),("sharpe","average_sharpe"),
                      ("trade_count","total_trade_count"),("net_pnl","net_pnl_sum"),
                      ("transaction_cost","transaction_cost_sum"),("final_equity","average_final_equity"))
        for strategy,old_strategy in old_names.items():
            current_stock=per_stock.loc[(per_stock.entry_value==55)&(per_stock.strategy==strategy)]
            old_stock_rows=prior_stock.loc[prior_stock.strategy==old_strategy]
            current_fold=per_fold.loc[(per_fold.entry_value==55)&(per_fold.strategy==strategy)]
            old_fold_rows=prior_fold.loc[prior_fold.strategy==old_strategy]
            current_overall=overall.loc[(overall.entry_value==55)&(overall.strategy==strategy)]
            old_overall=prior_summary.loc[prior_summary.strategy==old_strategy]
            if len(current_stock)!=len(SUBJECTS) or len(old_stock_rows)!=len(SUBJECTS) or len(current_fold)!=len(FOLD_IDS) or len(old_fold_rows)!=len(FOLD_IDS) or len(current_overall)!=1 or len(old_overall)!=1:
                raise HybridPOCError(f"Frozen benchmark summary cells incomplete for {strategy}")
            checks=(("per_stock",current_stock,old_stock_rows,"stock_code"),
                    ("per_fold",current_fold,old_fold_rows,"fold_id"),
                    ("overall",current_overall,old_overall,None))
            for scope,cur_frame,old_frame,key in checks:
                for row in cur_frame.to_dict("records"):
                    if key is None:
                        oldrow=old_frame.iloc[0]
                        identity={"scope":scope}
                    else:
                        old=old_frame.loc[old_frame[key].astype(str)==str(row[key])]
                        if old.empty:raise HybridPOCError(f"Missing benchmark reproduction cell {scope}/{strategy}/{row[key]}")
                        oldrow=old.iloc[0]
                        identity={"scope":scope,key:row[key]}
                    for metric,old_metric in metric_pairs:
                        a,b=float(row[metric]),float(oldrow[old_metric])
                        if not math.isclose(a,b,rel_tol=1e-9,abs_tol=0.02):
                            raise HybridPOCError(f"Control reproduction failed {scope}/{strategy}/{identity}/{metric}: {a} != {b}")
                    check.append({"strategy":strategy,**identity,"status":"PASS"})
        db_hash_after=sha256_file(db)
        if db_hash_before!=db_hash_after:raise HybridPOCError("Raw market database changed during read-only run")
        if any(sha256_file(ROOT/p)!=h for p,h in prereg_hashes.items()):raise HybridPOCError("Preregistered input changed during experiment")
        audit={"experiment_id":"strategy-entry-sensitivity-exp001","status":"COMPLETED","generated_at":datetime.now(TZ).isoformat(timespec="seconds"),
            "preregistration_status_before":"PREREGISTERED_NOT_EXECUTED","preregistration_followed":True,"only_entry_changed":True,
            "entry_values":list(ENTRY_VALUES),"control":55,"exit_fixed":30,"market_regime":"TECH_V2_B_MARKET_REGIME_FILTER","risk":"RISK_V1",
            "models":list(MODELS),"stocks":list(SUBJECTS),"proxy_only_stocks":[x for x in CORE_UNIVERSE if x not in SUBJECTS],"folds":folds,
            "data_role":"EXPOSED_DEVELOPMENT_DATA","period":["2023-01-01","2024-12-31"],"technical_candidates":int(len(all_candidates)),
            "candidate_count_by_entry":all_candidates.groupby("entry_value").size().to_dict(),"common_coverage_counts":coverage_candidates.groupby("entry_value").size().to_dict(),
            "common_coverage_fair":bool(coverage_frame.common_coverage_fair.all()),"coverage_rows":coverage_rows,"candidate_matching_rule":
            "Within each stock-fold, sort signal timestamps only; group consecutive signals <=162 observed tradable 5-minute bars apart; select first signal per Entry in each episode; compare signal-time bar-index differences; never use outcomes.",
            "candidate_overlap_key":"stock_code,fold_id,signal_time,earliest_entry_time(next-tradable-bar fill)","regime_context_audit":regime_audit,
            "control_candidate_source":"existing Technical V1 frozen signals export; Entry 40/70 candidates generated by replaying the same Technical V1 candidate engine with only Entry varied",
            "control_signal_source_sha256":sha256_file(source_signals_path),
            "execution_contract":"completed signal K-bar -> next tradable 5-minute bar Open","execution_fill_audit":execution_audit,"control_reproduction":check,
            "control_reproduction_status":"PASS","classification":classes,"prereg_input_hashes":prereg_hashes,
            "execution_runner_sha256":sha256_file(Path(__file__)),"git_before_performance":git_before,"database":{"path":str(db.relative_to(ROOT)),"sha256_before":db_hash_before,"sha256_after":db_hash_after,"read_only":True},
            "forbidden_access":{"2025_used":False,"2026_used":False,"final_oos_used":False,"additional_holdout_used":False},
            "change_flags":{"entry_changed_in_memory_only":True,"exit_changed":False,"market_regime_changed":False,"risk_changed":False,"ml_changed":False,
                "ml_retrained":False,"threshold_changed":False,"label_changed":False,"stocks_changed":False,"folds_changed":False,"post_hoc_parameter_added":False,
                "parameter_search":False,"final_entry_selected":False,"development_candidate_modified":False}}
        # Required standalone data products.
        csv_write(out/"entry_sensitivity_summary.csv",overall)
        csv_write(out/"entry_sensitivity_per_stock.csv",per_stock)
        csv_write(out/"entry_sensitivity_per_fold.csv",per_fold)
        csv_write(out/"entry_sensitivity_common_coverage.csv",coverage_frame)
        csv_write(out/"entry_timing_comparison.csv",timing)
        csv_write(out/"entry_candidate_overlap.csv",overlap)
        cost=overall[["entry_value","strategy","trade_count","gross_positive_pnl","transaction_cost","net_pnl","net_edge_per_trade","cost_ratio"]].copy()
        csv_write(out/"entry_cost_efficiency.csv",cost)
        exposure=overall[["entry_value","strategy","average_capital_exposure","maximum_capital_exposure","trade_count","average_holding_bars"]].copy()
        csv_write(out/"entry_exposure_comparison.csv",exposure)
        csv_write(out/"entry_delta_vs_control.csv",delta)
        csv_write(out/"entry_sensitivity_candidates.csv",all_candidates)
        csv_write(out/"entry_sensitivity_oof_eligible_candidates.csv",coverage_candidates)
        csv_write(out/"entry_sensitivity_trade_ledger.csv",pd.DataFrame(trade_rows))
        write_json(out/"entry_sensitivity_audit.json",audit)
        (out/"entry_sensitivity_report.html").write_text(render_full(audit,overall,per_stock,per_fold,coverage_frame,timing,overlap,delta),encoding="utf-8")
        (out/"entry_sensitivity_user_analysis.html").write_text(render_user(audit,summary,per_stock,per_fold,timing,overlap,coverage_frame),encoding="utf-8")
        if sha256_file(db)!=db_hash_before:raise HybridPOCError("Database hash changed after outputs were written")
    return finish(out,audit,overall,per_stock,per_fold,timing,overlap,coverage_frame,delta)


def finish(out,audit,overall,per_stock,per_fold,timing,overlap,coverage,delta):
    # A separate result record preserves the immutable preregistration text.
    report_lines=["# Entry Sensitivity Audit Exp001 — Results", "", f"Experiment ID: `{audit['experiment_id']}`", "",
        "This is a 2023–2024 exposed-development sensitivity audit. It is diagnostic only; no final Entry was selected.", "",
        f"Overall preregistered classification: `{audit['classification']['overall']}`", "",
        "## Entry × Strategy", "", overall.to_markdown(index=False), "", "## Candidate timing", "", timing.to_markdown(index=False), "",
        "## Candidate overlap", "", overlap.to_markdown(index=False), "", "## Reading limits", "",
        "The folds and stocks are the preregistered set. Cells with fewer than five completed trades are marked INSUFFICIENT. No 2025, 2026, Additional Holdout, or Final OOS data were used.", "",
        "Entry 55 remains Control. Adoption is deferred to the independent P2 decision; this audit does not modify the frozen Development Candidate.", ""]
    (ROOT/"research/strategy_entry_sensitivity_exp001.md").write_text("\n".join(report_lines),encoding="utf-8")
    update_status(audit,out);update_registries(out,audit["classification"]["overall"])
    print(json.dumps({"status":"COMPLETED","experiment_id":audit["experiment_id"],"export_directory":str(out.relative_to(ROOT)),
        "classification":audit["classification"],"files":sorted(x.name for x in out.iterdir()),"research_report":"research/strategy_entry_sensitivity_exp001.md"},ensure_ascii=False,indent=2,default=str))
    return 0


if __name__=="__main__":
    try:raise SystemExit(main())
    except (HybridPOCError,tv1.TechnicalV1Error) as exc:raise SystemExit(f"ENTRY_SENSITIVITY_BLOCKED: {exc}") from exc

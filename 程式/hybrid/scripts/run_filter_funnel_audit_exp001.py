"""Run the fixed, diagnostic-only representative-stock filter funnel audit."""
from __future__ import annotations

import contextlib
import html
import io
import json
import math
import sys
import bisect
from collections import Counter, defaultdict
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
    build_prediction_lookup, sha256_file, validate_oof_predictions,
    validate_training_only_dates,
)
from hybrid.core.risk_v1 import calculate_risk_size  # noqa: E402
from hybrid.core.strategy_v2_batch import regime_confirmation_passes  # noqa: E402
from hybrid.scripts import run_representative_benchmark_exp001 as benchmark  # noqa: E402
from hybrid.scripts.run_strategy_v2_batch import MODELS, simulate_one  # noqa: E402
from trading_system import backtest as bt  # noqa: E402
from trading_system import technical_v1 as tv1  # noqa: E402

TZ = ZoneInfo("Asia/Taipei")
EXPERIMENT_ID = "strategy-representative-filter-funnel-exp001"
SUBJECTS = ("2330", "2454", "2303")
MODEL_NAMES = {"random_forest": "RF", "xgboost": "XGBOOST", "gru": "GRU"}
COMMON_RUN = benchmark.COMMON_RUN
POC_RUN = benchmark.POC_RUN
BENCHMARK_RUN = ROOT / "exports/representative_benchmark_exp001_20261020T111920272910"
PREREG = ROOT / "research/strategy_filter_funnel_exp001.md"
SOURCE_DIRS = (COMMON_RUN, POC_RUN, benchmark.TECH_BATCH, BENCHMARK_RUN)
CAPTURE: list[dict[str, Any]] | None = None


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


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_subject_rows(path: Path, key: str = "stock_code") -> pd.DataFrame:
    frames = []
    for chunk in pd.read_csv(path, dtype={key: str}, chunksize=50_000):
        frames.append(chunk.loc[chunk[key].astype(str).isin(SUBJECTS)].copy())
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _entry_key(value: Any) -> str:
    ts = pd.Timestamp(value)
    ts = ts.tz_localize(TZ) if ts.tzinfo is None else ts.tz_convert(TZ)
    return ts.isoformat(timespec="microseconds")


def _candidate_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(row["stock_code"]), str(row["fold_id"]), str(row["signal_time"]), str(row["earliest_entry_time"]))


def _block_reasons(strategy: Any, decision_time: datetime) -> tuple[bool, list[str]]:
    """Mirror MovingAverageTsst._can_enter state checks after its day/cooldown updates."""
    minute = decision_time.hour * 60 + decision_time.minute
    cfg = strategy.config
    reasons: list[str] = []
    if strategy.is_bought:
        reasons.append("ALREADY_IN_POSITION")
    if strategy.is_entry or strategy.is_exit:
        reasons.append("ORDER_IN_PROGRESS")
    if strategy.cooldown_remaining > 0 and not strategy.is_bought:
        reasons.append("COOLDOWN")
    if strategy.entries_today >= cfg.max_entries_per_day:
        reasons.append("SAME_DAY_ENTRY_LIMIT")
    if not (cfg.entry_start_hour * 60 + cfg.entry_start_minute <= minute <= cfg.entry_cutoff_hour * 60 + cfg.entry_cutoff_minute):
        reasons.append("ENTRY_TIME_RESTRICTION")
    block_start = cfg.entry_block_start_hour * 60 + cfg.entry_block_start_minute
    block_end = cfg.entry_block_end_hour * 60 + cfg.entry_block_end_minute
    if cfg.use_entry_block and block_start <= minute < block_end:
        reasons.append("ENTRY_TIME_BLOCK")
    return not reasons, reasons


def _install_eligibility_capture() -> Any:
    """Instrument, without altering decisions, each scheduled candidate at the engine boundary."""
    original = bt.MovingAverageTsst.process_completed_kbar

    def wrapped(self: Any, code: str, decision_time: datetime, completed_kbar: dict[str, Any]) -> None:
        global CAPTURE
        if CAPTURE is not None and self.candidate_schedule is not None:
            aware = decision_time.replace(tzinfo=TZ) if decision_time.tzinfo is None else decision_time.astimezone(TZ)
            key = aware.isoformat(timespec="microseconds")
            if key in self.candidate_schedule:
                close = tv1.number_or_none(completed_kbar.get("Close"))
                snap = bt.indicator_snapshot(completed_kbar)
                if close is not None and self._has_required_indicators(snap) and self._entry_reasons(close, snap):
                    new_day = self.current_trading_day != aware.date()
                    cooldown_after_update = 0 if new_day else self.cooldown_remaining
                    if cooldown_after_update > 0 and not self.is_bought:
                        cooldown_after_update -= 1
                    entries_today = 0 if new_day else self.entries_today
                    can_enter, reasons = _block_reasons_snapshot(self, aware, cooldown_after_update, entries_today)
                    CAPTURE.append({
                        "model": str(getattr(self, "ml_model", "")), "stock_code": str(code),
                        "fold_id": str(self.fold_id), "entry_time": key,
                        "is_in_position": bool(self.is_bought), "position_shares": int(self.local_position),
                        "cooldown_remaining_after_bar_update": int(cooldown_after_update),
                        "entries_today_after_day_roll": int(entries_today),
                        "execution_state_eligible": can_enter, "execution_block_reasons": reasons,
                    })
        return original(self, code, decision_time, completed_kbar)

    bt.MovingAverageTsst.process_completed_kbar = wrapped
    return original


def _block_reasons_snapshot(strategy: Any, decision_time: datetime, cooldown: int, entries_today: int) -> tuple[bool, list[str]]:
    minute = decision_time.hour * 60 + decision_time.minute
    cfg = strategy.config
    reasons: list[str] = []
    if strategy.is_bought:
        reasons.append("ALREADY_IN_POSITION")
    if strategy.is_entry or strategy.is_exit:
        reasons.append("ORDER_IN_PROGRESS")
    if cooldown > 0 and not strategy.is_bought:
        reasons.append("COOLDOWN")
    if entries_today >= cfg.max_entries_per_day:
        reasons.append("SAME_DAY_ENTRY_LIMIT")
    if not (cfg.entry_start_hour * 60 + cfg.entry_start_minute <= minute <= cfg.entry_cutoff_hour * 60 + cfg.entry_cutoff_minute):
        reasons.append("ENTRY_TIME_RESTRICTION")
    block_start = cfg.entry_block_start_hour * 60 + cfg.entry_block_start_minute
    block_end = cfg.entry_block_end_hour * 60 + cfg.entry_block_end_minute
    if cfg.use_entry_block and block_start <= minute < block_end:
        reasons.append("ENTRY_TIME_BLOCK")
    return not reasons, reasons


class DiagnosticShadowTechnical(benchmark.TechnicalOnlyV2B):
    """A single-candidate diagnostic replay: bypass only the tested regime filter."""
    def _try_entry(self, code: str, tick_time: datetime, close: float, snapshot: dict[str, float | None]) -> None:
        reasons = self._entry_reasons(close, snapshot)
        if not reasons:
            return
        aware = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=TZ)
        signal_time = aware.astimezone(TZ)
        if self.candidate_schedule is not None and signal_time.isoformat(timespec="microseconds") not in self.candidate_schedule:
            return
        equity = float(self.local_cash or 0.0) + int(self.local_position) * float(close)
        original_quantity = int(tv1.TechnicalV1Strategy._order_quantity(self, close))
        sizing = calculate_risk_size(current_equity=equity, entry_price=float(close),
                                     original_quantity=original_quantity,
                                     stop_loss_pct=float(self.config.stop_loss_pct))
        if sizing["final_quantity"] <= 0:
            self.record_signal(code=code, action="Buy", signal_type="entry", signal_time=tick_time,
                               price=close, reason=", ".join(reasons), indicators=snapshot)
            return
        self._risk_quantity_for_entry = int(sizing["final_quantity"])
        try:
            tv1.TechnicalV1Strategy._try_entry(self, code, tick_time, close, snapshot)
        finally:
            self._risk_quantity_for_entry = None


def _quality(frame: pd.DataFrame, model: str, predicted_class: str, group: str) -> dict[str, Any]:
    rows = frame.loc[(frame.model == model) & (frame.candidate_group == group)]
    if group == "prediction_class":
        rows = rows.loc[rows.prediction == predicted_class]
    vals = pd.to_numeric(rows.net_trade_return, errors="coerce").dropna()
    gp = float(pd.to_numeric(rows.gross_pnl, errors="coerce").clip(lower=0).sum())
    gl = float(pd.to_numeric(rows.gross_pnl, errors="coerce").clip(upper=0).sum())
    return {
        "candidate_count": int(len(rows)), "evaluable_count": int(rows["evaluable"].sum()),
        "average_net_trade_return": float(vals.mean()) if len(vals) else None,
        "median_net_trade_return": float(vals.median()) if len(vals) else None,
        "win_rate": float((vals > 0).mean()) if len(vals) else None,
        "profit_factor": gp / abs(gl) if gl < 0 else (None if gp == 0 else None),
        "gross_pnl": float(pd.to_numeric(rows.gross_pnl, errors="coerce").sum(min_count=1)) if len(rows) else None,
        "transaction_cost": float(pd.to_numeric(rows.transaction_cost, errors="coerce").sum(min_count=1)) if len(rows) else None,
        "net_pnl": float(pd.to_numeric(rows.net_pnl, errors="coerce").sum(min_count=1)) if len(rows) else None,
        "average_holding_bars": float(pd.to_numeric(rows.holding_bars, errors="coerce").mean()) if rows.holding_bars.notna().any() else None,
        "positive_trade_count": int((vals > 0).sum()), "negative_trade_count": int((vals < 0).sum()),
    }


def _filter_classification(buy: dict[str, Any], blocked: dict[str, Any], by_stock: pd.DataFrame) -> str:
    if blocked["evaluable_count"] < 10 or by_stock.stock_code.nunique() < 2:
        return "INSUFFICIENT_EVIDENCE"
    if (buy["evaluable_count"] > 0 and buy["average_net_trade_return"] is not None
            and blocked["average_net_trade_return"] is not None
            and buy["average_net_trade_return"] > blocked["average_net_trade_return"]
            and (buy["profit_factor"] or 0) > (blocked["profit_factor"] or 0)
            and (blocked["average_net_trade_return"] <= 0 or (blocked["profit_factor"] or 0) <= 1)
            and by_stock.loc[by_stock.block_class.eq("blocked"), "negative_evidence"].sum() >= 2):
        return "PRECISE_FILTER_EVIDENCE"
    blocked_stock = by_stock.loc[by_stock.block_class.eq("blocked")]
    if (blocked["average_net_trade_return"] is not None and blocked["average_net_trade_return"] > 0
            and (blocked["profit_factor"] or 0) > 1
            and int(blocked_stock.positive_evidence.sum()) >= 2):
        return "OVER_CONSERVATIVE_EVIDENCE"
    return "MIXED"


def _reproduction_preflight() -> dict[str, Any]:
    prereg = PREREG.read_text(encoding="utf-8")
    if "Status:** PREREGISTERED" not in prereg or EXPERIMENT_ID not in prereg:
        raise RuntimeError("Filter-funnel preregistration missing or already amended")
    audit = json.loads((BENCHMARK_RUN / "benchmark_audit.json").read_text(encoding="utf-8"))
    if audit.get("status") != "COMPLETED" or audit.get("common_coverage_status") != "PASS":
        raise RuntimeError("Existing representative benchmark/Common Coverage is not complete PASS")
    if audit.get("period") != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise RuntimeError("Benchmark scope is outside the registered 2023-2024 period")
    if audit.get("forbidden_access", {}).get("2025_used") or audit.get("forbidden_access", {}).get("2026_used") or audit.get("forbidden_access", {}).get("final_oos_used"):
        raise RuntimeError("Benchmark audit indicates forbidden-period access")
    if audit.get("execution_fill_audit", {}).get("status") != "PASS":
        raise RuntimeError("Existing next-bar-open fill audit failed")
    candidates = read_subject_rows(COMMON_RUN / "common_coverage_candidates.csv")
    if len(candidates) != int(audit["common_coverage_candidate_count"]) or len(candidates) != 119:
        raise RuntimeError("Common Coverage candidate count does not reproduce the fixed benchmark")
    oof = read_subject_rows(POC_RUN / "ml_v1_baseline_oof_predictions.csv")
    oof_audit = validate_oof_predictions(oof, expected_models=MODELS, expected_stocks=SUBJECTS,
                                         period_start="2023-01-01", period_end="2024-12-31")
    if oof_audit.get("forbidden_roles_used") or oof_audit.get("in_sample_predictions_used"):
        raise RuntimeError("OOF data failed training-only provenance checks")
    validate_training_only_dates(candidates.signal_time)
    lookups = {model: build_prediction_lookup(oof, model) for model in MODELS}
    alignments = {m: 0 for m in MODELS}
    for row in candidates.to_dict("records"):
        stock, day = str(row["stock_code"]), str(row["earliest_entry_time"])[:10]
        for model in MODELS:
            pred = lookups[model].get((stock, day))
            if pred is None or str(pred["signal_date"])[:10] != str(row["expected_previous_trading_day"])[:10]:
                raise RuntimeError(f"OOF previous-session alignment mismatch {model}/{stock}/{day}")
            alignments[model] += 1
    source_hash_checks = {}
    source_hashes = audit.get("source_hashes", {})
    for path in [COMMON_RUN / "common_coverage_candidates.csv", POC_RUN / "ml_v1_baseline_oof_predictions.csv",
                 benchmark.TECH_BATCH / "technical_v2_results.csv"]:
        relative = str(path.relative_to(ROOT)).replace("/", "\\")
        expected = source_hashes.get(relative)
        source_hash_checks[relative] = expected is not None and sha256_file(path) == expected
        if not source_hash_checks[relative]:
            raise RuntimeError(f"Frozen benchmark input hash mismatch: {relative}")
    candidate_counts = candidates.groupby("stock_code").size().to_dict()
    fold_counts = candidates.groupby("fold_id").size().to_dict()
    return {"status": "PASS", "common_coverage_status": "PASS", "candidate_count": len(candidates),
            "candidate_count_by_stock": {str(k): int(v) for k, v in candidate_counts.items()},
            "candidate_count_by_fold": {str(k): int(v) for k, v in fold_counts.items()},
            "oof_alignment_count": alignments, "next_bar_open_fill_status": "PASS",
            "frozen_source_hashes_match": source_hash_checks,
            "proxy_only_individual_metrics_computed": False,
            "scope": ["2023-01-01", "2024-12-31"], "forbidden_years_used": []}, candidates, oof, lookups, audit


def main() -> int:
    global CAPTURE
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--shadow-source", type=Path, help="Reuse an already completed identical candidate shadow file")
    args = parser.parse_args()
    if args.preflight == args.execute:
        parser.error("Specify exactly one of --preflight or --execute")
    preflight, candidates, oof, lookups, benchmark_audit = _reproduction_preflight()
    print(json.dumps(preflight, ensure_ascii=False, indent=2))
    if args.preflight:
        return 0

    config = tv1.read_config(ROOT / "configs/technical_v1_research.json")
    risk_cfg = json.loads((ROOT / "hybrid/config/risk_v1.json").read_text(encoding="utf-8"))
    if config["walk_forward_folds"] != json.loads((ROOT / "configs/technical_v1_research.json").read_text(encoding="utf-8"))["walk_forward_folds"]:
        raise RuntimeError("Fold schedule inconsistency")
    family_name, family, candidate = tv1.candidate_by_id(config, "BR_SENS_RANGE55_BB20_2")
    if family_name != "breakout":
        raise RuntimeError("Frozen Technical candidate family changed")
    folds = config["walk_forward_folds"]
    schedules = {(str(fold), str(stock)): {_entry_key(t) for t in group.earliest_entry_time}
                 for (fold, stock), group in candidates.groupby(["fold_id", "stock_code"])}
    candidate_dates = set(candidates.earliest_entry_time.astype(str).str[:10])
    stamp = datetime.now().astimezone(TZ).strftime("%Y%m%dT%H%M%S%f%z")
    out = ROOT / "exports" / f"filter_funnel_audit_exp001_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    shadow_checkpoint_path = out / ".shadow_checkpoint.csv"
    completed_shadows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    if shadow_checkpoint_path.is_file():
        saved = pd.read_csv(shadow_checkpoint_path, dtype={"stock_code": str, "fold_id": str})
        completed_shadows = {_candidate_key(r): r for r in saved.to_dict("records")}
    db_path = tv1.resolve_market_data_path(ROOT, config)
    db_hash_before = sha256_file(db_path)
    original_process = _install_eligibility_capture()
    regime_rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    candidate_records = candidates.to_dict("records")
    if args.shadow_source:
        source_path = args.shadow_source if args.shadow_source.is_absolute() else ROOT / args.shadow_source
        source = pd.read_csv(source_path, dtype={"stock_code": str, "fold_id": str})
        expected_keys = {_candidate_key(r) for r in candidate_records}
        source_keys = {_candidate_key(r) for r in source.to_dict("records")}
        if source_keys != expected_keys or len(source) != len(candidate_records):
            raise RuntimeError("Reusable shadow file does not contain the exact preregistered candidate set")
        if not source["shadow_status"].eq("EVALUABLE_SINGLE_CANDIDATE").all():
            raise RuntimeError("Reusable shadow file has non-evaluable rows; refusing partial reuse")
        fields = ("evaluable", "shadow_status", "not_evaluable_reason", "shadow_entry_timestamp",
                  "shadow_entry_price", "shadow_quantity", "shadow_exit_timestamp", "shadow_exit_price",
                  "gross_pnl", "transaction_cost", "net_pnl", "net_trade_return", "holding_bars", "shadow_label")
        source_map = {_candidate_key(r): r for r in source.to_dict("records")}
        completed_shadows = {
            _candidate_key(r): {**r, **{field: source_map[_candidate_key(r)].get(field) for field in fields}}
            for r in candidate_records
        }
    with tv1._readonly_connection(db_path) as connection:
        contexts, regime_source_audit = benchmark.build_target_regime_context(connection, candidate_dates)
        for row in candidate_records:
            stock, fold, day = str(row["stock_code"]), str(row["fold_id"]), str(row["earliest_entry_time"])[:10]
            ctx = contexts.get((stock, day))
            passed = bool(ctx and ctx.get("eligible") and regime_confirmation_passes(ctx.get("last_20_proxy_closes", [])))
            regime_rows[_candidate_key(row)] = {"regime_pass": passed,
                                               "regime_reason": "PASS" if passed else ("INSUFFICIENT_OR_MISSING_CONTEXT" if not ctx or not ctx.get("eligible") else "PROXY_NOT_ABOVE_SMA20")}

        state_events: list[dict[str, Any]] = []
        run_trades: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        run_risks: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        try:
            for model in MODELS:
                CAPTURE = state_events
                for fold in folds:
                    for stock in SUBJECTS:
                        result, trades, gates, risks, local_filter = simulate_one(
                            connection=connection, config=config, family_name=family_name, family=family,
                            candidate=candidate, stock=stock, fold=fold, version="TECH_V2_B", model=model,
                            lookup=lookups[model], schedule=schedules.get((fold["id"], stock), set()),
                            volume_context={}, regime_context=contexts,
                        )
                        key = (model, stock, fold["id"])
                        run_trades[key] = trades
                        run_risks[key] = risks
                        # The replay must exactly reproduce the already viewed frozen policy result.
                        recorded = read_subject_rows(BENCHMARK_RUN / "benchmark_account_results.csv")
                        arm = {"random_forest": "HYBRID_RF", "xgboost": "HYBRID_XGB", "gru": "HYBRID_GRU"}[model]
                        old = recorded.loc[(recorded.strategy == arm) & (recorded.stock_code.astype(str) == stock) & (recorded.fold_id == fold["id"])]
                        if len(old) != 1 or int(old.iloc[0].trade_count) != int(result["trade_count"]) or not math.isclose(float(old.iloc[0].net_pnl), float(result["net_pnl"]), abs_tol=1e-6):
                            raise RuntimeError(f"Frozen policy replay did not reproduce {model}/{stock}/{fold['id']}")
        finally:
            CAPTURE = None
            bt.MovingAverageTsst.process_completed_kbar = original_process

        # One independent, explicitly diagnostic-only shadow replay per common candidate.
        frame_cache: dict[tuple[str, str], tuple[Any, Any, Any, int, int]] = {}
        shadow_rows: list[dict[str, Any]] = []
        for idx, row in enumerate(candidate_records, start=1):
            cached_shadow = completed_shadows.get(_candidate_key(row))
            if cached_shadow is not None:
                shadow_rows.append(cached_shadow)
                continue
            stock, fold_id = str(row["stock_code"]), str(row["fold_id"])
            fold = next(f for f in folds if f["id"] == fold_id)
            cache_key = (stock, fold_id)
            if cache_key not in frame_cache:
                frame_cache[cache_key] = tv1._load_fold_frame(connection, config, candidate, stock, fold)
            full, evaluation, app, warmup_count, evaluate_lower = frame_cache[cache_key]
            only_time = _entry_key(row["earliest_entry_time"])
            full_times = full.get_column("kbar_timestamp").to_list()
            entry_stamp = int(pd.Timestamp(only_time).timestamp())
            entry_index = bisect.bisect_left(full_times, entry_stamp)
            if entry_index >= len(full_times) or int(full_times[entry_index]) != entry_stamp:
                raise RuntimeError(f"Candidate next-bar timestamp is not present in raw Technical data: {stock}/{only_time}")
            # Indicators are already precomputed causally on the frozen fold frame. Replay only
            # the two pre-entry bars plus enough later bars for the unchanged 162-bar max hold
            # and its next-open fill. This preserves exact event logic while avoiding repeated
            # full-fold scans for each independent one-candidate diagnostic.
            replay_start = max(0, entry_index - 2)
            replay_stop = min(full.height, entry_index + 164)
            shadow_frame = full.slice(replay_start, replay_stop - replay_start)
            strategy = DiagnosticShadowTechnical(
                config=app, study_config=config, family_name=family_name, family=family,
                candidate=candidate, data_role="training", fold_id=fold_id,
                is_backtest=True, is_simulation=True, local_only=True,
                candidate_schedule={only_time}, regime_context={},
            )
            strategy.configure_local_account(app)
            strategy.evaluation_start_timestamp = evaluate_lower
            stats = bt.MarketDataStats(tick_count=shadow_frame.height,
                                       first_timestamp=float(shadow_frame["kbar_timestamp"][0]),
                                       last_timestamp=float(shadow_frame["kbar_timestamp"][-1]))
            with contextlib.redirect_stdout(io.StringIO()):
                bt.replay_kbars(strategy, shadow_frame, stats)
            trades = bt._build_trade_rows(strategy, app)
            pnl_rows = bt._build_pnl_rows(trades)
            buy_rows = [t for t in trades if t["action"] == "Buy"]
            sell_rows = [t for t in trades if t["action"] == "Sell"]
            if len(buy_rows) != 1 or len(sell_rows) != 1 or not pnl_rows:
                shadow_rows.append({**row, "evaluable": False, "shadow_status": "NOT_EVALUABLE",
                                    "not_evaluable_reason": "NO_SINGLE_COMPLETED_TRADE_UNDER_FROZEN_MECHANICS",
                                    "gross_pnl": None, "transaction_cost": None, "net_pnl": None,
                                    "net_trade_return": None, "holding_bars": None,
                                    "shadow_label": "DIAGNOSTIC_ONLY"})
                continue
            pnl = pnl_rows[0]
            buy, sell = buy_rows[0], sell_rows[0]
            if int(sell["timestamp"]) <= int(buy["timestamp"]):
                raise RuntimeError("Shadow trade violates next-bar execution chronology")
            gross = float(sell["gross_amount"] - buy["gross_amount"])
            cost = float(buy["fee"] + sell["fee"] + sell["tax"])
            net = float(gross - cost)
            eval_times = frame_cache[cache_key][1].get_column("kbar_timestamp").to_list()
            bars_held = int(sum(int(t) > int(buy["timestamp"]) and int(t) <= int(sell["timestamp"]) for t in eval_times))
            shadow_rows.append({**row, "evaluable": True, "shadow_status": "EVALUABLE_SINGLE_CANDIDATE",
                                "not_evaluable_reason": "", "shadow_entry_timestamp": int(buy["timestamp"]),
                                "shadow_entry_price": float(buy["price"]), "shadow_quantity": int(buy["quantity"]),
                                "shadow_exit_timestamp": int(sell["timestamp"]), "shadow_exit_price": float(sell["price"]),
                                "gross_pnl": gross, "transaction_cost": cost, "net_pnl": net,
                                "net_trade_return": net / float(buy["gross_amount"]) if buy["gross_amount"] else None,
                                "holding_bars": bars_held, "shadow_label": "DIAGNOSTIC_ONLY"})
            if idx % 10 == 0 or idx == len(candidate_records):
                pd.DataFrame(shadow_rows).to_csv(shadow_checkpoint_path, index=False, encoding="utf-8-sig")
            if idx % 25 == 0:
                print(f"Shadow diagnostic progress: {idx}/{len(candidate_records)}")

    if sha256_file(db_path) != db_hash_before:
        raise RuntimeError("Technical raw database hash changed during read-only audit")

    # Assemble one row per formal candidate. Proxy-only stocks never enter these tables.
    capture_by_identity: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in state_events:
        capture_by_identity[(event["stock_code"], event["fold_id"], "", event["entry_time"])].append(event)
    rows: list[dict[str, Any]] = []
    for row in candidate_records:
        base = {**row, **regime_rows[_candidate_key(row)]}
        for model in MODELS:
            pred = lookups[model].get((str(row["stock_code"]), str(row["earliest_entry_time"])[:10]))
            if pred is None:
                raise RuntimeError(f"OOF candidate missing after preflight: {model}/{row['stock_code']}/{row['earliest_entry_time']}")
            base[f"{model}_prediction"] = str(pred["prediction"])
        rows.append(base)
    funnel_base = pd.DataFrame(rows)

    # Execution block reasons use same schedule/fold state and exact decision gate replay.
    state_index = {(e["model"], e["stock_code"], e["fold_id"], e["entry_time"]): e for e in state_events}
    risk_index: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for (model, stock, fold), decisions in run_risks.items():
        risk_index[(model, stock, fold)] = {str(r["timestamp"]): r for r in decisions}
    actual_buy_ts: dict[tuple[str, str, str], list[int]] = {}
    for key, trades in run_trades.items():
        actual_buy_ts[key] = sorted(int(t["timestamp"]) for t in trades if t["action"] == "Buy")

    ml_rows = []
    exec_rows = []
    all_shadows = pd.DataFrame(shadow_rows)
    shadow_index = {(_candidate_key(r)): r for r in shadow_rows}
    gate_frames = {m: [] for m in MODELS}
    for row in rows:
        key = _candidate_key(row)
        shadow = shadow_index[key]
        for model in MODELS:
            prediction = row[f"{model}_prediction"]
            if row["regime_pass"]:
                gate_record = {**row, "model": model, "prediction": prediction,
                               "candidate_group": "prediction_class",
                               **{k: shadow.get(k) for k in ("evaluable", "gross_pnl", "transaction_cost", "net_pnl", "net_trade_return", "holding_bars", "shadow_status")}}
                ml_rows.append(gate_record)
                if prediction in ("HOLD", "SELL"):
                    gate_frames[model].append({**gate_record, "candidate_group": "ml_blocked"})
                else:
                    entry_time = _entry_key(row["earliest_entry_time"])
                    stock, fold = str(row["stock_code"]), str(row["fold_id"])
                    event = state_index.get((model, stock, fold, entry_time))
                    decision_key = entry_time
                    risk = risk_index.get((model, stock, fold), {}).get(decision_key)
                    reasons = []
                    eligible = False
                    if event is None:
                        reasons = ["NO_ENGINE_CANDIDATE_EVENT"]
                    elif not event["execution_state_eligible"]:
                        reasons = event["execution_block_reasons"] or ["OTHER_EXECUTION_RESTRICTION"]
                    elif risk is None:
                        reasons = ["NO_RISK_SIZING_DECISION"]
                    elif int(risk["final_quantity"]) <= 0:
                        reasons = ["RISK_SIZING_ZERO_QUANTITY"]
                    else:
                        eligible = True
                    # The fill itself is separately counted at Stage E. Risk eligibility is D.
                    exec_rows.append({**row, "model": model, "prediction": prediction,
                                      "execution_eligible": eligible,
                                      "execution_block_reason": "|".join(reasons),
                                      "risk_decision": risk.get("decision") if risk else None,
                                      "final_quantity": risk.get("final_quantity") if risk else None,
                                      "actual_buy_fill_count_account": len(actual_buy_ts.get((model, stock, fold), []))})

    prediction_frame = pd.DataFrame(ml_rows)
    execution_frame = pd.DataFrame(exec_rows)
    # Attach regime blocks and per-model gate blocks to the candidate-level diagnostic shadow table.
    shadow_for_quality: list[dict[str, Any]] = []
    for row in rows:
        key = _candidate_key(row)
        shadow = shadow_index[key]
        if not row["regime_pass"]:
            shadow_for_quality.append({**row, **shadow, "model": "MARKET_REGIME", "prediction": "ALL",
                                       "candidate_group": "regime_blocked"})
        else:
            shadow_for_quality.append({**row, **shadow, "model": "MARKET_REGIME", "prediction": "PASS",
                                       "candidate_group": "regime_pass"})
        for model in MODELS:
            if row["regime_pass"]:
                group = "BUY" if row[f"{model}_prediction"] == "BUY" else "blocked"
                shadow_for_quality.append({**row, **shadow, "model": model, "prediction": row[f"{model}_prediction"],
                                           "candidate_group": group})
    quality_source = pd.DataFrame(shadow_for_quality)

    # Funnel rows: overall, stock, fold, plus stock-fold for audit traceability.
    def emit_scope(scope: str, group: pd.DataFrame, stock: str = "ALL", fold: str = "ALL") -> list[dict[str, Any]]:
        total = len(group)
        reg_pass = int(group.regime_pass.sum())
        out = [{"scope": scope, "stock_code": stock, "fold_id": fold, "model": "ALL", "stage": "MARKET_REGIME",
                "input_count": total, "pass_count": reg_pass, "blocked_count": total-reg_pass,
                "retention_rate": reg_pass/total if total else None, "cumulative_retention_rate": reg_pass/total if total else None}]
        for model in MODELS:
            passed = group.loc[group.regime_pass]
            dist = passed[f"{model}_prediction"].value_counts()
            buy, hold, sell = int(dist.get("BUY", 0)), int(dist.get("HOLD", 0)), int(dist.get("SELL", 0))
            out.append({"scope": scope, "stock_code": stock, "fold_id": fold, "model": model, "stage": "ML_GATE",
                        "input_count": len(passed), "pass_count": buy, "blocked_count": hold+sell,
                        "retention_rate": buy/len(passed) if len(passed) else None,
                        "cumulative_retention_rate": buy/total if total else None,
                        "buy_count": buy, "hold_count": hold, "sell_count": sell})
            e = execution_frame.loc[(execution_frame.model == model) & (execution_frame.stock_code.astype(str).isin(passed.stock_code.astype(str)))
                                    & (execution_frame.fold_id.isin(passed.fold_id))]
            # Restrict to exact candidates in this scope.
            scope_keys = {_candidate_key(r) for r in passed.to_dict("records")}
            e = e.loc[e.apply(lambda x: (str(x.stock_code), str(x.fold_id), str(x.signal_time), str(x.earliest_entry_time)) in scope_keys, axis=1)]
            eligible = int(e.execution_eligible.sum())
            out.append({"scope": scope, "stock_code": stock, "fold_id": fold, "model": model, "stage": "EXECUTION",
                        "input_count": buy, "pass_count": eligible, "blocked_count": buy-eligible,
                        "retention_rate": eligible/buy if buy else None,
                        "cumulative_retention_rate": eligible/total if total else None})
            actual = sum(len(actual_buy_ts.get((model, str(s), str(f)), [])) for s, f in passed[["stock_code", "fold_id"]].drop_duplicates().itertuples(index=False, name=None))
            # Actual count for scope is counted from the stock/fold accounts represented, not added across every candidate.
            if scope == "overall":
                actual = sum(len(actual_buy_ts.get((model, s, f), [])) for s in SUBJECTS for f in ("WF1", "WF2", "WF3"))
            else:
                pairs = set((str(s), str(f)) for s, f in passed[["stock_code", "fold_id"]].itertuples(index=False, name=None))
                actual = sum(len(actual_buy_ts.get((model, s, f), [])) for s, f in pairs)
            if actual > eligible:
                raise RuntimeError(f"Actual fills exceed execution-eligible BUY candidates for {scope}/{model}: {actual}>{eligible}")
            out.append({"scope": scope, "stock_code": stock, "fold_id": fold, "model": model, "stage": "ACTUAL_TRADE",
                        "input_count": eligible, "pass_count": actual, "blocked_count": eligible-actual,
                        "retention_rate": actual/eligible if eligible else None,
                        "cumulative_retention_rate": actual/total if total else None})
        return out

    funnel_rows = emit_scope("overall", funnel_base)
    for stock, group in funnel_base.groupby("stock_code"):
        funnel_rows += emit_scope("per_stock", group, str(stock), "ALL")
    for fold, group in funnel_base.groupby("fold_id"):
        funnel_rows += emit_scope("per_fold", group, "ALL", str(fold))
    for (stock, fold), group in funnel_base.groupby(["stock_code", "fold_id"]):
        funnel_rows += emit_scope("stock_fold", group, str(stock), str(fold))
    funnel = pd.DataFrame(funnel_rows)

    shadow_candidates = all_shadows.merge(funnel_base[["stock_code", "fold_id", "signal_time", "earliest_entry_time", "regime_pass", "regime_reason"]],
                                          on=["stock_code", "fold_id", "signal_time", "earliest_entry_time"], how="left", validate="one_to_one")
    shadow_candidates["candidate_group"] = np.where(shadow_candidates.regime_pass, "REGIME_PASS", "REGIME_BLOCKED")
    regime_blocked = shadow_candidates.loc[~shadow_candidates.regime_pass].copy()
    gate_candidates: dict[str, pd.DataFrame] = {}
    for model in MODELS:
        mask = shadow_candidates.regime_pass & shadow_candidates[f"{model}_prediction"].isin(["HOLD", "SELL"])
        gate_candidates[model] = shadow_candidates.loc[mask].copy()

    quality_rows = []
    for model in MODELS:
        for cls in ("BUY", "HOLD", "SELL"):
            source = prediction_frame.loc[prediction_frame.model == model].copy()
            source["candidate_group"] = "prediction_class"
            q = _quality(source, model, cls, "prediction_class")
            quality_rows.append({"model": model, "prediction_class": cls, **q,
                                 "diagnostic_scope": "REGIME_PASS_CANDIDATES; isolated shadow outcomes; not policy returns"})
        source = quality_source.loc[quality_source.model == model]
        for group_name in ("BUY", "blocked"):
            quality_rows.append({"model": model, "prediction_class": group_name,
                                 "candidate_group": group_name, **_quality(source, model, group_name, group_name),
                                 "diagnostic_scope": "REGIME_PASS_CANDIDATES; isolated shadow outcomes; not policy returns"})
    # Regime pass vs block shadow outcome quality.
    regime_quality = {g: _quality(quality_source, "MARKET_REGIME", "PASS" if g == "regime_pass" else "ALL", g)
                      for g in ("regime_pass", "regime_blocked")}

    blocked_summary_rows = []
    for label, src in [("MARKET_REGIME", quality_source.loc[quality_source.candidate_group == "regime_blocked"])] + [
            (m, quality_source.loc[(quality_source.model == m) & (quality_source.candidate_group == "blocked")]) for m in MODELS]:
        q = _quality(src, label if label in MODELS else "MARKET_REGIME",
                     "ALL" if label == "MARKET_REGIME" else "blocked", "regime_blocked" if label == "MARKET_REGIME" else "blocked")
        # _quality's selectors are exact and the regime row uses special class labels.
        if label == "MARKET_REGIME":
            q = {**q, "candidate_count": int(len(src)), "evaluable_count": int(src.evaluable.sum()),
                 "average_net_trade_return": float(pd.to_numeric(src.net_trade_return, errors="coerce").mean()),
                 "profit_factor": _pf(src), "gross_pnl": float(pd.to_numeric(src.gross_pnl, errors="coerce").sum(min_count=1)),
                 "transaction_cost": float(pd.to_numeric(src.transaction_cost, errors="coerce").sum(min_count=1)),
                 "net_pnl": float(pd.to_numeric(src.net_pnl, errors="coerce").sum(min_count=1)),
                 "positive_trade_count": int((pd.to_numeric(src.net_pnl, errors="coerce") > 0).sum()),
                 "negative_trade_count": int((pd.to_numeric(src.net_pnl, errors="coerce") < 0).sum()),
                 "missed_positive_gross_pnl": float(pd.to_numeric(src.loc[pd.to_numeric(src.net_pnl, errors="coerce") > 0, "gross_pnl"], errors="coerce").clip(lower=0).sum()),
                 "avoided_negative_gross_loss": abs(float(pd.to_numeric(src.loc[pd.to_numeric(src.net_pnl, errors="coerce") < 0, "gross_pnl"], errors="coerce").clip(upper=0).sum()))}
        else:
            q.update({"candidate_count": int(len(src)), "evaluable_count": int(src.evaluable.sum()),
                      "profit_factor": _pf(src),
                      "positive_trade_count": int((pd.to_numeric(src.net_pnl, errors="coerce") > 0).sum()),
                      "negative_trade_count": int((pd.to_numeric(src.net_pnl, errors="coerce") < 0).sum()),
                      "missed_positive_gross_pnl": float(pd.to_numeric(src.loc[pd.to_numeric(src.net_pnl, errors="coerce") > 0, "gross_pnl"], errors="coerce").clip(lower=0).sum()),
                      "avoided_negative_gross_loss": abs(float(pd.to_numeric(src.loc[pd.to_numeric(src.net_pnl, errors="coerce") < 0, "gross_pnl"], errors="coerce").clip(upper=0).sum()))})
        blocked_summary_rows.append({"stage": label, **q, "interpretation_scope": "DIAGNOSTIC_ONLY; not a strategy return"})
    blocked_summary = pd.DataFrame(blocked_summary_rows)

    # Per-class metrics are split by stock/fold for error and stability diagnostics.
    per_quality = []
    for model in MODELS:
        for (stock, fold), group in prediction_frame.loc[prediction_frame.model == model].groupby(["stock_code", "fold_id"]):
            for cls in ("BUY", "HOLD", "SELL"):
                selected = group.loc[group.prediction == cls]
                ret = pd.to_numeric(selected.net_trade_return, errors="coerce").dropna()
                per_quality.append({"model": model, "stock_code": stock, "fold_id": fold,
                                    "prediction_class": cls, "candidate_count": len(selected),
                                    "evaluable_count": int(selected.evaluable.sum()),
                                    "average_net_trade_return": float(ret.mean()) if len(ret) else None,
                                    "median_net_trade_return": float(ret.median()) if len(ret) else None,
                                    "win_rate": float((ret > 0).mean()) if len(ret) else None,
                                    "profit_factor": _pf(selected),
                                    "gross_pnl": pd.to_numeric(selected.gross_pnl, errors="coerce").sum(min_count=1),
                                    "transaction_cost": pd.to_numeric(selected.transaction_cost, errors="coerce").sum(min_count=1),
                                    "net_pnl": pd.to_numeric(selected.net_pnl, errors="coerce").sum(min_count=1),
                                    "average_holding_bars": pd.to_numeric(selected.holding_bars, errors="coerce").mean()})
    ml_quality = pd.DataFrame(quality_rows + per_quality)

    # Frozen diagnostic classification, computed from shadow candidate quality only.
    diagnosis = {}
    for model in MODELS:
        src = quality_source.loc[quality_source.model == model]
        buy_rows = src.loc[src.candidate_group == "BUY"]
        blocked_rows = src.loc[src.candidate_group == "blocked"]
        buy = _quality(src, model, "BUY", "BUY")
        block = _quality(src, model, "blocked", "blocked")
        by_stock = _stock_block_evidence(src)
        diagnosis[model] = {"classification": _filter_classification(buy, block, by_stock),
                            "buy_quality": buy, "blocked_quality": block,
                            "stock_evidence": by_stock.to_dict("records")}
    regime_stock = _stock_block_evidence(quality_source.loc[quality_source.model == "MARKET_REGIME"], regime=True)
    regime_class = _filter_classification(regime_quality["regime_pass"], regime_quality["regime_blocked"], regime_stock)

    # Exposure attribution reuses only the already frozen benchmark policy summaries.
    accounts = pd.read_csv(BENCHMARK_RUN / "benchmark_account_results.csv", dtype={"stock_code": str})
    target_accounts = accounts.loc[accounts.stock_code.isin(SUBJECTS)].copy()
    attribution = []
    candidate_n = len(candidates)
    reg_block_n = int((~funnel_base.regime_pass).sum())
    for model in MODELS:
        buy_n = int(funnel_base.loc[funnel_base.regime_pass, f"{model}_prediction"].eq("BUY").sum())
        block_n = int(funnel_base.loc[funnel_base.regime_pass, f"{model}_prediction"].isin(["HOLD", "SELL"]).sum())
        e = execution_frame.loc[execution_frame.model == model]
        exec_block_n = int((~e.execution_eligible).sum())
        actual_n = sum(len(actual_buy_ts.get((model, s, f), [])) for s in SUBJECTS for f in ("WF1", "WF2", "WF3"))
        stages = [("Technical common-coverage opportunities", 0, candidate_n),
                  ("Market Regime blocked", reg_block_n, candidate_n-reg_block_n),
                  (f"{MODEL_NAMES[model]} ML blocked", block_n, buy_n),
                  ("Execution blocked among BUY", exec_block_n, max(0, buy_n-exec_block_n)),
                  ("Holding duration / path conflicts", max(0, int(e.execution_eligible.sum())-actual_n), actual_n)]
        for stage, removed, remainder in stages:
            attribution.append({"model": model, "stage": stage, "removed_opportunities": removed,
                                "percent_of_total_candidates": removed/candidate_n if candidate_n else None,
                                "estimated_contribution_to_low_exposure": "COUNT_ATTRIBUTION_ONLY; exposure is path-dependent",
                                "remaining_or_pass_count": remainder})
    capital_exposure = pd.DataFrame(attribution)

    # Policy-level cost comparison; gross profit uses the existing benchmark account-level gross-positive totals.
    arm_names = {"technical_only": "TECHNICAL_ONLY", "random_forest": "HYBRID_RF", "xgboost": "HYBRID_XGB", "gru": "HYBRID_GRU"}
    costs = []
    for model, arm in arm_names.items():
        g = target_accounts.loc[target_accounts.strategy == arm]
        trades_n = int(g.trade_count.sum())
        gross_positive = float(pd.to_numeric(g.gross_profit, errors="coerce").sum())
        gross_pnl = float(pd.to_numeric(g.gross_pnl, errors="coerce").sum())
        tx_cost = float(pd.to_numeric(g.transaction_cost, errors="coerce").sum())
        net = float(pd.to_numeric(g.net_pnl, errors="coerce").sum())
        costs.append({"arm": arm, "account_count": len(g), "trade_count": trades_n,
                      "gross_positive_pnl": gross_positive, "gross_pnl": gross_pnl,
                      "transaction_cost": tx_cost, "net_pnl": net,
                      "cost_over_gross_positive_pnl": tx_cost/gross_positive if gross_positive > 0 else None,
                      "average_cost_per_trade": tx_cost/trades_n if trades_n else None,
                      "net_edge_per_trade": net/trades_n if trades_n else None,
                      "average_capital_exposure": float(pd.to_numeric(g.average_capital_exposure, errors="coerce").mean())})
    cost_frame = pd.DataFrame(costs)
    technical_cost = cost_frame.loc[cost_frame.arm == "TECHNICAL_ONLY"].iloc[0]
    cost_diagnosis = ("Technical V2-B 的 gross positive PnL 被成本大量侵蝕，成本大於總 gross PnL 的 50%。"
                      if technical_cost.cost_over_gross_positive_pnl is not None and technical_cost.cost_over_gross_positive_pnl > 0.5
                      else "Technical V2-B 成本占 gross positive PnL 未超過一半；是否屬低 edge 交易，需結合淨 edge/trade 與 blocked-shadow 品質看。")

    # Execution block reasons, deduplicated by model/candidate/reason.
    reason_rows = []
    if not execution_frame.empty:
        for (model, reason), g in execution_frame.loc[~execution_frame.execution_eligible].groupby(["model", "execution_block_reason"]):
            reason_rows.append({"model": model, "block_reason": reason or "UNKNOWN", "candidate_count": len(g),
                                "scope": "ML BUY candidates after regime pass"})
    execution_reasons = pd.DataFrame(reason_rows, columns=["model", "block_reason", "candidate_count", "scope"])

    # Assemble top-level findings without elevating shadow results into strategy performance.
    model_block_counts = {m: int((funnel_base.loc[funnel_base.regime_pass, f"{m}_prediction"].isin(["HOLD", "SELL"])).sum()) for m in MODELS}
    actual_count = {m: sum(len(actual_buy_ts.get((m, s, f), [])) for s in SUBJECTS for f in ("WF1", "WF2", "WF3")) for m in MODELS}
    summary_by_model = {m: blocked_summary.loc[blocked_summary.stage == m].iloc[0].to_dict() for m in MODELS}
    positive_missed = {m: summary_by_model[m].get("missed_positive_gross_pnl") for m in MODELS}
    main_source = "Mixed"
    if reg_block_n >= max(model_block_counts.values(), default=0) and reg_block_n > 0:
        main_source = "Market Regime"
    elif max(model_block_counts.values(), default=0) > 0:
        main_source = "ML Gate"
    exec_block_total = int((~execution_frame.execution_eligible).sum()) if not execution_frame.empty else 0
    if exec_block_total > max(reg_block_n, max(model_block_counts.values(), default=0)):
        main_source = "Execution"
    if not actual_count or max(actual_count.values(), default=0) == 0:
        main_source = "Technical"

    # Regime classification compares frozen isolated shadows, not policy returns.
    regime_comp = {"classification": regime_class,
                   "pass_quality": regime_quality["regime_pass"],
                   "blocked_quality": regime_quality["regime_blocked"],
                   "stock_evidence": regime_stock.to_dict("records")}
    audit = {
        "experiment_id": EXPERIMENT_ID, "status": "COMPLETED", "purpose": "DIAGNOSTIC_ONLY",
        "common_coverage_reproduction": "PASS", "technical_candidate_count": len(candidates),
        "regime_pass": int(funnel_base.regime_pass.sum()), "regime_blocked": reg_block_n,
        "model_diagnosis": diagnosis, "market_regime_diagnosis": regime_comp,
        "main_source_of_low_exposure": main_source,
        "important_findings": {
            "models_that_block_more_candidates": [m for m, n in model_block_counts.items() if n == max(model_block_counts.values())],
            "most_missed_positive_gross_pnl": [m for m, v in positive_missed.items() if v is not None and v == max([x for x in positive_missed.values() if x is not None], default=None)],
            "model_with_best_bad_trade_avoidance_evidence": [m for m in MODELS if diagnosis[m]["classification"] == "PRECISE_FILTER_EVIDENCE"],
            "entry_or_exit_next_diagnostic": "先檢查 Entry/Filter 對機會的保留與阻擋結構；本輪不能據此修改規則。",
            "cost_diagnosis": cost_diagnosis,
        },
        "benchmark_policy_reference": "Existing Representative Benchmark Exp001 only; policy returns are not recalculated from shadows.",
        "proxy_only_codes": ["2379", "3034"], "proxy_only_individual_metrics_computed": False,
        "period": {"start": "2023-01-01", "end": "2024-12-31", "role": "EXPOSED_DEVELOPMENT"},
        "folds": [f["id"] for f in folds], "regime_source_audit": regime_source_audit,
        "technical_database_sha256_before_after": {"before": db_hash_before, "after": sha256_file(db_path)},
        "execution_contract": "completed candidate signal -> next tradable 5-minute bar Open",
        "shadow_scope": "single-candidate isolated shadow outcome; DIAGNOSTIC ONLY; never summed as strategy/portfolio return",
        "shadow_source_export": str(args.shadow_source) if args.shadow_source else "computed_in_run",
        "forbidden_access": {"2025_used": False, "2026_used": False, "final_oos_used": False},
        "change_flags": {"parameter_search": False, "ml_retrained": False, "technical_changed": False,
                          "market_regime_changed": False, "gate_changed": False, "risk_changed": False,
                          "exit_changed": False, "entry_changed": False, "stocks_changed": False,
                          "shadow_results_used_as_strategy_return": False},
    }

    funnel.to_csv(out / "funnel_overall.csv", index=False, encoding="utf-8-sig")
    funnel.loc[funnel.scope == "per_stock"].to_csv(out / "funnel_per_stock.csv", index=False, encoding="utf-8-sig")
    funnel.loc[funnel.scope == "per_fold"].to_csv(out / "funnel_per_fold.csv", index=False, encoding="utf-8-sig")
    regime_blocked.to_csv(out / "regime_blocked_candidates.csv", index=False, encoding="utf-8-sig")
    for model, fn in [("random_forest", "rf_gate_candidates.csv"), ("xgboost", "xgb_gate_candidates.csv"), ("gru", "gru_gate_candidates.csv")]:
        gate_candidates[model].to_csv(out / fn, index=False, encoding="utf-8-sig")
    ml_quality.to_csv(out / "ml_prediction_quality.csv", index=False, encoding="utf-8-sig")
    blocked_summary.to_csv(out / "blocked_opportunity_summary.csv", index=False, encoding="utf-8-sig")
    execution_reasons.to_csv(out / "execution_block_reasons.csv", index=False, encoding="utf-8-sig")
    capital_exposure.to_csv(out / "capital_exposure_attribution.csv", index=False, encoding="utf-8-sig")
    cost_frame.to_csv(out / "cost_diagnosis.csv", index=False, encoding="utf-8-sig")
    shadow_candidates.to_csv(out / "shadow_diagnostic_candidates.csv", index=False, encoding="utf-8-sig")
    write_json(out / "filter_funnel_audit.json", audit)
    (out / "filter_funnel_report.html").write_text(render_full_html(audit, funnel, ml_quality, blocked_summary, execution_reasons, capital_exposure, cost_frame), encoding="utf-8")
    (out / "filter_funnel_user_analysis.html").write_text(render_user_html(audit, funnel, blocked_summary, shadow_candidates), encoding="utf-8")
    shadow_checkpoint_path.unlink(missing_ok=True)
    append_research_record(audit, out)
    update_registries(out)
    print(json.dumps({"status": "COMPLETED", "export_directory": str(out.relative_to(ROOT)),
                      "audit": audit, "files": sorted(p.name for p in out.iterdir())}, ensure_ascii=False, indent=2))
    return 0


def _pf(frame: pd.DataFrame) -> float | None:
    gross = pd.to_numeric(frame.gross_pnl, errors="coerce")
    positive = float(gross.clip(lower=0).sum())
    negative = float(gross.clip(upper=0).sum())
    return positive/abs(negative) if negative < 0 else (None if positive == 0 else None)


def _stock_block_evidence(frame: pd.DataFrame, regime: bool = False) -> pd.DataFrame:
    rows = []
    for stock, group in frame.groupby("stock_code"):
        blocked = group.loc[group.candidate_group == ("regime_blocked" if regime else "blocked")]
        net = pd.to_numeric(blocked.net_pnl, errors="coerce").dropna()
        pf = _pf(blocked) if len(blocked) else None
        rows.append({"stock_code": stock, "block_class": "blocked", "candidate_count": len(blocked),
                     "evaluable_count": int(blocked.evaluable.sum()) if len(blocked) else 0,
                     "average_net_trade_return": float(pd.to_numeric(blocked.net_trade_return, errors="coerce").mean()) if len(blocked) else None,
                     "profit_factor": pf, "positive_evidence": bool(len(net) and net.mean() > 0 and (pf or 0) > 1),
                     "negative_evidence": bool(len(net) and net.mean() <= 0 or (pf is not None and pf <= 1))})
    return pd.DataFrame(rows)


def append_research_record(audit: dict[str, Any], out: Path) -> None:
    text = PREREG.read_text(encoding="utf-8")
    text += "\n\n## Execution record\n\n"
    text += f"- Completed output: `{out.relative_to(ROOT).as_posix()}`.\n"
    text += f"- Common Coverage reproduction: {audit['common_coverage_reproduction']}; {audit['technical_candidate_count']} candidate rows, existing OOF and next-bar-open audits passed.\n"
    text += f"- Market Regime: {audit['regime_pass']} pass / {audit['regime_blocked']} blocked.\n"
    text += "- All blocked candidate outcomes are isolated single-candidate shadows, labelled DIAGNOSTIC ONLY; no shadow values are presented as policy or portfolio returns.\n"
    text += "- No 2025/2026/Final OOS data, training, search, or strategy changes. 2379/3034 remained proxy-only.\n"
    PREREG.write_text(text, encoding="utf-8")


def update_registries(out: Path) -> None:
    import csv
    exp_path = ROOT / "research/registries/experiment_registry.csv"
    with exp_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f)); fields = list(rows[0])
    for row in rows:
        if row.get("experiment_id") == EXPERIMENT_ID:
            row.update({"status": "completed", "decision": "DIAGNOSTIC_ONLY_NO_WINNER",
                        "report_file": str((out / "filter_funnel_report.html").relative_to(ROOT)).replace("\\", "/"),
                        "result_file": str((out / "filter_funnel_audit.json").relative_to(ROOT)).replace("\\", "/"),
                        "notes": "Completed fixed diagnostic-only funnel and isolated shadow audit; no strategy changes, search, retraining, or forbidden periods."})
    with exp_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    ds_path = ROOT / "research/registries/dataset_registry.csv"
    with ds_path.open("r", encoding="utf-8-sig", newline="") as f:
        ds = list(csv.DictReader(f)); ds_fields = list(ds[0])
    for row in ds:
        if str(row.get("dataset_id", "")).startswith("filter-funnel-exp001-2023-2024-audit_subject-"):
            row.update({"results_viewed": "true", "report_location": str((out / "filter_funnel_user_analysis.html").relative_to(ROOT)).replace("\\", "/"),
                        "result_location": str((out / "filter_funnel_audit.json").relative_to(ROOT)).replace("\\", "/"),
                        "notes": "Filter funnel and diagnostic-only isolated shadow results viewed; no strategy changes made."})
        elif str(row.get("dataset_id", "")).startswith("filter-funnel-exp001-2023-2024-market_proxy_only-"):
            row.update({"results_viewed": "false", "report_location": "research/strategy_filter_funnel_exp001.md",
                        "result_location": "", "notes": "Used only as existing V2-B market proxy component. No individual performance/funnel/shadow results calculated, stored, displayed, or viewed."})
    with ds_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ds_fields); writer.writeheader(); writer.writerows(ds)


def _table(frame: pd.DataFrame, cols: list[str]) -> str:
    if frame.empty:
        return "<p>無資料</p>"
    selected = frame[[c for c in cols if c in frame.columns]].copy()
    return selected.to_html(index=False, escape=True, border=0, classes="data")


def render_full_html(audit: dict[str, Any], funnel: pd.DataFrame, quality: pd.DataFrame,
                     blocked: pd.DataFrame, reasons: pd.DataFrame, exposure: pd.DataFrame,
                     cost: pd.DataFrame) -> str:
    data = {"audit": audit, "funnel": funnel.to_dict("records"), "quality": quality.to_dict("records"),
            "blocked": blocked.to_dict("records"), "execution_reasons": reasons.to_dict("records"),
            "exposure": exposure.to_dict("records"), "cost": cost.to_dict("records")}
    payload = json.dumps(safe(data), ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Filter Funnel Audit Exp001</title>
<style>body{{font:15px/1.6 system-ui,"Microsoft JhengHei",sans-serif;background:#08131d;color:#e8f0f7;margin:0}}main{{max-width:1200px;margin:auto;padding:24px}}header,.card{{background:#102434;border:1px solid #284459;border-radius:14px;padding:20px;margin:16px 0}}h1,h2{{color:#fff}}.notice{{color:#ffd58e}}.data{{border-collapse:collapse;width:100%;font-size:13px}}.data td,.data th{{padding:8px;border-bottom:1px solid #294458;text-align:left}}.scroll{{overflow:auto}}select{{padding:8px;background:#091923;color:#fff;border-radius:8px}}details{{padding:10px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}</style><main><header><h1>代表股 Filter Funnel Audit Exp001</h1><p>固定 Technical V2-B → Market Regime → ML Gate → Execution。期間僅 2023–2024 開發資料；2379、3034 僅為市場代理資料。</p><p class="notice">Shadow 為單一候選隔離診斷，不是新策略或投資組合報酬，不能累加成策略績效。</p><label>模型 <select id="model"><option value="random_forest">Random Forest</option><option value="xgboost">XGBoost</option><option value="gru">GRU</option></select></label></header>
<section class="card"><h2>整體漏斗</h2><div id="overall" class="scroll"></div></section><section class="card"><h2>阻擋機會與交易品質</h2><div id="blocked" class="scroll"></div><div id="quality" class="scroll"></div></section><section class="grid"><section class="card"><h2>執行阻擋原因</h2><div id="reasons" class="scroll"></div></section><section class="card"><h2>曝險來源</h2><div id="exposure" class="scroll"></div></section></section><section class="card"><h2>交易成本</h2><div id="cost" class="scroll"></div></section><section class="card"><h2>逐股票與 Fold 漏斗</h2><div id="stock" class="scroll"></div><div id="fold" class="scroll"></div></section><section class="card"><details><summary>完整研究稽核 JSON</summary><pre id="audit"></pre></details></section></main>
<script>const D={payload};const model=()=>document.querySelector('#model').value;const table=(id,rows,filter=()=>true)=>{{let x=rows.filter(filter);document.querySelector(id).innerHTML=x.length?'<table class="data"><thead><tr>'+Object.keys(x[0]).map(k=>'<th>'+k+'</th>').join('')+'</tr></thead><tbody>'+x.map(r=>'<tr>'+Object.values(r).map(v=>'<td>'+String(v??'—')+'</td>').join('')+'</tr>').join('')+'</tbody></table>':'<p>無資料</p>'}};function draw(){{const m=model();table('#overall',D.funnel,r=>r.scope==='overall'&&(r.model==='ALL'||r.model===m));table('#blocked',D.blocked,r=>r.stage==='MARKET_REGIME'||r.stage===m);table('#quality',D.quality,r=>r.model===m);table('#reasons',D.execution_reasons,r=>r.model===m);table('#exposure',D.exposure,r=>r.model===m);table('#cost',D.cost);table('#stock',D.funnel,r=>r.scope==='per_stock'&&(r.model==='ALL'||r.model===m));table('#fold',D.funnel,r=>r.scope==='per_fold'&&(r.model==='ALL'||r.model===m));document.querySelector('#audit').textContent=JSON.stringify(D.audit,null,2)}}document.querySelector('#model').addEventListener('change',draw);draw();</script></html>'''


def render_user_html(audit: dict[str, Any], funnel: pd.DataFrame, blocked: pd.DataFrame, shadows: pd.DataFrame) -> str:
    compact = []
    for model in MODELS:
        overall = funnel.loc[(funnel.scope == "overall") & (funnel.model == model)]
        def val(stage: str, column: str = "pass_count") -> int:
            r = overall.loc[overall.stage == stage]
            return int(r.iloc[0][column]) if len(r) else 0
        b = blocked.loc[blocked.stage == model]
        b = b.iloc[0].to_dict() if len(b) else {}
        compact.append({"model": model, "regime_pass": int(audit["regime_pass"]), "regime_blocked": int(audit["regime_blocked"]),
                        "buy": val("ML_GATE", "buy_count"), "hold": val("ML_GATE", "hold_count"), "sell": val("ML_GATE", "sell_count"),
                        "actual_trades": val("ACTUAL_TRADE"), "blocked_positive": b.get("positive_trade_count", 0),
                        "blocked_negative": b.get("negative_trade_count", 0),
                        "blocked_avg_net_return": b.get("average_net_trade_return"),
                        "diagnosis": audit["model_diagnosis"][model]["classification"]})
    payload = json.dumps(safe({"audit": audit, "models": compact}), ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>交易機會漏斗</title>
<style>body{{font:16px/1.6 system-ui,"Microsoft JhengHei",sans-serif;background:#07141f;color:#eaf3fa;margin:0}}main{{max-width:1000px;margin:auto;padding:22px}}header,.panel{{background:#102536;border:1px solid #28465b;border-radius:16px;padding:20px;margin:16px 0}}header{{background:#0c344d}}select{{padding:10px;background:#061722;color:#fff;border:1px solid #42647a;border-radius:8px}}.funnel{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.step{{background:#0a1b29;border-radius:12px;padding:16px;text-align:center}}.num{{font-size:30px;font-weight:800;color:#49a6ff}}.muted{{color:#aac0d0}}.callout{{background:#172d3d;padding:14px;border-radius:10px}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:8px;border-bottom:1px solid #29485d}}@media(max-width:650px){{.funnel{{grid-template-columns:1fr 1fr}}}}</style><main><header><h1>119 個共同合格機會，最後留下多少？</h1><p>這是漏斗診斷，不是策略最佳化。正式對象：2330、2454、2303；2379、3034 只作既有市場代理資料。</p><label>查看模型 <select id="model"><option value="random_forest">Random Forest</option><option value="xgboost">XGBoost</option><option value="gru">GRU</option></select></label></header>
<section class="panel"><h2>交易機會怎麼逐層減少</h2><div class="funnel"><div class="step"><div>Technical 機會</div><div class="num">119</div></div><div class="step"><div>通過市場環境</div><div class="num" id="pass"></div><div id="regblock" class="muted"></div></div><div class="step"><div>ML BUY 訊號</div><div class="num" id="buy"></div><div class="muted">HOLD / SELL 會擋下</div></div><div class="step"><div>實際成交</div><div class="num" id="trades"></div></div></div><p id="gate"></p></section>
<section class="panel"><h2>被 ML 擋下的機會後來如何？</h2><div class="callout" id="diagnosis"></div><p id="quality" class="muted"></p></section>
<section class="panel"><details><summary>查看研究細節</summary><div id="details"></div><p class="muted">單筆 shadow 逐筆重置資金與持倉，用相同凍結進出場與成本作候選品質診斷。它不含原策略的持倉、冷卻與資金路徑互動，因此不能加總成新的策略報酬。策略層級請參考已完成的 Benchmark Exp001。</p></details></section>
<section class="panel muted"><p>期間：2023–2024 已公開於研究開發的 Development 資料；三個固定 Fold。執行仍為完成訊號後，下一根可交易 5 分 K 開盤成交。</p><p>本輪沒有修改策略、風控、模型或參數，也沒有訓練模型。</p></section></main>
<script>const D={payload};const n=x=>x==null?'—':Number(x).toFixed(1);const money=x=>x==null?'—':'NT$'+Math.round(Number(x)).toLocaleString('zh-TW');function draw(){{const m=document.querySelector('#model').value;const r=D.models.find(x=>x.model===m);document.querySelector('#pass').textContent=D.audit.regime_pass;document.querySelector('#regblock').textContent='市場環境擋下 '+D.audit.regime_blocked+' 個';document.querySelector('#buy').textContent=r.buy;document.querySelector('#trades').textContent=r.actual_trades;document.querySelector('#gate').textContent='ML 分類：BUY '+r.buy+'、HOLD '+r.hold+'、SELL '+r.sell+'。';let words=r.diagnosis==='PRECISE_FILTER_EVIDENCE'?'目前有較精準過濾的證據。':r.diagnosis==='OVER_CONSERVATIVE_EVIDENCE'?'被擋下的機會整體仍偏正向，呈現過度保守跡象。':r.diagnosis==='INSUFFICIENT_EVIDENCE'?'目前可評估的樣本不足，無法下結論。':'目前結果混合，尚無一致證據。';document.querySelector('#diagnosis').textContent=words;document.querySelector('#quality').textContent='在可評估的隔離 shadow 候選中，被擋交易有 '+r.blocked_positive+' 筆正向、'+r.blocked_negative+' 筆負向；平均淨交易報酬 '+(r.blocked_avg_net_return==null?'—':(Number(r.blocked_avg_net_return)*100).toFixed(2)+'%')+'。這些不是策略累計報酬。';let q=D.audit.model_diagnosis[m];document.querySelector('#details').innerHTML='<p>診斷分類：'+q.classification+'</p><p>BUY 平均淨交易報酬：'+(q.buy_quality.average_net_trade_return==null?'—':(q.buy_quality.average_net_trade_return*100).toFixed(2)+'%')+'；Profit Factor：'+n(q.buy_quality.profit_factor)+'</p><p>HOLD / SELL 阻擋集合平均淨交易報酬：'+(q.blocked_quality.average_net_trade_return==null?'—':(q.blocked_quality.average_net_trade_return*100).toFixed(2)+'%')+'；Profit Factor：'+n(q.blocked_quality.profit_factor)+'</p><h3>不同股票的阻擋證據</h3><table><tr><th>股票</th><th>阻擋候選</th><th>可評估</th><th>平均報酬</th><th>PF</th></tr>'+q.stock_evidence.map(x=>'<tr><td>'+x.stock_code+'</td><td>'+x.candidate_count+'</td><td>'+x.evaluable_count+'</td><td>'+ (x.average_net_trade_return==null?'—':(x.average_net_trade_return*100).toFixed(2)+'%')+'</td><td>'+n(x.profit_factor)+'</td></tr>').join('')+'</table>'}}document.querySelector('#model').addEventListener('change',draw);draw();</script></html>'''


if __name__ == "__main__":
    raise SystemExit(main())

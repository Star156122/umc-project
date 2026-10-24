"""Development-only four-variant backtest orchestration and audit helpers."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import numpy as np

STRATEGIES = ("technical", "rf", "xgboost", "gru")
MAX_DATE = "2024-12-31"


class DevelopmentBacktestBlocked(RuntimeError):
    """Raised when the development-mode data/period contract is not met."""


def validate_request(stock_code: str, strategies: Iterable[str], config: dict[str, Any]) -> list[str]:
    code = str(stock_code).zfill(4)
    if code not in config["stock_universe"]:
        raise DevelopmentBacktestBlocked("BLOCKED_UNSUPPORTED_STOCK: stock is outside the frozen Technical V2-B universe")
    selected = list(strategies)
    if not selected or len(selected) != len(set(selected)) or any(item not in STRATEGIES for item in selected):
        raise DevelopmentBacktestBlocked("BLOCKED_INVALID_STRATEGY_SELECTION")
    if config["data_role"] != "historical_development" or config["data_protection"]["final_oos_used"]:
        raise DevelopmentBacktestBlocked("BLOCKED_DATA_ROLE_POLICY")
    for period in config["periods"].values():
        if period["start"] > period["end"] or period["end"] > MAX_DATE:
            raise DevelopmentBacktestBlocked("BLOCKED_PERIOD_OUTSIDE_AUTHORIZED_DEVELOPMENT")
    if config["periods"]["training"]["end"] >= config["periods"]["validation"]["start"]:
        raise DevelopmentBacktestBlocked("BLOCKED_TRAIN_VALIDATION_OVERLAP")
    if config["periods"]["validation"]["end"] >= config["periods"]["backtest"]["start"]:
        raise DevelopmentBacktestBlocked("BLOCKED_VALIDATION_BACKTEST_OVERLAP")
    return selected


def mature_label_mask(meta, period: dict[str, str]) -> np.ndarray:
    """A sample is eligible only when signal and complete target stay in one split."""
    signal = np.asarray(meta["signal_date"].astype(str))
    target = np.asarray(meta["target_date"].astype(str))
    start, end = period["start"], period["end"]
    return (signal >= start) & (signal <= end) & (target >= start) & (target <= end)


def assert_no_target_leakage(meta, training_period: dict[str, str], validation_period: dict[str, str]) -> None:
    train = mature_label_mask(meta, training_period)
    validation = mature_label_mask(meta, validation_period)
    if np.any(train & validation):
        raise DevelopmentBacktestBlocked("BLOCKED_SPLIT_OVERLAP")
    if np.any(train & (np.asarray(meta["target_date"].astype(str)) > training_period["end"])):
        raise DevelopmentBacktestBlocked("BLOCKED_IMMATURE_TRAIN_LABEL")
    if np.any(validation & (np.asarray(meta["target_date"].astype(str)) > validation_period["end"])):
        raise DevelopmentBacktestBlocked("BLOCKED_IMMATURE_VALIDATION_LABEL")


def prediction_precedes_candidate(prediction: dict[str, Any] | None, candidate_time: Any) -> bool:
    """Enforce prior-session 13:30 ML decision before Technical entry opportunity."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    if not prediction:
        return False
    try:
        candidate = candidate_time if isinstance(candidate_time, datetime) else datetime.fromisoformat(str(candidate_time))
        decision = datetime.fromisoformat(str(prediction["decision_time"]))
        signal_date = str(prediction["signal_date"])
        entry_date = str(prediction["entry_date"])
    except (KeyError, TypeError, ValueError):
        return False
    tz = ZoneInfo("Asia/Taipei")
    candidate = candidate.replace(tzinfo=tz) if candidate.tzinfo is None else candidate.astimezone(tz)
    decision = decision.replace(tzinfo=tz) if decision.tzinfo is None else decision.astimezone(tz)
    return (
        entry_date == candidate.date().isoformat()
        and signal_date < entry_date
        and decision.date().isoformat() == signal_date
        and decision.time().replace(tzinfo=None) == time(13, 30)
        and decision < candidate
    )


def open_readonly_sqlite(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise DevelopmentBacktestBlocked(f"BLOCKED_MISSING_DATABASE: {path}")
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def data_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_metric(value: Any, reason: str) -> dict[str, Any]:
    if value is None:
        return {"value": None, "reason": reason}
    numeric = float(value)
    if not math.isfinite(numeric):
        return {"value": None, "reason": reason}
    return {"value": numeric, "reason": None}


def gate_audit_fields(strategy_id: str, candidate_count: int, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep pure Technical results distinct from ML-gate statistics."""
    if strategy_id == "technical":
        return {
            "gate_applicable": False,
            "technical_candidate_schedule_count": int(candidate_count),
            "gate_evaluated_candidate_count": None,
            "gate_not_evaluated_candidate_count": None,
            "gate_entry_count": None,
            "gate_block_counts": None,
            "gate_not_applicable_reason": "PURE_TECHNICAL_STRATEGY",
        }
    evaluated = len(decisions)
    return {
        "gate_applicable": True,
        "technical_candidate_schedule_count": int(candidate_count),
        "gate_evaluated_candidate_count": evaluated,
        "gate_not_evaluated_candidate_count": max(0, int(candidate_count) - evaluated),
        "gate_not_evaluated_reason": (
            "Entry engine evaluates ML gate only when account state allows an entry; "
            "unprocessed candidates remain visible in the shared schedule count."
        ),
        "gate_entry_count": sum(row.get("hybrid_action") == "ENTRY" for row in decisions),
        "gate_block_counts": {
            key: sum(str(row.get("hybrid_action", "")).startswith("BLOCK_" + key)
                     for row in decisions)
            for key in ("BUY", "HOLD", "SELL", "MISSING_OR_LATE_PREDICTION")
        },
    }


def derive_equity_curve(
    bars: list[dict[str, Any]], trades: list[dict[str, Any]], initial_capital: float,
) -> list[dict[str, Any]]:
    """Mark equity each completed bar using actual engine fills and cash flows."""
    ordered = sorted(trades, key=lambda row: int(row["timestamp"]))
    index = 0
    cash, position = float(initial_capital), 0
    cost_basis, realized_pnl = 0.0, 0.0
    curve: list[dict[str, Any]] = []
    peak = float(initial_capital)
    for bar in bars:
        stamp = int(bar.get("time", bar.get("timestamp")))
        while index < len(ordered) and int(ordered[index]["timestamp"]) <= stamp:
            trade = ordered[index]
            cash += float(trade["net_cash_flow"])
            qty = int(trade["quantity"])
            if trade["action"] == "Buy":
                position += qty
                cost_basis += float(trade["gross_amount"]) + float(trade["fee"])
            else:
                matched = min(qty, position)
                released = cost_basis * (matched / position) if position else 0.0
                realized_pnl += float(trade["gross_amount"]) - float(trade["fee"]) - float(trade["tax"]) - released
                cost_basis -= released
                position -= qty
            index += 1
        close = float(bar.get("close", bar.get("Close")))
        position_value = position * close
        equity = cash + position_value
        peak = max(peak, equity)
        curve.append({
            "timestamp": bar.get("datetime", bar.get("timestamp")),
            "equity": equity,
            "cash": cash,
            "position_value": position_value,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": position_value - cost_basis,
            "cumulative_return_pct": (equity / initial_capital - 1) * 100 if initial_capital else None,
            "drawdown_pct": (equity / peak - 1) * 100 if peak else None,
            "position_quantity": position,
        })
    return curve


def summary_from_curve(curve: list[dict[str, Any]], completed_pnl: list[dict[str, Any]], initial_capital: float) -> dict[str, Any]:
    if not curve:
        raise DevelopmentBacktestBlocked("BLOCKED_NO_BACKTEST_BARS")
    final_equity = float(curve[-1]["equity"])
    wins = sum(float(row["net_pnl"]) > 0 for row in completed_pnl)
    closed_count = len(completed_pnl)
    gross_wins = sum(float(row["net_pnl"]) for row in completed_pnl if float(row["net_pnl"]) > 0)
    gross_losses = abs(sum(float(row["net_pnl"]) for row in completed_pnl if float(row["net_pnl"]) < 0))
    max_drawdown = max(0.0, max((-(float(row["drawdown_pct"])) for row in curve), default=0.0))
    return {
        "initial_capital": float(initial_capital), "final_equity": final_equity,
        "total_return_pct": (final_equity / initial_capital - 1) * 100,
        "net_pnl": final_equity - initial_capital,
        "realized_net_pnl": sum(float(row["net_pnl"]) for row in completed_pnl),
        "unrealized_pnl": final_equity - initial_capital - sum(float(row["net_pnl"]) for row in completed_pnl),
        "win_rate_pct": (wins / closed_count * 100) if closed_count else None,
        "win_rate_reason": None if closed_count else "NO_COMPLETED_TRADES",
        "trade_count": closed_count, "max_drawdown_pct": max_drawdown,
        "profit_factor": (gross_wins / gross_losses) if gross_losses else (None if not closed_count else None),
        "profit_factor_reason": "NO_LOSSES_OR_NO_COMPLETED_TRADES" if not gross_losses else None,
    }


def build_buy_and_hold_result(*, connection, stock_code: str, period: dict[str, str],
                              initial_capital: float, commission_rate: float) -> dict[str, Any]:
    """Build a same-bars, long-only mark-to-market benchmark without Risk V1.

    The benchmark buys the maximum whole-share quantity affordable after entry
    commission at the first available in-period bar open, then marks those
    shares at each bar close. It does not liquidate at period end, so no exit
    commission or transaction tax is charged.
    """
    from datetime import date, datetime, time, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Taipei")
    start_day = date.fromisoformat(period["start"])
    end_day = date.fromisoformat(period["end"])
    lower = int(datetime.combine(start_day, time.min, tz).timestamp())
    upper = int(datetime.combine(end_day + timedelta(days=1), time.min, tz).timestamp())
    rows = connection.execute(
        "SELECT kbar_timestamp, open, close FROM market_kbars "
        "WHERE stock_code=? AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
        "ORDER BY kbar_timestamp",
        (str(stock_code).zfill(4), lower, upper),
    ).fetchall()
    if not rows:
        raise DevelopmentBacktestBlocked("BLOCKED_BUY_AND_HOLD_NO_BACKTEST_BARS")
    first_open = float(rows[0]["open"])
    if first_open <= 0:
        raise DevelopmentBacktestBlocked("BLOCKED_BUY_AND_HOLD_INVALID_ENTRY_PRICE")
    quantity = int(initial_capital / (first_open * (1.0 + commission_rate)))
    if quantity <= 0:
        raise DevelopmentBacktestBlocked("BLOCKED_BUY_AND_HOLD_INSUFFICIENT_CAPITAL")
    gross = quantity * first_open
    fee = gross * commission_rate
    fill = {
        "timestamp": int(rows[0]["kbar_timestamp"]), "action": "Buy", "quantity": quantity,
        "gross_amount": gross, "fee": fee, "tax": 0.0,
        "net_cash_flow": -(gross + fee), "price": first_open,
    }
    bars = [{
        "time": int(row["kbar_timestamp"]),
        "datetime": datetime.fromtimestamp(int(row["kbar_timestamp"]), tz).strftime("%Y-%m-%d %H:%M:%S"),
        "close": float(row["close"]),
    } for row in rows]
    curve = derive_equity_curve(bars, [fill], initial_capital)
    summary = summary_from_curve(curve, [], initial_capital)
    summary["transaction_count"] = 1
    summary["trade_count"] = None
    summary["win_rate_pct"] = None
    summary["win_rate_reason"] = "BUY_AND_HOLD_HAS_NO_CLOSED_TRADES"
    return {
        "strategy_id": "buy_and_hold", "strategy_version": "buy_and_hold_benchmark_v1",
        "stock_code": str(stock_code).zfill(4), "period": dict(period),
        "data_role": "historical_development", "summary": summary,
        "details": {
            "profit_factor": None, "profit_factor_reason": "BUY_AND_HOLD_HAS_NO_CLOSED_TRADES",
            "sharpe_ratio": None, "sharpe_reason": "NOT_COMPUTED_FOR_PASSIVE_BENCHMARK",
            "payoff_ratio": None, "commission_total": fee, "transaction_tax_total": 0.0,
            "total_transaction_cost": fee, "transaction_count": 1,
            "shares_held": quantity, "entry_timestamp": bars[0]["datetime"],
            "entry_price": first_open, "mark_to_market_exit": True,
            "risk_contract": None,
            "exposure_assumption": "Maximum affordable whole shares; no Risk V1 stop or 40% position cap; held to final close and marked to market without liquidation.",
        },
        "trades": [fill], "completed_trade_pnl": [], "equity_curve": curve,
        "audit": {"benchmark": True, "entry_execution": "first available in-period 5-minute bar open",
                  "price_source": "same Technical V2-B clean 5-minute bar database and backtest period",
                  "exit_execution": "marked to market at final close; no sale executed",
                  "risk_v1_applied": False},
    }


def build_exposure_matched_buy_and_hold_result(
    *, connection, stock_code: str, period: dict[str, str], initial_capital: float,
    commission_rate: float, transaction_tax_rate: float,
    max_position_fraction: float,
) -> dict[str, Any]:
    """Buy-and-hold comparator capped at the active strategy's entry exposure.

    Unlike the original benchmark, this comparator explicitly liquidates at
    the final close so both entry and exit costs are included.  The original
    100% benchmark remains unchanged and is still reported separately.
    """
    from datetime import date, datetime, time, timedelta
    from zoneinfo import ZoneInfo

    if not 0 < max_position_fraction <= 1:
        raise DevelopmentBacktestBlocked("BLOCKED_INVALID_EXPOSURE_MATCH_FRACTION")
    tz = ZoneInfo("Asia/Taipei")
    start_day = date.fromisoformat(period["start"])
    end_day = date.fromisoformat(period["end"])
    lower = int(datetime.combine(start_day, time.min, tz).timestamp())
    upper = int(datetime.combine(end_day + timedelta(days=1), time.min, tz).timestamp())
    rows = connection.execute(
        "SELECT kbar_timestamp, open, high, low, close FROM market_kbars "
        "WHERE stock_code=? AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
        "ORDER BY kbar_timestamp",
        (str(stock_code).zfill(4), lower, upper),
    ).fetchall()
    if not rows:
        raise DevelopmentBacktestBlocked("BLOCKED_EXPOSURE_MATCHED_BUY_AND_HOLD_NO_BACKTEST_BARS")
    first_open = float(rows[0]["open"])
    final_close = float(rows[-1]["close"])
    quantity = int(
        initial_capital * max_position_fraction
        / (first_open * (1.0 + commission_rate))
    )
    if quantity <= 0:
        raise DevelopmentBacktestBlocked("BLOCKED_EXPOSURE_MATCHED_BUY_AND_HOLD_INSUFFICIENT_CAPITAL")
    buy_gross = quantity * first_open
    buy_fee = buy_gross * commission_rate
    sell_gross = quantity * final_close
    sell_fee = sell_gross * commission_rate
    sell_tax = sell_gross * transaction_tax_rate
    buy = {
        "timestamp": int(rows[0]["kbar_timestamp"]), "action": "Buy", "quantity": quantity,
        "gross_amount": buy_gross, "fee": buy_fee, "tax": 0.0,
        "net_cash_flow": -(buy_gross + buy_fee), "price": first_open,
    }
    sell = {
        "timestamp": int(rows[-1]["kbar_timestamp"]), "action": "Sell", "quantity": quantity,
        "gross_amount": sell_gross, "fee": sell_fee, "tax": sell_tax,
        "net_cash_flow": sell_gross - sell_fee - sell_tax, "price": final_close,
    }
    bars = [{
        "time": int(row["kbar_timestamp"]),
        "datetime": datetime.fromtimestamp(int(row["kbar_timestamp"]), tz).strftime("%Y-%m-%d %H:%M:%S"),
        "open": float(row["open"]), "high": float(row["high"]),
        "low": float(row["low"]), "close": float(row["close"]),
    } for row in rows]
    curve = derive_equity_curve(bars, [buy, sell], initial_capital)
    gross_pnl = sell_gross - buy_gross
    total_cost = buy_fee + sell_fee + sell_tax
    pnl = [{
        "gross_pnl": gross_pnl, "fee": buy_fee + sell_fee, "tax": sell_tax,
        "slippage": 0.0, "net_pnl": gross_pnl - total_cost,
        "return_pct": (gross_pnl - total_cost) / buy_gross if buy_gross else None,
    }]
    summary = summary_from_curve(curve, pnl, initial_capital)
    return {
        "strategy_id": "exposure_matched_buy_and_hold",
        "strategy_version": "exposure_matched_buy_and_hold_v1",
        "stock_code": str(stock_code).zfill(4), "period": dict(period),
        "data_role": "historical_development", "summary": summary,
        "details": {
            "commission_total": buy_fee + sell_fee,
            "transaction_tax_total": sell_tax,
            "total_transaction_cost": total_cost,
            "transaction_count": 2,
            "shares_held": quantity,
            "entry_price": first_open,
            "exit_price": final_close,
            "entry_exposure_fraction": buy_gross / initial_capital if initial_capital else None,
            "maximum_entry_exposure_fraction": max_position_fraction,
            "risk_contract": None,
        },
        "trades": [buy, sell], "completed_trade_pnl": pnl, "equity_curve": curve,
        "audit": {
            "benchmark": True,
            "entry_execution": "first available in-period 5-minute bar open",
            "exit_execution": "final available in-period 5-minute bar close with commission and tax",
            "max_position_fraction": max_position_fraction,
            "risk_v1_applied": False,
        },
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False, default=str), encoding="utf-8")


def train_fixed_model_set(config: dict[str, Any], selected_models: Iterable[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit ML V1 models once on mature 2023 labels; validation never tunes them."""
    import pandas as pd
    from ml.data_pipeline import PreparedData
    from ml.models import evaluate_multiclass, fit_multiclass_model
    from ml.ml_v1_sequence import SEQUENCE_COMPLETE, select_exact_sequence
    from ml.research_features import build_daily_stock_features
    from ml.trading_pipeline import _intraday_features
    from scripts import run_ml_v1_phase_b as phase_b
    from scripts.audit_ml_v1_labels import load_training_kbars

    phase, research, _ = phase_b.load_phase_b_config()
    if phase["training_period"] != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise DevelopmentBacktestBlocked("BLOCKED_ML_V1_SOURCE_PERIOD_CHANGED")
    base, input_audit = phase_b.build_raw_inputs(phase, research)
    meta = base["meta"].reset_index(drop=True)
    label = base["labels"]["fixed_1pct"]
    train_period = config["periods"]["training"]
    val_period = config["periods"]["validation"]
    tech_codes = set(str(code).zfill(4) for code in config["stock_universe"])
    stock_mask = meta["stock_code"].astype(str).str.zfill(4).isin(tech_codes).to_numpy()
    train_mask = mature_label_mask(meta, train_period) & stock_mask
    val_mask = mature_label_mask(meta, val_period) & stock_mask
    train_idx, val_idx = np.flatnonzero(train_mask), np.flatnonzero(val_mask)
    assert_no_target_leakage(meta, train_period, val_period)
    if not len(train_idx) or not len(val_idx):
        raise DevelopmentBacktestBlocked("BLOCKED_EMPTY_TRAIN_OR_VALIDATION")
    if set(np.unique(label[train_idx])) != {0, 1, 2}:
        raise DevelopmentBacktestBlocked("BLOCKED_TRAINING_MISSING_CLASS")
    label_names = ("SELL", "HOLD", "BUY")
    training_label_counts = {
        label_names[index]: int((label[train_idx] == index).sum())
        for index in range(3)
    }
    selected_universe_feature_audit = {
        "scope": "frozen Technical V2-B stock universe after shared feature cleaning",
        "model_ready_samples": int(stock_mask.sum()),
        "model_ready_samples_by_stock": {
            str(code): int(count)
            for code, count in (
                meta.loc[stock_mask, "stock_code"].astype(str).str.zfill(4).value_counts().sort_index().items()
            )
        },
    }

    # Build inference-only rows from exact feature sequences, independent of
    # whether a future target/label is mature. Labels are never created here.
    inference_raw = load_training_kbars(research)
    tech_codes = set(str(code).zfill(4) for code in config["stock_universe"])
    inference_raw = inference_raw.loc[inference_raw["stock_code"].astype(str).str.zfill(4).isin(tech_codes)].copy()
    inference_raw = inference_raw.loc[inference_raw["datetime"].dt.dayofweek < 5].copy()
    inference_raw["trade_date"] = inference_raw["datetime"].dt.date
    featured, _inference_cleaning = _intraday_features(inference_raw, research["feature_policy"]["core_features"])
    context_frame = build_daily_stock_features(featured)
    context_names = list(research["feature_policy"]["gru"]["auxiliary_context"])
    context_frame["stock_code"] = context_frame["stock_code"].astype(str).str.zfill(4)
    context_frame["decision_date"] = pd.to_datetime(context_frame["trade_date"]).dt.date.astype(str)
    context_map = {
        (str(row.stock_code), str(row.decision_date)): np.asarray([getattr(row, key) for key in context_names], dtype=np.float32)
        for row in context_frame[["stock_code", "decision_date", *context_names]].itertuples(index=False)
    }
    calendars = {
        str(code).zfill(4): sorted(group["trade_date"].astype(str).unique())
        for code, group in inference_raw.groupby("stock_code", sort=False)
    }
    next_session = {
        (code, dates[i]): dates[i + 1]
        for code, dates in calendars.items()
        for i in range(len(dates) - 1)
    }
    inference_rows: list[tuple[str, str, str, np.ndarray, np.ndarray]] = []
    inference_exclusions = {"incomplete_sequence": 0, "nonfinite_feature": 0, "missing_context": 0, "no_next_session_in_range": 0}
    feature_names = list(research["feature_policy"]["core_features"])
    for (code, day), group in featured.groupby(["stock_code", "trade_date"], sort=True):
        stock, signal_date = str(code).zfill(4), str(day)
        sequence, eligibility = select_exact_sequence(group)
        if sequence is None or eligibility.status != SEQUENCE_COMPLETE:
            inference_exclusions["incomplete_sequence"] += 1
            continue
        values = sequence[feature_names].to_numpy(dtype=np.float32)
        if not np.isfinite(values).all():
            inference_exclusions["nonfinite_feature"] += 1
            continue
        ctx = context_map.get((stock, signal_date))
        if ctx is None or not np.isfinite(ctx).all():
            inference_exclusions["missing_context"] += 1
            continue
        entry_date = next_session.get((stock, signal_date))
        if entry_date is None:
            inference_exclusions["no_next_session_in_range"] += 1
            continue
        inference_rows.append((stock, signal_date, entry_date, values, ctx))
    if not inference_rows:
        raise DevelopmentBacktestBlocked("BLOCKED_NO_EXACT_INFERENCE_SEQUENCES")
    inference_X = np.stack([row[3] for row in inference_rows]).astype(np.float32)
    inference_context = np.stack([row[4] for row in inference_rows]).astype(np.float32)
    inference_stock = np.asarray([row[0] for row in inference_rows], dtype=object)
    inference_signal = np.asarray([row[1] for row in inference_rows], dtype=object)
    inference_entry = np.asarray([row[2] for row in inference_rows], dtype=object)

    models: dict[str, Any] = {}
    model_audit: dict[str, Any] = {}
    model_config_path = Path(__file__).resolve().parents[2] / config["ml"]["config"]
    if data_hash(model_config_path) != config["ml"].get("model_parameters_source_sha256"):
        raise DevelopmentBacktestBlocked("BLOCKED_ML_V1_PARAMETER_SOURCE_HASH_MISMATCH")
    model_config = json.loads(model_config_path.read_text(encoding="utf-8"))
    params_by_model = model_config["models"]
    raw = {"X": base["X"], "y": label, "meta": meta, "context": base["context"],
           "feature_names": base["feature_names"], "context_feature_names": base["context_feature_names"]}
    for name in selected_models:
        include_context = name == "gru"
        from scripts.run_ml_industry_generalization import make_scaled_data
        data = make_scaled_data(
            raw, train_idx, {"validation": val_idx}, include_context=include_context,
            stage="development_mode_fixed_training",
            audit_extra={"train_period": train_period, "validation_period": val_period,
                         "validation_used_for_selection": False, "scaler_fit_indices": "training_only"},
        )
        # V1's fixed GRU cap is ten epochs. No validation-based early stopping
        # or epoch selection occurs in this one-shot system development run.
        fixed_epochs = int(params_by_model[name]["max_epochs"]) if name == "gru" else None
        model, seconds, predictor, extra = fit_multiclass_model(name, data, params_by_model[name], fixed_epochs=fixed_epochs)
        validation_context = None if data.context_X is None else data.context_X["validation"]
        validation_prob = np.asarray(predictor(data.X["validation"], validation_context), dtype=float)
        if validation_prob.shape != (len(val_idx), 3) or not np.isfinite(validation_prob).all():
            raise DevelopmentBacktestBlocked(f"BLOCKED_INVALID_{name.upper()}_PREDICTIONS")
        val_metrics = evaluate_multiclass(label[val_idx], validation_prob)

        all_scaled = ((inference_X - data.scaler_mean) / data.scaler_scale).astype(np.float32)
        all_context = None
        if include_context:
            all_context = ((inference_context - data.context_scaler_mean) / data.context_scaler_scale).astype(np.float32)
        all_prob = np.asarray(predictor(all_scaled, all_context), dtype=float)
        if all_prob.shape != (len(inference_rows), 3) or not np.isfinite(all_prob).all():
            raise DevelopmentBacktestBlocked(f"BLOCKED_INVALID_{name.upper()}_INFERENCE")
        row_prediction = np.argmax(all_prob, axis=1)
        lookup = {}
        for i in range(len(inference_rows)):
            if inference_entry[i] < config["periods"]["backtest"]["start"] or inference_entry[i] > config["periods"]["backtest"]["end"]:
                continue
            # The feature day must be the previous actual session. The upstream
            # ML V1 label builder supplies the T+1 entry date; assert chronology.
            if inference_signal[i] >= inference_entry[i]:
                continue
            lookup[(inference_stock[i], inference_entry[i])] = {
                "prediction": ("SELL", "HOLD", "BUY")[int(row_prediction[i])],
                "prob_sell": float(all_prob[i, 0]), "prob_hold": float(all_prob[i, 1]),
                "prob_buy": float(all_prob[i, 2]), "signal_date": str(inference_signal[i]),
                "entry_date": str(inference_entry[i]), "decision_time": str(inference_signal[i]) + "T13:30:00+08:00",
                "model_version": "ml-v1-draft-20261012", "data_role": "historical_development",
            }
        models[name] = {"model": model, "predictor": predictor, "prediction_lookup": lookup}
        model_audit[name] = {
            "model_version": "ml-v1-draft-20261012", "training_parameters": params_by_model[name],
            "training_samples": int(len(train_idx)), "validation_samples": int(len(val_idx)),
            "training_label_counts": training_label_counts,
            "training_signal_range": [str(meta.iloc[train_idx].signal_date.min()), str(meta.iloc[train_idx].signal_date.max())],
            "training_target_max": str(meta.iloc[train_idx].target_date.max()),
            "validation_signal_range": [str(meta.iloc[val_idx].signal_date.min()), str(meta.iloc[val_idx].signal_date.max())],
            "validation_target_max": str(meta.iloc[val_idx].target_date.max()),
            "validation_metrics_diagnostic_only": val_metrics, "training_seconds": float(seconds),
            "fit_epochs": extra.get("epochs_completed", fixed_epochs),
            "early_stopping_source": extra.get("early_stopping_source"),
            "scaler_fit_on": "2023 training samples only",
            "scaler_fit_on_training_only": True,
            "scaler_fit_sample_count": int(len(train_idx)),
            "model_source": "fitted_in_process",
            "model_load_failure_fallback_used": False,
            "model_failure_policy": "RAISE_BLOCKED_ERROR_NO_DEFAULT_HOLD",
            "sequence_alignment": {
                "bars_per_sequence": int(inference_X.shape[1]),
                "decision_time": "13:30 Asia/Taipei on signal_date",
                "entry_session": "next actual trading session",
                "chronology_assertion": "signal_date < entry_date",
            },
            "future_leakage_checks": {
                "training_target_dates_bounded_before_validation": True,
                "inference_labels_created": False,
                "validation_used_for_selection": False,
                "scaler_fit_on_training_only": True,
            },
            "fixed_parameters": True,
            "validation_used_for_selection": False,
            "prediction_rows_for_backtest": len(lookup),
            "inference_sequence_eligible_samples": int(len(inference_rows)),
            "inference_sequence_exclusions": inference_exclusions,
        }
    return models, {
        "input_audit": input_audit,
        "selected_universe_feature_audit": selected_universe_feature_audit,
        "models": model_audit,
    }


def run_one_strategy(*, connection, config: dict[str, Any], stock_code: str, strategy_id: str,
                     model_runtime: dict[str, Any] | None, regime_context: dict[str, Any],
                     candidate_schedule: set[str]) -> dict[str, Any]:
    """Replay frozen V2-B using engine fills and common Risk V1 sizing."""
    import contextlib
    import io
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import numpy as np
    import pandas as pd
    from hybrid.core.risk_v1 import calculate_risk_size
    from hybrid.scripts.run_ml_v2_exp001 import TechnicalV2AloneStrategy
    from trading_system import backtest as bt
    from trading_system import technical_v1 as tv1
    from trading_system.technical_v1 import TechnicalV1Strategy

    root = Path(__file__).resolve().parents[2]
    tech = json.loads((root / config["technical"]["config"]).read_text(encoding="utf-8"))
    candidate_cfg = json.loads((root / config["technical"]["candidate_config"]).read_text(encoding="utf-8"))
    if candidate_cfg.get("base_candidate") != config["technical"]["candidate_id"] or candidate_cfg.get("market_regime_filter", {}).get("enabled") is not True:
        raise DevelopmentBacktestBlocked("BLOCKED_TECH_V2_B_IDENTITY_CHANGED")
    family_name, family, candidate = tv1.candidate_by_id(tech, config["technical"]["candidate_id"])
    stock_code = str(stock_code).zfill(4)
    fold = {"id": "DEV2024H2", "train_start": "2023-01-01", "train_end": "2024-06-30",
            "evaluate_start": config["periods"]["backtest"]["start"],
            "evaluate_end": config["periods"]["backtest"]["end"]}
    full, evaluation, app, warmup_count, eval_lower = tv1._load_fold_frame(connection, tech, candidate, stock_code, fold)
    preserved = candidate_cfg["preserved_common_backtest"]
    if (app.initial_capital != preserved["initial_capital"]
            or app.stock_fee_rate != preserved["stock_fee_rate"]
            or app.stock_tax_rate != preserved["stock_tax_rate"]
            or app.stop_loss_pct != preserved["stop_loss_pct"]):
        raise DevelopmentBacktestBlocked("BLOCKED_EXECUTION_COST_OR_RISK_CONTRACT_MISMATCH")

    if strategy_id == "technical":
        strategy = TechnicalV2AloneStrategy(
            config=app, study_config=tech, family_name=family_name, family=family,
            candidate=candidate, data_role="historical_development", fold_id=fold["id"],
            is_backtest=True, is_simulation=True, local_only=True,
            ml_model="technical_only", prediction_lookup={},
            candidate_schedule=candidate_schedule, regime_context=regime_context,
        )
    else:
        if model_runtime is None:
            raise DevelopmentBacktestBlocked("BLOCKED_MODEL_RUNTIME_MISSING")
        class FixedModelGateStrategy(TechnicalV2AloneStrategy):
            def __init__(self, *args, prediction_lookup, model_name, **kwargs):
                kwargs["ml_model"] = model_name
                kwargs["prediction_lookup"] = prediction_lookup
                super().__init__(*args, **kwargs)
                self.prediction_lookup = prediction_lookup
                self.ml_model = model_name
                self.hybrid_decisions = []
                self.risk_decisions = []
            def _try_entry(self, code, tick_time, close, snapshot):
                reasons = self._entry_reasons(close, snapshot)
                if not reasons:
                    return
                aware = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=ZoneInfo("Asia/Taipei"))
                signal_time = aware.astimezone(ZoneInfo("Asia/Taipei"))
                if signal_time.isoformat(timespec="microseconds") not in self.candidate_schedule:
                    return
                context = self.regime_context.get((str(code), signal_time.date().isoformat()))
                if not context or context.get("eligible") is not True:
                    return
                values = context.get("last_20_proxy_closes", [])
                if len(values) != 20 or not values[-1] > sum(values) / 20:
                    return
                pred = self.prediction_lookup.get((str(code).zfill(4), signal_time.date().isoformat()))
                base = {"model": self.ml_model, "stock_code": str(code), "technical_fold_id": self.fold_id,
                        "technical_signal": "BUY_CANDIDATE", "technical_signal_time": signal_time.isoformat(timespec="microseconds"),
                        "technical_candidate_date": signal_time.date().isoformat(), "close_at_signal": float(close),
                        "technical_reason": ", ".join(reasons), "data_role": "historical_development",
                        "prediction_source": "fixed_model_trained_before_backtest", "prediction_is_oof": False}
                if not prediction_precedes_candidate(pred, signal_time):
                    self.hybrid_decisions.append({**base, "ml_prediction": None, "hybrid_action": "BLOCK_MISSING_OR_LATE_PREDICTION"})
                    return
                action = "ENTRY" if pred["prediction"] == "BUY" else "BLOCK_" + pred["prediction"]
                self.hybrid_decisions.append({**base, **pred, "ml_prediction": pred["prediction"], "hybrid_action": action})
                if action != "ENTRY":
                    return
                current_equity = float(self.local_cash or 0.0) + int(self.local_position) * float(close)
                original_quantity = int(TechnicalV1Strategy._order_quantity(self, close))
                sizing = calculate_risk_size(current_equity=current_equity, entry_price=float(close),
                    original_quantity=original_quantity, stop_loss_pct=float(self.config.stop_loss_pct))
                self.risk_decisions.append({"timestamp": signal_time.isoformat(timespec="microseconds"),
                    "stock_code": str(code), "fold_id": str(self.fold_id), "model": self.ml_model, **sizing})
                if sizing["final_quantity"] <= 0:
                    return
                self._risk_quantity_for_entry = int(sizing["final_quantity"])
                try:
                    TechnicalV1Strategy._try_entry(self, code, tick_time, close, snapshot)
                finally:
                    self._risk_quantity_for_entry = None
        strategy = FixedModelGateStrategy(
            config=app, study_config=tech, family_name=family_name, family=family,
            candidate=candidate, data_role="historical_development", fold_id=fold["id"],
            is_backtest=True, is_simulation=True, local_only=True,
            candidate_schedule=candidate_schedule, regime_context=regime_context,
            prediction_lookup=model_runtime["prediction_lookup"], model_name=strategy_id,
        )

    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = eval_lower
    stats = bt.MarketDataStats(tick_count=full.height,
        first_timestamp=float(full["kbar_timestamp"][0]), last_timestamp=float(full["kbar_timestamp"][-1]))
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full, stats)
    trade_rows = bt._build_trade_rows(strategy, app)
    signal_rows = bt._build_signal_rows(strategy)
    pnl_rows = bt._build_pnl_rows(trade_rows)
    bars = bt.build_kbar_rows_from_frame(evaluation, app)
    curve = derive_equity_curve(bars, trade_rows, app.initial_capital)
    summary = summary_from_curve(curve, pnl_rows, app.initial_capital)
    if trade_rows:
        fees = sum(float(row["fee"]) for row in trade_rows)
        tax = sum(float(row["tax"]) for row in trade_rows)
    else:
        fees = tax = 0.0
    daily_returns = []
    by_day = {}
    for row in curve:
        by_day[row["timestamp"][:10]] = float(row["equity"])
    prior = float(app.initial_capital)
    for day in sorted(by_day):
        equity = by_day[day]
        daily_returns.append(equity / prior - 1 if prior else 0.0)
        prior = equity
    sharpe = None
    sharpe_reason = "FEWER_THAN_TWO_DAILY_RETURNS" if len(daily_returns) < 2 else None
    if len(daily_returns) >= 2:
        vol = float(np.std(daily_returns, ddof=0))
        sharpe = float(np.mean(daily_returns) / vol * np.sqrt(252)) if vol > 0 else None
        if vol == 0:
            sharpe_reason = "ZERO_DAILY_RETURN_VARIANCE"
    wins = [float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) > 0]
    losses = [float(row["net_pnl"]) for row in pnl_rows if float(row["net_pnl"]) < 0]
    completed = [float(row["net_pnl"]) for row in pnl_rows]
    max_loss_streak = streak = 0
    for value in completed:
        streak = streak + 1 if value < 0 else 0
        max_loss_streak = max(max_loss_streak, streak)
    from hybrid.core.multi_strategy_diagnostics import exposure_metrics, pnl_decomposition
    details = {
        "commission_total": fees, "transaction_tax_total": tax, "total_transaction_cost": fees + tax,
        "risk_contract": {"version": "risk_v1", "risk_per_trade": 0.01,
                          "max_position_fraction": 0.4, "stop_loss_pct": float(app.stop_loss_pct),
                          "sizing_reference": "completed_signal_bar_close", "gap_risk_note": "Next-open gaps may exceed estimated signal-price stop risk."},
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses else None,
        "profit_factor_reason": None if losses else "NO_LOSING_COMPLETED_TRADES",
        "sharpe_ratio": sharpe, "sharpe_annualization": "daily mark-to-market returns, population std, sqrt(252)",
        "sharpe_reason": sharpe_reason,
        "average_win": float(np.mean(wins)) if wins else None,
        "average_loss": float(abs(np.mean(losses))) if losses else None,
        "payoff_ratio": (float(np.mean(wins) / abs(np.mean(losses)))) if wins and losses else None,
        "average_trade_return_pct": float(np.mean([float(row["return_pct"]) for row in pnl_rows]) * 100) if pnl_rows else None,
        "maximum_consecutive_losses": max_loss_streak,
        **gate_audit_fields(strategy_id, len(candidate_schedule),
                            list(getattr(strategy, "hybrid_decisions", []))),
        "risk_decisions": list(getattr(strategy, "risk_decisions", [])),
        "gate_decisions": list(getattr(strategy, "hybrid_decisions", [])),
        "open_position_quantity_at_end": int(strategy.local_position),
        "slippage_rate": float(candidate_cfg["preserved_common_backtest"]["slippage_rate"]),
        "slippage_simulated": bool(candidate_cfg["preserved_common_backtest"]["slippage_rate"]),
        "performance_decomposition": pnl_decomposition(pnl_rows, app.initial_capital),
        "exposure": exposure_metrics(curve, pnl_rows, app.initial_capital),
    }
    return {
        "strategy_id": strategy_id, "strategy_version": "technical_v2_b+risk_v1" if strategy_id == "technical" else f"technical_v2_b+ml_v1_{strategy_id}+risk_v1",
        "stock_code": stock_code, "period": config["periods"]["backtest"], "data_role": "historical_development",
        "summary": summary, "details": details, "trades": trade_rows, "signals": signal_rows,
        "completed_trade_pnl": pnl_rows,
        "equity_curve": curve, "audit": {"candidate_id": candidate["id"], "variant": "TECH_V2_B",
            "entry_execution": "completed K-bar signal -> next tradable bar open",
            "prediction_source": "fixed newly trained model" if strategy_id != "technical" else None,
            "prediction_oof_claimed": False, "warmup_bars": int(warmup_count), "bars": len(bars),
            "candidate_schedule_size": len(candidate_schedule),
            "candidate_signal_hash": hashlib.sha256("\n".join(sorted(candidate_schedule)).encode("utf-8")).hexdigest()},
        "_diagnostic_bars": bars,
    }


def discover_v2b_candidates(*, connection, config: dict[str, Any], stock_code: str,
                            regime_context: dict[str, Any]) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Create raw and post-regime candidate ledgers plus the shared schedule."""
    import contextlib
    import io
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    import pandas as pd
    from trading_system import backtest as bt
    from trading_system import technical_v1 as tv1
    from trading_system.technical_v1 import TechnicalV1Strategy

    root = Path(__file__).resolve().parents[2]
    tech = json.loads((root / config["technical"]["config"]).read_text(encoding="utf-8"))
    candidate_cfg = json.loads((root / config["technical"]["candidate_config"]).read_text(encoding="utf-8"))
    family_name, family, candidate = tv1.candidate_by_id(tech, config["technical"]["candidate_id"])
    stock_code = str(stock_code).zfill(4)
    fold = {"id": "DEV2024H2", "train_start": "2023-01-01", "train_end": "2024-06-30",
            "evaluate_start": config["periods"]["backtest"]["start"],
            "evaluate_end": config["periods"]["backtest"]["end"]}
    full, _evaluation, app, _warmup, eval_lower = tv1._load_fold_frame(connection, tech, candidate, stock_code, fold)
    all_bar_decisions = {
        ((pd.Timestamp(value).to_pydatetime().replace(tzinfo=ZoneInfo("Asia/Taipei"))
          if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value).to_pydatetime().astimezone(ZoneInfo("Asia/Taipei")))
         + timedelta(minutes=5)).isoformat(timespec="microseconds")
        for value in full["kbar_time"].to_list()
    }

    class CandidateDiscoveryStrategy(TechnicalV1Strategy):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.candidate_schedule = all_bar_decisions
            self.regime_context = regime_context
            self.regime_audit_by_time: dict[str, dict[str, Any]] = {}
        def _try_entry(self, code, tick_time, close, snapshot):
            reasons = self._entry_reasons(close, snapshot)
            if not reasons:
                return
            aware = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=ZoneInfo("Asia/Taipei"))
            signal_time = aware.astimezone(ZoneInfo("Asia/Taipei"))
            if signal_time.isoformat(timespec="microseconds") not in self.candidate_schedule:
                return
            context = self.regime_context.get((str(code), signal_time.date().isoformat()))
            prior = [] if not context else context.get("last_20_proxy_closes", [])
            passed = bool(
                context and context.get("eligible") is True and len(prior) == 20
                and float(prior[-1]) > sum(float(x) for x in prior) / 20
            )
            self.regime_audit_by_time[signal_time.isoformat(timespec="microseconds")] = {
                "market_regime": "PRIOR_PEER_PROXY_ABOVE_SMA20" if passed else "PRIOR_PEER_PROXY_NOT_ABOVE_SMA20",
                "market_regime_pass": passed,
                "market_regime_reason": (
                    "PASS_PRIOR_PROXY_ABOVE_SMA20" if passed
                    else "BLOCK_MISSING_OR_NON_BULLISH_PRIOR_PROXY"
                ),
                "market_regime_proxy_close": float(prior[-1]) if len(prior) == 20 else None,
                "market_regime_proxy_sma20": (
                    sum(float(x) for x in prior) / 20 if len(prior) == 20 else None
                ),
            }
            self.record_signal(code=code, action="Buy", signal_type="entry", signal_time=tick_time,
                               price=close, reason=", ".join(reasons), indicators=snapshot)

    strategy = CandidateDiscoveryStrategy(
        config=app, study_config=tech, family_name=family_name, family=family,
        candidate=candidate, data_role="historical_development", fold_id=fold["id"],
        is_backtest=True, is_simulation=True, local_only=True,
    )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = eval_lower
    stats = bt.MarketDataStats(tick_count=full.height,
        first_timestamp=float(full["kbar_timestamp"][0]), last_timestamp=float(full["kbar_timestamp"][-1]))
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full, stats)
    raw_rows = []
    for source in strategy.candidate_signals:
        row = dict(source)
        row.update(strategy.regime_audit_by_time.get(str(row["earliest_entry_time"]), {
            "market_regime": "UNKNOWN", "market_regime_pass": False,
            "market_regime_reason": "BLOCK_MISSING_REGIME_AUDIT",
            "market_regime_proxy_close": None, "market_regime_proxy_sma20": None,
        }))
        raw_rows.append(row)
    rows = [row for row in raw_rows if row["market_regime_pass"]]
    schedule = {str(row["earliest_entry_time"]) for row in rows}
    if not schedule:
        raise DevelopmentBacktestBlocked("BLOCKED_NO_V2B_CANDIDATES_IN_SELECTED_PERIOD")
    if len(schedule) != len(rows):
        raise DevelopmentBacktestBlocked("BLOCKED_DUPLICATE_TECHNICAL_CANDIDATE_TIMES")
    return schedule, rows, raw_rows

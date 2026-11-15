"""Read-only integration checks for website datasets.

This module verifies data identity, date bounds, file schemas and shared model
coverage.  It never calculates strategy performance and the shared research
date guard remains authoritative.
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from hybrid.core.data_eligibility_v1 import audit_session
from trading_system.research_guard import assert_development_period, assert_timestamps


TZ = ZoneInfo("Asia/Taipei")
MODELS = ("random_forest", "xgboost", "gru")
BAR_COLUMNS = {
    "stock_code", "freq_minutes", "kbar_timestamp", "open", "high",
    "low", "close", "volume",
}
PREDICTION_FIELDS = {
    "stock_code", "model", "signal_date", "entry_date", "decision_time",
    "prob_sell", "prob_hold", "prob_buy", "prediction", "data_role",
}


def _file(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"Dataset path escapes project root: {relative}")
    if not path.is_file():
        raise ValueError(f"Dataset file is missing: {relative}")
    return path


def _valid_date(value: object) -> str:
    text = str(value)
    datetime.strptime(text, "%Y-%m-%d")
    return text


def _load_index(root: Path, spec: dict, start: str, end: str) -> tuple[list[dict], set[str]]:
    rows = json.loads(_file(root, spec["index"]).read_text(encoding="utf8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Index data must be a non-empty JSON array")
    dates: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or "date" not in row or "close" not in row:
            raise ValueError("Each index row requires date and close")
        day = _valid_date(row["date"])
        close = float(row["close"])
        if not math.isfinite(close) or close <= 0:
            raise ValueError(f"Invalid index close on {day}")
        assert_development_period(day, day)
        dates.append(day)
    if dates != sorted(set(dates)):
        raise ValueError("Index dates must be unique and strictly increasing")
    period_dates = {day for day in dates if start <= day <= end}
    if not period_dates:
        raise ValueError("Index has no dates inside configured coverage")
    return rows, period_dates


def _audit_market_bars(root: Path, spec: dict, stock: str, warmup: str,
                       start: str, end: str) -> set[str]:
    path = _file(root, spec["database"])
    lo = int(datetime.fromisoformat(warmup).replace(tzinfo=TZ).timestamp())
    hi = int((datetime.fromisoformat(end) + timedelta(days=1)).replace(tzinfo=TZ).timestamp())
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(market_kbars)")}
        if not BAR_COLUMNS.issubset(columns):
            missing = sorted(BAR_COLUMNS - columns)
            raise ValueError(f"market_kbars missing columns: {missing}")
        rows = [dict(row) for row in connection.execute(
            "SELECT stock_code,kbar_timestamp,open,high,low,close,volume "
            "FROM market_kbars WHERE stock_code=? AND freq_minutes=5 "
            "AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp",
            (stock, lo, hi),
        )]
    if not rows:
        raise ValueError("No prepared 5-minute bars for configured stock and dates")
    assert_timestamps(row["kbar_timestamp"] for row in rows)
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if str(row["stock_code"]) != stock:
            raise ValueError("Market row stock identity mismatch")
        stamp = datetime.fromtimestamp(float(row["kbar_timestamp"]), TZ)
        if "09:00" <= stamp.strftime("%H:%M") <= "13:25":
            by_day[stamp.date().isoformat()].append(row)
    complete = {
        day for day, day_rows in by_day.items()
        if start <= day <= end and audit_session(day_rows, day)["session_complete"]
    }
    if not complete:
        raise ValueError("No complete 54-bar session inside configured coverage")
    return complete


def _prediction_rows(root: Path, spec: dict, stock: str) -> dict[str, list[dict]]:
    if spec.get("prediction_kind") == "fixed_json":
        payload = json.loads(_file(root, spec["predictions"]).read_text(encoding="utf8"))
        if not isinstance(payload, dict):
            raise ValueError("Fixed predictions must be a JSON object keyed by model")
        return {model: payload.get(model) for model in MODELS}
    if spec.get("prediction_kind") == "oof_csv":
        with _file(root, spec["predictions"]).open(encoding="utf-8-sig", newline="") as handle:
            rows = [row for row in csv.DictReader(handle) if str(row.get("stock_code")) == stock]
        return {model: [row for row in rows if row.get("model") == model] for model in MODELS}
    raise ValueError("prediction_kind must be fixed_json or oof_csv")


def _valid_prediction_days(root: Path, spec: dict, stock: str, start: str,
                           end: str, calendar: set[str]) -> dict[str, set[str]]:
    grouped = _prediction_rows(root, spec, stock)
    result: dict[str, set[str]] = {}
    for model in MODELS:
        rows = grouped.get(model)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"Missing required model predictions: {model}")
        valid_days: set[str] = set()
        seen_days: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not PREDICTION_FIELDS.issubset(row):
                raise ValueError(f"Invalid prediction schema for {model}")
            signal_day = _valid_date(row["signal_date"])
            entry_day = _valid_date(row["entry_date"])
            assert_development_period(signal_day, signal_day)
            assert_development_period(entry_day, entry_day)
            if str(row["stock_code"]) != stock or row["model"] != model:
                raise ValueError(f"Prediction identity mismatch for {model}")
            if entry_day in seen_days:
                raise ValueError(f"Duplicate prediction entry date for {model}: {entry_day}")
            seen_days.add(entry_day)
            probabilities = [float(row[key]) for key in ("prob_sell", "prob_hold", "prob_buy")]
            expected = ("SELL", "HOLD", "BUY")[int(np.argmax(probabilities))]
            if (not all(math.isfinite(value) and 0 <= value <= 1 for value in probabilities)
                    or abs(sum(probabilities) - 1) >= 1e-5 or row["prediction"] != expected):
                raise ValueError(f"Invalid prediction probabilities or label for {model} on {entry_day}")
            available = datetime.fromisoformat(row["decision_time"])
            earliest_entry = datetime.fromisoformat(entry_day + "T09:10:00+08:00")
            if available.tzinfo is None or available >= earliest_entry:
                raise ValueError(f"Prediction is late or timezone-naive for {model} on {entry_day}")
            if str(row.get("is_oof", "")).lower() == "true":
                legal = (row["data_role"] == "training"
                         and row.get("label_version") == "ml_v1_fixed_1pct"
                         and row.get("fold_train_end", "9999-99-99") < signal_day
                         and row.get("fold_validation_start", "9999-99-99") <= signal_day
                         <= row.get("fold_validation_end", "0000-00-00"))
            else:
                legal = (row.get("prediction_source") == "fixed_2023_2024_new_stock_inference"
                         and row["data_role"] == "validation"
                         and row.get("training_target_max", "9999-99-99") <= signal_day
                         and row.get("training_target_max", "9999-99-99") <= "2024-12-31")
            if legal and start <= entry_day <= end and entry_day in calendar:
                valid_days.add(entry_day)
        if not valid_days:
            raise ValueError(f"No legal in-range predictions for {model}")
        result[model] = valid_days
    return result


def _audit_dividends(root: Path, spec: dict) -> int:
    events = spec.get("events", [])
    if spec.get("dividends"):
        payload = json.loads(_file(root, spec["dividends"]).read_text(encoding="utf8"))
        events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise ValueError("Dividend data must contain an events array")
    for event in events:
        if not isinstance(event, dict) or not {"ex_date", "payment_date", "cash_per_share"}.issubset(event):
            raise ValueError("Dividend event requires ex_date, payment_date and cash_per_share")
        ex_date = _valid_date(event["ex_date"])
        payment_date = _valid_date(event["payment_date"])
        amount = float(event["cash_per_share"])
        if payment_date < ex_date or not math.isfinite(amount) or amount < 0:
            raise ValueError(f"Invalid dividend event on {ex_date}")
    return len(events)


def preflight_dataset(root: Path, config: dict, stock: str) -> dict:
    stock = str(stock)
    if stock not in {str(code) for code in config.get("stock_universe", [])}:
        raise ValueError("Stock is not registered in stock_universe")
    spec = config.get("datasets", {}).get(stock)
    if not isinstance(spec, dict):
        raise ValueError("Stock is missing a datasets registration")
    start, end = _valid_date(spec.get("start")), _valid_date(spec.get("end"))
    assert_development_period(start, end)
    warmup = _valid_date(spec.get("warmup_start"))
    assert_development_period(warmup, end)
    _, index_days = _load_index(root, spec, start, end)
    complete_days = _audit_market_bars(root, spec, stock, warmup, start, end)
    prediction_days = _valid_prediction_days(root, spec, stock, start, end, index_days)
    common_days = set.intersection(complete_days, *(prediction_days[model] for model in MODELS))
    if not common_days:
        raise ValueError("No shared complete session with all three legal model predictions")
    dividends = _audit_dividends(root, spec)
    return {
        "stock_code": stock,
        "status": "QA_PASSED",
        "coverage": {"start": start, "end": end},
        "complete_sessions": len(complete_days),
        "common_eligible_sessions": len(common_days),
        "valid_prediction_days": {model: len(prediction_days[model]) for model in MODELS},
        "dividend_events": dividends,
        "performance_calculated": False,
        "locked_data_used": False,
    }


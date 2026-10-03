"""ML Trading V1 的資料檢查、日內序列與三日報酬標籤。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ml.data_pipeline import PreparedData, _rsi
from trading_system.research_guard import assert_development_period, assert_payload

TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass
class Coverage:
    complete: bool
    rows: list[dict[str, Any]]


def load_trading_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    assert_payload(plan)
    if plan.get("data_role") != "ml_development_seen":
        raise ValueError("ML Trading V1 只能使用已登記的開發資料。")
    for split in ("train", "validation", "test"):
        spec = plan["periods"][split]
        assert_development_period(spec["start"], spec["end"])
    if plan["holdout"] != {"start": "2025-07-01", "end": "2025-12-31", "access": "forbidden"}:
        raise ValueError("保留區間宣告遭到變更。")
    return plan


def coverage_report(database: Path, plan: dict[str, Any]) -> Coverage:
    rows: list[dict[str, Any]] = []
    with sqlite3.connect(database) as connection:
        for split in ("train", "validation", "test"):
            spec = plan["periods"][split]
            assert_development_period(spec["start"], spec["end"])
            for code in plan["stock_codes"]:
                item = connection.execute(
                    """SELECT COUNT(*), MIN(kbar_timestamp), MAX(kbar_timestamp)
                       FROM market_kbars
                       WHERE stock_code=? AND freq_minutes=? AND range_start=? AND range_end=?""",
                    (code, plan["bar_minutes"], spec["start"], spec["end"]),
                ).fetchone()
                count = int(item[0])
                rows.append({
                    "split": split, "stock_code": code,
                    "start": spec["start"], "end": spec["end"],
                    "kbar_rows": count,
                    "first_timestamp": item[1], "last_timestamp": item[2],
                    "available": count > 0,
                })
    return Coverage(all(row["available"] for row in rows), rows)


def _read_split(database: Path, plan: dict[str, Any], split: str) -> pd.DataFrame:
    spec = plan["periods"][split]
    assert_development_period(spec["start"], spec["end"])
    placeholders = ",".join("?" for _ in plan["stock_codes"])
    query = f"""SELECT stock_code, kbar_timestamp, open, high, low, close, volume
        FROM market_kbars WHERE stock_code IN ({placeholders}) AND freq_minutes=?
        AND range_start=? AND range_end=? ORDER BY stock_code, kbar_timestamp"""
    params = [*plan["stock_codes"], plan["bar_minutes"], spec["start"], spec["end"]]
    with sqlite3.connect(database) as connection:
        frame = pd.read_sql_query(query, connection, params=params)
    if frame.empty:
        raise RuntimeError(f"{split} 沒有 5 分 K 資料。")
    frame["datetime"] = pd.to_datetime(frame["kbar_timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Taipei")
    frame["trade_date"] = frame["datetime"].dt.date
    assert_development_period(frame["trade_date"].min(), frame["trade_date"].max())
    return frame


def _intraday_features(raw: pd.DataFrame, feature_names: list[str]) -> tuple[pd.DataFrame, dict[str, int]]:
    frame = raw.sort_values(["stock_code", "kbar_timestamp"]).copy()
    duplicated = frame.duplicated(["stock_code", "kbar_timestamp"], keep="first")
    duplicate_count = int(duplicated.sum())
    frame = frame.loc[~duplicated].copy()
    invalid = (
        (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (frame["volume"] < 0)
        | (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
    )
    invalid_count = int(invalid.sum())
    frame = frame.loc[~invalid].copy()
    pieces = []
    for _, g0 in frame.groupby(["stock_code", "trade_date"], sort=False):
        g = g0.copy().sort_values("kbar_timestamp")
        close = g["close"]
        previous = close.shift(1)
        ret1 = close.pct_change(fill_method=None)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        g["open_vs_prev_close"] = g["open"] / previous - 1
        g["high_vs_prev_close"] = g["high"] / previous - 1
        g["low_vs_prev_close"] = g["low"] / previous - 1
        g["return_1"] = ret1
        g["return_3"] = close.pct_change(3, fill_method=None)
        g["volume_ratio_20"] = g["volume"] / g["volume"].rolling(20, min_periods=20).mean().replace(0, np.nan)
        g["close_vs_ma5"] = close / close.rolling(5, min_periods=5).mean() - 1
        g["close_vs_ma20"] = close / close.rolling(20, min_periods=20).mean() - 1
        g["rsi14_scaled"] = _rsi(close) / 100
        g["macd_vs_close"] = macd / close
        g["macd_signal_vs_close"] = signal / close
        g["macd_hist_vs_close"] = (macd - signal) / close
        g["volatility_12"] = ret1.rolling(12, min_periods=12).std()
        g["trend_slope_12"] = (close / close.shift(12) - 1) / 12
        pieces.append(g)
    result = pd.concat(pieces, ignore_index=True)
    missing = sorted(set(feature_names) - set(result.columns))
    if missing:
        raise ValueError(f"缺少特徵欄位：{missing}")
    return result, {"duplicates_removed": duplicate_count, "invalid_ohlcv_removed": invalid_count}


def _daily_market(frame: pd.DataFrame) -> pd.DataFrame:
    return (frame.sort_values("kbar_timestamp")
        .groupby(["stock_code", "trade_date"], as_index=False)
        .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
             close=("close", "last"), volume=("volume", "sum"),
             first_timestamp=("kbar_timestamp", "first"), last_timestamp=("kbar_timestamp", "last")))


def _samples_for_split(featured: pd.DataFrame, plan: dict[str, Any]) -> tuple[list[np.ndarray], list[int], list[dict[str, Any]], pd.DataFrame, int]:
    seq_len = int(plan["sequence_bars"])
    holding = int(plan["target"]["holding_sessions"])
    threshold = float(plan["target"]["return_threshold"])
    names = plan["features"]
    daily = _daily_market(featured)
    samples: list[np.ndarray] = []
    labels: list[int] = []
    metadata: list[dict[str, Any]] = []
    rejected = 0
    for code, days in daily.groupby("stock_code", sort=False):
        days = days.sort_values("trade_date").reset_index(drop=True)
        bars_by_day = {day: g.sort_values("kbar_timestamp") for day, g in featured[featured["stock_code"] == code].groupby("trade_date")}
        for i in range(0, len(days) - holding):
            day = days.loc[i, "trade_date"]
            bars = bars_by_day[day]
            if len(bars) < seq_len:
                rejected += 1; continue
            window = bars[names].tail(seq_len).to_numpy(dtype=np.float64)
            if not np.isfinite(window).all():
                rejected += 1; continue
            entry = float(days.loc[i + 1, "open"])
            target_close = float(days.loc[i + holding, "close"])
            samples.append(window.astype(np.float32))
            labels.append(int(target_close / entry - 1 > threshold))
            metadata.append({
                "stock_code": str(code), "signal_date": str(day),
                "signal_time": datetime.fromtimestamp(int(days.loc[i, "last_timestamp"]), timezone.utc).astimezone(TAIPEI).isoformat(),
                "entry_date": str(days.loc[i + 1, "trade_date"]), "entry_open": entry,
                "target_date": str(days.loc[i + holding, "trade_date"]), "target_close": target_close,
            })
    return samples, labels, metadata, daily, rejected


def prepare_trading_data(database: Path, plan: dict[str, Any]) -> tuple[PreparedData, dict[str, pd.DataFrame]]:
    coverage = coverage_report(database, plan)
    if not coverage.complete:
        missing = [f"{r['split']}:{r['stock_code']}" for r in coverage.rows if not r["available"]]
        raise RuntimeError("資料不足：" + ", ".join(missing))
    X0: dict[str, np.ndarray] = {}
    y: dict[str, np.ndarray] = {}
    metadata: dict[str, pd.DataFrame] = {}
    markets: dict[str, pd.DataFrame] = {}
    cleaning: dict[str, Any] = {}
    rejected: dict[str, int] = {}
    for split in ("train", "validation", "test"):
        raw = _read_split(database, plan, split)
        featured, audit = _intraday_features(raw, plan["features"])
        samples, labels, meta, market, reject_count = _samples_for_split(featured, plan)
        if not samples:
            raise RuntimeError(f"{split} 無法建立有效樣本。")
        X0[split] = np.asarray(samples, dtype=np.float32)
        y[split] = np.asarray(labels, dtype=np.int64)
        metadata[split] = pd.DataFrame(meta)
        markets[split] = market
        cleaning[split] = audit
        rejected[split] = reject_count
    train_flat = X0["train"].reshape(-1, len(plan["features"])).astype(np.float64)
    mean = train_flat.mean(axis=0)
    scale = train_flat.std(axis=0)
    scale[scale == 0] = 1.0
    X = {key: ((value - mean) / scale).astype(np.float32) for key, value in X0.items()}
    audit = {
        "coverage": coverage.rows, "cleaning": cleaning, "sample_rejections": rejected,
        "samples": {key: int(len(value)) for key, value in X.items()},
        "positive_rate": {key: float(y[key].mean()) for key in y},
        "samples_by_stock": {key: metadata[key]["stock_code"].value_counts().sort_index().astype(int).to_dict() for key in metadata},
        "scaler_fit_on": "train_only",
    }
    return PreparedData(X, y, metadata, plan["features"], mean, scale, audit), markets

"""建立公平且無未來資料洩漏的時間序列資料集。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.data_roles import assert_ml_read_period
from trading_system.research_guard import assert_development_period, assert_payload


@dataclass
class PreparedData:
    X: dict[str, np.ndarray]
    y: dict[str, np.ndarray]
    metadata: dict[str, pd.DataFrame]
    feature_names: list[str]
    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    audit: dict[str, Any]
    context_X: dict[str, np.ndarray] | None = None
    context_feature_names: list[str] = field(default_factory=list)
    context_scaler_mean: np.ndarray | None = None
    context_scaler_scale: np.ndarray | None = None


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    assert_payload(plan)
    if plan.get("data_role") != "ml_development_seen":
        raise ValueError("ML baseline 只能使用已標示的開發資料。")
    if plan.get("data_policy") != "configs/ml_data_policy.json":
        raise ValueError("ML baseline 必須使用 configs/ml_data_policy.json。")
    assert_ml_read_period(plan["period"]["start"], plan["period"]["end"])
    assert_development_period(plan["period"]["start"], plan["period"]["end"])
    return plan


def load_kbars(database: Path, plan: dict[str, Any]) -> pd.DataFrame:
    """只查詢計畫登記的開發區間，查詢前後都套用日期鎖。"""
    start, end = plan["period"]["start"], plan["period"]["end"]
    assert_ml_read_period(start, end)
    assert_development_period(start, end)
    placeholders = ",".join("?" for _ in plan["stock_codes"])
    sql = f"""
        SELECT stock_code, kbar_timestamp, open, high, low, close, volume
        FROM market_kbars
        WHERE stock_code IN ({placeholders}) AND freq_minutes = ?
          AND range_start = ? AND range_end = ?
        ORDER BY stock_code, kbar_timestamp
    """
    params = [*plan["stock_codes"], plan["bar_minutes"], start, end]
    with sqlite3.connect(database) as connection:
        frame = pd.read_sql_query(sql, connection, params=params)
    if frame.empty:
        raise RuntimeError("market_kbars 找不到 ML 計畫指定的開發資料。")
    frame["datetime"] = pd.to_datetime(frame["kbar_timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Taipei")
    assert_development_period(frame["datetime"].min().date(), frame["datetime"].max().date())
    return frame


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def engineer_features(raw: pd.DataFrame, feature_names: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = raw.copy().sort_values(["stock_code", "kbar_timestamp"])
    before = len(frame)
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
    for _, group in frame.groupby("stock_code", sort=False):
        g = group.copy()
        close = g["close"]
        prev = close.shift(1)
        ret1 = close.pct_change(fill_method=None)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        g["open_vs_prev_close"] = g["open"] / prev - 1
        g["high_vs_prev_close"] = g["high"] / prev - 1
        g["low_vs_prev_close"] = g["low"] / prev - 1
        g["return_1"] = ret1
        g["return_3"] = close.pct_change(3, fill_method=None)
        g["volume_ratio_20"] = g["volume"] / g["volume"].rolling(20, min_periods=20).mean().replace(0, np.nan)
        for window in (5, 20, 60):
            g[f"close_vs_ma{window}"] = close / close.rolling(window, min_periods=window).mean() - 1
        g["rsi14_scaled"] = _rsi(close) / 100
        g["macd_vs_close"] = macd / close
        g["macd_signal_vs_close"] = signal / close
        g["macd_hist_vs_close"] = (macd - signal) / close
        g["volatility_12"] = ret1.rolling(12, min_periods=12).std()
        g["trend_slope_12"] = (close / close.shift(12) - 1) / 12
        pieces.append(g)
    result = pd.concat(pieces, ignore_index=True)
    audit = {"input_rows": before, "duplicates_removed": duplicate_count, "invalid_ohlcv_removed": invalid_count}
    missing_features = sorted(set(feature_names) - set(result.columns))
    if missing_features:
        raise ValueError(f"缺少特徵欄位：{missing_features}")
    return result, audit


def make_sequences(featured: pd.DataFrame, plan: dict[str, Any]) -> PreparedData:
    names = plan["features"]
    seq_len = int(plan["sequence_bars"])
    horizon = int(plan["target"]["horizon_bars"])
    expected_delta = int(plan["bar_minutes"]) * 60
    split_specs = plan["splits"]
    rows: dict[str, list[np.ndarray]] = {k: [] for k in ("train", "validation", "development")}
    labels: dict[str, list[int]] = {k: [] for k in rows}
    metas: dict[str, list[dict[str, Any]]] = {k: [] for k in rows}
    warmup_or_missing = 0
    gap_rejected = 0
    boundary_rejected = 0

    for (code, day), group in featured.groupby(["stock_code", featured["datetime"].dt.date], sort=False):
        g = group.sort_values("kbar_timestamp").reset_index(drop=True)
        values = g[names].to_numpy(dtype=np.float64)
        times = g["kbar_timestamp"].to_numpy(dtype=np.int64)
        closes = g["close"].to_numpy(dtype=np.float64)
        for i in range(seq_len - 1, len(g) - horizon):
            left, future = i - seq_len + 1, i + horizon
            window = values[left : i + 1]
            if not np.isfinite(window).all():
                warmup_or_missing += 1
                continue
            if not np.all(np.diff(times[left : future + 1]) == expected_delta):
                gap_rejected += 1
                continue
            target_date = g.loc[future, "datetime"].date()
            anchor_date = g.loc[i, "datetime"].date()
            split_name = None
            for candidate in ("train", "validation", "development"):
                spec = split_specs[candidate]
                if pd.Timestamp(spec["start"]).date() <= anchor_date <= pd.Timestamp(spec["end"]).date() and pd.Timestamp(spec["start"]).date() <= target_date <= pd.Timestamp(spec["end"]).date():
                    split_name = candidate
                    break
            if split_name is None:
                boundary_rejected += 1
                continue
            rows[split_name].append(window.astype(np.float32))
            labels[split_name].append(int(closes[future] > closes[i]))
            metas[split_name].append({"stock_code": str(code), "signal_time": g.loc[i, "datetime"].isoformat(), "target_time": g.loc[future, "datetime"].isoformat()})

    X = {k: np.asarray(v, dtype=np.float32) for k, v in rows.items()}
    y = {k: np.asarray(v, dtype=np.int64) for k, v in labels.items()}
    if any(len(X[k]) == 0 for k in X):
        raise RuntimeError(f"時間切分後有空資料集：{{k: len(v) for k, v in X.items()}}")
    train_flat = X["train"].reshape(-1, len(names)).astype(np.float64)
    mean = train_flat.mean(axis=0)
    scale = train_flat.std(axis=0)
    scale[scale == 0] = 1.0
    for key in X:
        X[key] = ((X[key] - mean) / scale).astype(np.float32)
    metadata = {k: pd.DataFrame(v) for k, v in metas.items()}
    audit = {
        "indicator_warmup_or_missing_rejected": warmup_or_missing,
        "gap_or_cross_session_rejected": gap_rejected,
        "split_boundary_rejected": boundary_rejected,
        "samples": {k: int(len(v)) for k, v in X.items()},
        "positive_rate": {k: float(y[k].mean()) for k in y},
        "samples_by_stock": {k: metadata[k]["stock_code"].value_counts().sort_index().astype(int).to_dict() for k in metadata},
    }
    return PreparedData(X, y, metadata, names, mean, scale, audit)


def prepare(database: Path, plan: dict[str, Any]) -> PreparedData:
    featured, cleaning = engineer_features(load_kbars(database, plan), plan["features"])
    prepared = make_sequences(featured, plan)
    prepared.audit["cleaning"] = cleaning
    return prepared


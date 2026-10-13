from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ml.ml_v1_labels import (
    WARMUP_ELIGIBLE,
    WARMUP_INSUFFICIENT,
    build_label_frame,
    eligible_common_samples,
)


def daily_frame(days: int = 32) -> pd.DataFrame:
    index = np.arange(days, dtype=float)
    close = 100.0 + index + np.sin(index) * 0.5
    return pd.DataFrame({
        "stock_code": "2303",
        "trade_date": pd.bdate_range("2023-01-02", periods=days).date,
        "open": close - 0.2,
        "close": close,
    })


def test_confirmatory_label_rejects_k_search_and_other_horizons():
    frame = daily_frame()
    with pytest.raises(ValueError, match="K=0.5"):
        build_label_frame(frame, k=0.75)
    with pytest.raises(ValueError, match="holding_sessions=3"):
        build_label_frame(frame, holding_sessions=2)


def test_sigma_at_t_does_not_use_future_close():
    original = daily_frame()
    changed = original.copy()
    changed.loc[changed.index > 22, "close"] *= 5.0
    left = build_label_frame(original)
    right = build_label_frame(changed)
    assert left.loc[22, "sigma_20d"] == pytest.approx(right.loc[22, "sigma_20d"])
    assert left.loc[22, "future_net_return"] != pytest.approx(right.loc[22, "future_net_return"])


def test_cost_aware_target_is_day_t1_open_to_day_t3_close_and_deducted_once():
    frame = daily_frame()
    labels = build_label_frame(frame)
    row = labels.iloc[20]
    expected = (
        frame.iloc[23]["close"] * (1.0 - 0.001425 - 0.003)
        / (frame.iloc[21]["open"] * (1.0 + 0.001425))
        - 1.0
    )
    assert row["entry_date"] == frame.iloc[21]["trade_date"]
    assert row["target_date"] == frame.iloc[23]["trade_date"]
    assert row["future_net_return"] == pytest.approx(expected)


def test_warmup_requires_twenty_valid_trailing_returns():
    labels = build_label_frame(daily_frame())
    assert (labels.iloc[:20]["warmup_status"] == WARMUP_INSUFFICIENT).all()
    assert labels.iloc[20]["warmup_status"] == WARMUP_ELIGIBLE
    assert math.isfinite(labels.iloc[20]["sigma_20d"])


def test_both_label_versions_use_same_eligible_sample_set():
    labels = build_label_frame(daily_frame())
    eligible = eligible_common_samples(labels)
    assert len(eligible) == len(labels) - 20
    assert eligible[["fixed_label", "volatility_label"]].notna().all().all()

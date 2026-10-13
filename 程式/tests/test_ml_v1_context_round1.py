from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ml.research_features import CONTEXT_CANDIDATE_FEATURES, build_context_candidate_features
from ml.research_universe import build_leave_one_out_proxy_daily_returns

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_ml_v1_context_round1", ROOT / "scripts/run_ml_v1_context_round1.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

TECHNOLOGY = ["2303", "2330", "2379", "2454", "3034"]
FINANCIAL = ["2881", "2882", "2884", "2886", "2891"]
GROUPS = {"technology": TECHNOLOGY, "financial": FINANCIAL}
CORE = TECHNOLOGY + FINANCIAL


def intraday_frame(periods: int = 35) -> pd.DataFrame:
    rows = []
    dates = pd.bdate_range("2023-01-02", periods=periods)
    for stock_index, code in enumerate(CORE):
        for day_index, day in enumerate(dates):
            close = 100.0 * (1.0 + 0.0005 * (stock_index + 1)) ** day_index
            rows.append({
                "stock_code": code,
                "trade_date": day.date(),
                "kbar_timestamp": int(day.timestamp()) + 13 * 3600 + 30 * 60,
                "open": close * 0.999,
                "high": close * 1.002,
                "low": close * 0.998,
                "close": close,
                "volume": 1000.0 + stock_index,
            })
    return pd.DataFrame(rows)


def one_day_returns() -> pd.DataFrame:
    return pd.DataFrame({
        "stock_code": CORE,
        "trade_date": [pd.Timestamp("2023-02-01").date()] * len(CORE),
        "stock_return_1d": np.arange(1, len(CORE) + 1, dtype=float) / 100,
    })


def test_candidate_has_exactly_the_fixed_nine_features():
    config = json.loads((ROOT / "configs/ml_v1_context_round1.json").read_text(encoding="utf-8"))
    assert config["candidate_features"] == CONTEXT_CANDIDATE_FEATURES
    assert len(CONTEXT_CANDIDATE_FEATURES) == 9
    assert len(set(CONTEXT_CANDIDATE_FEATURES)) == 9


def test_leave_one_out_proxies_exclude_target_stock():
    daily = one_day_returns()
    result = build_leave_one_out_proxy_daily_returns(daily, GROUPS, CORE)
    row = result.loc[result["stock_code"] == "2303"].iloc[0]
    expected_industry = daily.loc[daily["stock_code"].isin(TECHNOLOGY[1:]), "stock_return_1d"].mean()
    expected_market = daily.loc[daily["stock_code"] != "2303", "stock_return_1d"].mean()
    assert row["industry_proxy_return_1d"] == expected_industry
    assert row["market_proxy_return_1d"] == expected_market
    assert row["industry_proxy_component_count"] == 4
    assert row["market_proxy_component_count"] == 9

    changed = daily.copy()
    changed.loc[changed["stock_code"] == "2303", "stock_return_1d"] = 999.0
    changed_result = build_leave_one_out_proxy_daily_returns(changed, GROUPS, CORE)
    changed_row = changed_result.loc[changed_result["stock_code"] == "2303"].iloc[0]
    assert changed_row["industry_proxy_return_1d"] == row["industry_proxy_return_1d"]
    assert changed_row["market_proxy_return_1d"] == row["market_proxy_return_1d"]


def test_missing_required_proxy_component_is_not_filled():
    daily = one_day_returns()
    daily.loc[daily["stock_code"] == "2330", "stock_return_1d"] = np.nan
    result = build_leave_one_out_proxy_daily_returns(daily, GROUPS, CORE)
    target = result.loc[result["stock_code"] == "2303"].iloc[0]
    assert target["industry_proxy_component_count"] == 3
    assert target["market_proxy_component_count"] == 8
    assert np.isnan(target["industry_proxy_return_1d"])
    assert np.isnan(target["market_proxy_return_1d"])


def test_context_features_are_causal_to_future_price_changes():
    original = intraday_frame()
    cutoff = pd.Timestamp("2023-02-06").date()
    first = build_context_candidate_features(original, GROUPS, CORE)

    changed = original.copy()
    future = pd.to_datetime(changed["trade_date"]) > pd.Timestamp(cutoff)
    changed.loc[future, ["open", "high", "low", "close"]] *= 7.0
    second = build_context_candidate_features(changed, GROUPS, CORE)

    keys = ["stock_code", "trade_date"]
    left = first.loc[pd.to_datetime(first["trade_date"]) <= pd.Timestamp(cutoff), keys + CONTEXT_CANDIDATE_FEATURES]
    right = second.loc[pd.to_datetime(second["trade_date"]) <= pd.Timestamp(cutoff), keys + CONTEXT_CANDIDATE_FEATURES]
    pd.testing.assert_frame_equal(
        left.sort_values(keys).reset_index(drop=True),
        right.sort_values(keys).reset_index(drop=True),
        check_exact=True,
    )


def test_five_and_twenty_day_context_formulas_use_trailing_daily_returns():
    result = build_context_candidate_features(intraday_frame(), GROUPS, CORE)
    target = result.loc[result["stock_code"] == "2303"].sort_values("trade_date").iloc[-1]
    stock_daily = 0.0005
    industry_daily = np.mean([0.0010, 0.0015, 0.0020, 0.0025])
    market_daily = np.mean([0.0005 * value for value in range(2, 11)])
    assert np.isclose(target["stock_return_5d"], (1 + stock_daily) ** 5 - 1)
    assert np.isclose(target["stock_return_20d"], (1 + stock_daily) ** 20 - 1)
    assert np.isclose(target["industry_proxy_return_5d"], (1 + industry_daily) ** 5 - 1)
    assert np.isclose(target["industry_proxy_return_20d"], (1 + industry_daily) ** 20 - 1)
    assert np.isclose(target["market_proxy_return_5d"], (1 + market_daily) ** 5 - 1)
    assert np.isclose(target["market_proxy_return_20d"], (1 + market_daily) ** 20 - 1)
    assert np.isclose(
        target["stock_vs_industry_proxy_5d"],
        target["stock_return_5d"] - target["industry_proxy_return_5d"],
    )
    assert np.isclose(
        target["stock_vs_market_proxy_5d"],
        target["stock_return_5d"] - target["market_proxy_return_5d"],
    )


def test_sample_alignment_uses_identical_intersection_without_fill():
    manifest = pd.DataFrame({
        "stock_code": ["2303", "2303", "2303"],
        "decision_date": ["2023-02-01", "2023-02-02", "2023-02-03"],
        "sequence_status": ["SEQUENCE_COMPLETE"] * 3,
    })
    exclusions = pd.DataFrame({
        "stock_code": ["2303"],
        "decision_date": ["2023-02-03"],
    })
    candidate = pd.DataFrame({
        "stock_code": ["2303", "2303", "2303"],
        "trade_date": pd.to_datetime(["2023-02-01", "2023-02-02", "2023-02-03"]).date,
    })
    for feature in CONTEXT_CANDIDATE_FEATURES:
        candidate[feature] = [0.1, np.nan, 0.3]

    aligned, audit = MODULE.align_baseline_and_candidate(manifest, exclusions, candidate)
    assert list(aligned["decision_date"]) == ["2023-02-01"]
    assert aligned["baseline_eligible"].all()
    assert aligned["candidate_eligible"].all()
    assert audit["baseline_eligible_samples"] == audit["candidate_eligible_samples"] == 1
    assert audit["sample_keys_identical"] is True
    assert audit["candidate_context_exclusions_from_phase_b"] == 1


def test_config_and_runner_keep_training_and_locked_operations_disabled():
    config = MODULE.load_config()
    assert config["data"]["start"] == "2023-01-01"
    assert config["data"]["end"] == "2024-12-31"
    assert config["data"]["read_only"] is True
    assert all(config["forbidden"].values())
    source = (ROOT / "scripts/run_ml_v1_context_round1.py").read_text(encoding="utf-8")
    assert "run_multiclass_model" not in source
    assert "indices_for_industry_fold" not in source
    assert "indices_for_outer_fold" not in source
    assert "fit(" not in source

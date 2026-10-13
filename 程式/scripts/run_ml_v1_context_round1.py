"""Prepare ML V1 Improvement Round 1 context features without training models.

This script is intentionally limited to Training 2023-2024. It opens the raw
SQLite database read-only, builds the fixed nine-feature Context Candidate,
and aligns Baseline/Candidate to one identical stock-date intersection.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.research_features import CONTEXT_CANDIDATE_FEATURES, build_context_candidate_features

CONFIG_PATH = ROOT / "configs/ml_v1_context_round1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config["data"] != {
        "role": "training",
        "start": "2023-01-01",
        "end": "2024-12-31",
        "database": "data/market_data.sqlite3",
        "read_only": True,
    }:
        raise RuntimeError("Context Round 1 may use only read-only Training 2023-2024 data")
    if config["candidate_features"] != CONTEXT_CANDIDATE_FEATURES or len(CONTEXT_CANDIDATE_FEATURES) != 9:
        raise RuntimeError("Context Candidate must contain exactly the preregistered nine features")
    proxy = config["proxy_policy"]
    if proxy["rolling_centered"] or proxy["forward_fill"] or proxy["backward_fill"]:
        raise RuntimeError("Future-looking or filled proxy data is forbidden")
    if proxy["interpolation"] or proxy["synthetic_data"]:
        raise RuntimeError("Synthetic proxy data is forbidden")
    if not all(bool(value) for value in config["forbidden"].values()):
        raise RuntimeError("All Context Round 1 forbidden operations must remain locked")
    return config


def _core_universe(config: dict[str, Any]) -> list[str]:
    return [str(code) for group in config["stock_universe"].values() for code in group]


def load_training_bars_read_only(config: dict[str, Any]) -> tuple[pd.DataFrame, str, str]:
    database = ROOT / config["data"]["database"]
    before = sha256(database)
    stocks = _core_universe(config)
    placeholders = ",".join("?" for _ in stocks)
    query = f"""
        SELECT stock_code, kbar_timestamp, open, high, low, close, volume
        FROM market_kbars
        WHERE range_start = ? AND range_end = ?
          AND stock_code IN ({placeholders})
        ORDER BY stock_code, kbar_timestamp
    """
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        frame = pd.read_sql_query(
            query,
            connection,
            params=(config["data"]["start"], config["data"]["end"], *stocks),
        )
    finally:
        connection.close()
    after = sha256(database)
    if before != after:
        raise RuntimeError("Raw market database changed during Context Round 1 preparation")

    frame["stock_code"] = frame["stock_code"].astype(str)
    seconds = pd.to_numeric(frame["kbar_timestamp"], errors="raise")
    frame["datetime"] = (
        pd.to_datetime(seconds, unit="s", utc=True)
        .dt.tz_convert("Asia/Taipei")
        .dt.tz_localize(None)
    )
    frame["trade_date"] = frame["datetime"].dt.date
    start, end = pd.Timestamp(config["data"]["start"]), pd.Timestamp(config["data"]["end"])
    date_value = pd.to_datetime(frame["trade_date"])
    frame = frame.loc[
        date_value.between(start, end)
        & (frame["datetime"].dt.dayofweek < 5)
        & (frame["datetime"].dt.time <= time(13, 30))
    ].copy()
    if frame.empty or set(frame["stock_code"].unique()) != set(stocks):
        raise RuntimeError("Training bars do not cover the fixed ten-stock universe")
    if frame["datetime"].dt.time.max() > time(13, 30):
        raise RuntimeError("A Context feature attempted to use information after Day T 13:30")
    return frame, before, after


def align_baseline_and_candidate(
    manifest: pd.DataFrame,
    phase_b_exclusions: pd.DataFrame,
    candidate_daily: pd.DataFrame,
    feature_names: list[str] = CONTEXT_CANDIDATE_FEATURES,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return one shared Baseline/Candidate intersection and its audit."""
    if len(feature_names) != 9 or feature_names != CONTEXT_CANDIDATE_FEATURES:
        raise ValueError("Sample alignment accepts only the fixed nine Context features")
    manifest = manifest.copy()
    manifest["stock_code"] = manifest["stock_code"].astype(str)
    manifest["decision_date"] = manifest["decision_date"].astype(str)
    if manifest[["stock_code", "decision_date"]].duplicated().any():
        raise ValueError("Eligible manifest contains duplicate stock-date rows")

    exclusions = phase_b_exclusions.copy()
    if exclusions.empty:
        excluded_keys: set[tuple[str, str]] = set()
    else:
        exclusions["stock_code"] = exclusions["stock_code"].astype(str)
        exclusions["decision_date"] = exclusions["decision_date"].astype(str)
        excluded_keys = set(zip(exclusions["stock_code"], exclusions["decision_date"]))

    candidate = candidate_daily.copy()
    candidate["stock_code"] = candidate["stock_code"].astype(str)
    candidate["decision_date"] = pd.to_datetime(candidate["trade_date"]).dt.date.astype(str)
    if candidate[["stock_code", "decision_date"]].duplicated().any():
        raise ValueError("Candidate features contain duplicate stock-date rows")
    finite = np.isfinite(candidate[feature_names].to_numpy(dtype=float)).all(axis=1)
    candidate["candidate_context_complete"] = finite
    candidate_keys = set(zip(
        candidate.loc[finite, "stock_code"], candidate.loc[finite, "decision_date"]
    ))

    manifest_keys = set(zip(manifest["stock_code"], manifest["decision_date"]))
    baseline_keys = manifest_keys - excluded_keys
    common_keys = baseline_keys & candidate_keys
    selected = manifest.loc[
        [(code, date) in common_keys for code, date in zip(manifest["stock_code"], manifest["decision_date"])]
    ].copy()
    selected = selected.merge(
        candidate[["stock_code", "decision_date", *feature_names]],
        on=["stock_code", "decision_date"],
        how="left",
        validate="one_to_one",
    )
    selected["baseline_eligible"] = True
    selected["candidate_eligible"] = True
    selected = selected.sort_values(["decision_date", "stock_code"]).reset_index(drop=True)
    if not np.isfinite(selected[feature_names].to_numpy(dtype=float)).all():
        raise RuntimeError("Aligned Candidate contains a non-finite Context feature")
    baseline_aligned = set(zip(selected["stock_code"], selected["decision_date"]))
    candidate_aligned = set(zip(selected["stock_code"], selected["decision_date"]))
    if baseline_aligned != candidate_aligned:
        raise RuntimeError("Baseline and Candidate sample keys differ")

    by_stock = {
        str(code): int(count)
        for code, count in selected.groupby("stock_code", sort=True).size().items()
    }
    audit = {
        "sequence_label_eligible_samples": int(len(manifest)),
        "phase_b_model_ready_samples": int(len(baseline_keys)),
        "candidate_context_complete_within_manifest": int(len(manifest_keys & candidate_keys)),
        "candidate_context_exclusions_from_phase_b": int(len(baseline_keys - candidate_keys)),
        "final_common_eligible_samples": int(len(selected)),
        "baseline_eligible_samples": int(len(selected)),
        "candidate_eligible_samples": int(len(selected)),
        "sample_keys_identical": baseline_aligned == candidate_aligned,
        "by_stock": by_stock,
        "candidate_excluded_keys": [
            {"stock_code": code, "decision_date": date}
            for code, date in sorted(baseline_keys - candidate_keys)
        ],
    }
    return selected, audit


def main() -> int:
    config = load_config()
    raw, database_hash_before, database_hash_after = load_training_bars_read_only(config)
    core = _core_universe(config)
    candidate_daily = build_context_candidate_features(raw, config["stock_universe"], core)

    alignment = config["sample_alignment"]
    manifest = pd.read_csv(ROOT / alignment["sequence_manifest"], dtype={"stock_code": str})
    exclusions = pd.read_csv(ROOT / alignment["phase_b_nonfinite_exclusions"], dtype={"stock_code": str})
    selected, audit = align_baseline_and_candidate(manifest, exclusions, candidate_daily)
    if audit["phase_b_model_ready_samples"] != int(alignment["expected_phase_b_model_ready_samples"]):
        raise RuntimeError("Phase B model-ready sample count changed before Context comparison")

    output = ROOT / config["output"]["directory"]
    output.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output / config["output"]["eligible_samples"], index=False, encoding="utf-8-sig")
    report = {
        "experiment_id": config["experiment_id"],
        "status": "CONTEXT_CANDIDATE_DATA_READY_NOT_TRAINED",
        "context_features": CONTEXT_CANDIDATE_FEATURES,
        "context_feature_count": len(CONTEXT_CANDIDATE_FEATURES),
        "sample_alignment": audit,
        "leakage_guard": {
            "training_only_2023_2024": True,
            "day_t_cutoff": "13:30:00",
            "target_stock_excluded_from_proxies": True,
            "centered_rolling_used": False,
            "fill_used": False,
            "synthetic_data_used": False,
        },
        "execution": {
            "model_training_run": False,
            "walk_forward_run": False,
            "loso_run": False,
            "label_changed": False,
            "horizon_changed": False,
            "decision_time_changed": False,
            "model_parameters_changed": False,
        },
        "database_sha256_before": database_hash_before,
        "database_sha256_after": database_hash_after,
        "database_modified": database_hash_before != database_hash_after,
    }
    (output / config["output"]["audit"]).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "final_common_eligible_samples": audit["final_common_eligible_samples"],
        "baseline_eligible_samples": audit["baseline_eligible_samples"],
        "candidate_eligible_samples": audit["candidate_eligible_samples"],
        "sample_keys_identical": audit["sample_keys_identical"],
        "candidate_context_exclusions_from_phase_b": audit["candidate_context_exclusions_from_phase_b"],
        "by_stock": audit["by_stock"],
        "model_training_run": False,
        "walk_forward_run": False,
        "loso_run": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

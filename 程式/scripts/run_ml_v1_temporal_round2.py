"""ML V1 Temporal Round 2 preflight only; this file cannot train a model."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.temporal_round2 import assert_no_context_round1, build_three_session_intersection, load_config
from ml.trading_pipeline import _intraday_features
from scripts.audit_ml_v1_labels import load_training_kbars
from scripts.run_ml_v1_phase_b import load_phase_b_config


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _full_session_audit(raw: pd.DataFrame, expected_times: list[str]) -> dict[str, Any]:
    frame = raw.copy()
    frame["trade_date"] = frame["datetime"].dt.date.astype(str)
    frame["clock"] = frame["datetime"].dt.strftime("%H:%M:%S")
    expected = set(expected_times)
    rows = []
    for (code, day), group in frame.groupby(["stock_code", "trade_date"], sort=True):
        actual = set(group["clock"])
        rows.append({
            "stock_code": str(code), "decision_date": str(day),
            "raw_bar_count": int(len(group)),
            "complete_55": actual == expected and len(group) == len(expected),
            "missing_times": "|".join(sorted(expected - actual)),
        })
    audit = pd.DataFrame(rows)
    return {
        "stock_dates": int(len(audit)),
        "complete_55": int(audit["complete_55"].sum()),
        "incomplete": int((~audit["complete_55"]).sum()),
        "per_stock_complete": audit.groupby("stock_code")["complete_55"].sum().astype(int).to_dict(),
        "per_stock_incomplete": audit.groupby("stock_code")["complete_55"].apply(lambda x: int((~x).sum())).to_dict(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Training-only Temporal Round 2 data preflight; no model training.")
    parser.add_argument("--preflight", action="store_true", help="Required safety flag")
    parser.add_argument(
        "--verify-features", action="store_true",
        help="Also rebuild all Feature Set A values. This is still Training-only/no-model but may take several minutes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.preflight:
        raise SystemExit("Only --preflight is authorized; model training is intentionally unavailable.")
    config = load_config()
    phase, research, _ = load_phase_b_config()
    if research["data"]["start"] != config["data"]["start"] or research["data"]["end"] != config["data"]["end"]:
        raise RuntimeError("Training period mismatch")
    formal_universe = {
        industry: list(research["stock_universe"][industry])
        for industry in ("technology", "financial")
    }
    if formal_universe != config["stock_universe"]:
        raise RuntimeError("Stock universe mismatch")
    if research["prediction"]["decision_time"] != config["fixed_contract"]["decision_time"]:
        raise RuntimeError("Decision time mismatch")
    if research["prediction"]["prediction_horizon_sessions"] != config["fixed_contract"]["prediction_horizon_sessions"]:
        raise RuntimeError("Prediction horizon mismatch")
    core_features = list(research["feature_policy"]["core_features"])
    assert_no_context_round1(core_features)

    database = ROOT / config["data"]["database"]
    before = sha256(database)
    manifest_path = ROOT / phase["eligible_manifest"]
    baseline = pd.read_csv(manifest_path, dtype={"stock_code": str})
    baseline = baseline[["stock_code", "decision_date"]].copy()
    nonfinite_path = ROOT / "exports" / "ml_v1_phase_b_20261012" / "excluded_nonfinite_features.csv"
    nonfinite = pd.read_csv(nonfinite_path, dtype={"stock_code": str})
    baseline = baseline.merge(
        nonfinite[["stock_code", "decision_date"]].assign(_nonfinite=True),
        on=["stock_code", "decision_date"], how="left", validate="one_to_one",
    )
    baseline = baseline.loc[baseline["_nonfinite"].isna(), ["stock_code", "decision_date"]].copy()
    baseline_manifest = json.loads(
        (ROOT / "exports" / "ml_v1_phase_b_20261012" / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    baseline_input_audit = baseline_manifest["input_audit"]
    if len(baseline) != int(config["baseline"]["model_ready_samples"]):
        raise RuntimeError("Baseline model-ready sample count changed")
    raw = load_training_kbars(research)
    raw = raw.loc[raw["datetime"].dt.dayofweek < 5].copy()
    raw["trade_date"] = raw["datetime"].dt.date
    if args.verify_features:
        featured, cleaning = _intraday_features(raw, core_features)
        result = build_three_session_intersection(baseline, featured, core_features)
    else:
        # Fast structural estimate: exact timestamps and session alignment only.
        # Full feature finiteness remains a separate user-run verification.
        featured = raw[["stock_code", "trade_date", "datetime"]].copy()
        featured["_finite_marker"] = 1.0
        cleaning = {"duplicates_removed": None, "invalid_ohlcv_removed": None, "mode": "timestamp_only_estimate"}
        result = build_three_session_intersection(baseline, featured, ["_finite_marker"])
    after = sha256(database)
    if before != after:
        raise RuntimeError("market_data.sqlite3 changed during read-only Round 2 preflight")

    output = ROOT / config["output"]["directory"]
    output.mkdir(parents=True, exist_ok=True)
    common_path = output / config["output"]["common_samples"]
    excluded_path = output / config["output"]["exclusions"]
    result.common_samples.to_csv(common_path, index=False, encoding="utf-8-sig")
    result.exclusions.to_csv(excluded_path, index=False, encoding="utf-8-sig")
    report = {
        "experiment_id": config["experiment_id"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": (
            "PREFLIGHT_PASS_NOT_AUTHORIZED_FOR_TRAINING"
            if args.verify_features else "PREFLIGHT_ESTIMATE_REVIEW_REQUIRED"
        ),
        "baseline_input": {
            "samples": int(len(baseline)),
            "sequence_shape": [int(len(baseline)), 24, 24],
            "rf_xgb_flat_shape": [int(len(baseline)), 24 * 24],
            "gru_static_context_shape": [int(len(baseline)), 2],
            "feature_names": core_features,
            "gru_static_features": list(research["feature_policy"]["gru"]["auxiliary_context"]),
            "input_audit": baseline_input_audit,
        },
        "full_session": _full_session_audit(raw, config["session"]["raw_times"]),
        "round2": result.audit,
        "round2_formal_shape": {
            "sequence": [int(len(result.common_samples)), 72, 24],
            "rf_xgb_flat": [int(len(result.common_samples)), 1728],
            "gru_static_context": [int(len(result.common_samples)), 2],
        },
        "feature_level_verified": bool(args.verify_features),
        "cleaning": cleaning,
        "common_sample_files": {
            "common": str(common_path), "exclusions": str(excluded_path),
        },
        "representation": config["candidate"]["representation"],
        "research_guardrails": {
            "label_unchanged": True,
            "horizon_unchanged": True,
            "decision_time_unchanged": True,
            "universe_unchanged": True,
            "hyperparameters_unchanged": True,
            "context_round1_unused": True,
            "year_2025_used": False,
            "year_2026_used": False,
            "models_trained": False,
            "walk_forward_run": False,
            "loso_run": False,
            "database_modified": False,
            "database_sha256_before": before,
            "database_sha256_after": after,
        },
        "training_authorized": False,
        "remaining_before_training": (
            [] if args.verify_features else ["run the same preflight with --verify-features"]
        ),
    }
    report_path = output / config["output"]["report"]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "baseline_eligible": result.audit["baseline_eligible"],
        "round2_eligible": result.audit["round2_eligible"],
        "common_intersection": result.audit["common_intersection"],
        "excluded": result.audit["excluded"],
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


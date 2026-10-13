from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ============================================================
# Load Round 2 prepared runner
# ============================================================

RUNNER = (
    ROOT
    / "scripts"
    / "run_ml_v1_temporal_round2_walk_forward.py"
)

spec = importlib.util.spec_from_file_location(
    "round2_runner",
    RUNNER,
)

if spec is None or spec.loader is None:
    raise RuntimeError("找不到 Round 2 runner")

R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


from ml.data_pipeline import PreparedData
from ml.models import run_multiclass_model
from ml.walk_forward import (
    aggregate_fold_metrics,
    build_walk_forward_fold,
    load_walk_forward_protocol,
)


OUTPUT_DIR = (
    ROOT
    / "exports"
    / "ml_v1_temporal_round2_training_20261012"
)

PROGRESS_PATH = (
    OUTPUT_DIR
    / "rf_walk_forward_progress.jsonl"
)

RESULT_PATH = (
    OUTPUT_DIR
    / "rf_walk_forward_results.json"
)


INDUSTRIES = {
    "technology": [
        "2303", "2330", "2379", "2454", "3034"
    ],
    "financial": [
        "2881", "2882", "2884", "2886", "2891"
    ],
}


def make_raw_data(
    X,
    y,
    metadata,
    feature_names,
    note,
):

    return PreparedData(
        X={
            "train": X.astype(np.float32),
        },

        y={
            "train": y.astype(np.int64),
        },

        metadata={
            "train":
                metadata.reset_index(drop=True),
        },

        feature_names=list(feature_names),

        scaler_mean=np.zeros(
            len(feature_names),
            dtype=np.float64,
        ),

        scaler_scale=np.ones(
            len(feature_names),
            dtype=np.float64,
        ),

        audit={
            "loaded_roles": ["train"],
            "scaler_fit_on": None,
            "workflow_stage":
                "temporal_round2_rf_walk_forward_raw",
            "note": note,
        },

        context_X=None,
        context_feature_names=[],
        context_scaler_mean=None,
        context_scaler_scale=None,
    )


def add_direction_metrics(metrics):

    result = dict(metrics)

    cm = np.asarray(
        metrics["confusion_matrix"],
        dtype=float,
    )

    samples = float(metrics["samples"])

    buy_tp = cm[2, 2]
    buy_pred = cm[:, 2].sum()
    buy_actual = cm[2, :].sum()

    sell_tp = cm[0, 0]
    sell_pred = cm[:, 0].sum()
    sell_actual = cm[0, :].sum()

    def div(a, b):
        return float(a / b) if b else 0.0

    result["buy_precision"] = div(
        buy_tp,
        buy_pred,
    )

    result["buy_recall"] = div(
        buy_tp,
        buy_actual,
    )

    result["buy_coverage"] = div(
        buy_pred,
        samples,
    )

    result["sell_precision"] = div(
        sell_tp,
        sell_pred,
    )

    result["sell_recall"] = div(
        sell_tp,
        sell_actual,
    )

    result["sell_coverage"] = div(
        sell_pred,
        samples,
    )

    return result


def stats(values):

    values = np.asarray(
        [
            float(x)
            for x in values
            if x is not None
            and np.isfinite(x)
        ],
        dtype=float,
    )

    if not len(values):
        return {
            "mean": None,
            "standard_deviation": None,
            "worst": None,
        }

    return {
        "mean":
            float(values.mean()),

        "standard_deviation":
            float(values.std(ddof=1))
            if len(values) > 1
            else 0.0,

        "worst":
            float(values.min()),
    }


def summarize(folds):

    result = aggregate_fold_metrics(
        folds
    )

    for name in [
        "buy_precision",
        "buy_recall",
        "buy_coverage",
        "sell_precision",
        "sell_recall",
        "sell_coverage",
    ]:

        result[name] = stats(
            [row[name] for row in folds]
        )

    return result


def main():

    config = R.load_training_config()

    _, research, _ = (
        R.load_phase_b_config()
    )

    common = R.load_common_samples(
        config
    )

    feature_names = list(
        research["feature_policy"]
        ["core_features"]
    )

    raw = R.load_training_kbars(
        research
    )

    raw = raw.loc[
        raw["datetime"].dt.dayofweek < 5
    ].copy()

    raw["trade_date"] = (
        raw["datetime"].dt.date
    )

    featured, _ = (
        R._intraday_features(
            raw,
            feature_names,
        )
    )

    print("Building aligned 24 / 72 datasets...")

    X_baseline = (
        R.build_baseline_sequences(
            featured,
            common,
            feature_names,
        )
    )

    X_round2 = (
        R.build_round2_sequences(
            featured,
            common,
            feature_names,
        )
    )

    y, metadata = (
        R.build_labels_and_metadata(
            raw,
            common,
            research,
            config,
        )
    )

    protocol = (
        load_walk_forward_protocol(
            ROOT
            / "configs"
            / "ml_training_protocol_v3.json"
        )
    )

    folds = (
        protocol["walk_forward"]["folds"]
    )

    params = (
        research["models"]
        ["random_forest"]
    )

    all_results = {}

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    for industry, stocks in INDUSTRIES.items():

        mask = (
            metadata["stock_code"]
            .isin(stocks)
            .to_numpy()
        )

        local_y = y[mask]

        local_meta = (
            metadata.loc[mask]
            .reset_index(drop=True)
        )

        print()
        print("=" * 70)
        print(
            f"INDUSTRY: {industry}"
        )
        print(
            f"SAMPLES: {len(local_y)}"
        )
        print("=" * 70)

        variants = {
            "baseline_24":
                X_baseline[mask],

            "round2_72":
                X_round2[mask],
        }

        industry_result = {}

        for variant, X in variants.items():

            raw_data = make_raw_data(
                X,
                local_y,
                local_meta,
                feature_names,
                variant,
            )

            fold_rows = []

            print()
            print(f"[{variant}]")

            for fold_spec in folds:

                fold_number = int(
                    fold_spec["fold"]
                )

                print(
                    f"  Fold {fold_number}: training..."
                )

                fold = (
                    build_walk_forward_fold(
                        raw_data,
                        fold_spec,
                    )
                )

                _, metrics, _ = (
                    run_multiclass_model(
                        "random_forest",
                        fold,
                        params,
                        evaluation_splits=(
                            "validation",
                        ),
                    )
                )

                validation = (
                    add_direction_metrics(
                        metrics["splits"]
                        ["validation"]
                    )
                )

                row = {
                    "industry":
                        industry,

                    "variant":
                        variant,

                    "model":
                        "random_forest",

                    "fold":
                        fold_number,

                    "train_samples":
                        int(
                            len(
                                fold.y["train"]
                            )
                        ),

                    "validation_samples":
                        int(
                            len(
                                fold.y[
                                    "validation"
                                ]
                            )
                        ),

                    **validation,
                }

                fold_rows.append(row)

                with PROGRESS_PATH.open(
                    "a",
                    encoding="utf-8",
                ) as f:

                    f.write(
                        json.dumps(
                            row,
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

                print(
                    "    F1="
                    f"{validation['macro_f1']:.6f} "
                    "AUC="
                    f"{validation['macro_roc_auc_ovr']:.6f}"
                )

            industry_result[variant] = {
                "folds": fold_rows,
                "summary":
                    summarize(fold_rows),
            }

        b = (
            industry_result[
                "baseline_24"
            ]["summary"]
        )

        r = (
            industry_result[
                "round2_72"
            ]["summary"]
        )

        comparison = {
            "macro_f1_mean_delta":
                (
                    r["macro_f1"]["mean"]
                    - b["macro_f1"]["mean"]
                ),

            "macro_f1_worst_delta":
                (
                    r["macro_f1"]["worst"]
                    - b["macro_f1"]["worst"]
                ),

            "macro_auc_mean_delta":
                (
                    r[
                        "macro_roc_auc_ovr"
                    ]["mean"]
                    - b[
                        "macro_roc_auc_ovr"
                    ]["mean"]
                ),

            "macro_auc_worst_delta":
                (
                    r[
                        "macro_roc_auc_ovr"
                    ]["worst"]
                    - b[
                        "macro_roc_auc_ovr"
                    ]["worst"]
                ),
        }

        industry_result[
            "comparison"
        ] = comparison

        all_results[industry] = (
            industry_result
        )

    report = {
        "experiment":
            "ML V1 Temporal Round 2",

        "stage":
            "FORMAL_RF_WALK_FORWARD",

        "model":
            "random_forest",

        "candidate":
            "3 sessions x 24 bars = 72 timesteps",

        "common_samples":
            4447,

        "results":
            all_results,

        "guardrails": {
            "same_sample_keys": True,
            "same_labels": True,
            "same_model_params": True,
            "fold_local_scaler": True,
            "2025_used": False,
            "2026_used": False,
            "hyperparameter_search": False,
            "sequence_length_search": False,
        },
    }

    RESULT_PATH.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 70)
    print("RF WALK-FORWARD COMPLETE")
    print("=" * 70)

    for industry, result in (
        all_results.items()
    ):

        c = result["comparison"]

        print()
        print(industry)

        print(
            "  F1 mean delta : "
            f"{c['macro_f1_mean_delta']:+.6f}"
        )

        print(
            "  F1 worst delta: "
            f"{c['macro_f1_worst_delta']:+.6f}"
        )

        print(
            "  AUC mean delta: "
            f"{c['macro_auc_mean_delta']:+.6f}"
        )

        print(
            "  AUC worst delta: "
            f"{c['macro_auc_worst_delta']:+.6f}"
        )

    print()
    print(
        f"Result: {RESULT_PATH}"
    )

    print()
    print(
        "RF alone cannot determine "
        "Round 2 PASS/FAIL."
    )


if __name__ == "__main__":
    main()
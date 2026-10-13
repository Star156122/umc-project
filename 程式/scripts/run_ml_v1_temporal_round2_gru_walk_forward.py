from __future__ import annotations

import gc
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ============================================================
# Load Round 2 runner
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
    raise RuntimeError(
        "找不到 run_ml_v1_temporal_round2_walk_forward.py"
    )

R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


# ============================================================
# Project imports
# ============================================================

from ml.data_pipeline import PreparedData
from ml.models import run_multiclass_model
from ml.walk_forward import (
    aggregate_fold_metrics,
    build_walk_forward_fold,
    load_walk_forward_protocol,
)


# ============================================================
# Output
# ============================================================

OUTPUT_DIR = (
    ROOT
    / "exports"
    / "ml_v1_temporal_round2_training_20261012"
)

PROGRESS_PATH = (
    OUTPUT_DIR
    / "gru_walk_forward_progress.jsonl"
)

RESULT_PATH = (
    OUTPUT_DIR
    / "gru_walk_forward_results.json"
)


# ============================================================
# Fixed design
# ============================================================

INDUSTRIES = {
    "technology": [
        "2303",
        "2330",
        "2379",
        "2454",
        "3034",
    ],

    "financial": [
        "2881",
        "2882",
        "2884",
        "2886",
        "2891",
    ],
}


GRU_INNER_START = {
    1: "2023-07-20",
    2: "2023-10-26",
    3: "2024-01-22",
    4: "2024-05-09",
}


GRU_STATIC_FEATURES = [
    "volume_trend_5d",
    "volume_trend_20d",
]


EXPECTED_COMMON_SAMPLES = 4447


# ============================================================
# PreparedData
# ============================================================

def make_raw_data(
    X: np.ndarray,
    y: np.ndarray,
    metadata: pd.DataFrame,
    static_context: np.ndarray,
    feature_names: list[str],
    industry: str,
    variant: str,
) -> PreparedData:

    if len(X) != len(y):
        raise RuntimeError(
            f"{industry}/{variant} X/y 數量不一致"
        )

    if len(metadata) != len(y):
        raise RuntimeError(
            f"{industry}/{variant} metadata 數量不一致"
        )

    if len(static_context) != len(y):
        raise RuntimeError(
            f"{industry}/{variant} GRU context 數量不一致"
        )

    if not np.isfinite(X).all():
        raise RuntimeError(
            f"{industry}/{variant} X 含非有限值"
        )

    if not np.isfinite(static_context).all():
        raise RuntimeError(
            f"{industry}/{variant} static context 含非有限值"
        )

    return PreparedData(
        X={
            "train":
                X.astype(np.float32),
        },

        y={
            "train":
                y.astype(np.int64),
        },

        metadata={
            "train":
                metadata.reset_index(drop=True),
        },

        feature_names=list(
            feature_names
        ),

        scaler_mean=np.zeros(
            len(feature_names),
            dtype=np.float64,
        ),

        scaler_scale=np.ones(
            len(feature_names),
            dtype=np.float64,
        ),

        audit={
            "loaded_roles":
                ["train"],

            "scaler_fit_on":
                None,

            "workflow_stage":
                "temporal_round2_gru_walk_forward_raw",

            "industry":
                industry,

            "variant":
                variant,

            "training_only":
                True,

            "common_sample_alignment":
                True,

            "context_round1_used":
                False,
        },

        context_X={
            "train":
                static_context.astype(
                    np.float32
                ),
        },

        context_feature_names=list(
            GRU_STATIC_FEATURES
        ),

        context_scaler_mean=None,
        context_scaler_scale=None,
    )


# ============================================================
# Metrics
# ============================================================

def safe_div(
    a: float,
    b: float,
) -> float:

    return float(a / b) if b else 0.0


def add_direction_metrics(
    metrics: dict[str, Any],
) -> dict[str, Any]:

    result = dict(metrics)

    cm = np.asarray(
        metrics["confusion_matrix"],
        dtype=float,
    )

    samples = float(
        metrics["samples"]
    )

    # 0 = SELL
    # 1 = HOLD
    # 2 = BUY

    buy_tp = cm[2, 2]
    buy_pred = cm[:, 2].sum()
    buy_actual = cm[2, :].sum()

    sell_tp = cm[0, 0]
    sell_pred = cm[:, 0].sum()
    sell_actual = cm[0, :].sum()

    result["buy_precision"] = (
        safe_div(
            buy_tp,
            buy_pred,
        )
    )

    result["buy_recall"] = (
        safe_div(
            buy_tp,
            buy_actual,
        )
    )

    result["buy_coverage"] = (
        safe_div(
            buy_pred,
            samples,
        )
    )

    result["sell_precision"] = (
        safe_div(
            sell_tp,
            sell_pred,
        )
    )

    result["sell_recall"] = (
        safe_div(
            sell_tp,
            sell_actual,
        )
    )

    result["sell_coverage"] = (
        safe_div(
            sell_pred,
            samples,
        )
    )

    return result


def stats(
    values: list[float],
) -> dict[str, Any]:

    array = np.asarray(
        [
            float(value)
            for value in values
            if value is not None
            and np.isfinite(value)
        ],
        dtype=float,
    )

    if not len(array):
        return {
            "mean": None,
            "standard_deviation": None,
            "worst": None,
            "available_folds": 0,
        }

    return {
        "mean":
            float(array.mean()),

        "standard_deviation":
            (
                float(
                    array.std(ddof=1)
                )
                if len(array) > 1
                else 0.0
            ),

        "worst":
            float(array.min()),

        "available_folds":
            int(len(array)),
    }


def summarize(
    folds: list[dict[str, Any]],
) -> dict[str, Any]:

    result = (
        aggregate_fold_metrics(
            folds
        )
    )

    for name in [
        "buy_precision",
        "buy_recall",
        "buy_coverage",
        "sell_precision",
        "sell_recall",
        "sell_coverage",
    ]:

        result[name] = stats([
            row[name]
            for row in folds
        ])

    return result


# ============================================================
# GRU Inner Validation
# ============================================================

def select_gru_epochs(
    raw_data: PreparedData,
    outer_fold_spec: dict[str, Any],
    params: dict[str, Any],
) -> tuple[int, dict[str, Any]]:

    fold_number = int(
        outer_fold_spec["fold"]
    )

    if fold_number not in GRU_INNER_START:
        raise RuntimeError(
            f"沒有 Fold {fold_number} 的 Inner Validation"
        )

    inner_start = pd.Timestamp(
        GRU_INNER_START[
            fold_number
        ]
    )

    inner_train_end = (
        inner_start
        - pd.Timedelta(days=1)
    )

    inner_spec = {
        "fold":
            fold_number,

        "train_start":
            outer_fold_spec[
                "train_start"
            ],

        "train_end":
            inner_train_end.strftime(
                "%Y-%m-%d"
            ),

        "validation_start":
            inner_start.strftime(
                "%Y-%m-%d"
            ),

        "validation_end":
            outer_fold_spec[
                "train_end"
            ],
    }

    # --------------------------------------------------------
    # Critical leakage guard
    # Outer validation is NOT used here.
    # --------------------------------------------------------

    inner_fold = (
        build_walk_forward_fold(
            raw_data,
            inner_spec,
        )
    )

    _, metrics, _ = (
        run_multiclass_model(
            "gru",
            inner_fold,
            params,
            evaluation_splits=(
                "validation",
            ),
        )
    )

    selected_epochs = int(
        metrics["epochs_completed"]
    )

    max_epochs = int(
        params["max_epochs"]
    )

    if not (
        1
        <= selected_epochs
        <= max_epochs
    ):
        raise RuntimeError(
            "GRU selected_epochs 異常："
            f"{selected_epochs}"
        )

    audit = {
        "fold":
            fold_number,

        "selected_epochs":
            selected_epochs,

        "inner_train_start":
            inner_spec[
                "train_start"
            ],

        "inner_train_end":
            inner_spec[
                "train_end"
            ],

        "inner_validation_start":
            inner_spec[
                "validation_start"
            ],

        "inner_validation_end":
            inner_spec[
                "validation_end"
            ],

        "inner_best_validation_loss":
            metrics.get(
                "best_validation_loss"
            ),

        "outer_validation_used_for_epoch_selection":
            False,
    }

    return (
        selected_epochs,
        audit,
    )


# ============================================================
# Progress / Resume
# ============================================================

def load_progress_rows() -> list[dict[str, Any]]:

    if not PROGRESS_PATH.exists():
        return []

    rows = []

    for line in (
        PROGRESS_PATH
        .read_text(
            encoding="utf-8"
        )
        .splitlines()
    ):

        if line.strip():
            rows.append(
                json.loads(line)
            )

    return rows


def completed_keys() -> set[
    tuple[str, str, int]
]:

    result = set()

    for row in load_progress_rows():

        result.add((
            str(
                row["industry"]
            ),

            str(
                row["variant"]
            ),

            int(
                row["fold"]
            ),
        ))

    return result


def append_progress(
    row: dict[str, Any],
) -> None:

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with PROGRESS_PATH.open(
        "a",
        encoding="utf-8",
    ) as stream:

        stream.write(
            json.dumps(
                row,
                ensure_ascii=False,
            )
            + "\n"
        )


# ============================================================
# Main
# ============================================================

def main() -> int:

    # --------------------------------------------------------
    # Config
    # --------------------------------------------------------

    config = (
        R.load_training_config()
    )

    _, research, _ = (
        R.load_phase_b_config()
    )

    if (
        config["candidate"]
        ["timesteps"]
        != 72
    ):
        raise RuntimeError(
            "Round 2 timesteps 不是 72"
        )

    if (
        GRU_INNER_START[4]
        != "2024-05-09"
    ):
        raise RuntimeError(
            "GRU Fold 4 Inner Start "
            "必須是 2024-05-09"
        )

    common = (
        R.load_common_samples(
            config
        )
    )

    if (
        len(common)
        != EXPECTED_COMMON_SAMPLES
    ):
        raise RuntimeError(
            "Common samples 不是 4447"
        )

    feature_names = list(
        research[
            "feature_policy"
        ]["core_features"]
    )

    if len(feature_names) != 24:
        raise RuntimeError(
            "Core features 不是 24"
        )

    params = (
        research[
            "models"
        ]["gru"]
    )

    # --------------------------------------------------------
    # Data
    # --------------------------------------------------------

    print(
        "Building aligned "
        "24 / 72 GRU datasets..."
    )

    raw = (
        R.load_training_kbars(
            research
        )
    )

    raw = raw.loc[
        raw["datetime"]
        .dt.dayofweek
        < 5
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

    gru_static = (
        R.build_gru_static_context(
            featured,
            common,
        )
    )

    if not (
        len(X_baseline)
        == len(X_round2)
        == len(y)
        == len(metadata)
        == len(gru_static)
        == 4447
    ):
        raise RuntimeError(
            "Aligned dataset 數量不一致"
        )

    # --------------------------------------------------------
    # Walk-forward protocol
    # --------------------------------------------------------

    protocol = (
        load_walk_forward_protocol(
            ROOT
            / "configs"
            / "ml_training_protocol_v3.json"
        )
    )

    folds = (
        protocol[
            "walk_forward"
        ]["folds"]
    )

    if len(folds) != 4:
        raise RuntimeError(
            "Walk-forward 必須是 4 folds"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    completed = (
        completed_keys()
    )

    # --------------------------------------------------------
    # Loop
    # --------------------------------------------------------

    for industry, stocks in (
        INDUSTRIES.items()
    ):

        industry_mask = (
            metadata[
                "stock_code"
            ]
            .isin(stocks)
            .to_numpy()
        )

        local_y = (
            y[industry_mask]
        )

        local_metadata = (
            metadata
            .loc[industry_mask]
            .reset_index(drop=True)
        )

        local_static = (
            gru_static[
                industry_mask
            ]
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
                X_baseline[
                    industry_mask
                ],

            "round2_72":
                X_round2[
                    industry_mask
                ],
        }

        for variant, X in (
            variants.items()
        ):

            print()
            print(
                f"[{variant}]"
            )

            raw_data = (
                make_raw_data(
                    X=X,
                    y=local_y,
                    metadata=local_metadata,
                    static_context=
                        local_static,
                    feature_names=
                        feature_names,
                    industry=
                        industry,
                    variant=
                        variant,
                )
            )

            for fold_spec in folds:

                fold_number = int(
                    fold_spec["fold"]
                )

                key = (
                    industry,
                    variant,
                    fold_number,
                )

                if key in completed:

                    print(
                        f"  Fold {fold_number}: "
                        "已完成，跳過"
                    )

                    continue

                print(
                    f"  Fold {fold_number}: "
                    "Inner Validation..."
                )

                # --------------------------------------------
                # Select epochs using INNER validation only
                # --------------------------------------------

                (
                    fixed_epochs,
                    inner_audit,
                ) = (
                    select_gru_epochs(
                        raw_data,
                        fold_spec,
                        params,
                    )
                )

                print(
                    "    Selected Epochs: "
                    f"{fixed_epochs}"
                )

                print(
                    f"  Fold {fold_number}: "
                    "Outer Training..."
                )

                # --------------------------------------------
                # Build OUTER fold
                # --------------------------------------------

                outer_fold = (
                    build_walk_forward_fold(
                        raw_data,
                        fold_spec,
                    )
                )

                # --------------------------------------------
                # Critical:
                # train outer fold for fixed epochs,
                # do NOT use outer validation for ES.
                # --------------------------------------------

                _, metrics, _ = (
                    run_multiclass_model(
                        "gru",
                        outer_fold,
                        params,
                        fixed_epochs=
                            fixed_epochs,
                        evaluation_splits=(
                            "validation",
                        ),
                    )
                )

                validation = (
                    add_direction_metrics(
                        metrics[
                            "splits"
                        ]["validation"]
                    )
                )

                row = {
                    "experiment":
                        "temporal_round2",

                    "stage":
                        "FORMAL_GRU_WALK_FORWARD",

                    "industry":
                        industry,

                    "variant":
                        variant,

                    "model":
                        "gru",

                    "fold":
                        fold_number,

                    "timesteps":
                        (
                            24
                            if variant
                            == "baseline_24"
                            else 72
                        ),

                    "train_samples":
                        int(
                            len(
                                outer_fold
                                .y["train"]
                            )
                        ),

                    "validation_samples":
                        int(
                            len(
                                outer_fold
                                .y[
                                    "validation"
                                ]
                            )
                        ),

                    "selected_epochs":
                        fixed_epochs,

                    "gru_inner":
                        inner_audit,

                    "metrics":
                        validation,

                    "fold_audit":
                        outer_fold.audit,

                    "guardrails": {
                        "outer_validation_used_for_epoch_selection":
                            False,

                        "fold_local_scaler":
                            True,

                        "fold_local_context_scaler":
                            True,

                        "shuffle":
                            False,

                        "context_round1_used":
                            False,

                        "year_2025_used":
                            False,

                        "year_2026_used":
                            False,

                        "hyperparameter_search":
                            False,

                        "sequence_length_search":
                            False,
                    },
                }

                append_progress(
                    row
                )

                completed.add(
                    key
                )

                print(
                    "    F1="
                    f"{validation['macro_f1']:.6f} "
                    "AUC="
                    f"{validation['macro_roc_auc_ovr']:.6f}"
                )

                gc.collect()

    # ========================================================
    # Build final report from progress
    # ========================================================

    rows = (
        load_progress_rows()
    )

    # Deduplicate by key, keep latest
    latest = {}

    for row in rows:

        key = (
            row["industry"],
            row["variant"],
            int(row["fold"]),
        )

        latest[key] = row

    expected_keys = {
        (
            industry,
            variant,
            fold,
        )
        for industry
        in INDUSTRIES

        for variant
        in [
            "baseline_24",
            "round2_72",
        ]

        for fold
        in [1, 2, 3, 4]
    }

    missing = (
        expected_keys
        - set(latest)
    )

    if missing:
        raise RuntimeError(
            "GRU Walk-forward 尚未完整："
            f"{sorted(missing)}"
        )

    # 16 = 2 industries x 2 variants x 4 folds
    if len(expected_keys) != 16:
        raise RuntimeError(
            "Expected GRU runs 不是 16"
        )

    all_results = {}

    for industry in INDUSTRIES:

        industry_result = {}

        for variant in [
            "baseline_24",
            "round2_72",
        ]:

            variant_rows = []

            for fold_number in [
                1,
                2,
                3,
                4,
            ]:

                row = latest[
                    (
                        industry,
                        variant,
                        fold_number,
                    )
                ]

                metrics = dict(
                    row["metrics"]
                )

                metrics[
                    "fold"
                ] = fold_number

                metrics[
                    "selected_epochs"
                ] = row[
                    "selected_epochs"
                ]

                variant_rows.append(
                    metrics
                )

            industry_result[
                variant
            ] = {
                "folds":
                    variant_rows,

                "summary":
                    summarize(
                        variant_rows
                    ),
            }

        baseline_summary = (
            industry_result[
                "baseline_24"
            ]["summary"]
        )

        round2_summary = (
            industry_result[
                "round2_72"
            ]["summary"]
        )

        comparison = {
            "macro_f1_mean_delta":
                (
                    round2_summary[
                        "macro_f1"
                    ]["mean"]
                    -
                    baseline_summary[
                        "macro_f1"
                    ]["mean"]
                ),

            "macro_f1_worst_delta":
                (
                    round2_summary[
                        "macro_f1"
                    ]["worst"]
                    -
                    baseline_summary[
                        "macro_f1"
                    ]["worst"]
                ),

            "macro_f1_std_delta":
                (
                    round2_summary[
                        "macro_f1"
                    ]["standard_deviation"]
                    -
                    baseline_summary[
                        "macro_f1"
                    ]["standard_deviation"]
                ),

            "macro_auc_mean_delta":
                (
                    round2_summary[
                        "macro_roc_auc_ovr"
                    ]["mean"]
                    -
                    baseline_summary[
                        "macro_roc_auc_ovr"
                    ]["mean"]
                ),

            "macro_auc_worst_delta":
                (
                    round2_summary[
                        "macro_roc_auc_ovr"
                    ]["worst"]
                    -
                    baseline_summary[
                        "macro_roc_auc_ovr"
                    ]["worst"]
                ),

            "macro_auc_std_delta":
                (
                    round2_summary[
                        "macro_roc_auc_ovr"
                    ]["standard_deviation"]
                    -
                    baseline_summary[
                        "macro_roc_auc_ovr"
                    ]["standard_deviation"]
                ),

            "buy_precision_mean_delta":
                (
                    round2_summary[
                        "buy_precision"
                    ]["mean"]
                    -
                    baseline_summary[
                        "buy_precision"
                    ]["mean"]
                ),

            "buy_recall_mean_delta":
                (
                    round2_summary[
                        "buy_recall"
                    ]["mean"]
                    -
                    baseline_summary[
                        "buy_recall"
                    ]["mean"]
                ),

            "sell_precision_mean_delta":
                (
                    round2_summary[
                        "sell_precision"
                    ]["mean"]
                    -
                    baseline_summary[
                        "sell_precision"
                    ]["mean"]
                ),

            "sell_recall_mean_delta":
                (
                    round2_summary[
                        "sell_recall"
                    ]["mean"]
                    -
                    baseline_summary[
                        "sell_recall"
                    ]["mean"]
                ),
        }

        industry_result[
            "comparison"
        ] = comparison

        all_results[
            industry
        ] = industry_result

    report = {
        "experiment":
            "ML V1 Temporal Round 2",

        "stage":
            "FORMAL_GRU_WALK_FORWARD_COMPLETE",

        "model":
            "gru",

        "candidate":
            "T-2/T-1/T each exact 24 late-day bars = 72 timesteps",

        "common_samples":
            4447,

        "expected_runs":
            16,

        "completed_runs":
            16,

        "results":
            all_results,

        "guardrails": {
            "same_sample_keys":
                True,

            "same_labels":
                True,

            "same_model_params":
                True,

            "gru_static_context_same":
                True,

            "gru_static_features":
                list(
                    GRU_STATIC_FEATURES
                ),

            "inner_validation_only_for_epoch_selection":
                True,

            "fold4_inner_validation_start":
                "2024-05-09",

            "outer_validation_used_for_early_stopping":
                False,

            "fold_local_scaler":
                True,

            "fold_local_context_scaler":
                True,

            "2025_used":
                False,

            "2026_used":
                False,

            "hyperparameter_search":
                False,

            "probability_threshold_search":
                False,

            "sequence_length_search":
                False,

            "context_round1_used":
                False,
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

    # ========================================================
    # Console summary
    # ========================================================

    print()
    print("=" * 70)
    print(
        "GRU WALK-FORWARD COMPLETE"
    )
    print("=" * 70)

    for industry, result in (
        all_results.items()
    ):

        comparison = (
            result[
                "comparison"
            ]
        )

        print()
        print(industry)

        print(
            "  F1 mean delta : "
            f"{comparison['macro_f1_mean_delta']:+.6f}"
        )

        print(
            "  F1 worst delta: "
            f"{comparison['macro_f1_worst_delta']:+.6f}"
        )

        print(
            "  F1 std delta  : "
            f"{comparison['macro_f1_std_delta']:+.6f}"
        )

        print(
            "  AUC mean delta: "
            f"{comparison['macro_auc_mean_delta']:+.6f}"
        )

        print(
            "  AUC worst delta: "
            f"{comparison['macro_auc_worst_delta']:+.6f}"
        )

        print(
            "  AUC std delta : "
            f"{comparison['macro_auc_std_delta']:+.6f}"
        )

        print(
            "  BUY precision delta: "
            f"{comparison['buy_precision_mean_delta']:+.6f}"
        )

        print(
            "  BUY recall delta   : "
            f"{comparison['buy_recall_mean_delta']:+.6f}"
        )

        print(
            "  SELL precision delta: "
            f"{comparison['sell_precision_mean_delta']:+.6f}"
        )

        print(
            "  SELL recall delta   : "
            f"{comparison['sell_recall_mean_delta']:+.6f}"
        )

    print()
    print(
        f"Result: {RESULT_PATH}"
    )

    print()
    print(
        "GRU completed. "
        "Do not change parameters or sequence length "
        "based on these results."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
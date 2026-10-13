"""
ML V1 Improvement Round 1
Aligned Baseline Walk-forward

目的：
使用與 Context Candidate 完全相同的 4532 samples，
重新執行 Baseline Walk-forward，做公平比較。

Baseline：
- RF = Feature Set A only
- XGBoost = Feature Set A only
- GRU = Feature Set A
        + volume_trend_5d
        + volume_trend_20d

不使用 Round 1 的 9 個 Context Features。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np


# ============================================================
# Path
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


EXPERIMENT_SCRIPT = (
    ROOT
    / "scripts"
    / "run_ml_v1_context_round1_experiment.py"
)


spec = importlib.util.spec_from_file_location(
    "context_round1_experiment",
    EXPERIMENT_SCRIPT,
)

if spec is None or spec.loader is None:
    raise RuntimeError(
        "找不到 run_ml_v1_context_round1_experiment.py"
    )


BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)


# ============================================================
# 安全檢查
# ============================================================

if BASE.GRU_INNER_START.get(4) != "2024-05-09":
    raise RuntimeError(
        "GRU Fold 4 Inner Validation Start "
        "不是 2024-05-09。\n"
        "請先確認 run_ml_v1_context_round1_experiment.py "
        "已完成修正。"
    )


# ============================================================
# Separate Output
# ============================================================

BASE.PROGRESS_PATH = (
    BASE.OUTPUT_DIR
    / "baseline_aligned_walk_forward_progress.jsonl"
)

BASE.RESULT_JSON = (
    BASE.OUTPUT_DIR
    / "baseline_aligned_walk_forward_results.json"
)

BASE.RESULT_CSV = (
    BASE.OUTPUT_DIR
    / "baseline_aligned_walk_forward_results.csv"
)

BASE.DATASET_AUDIT = (
    BASE.OUTPUT_DIR
    / "baseline_aligned_dataset_audit.json"
)


GRU_BASELINE_CONTEXT = [
    "volume_trend_5d",
    "volume_trend_20d",
]


# ============================================================
# 保存原函式
# ============================================================

_original_prepare_candidate_dataset = (
    BASE.prepare_candidate_dataset
)

_original_make_raw_prepared_data = (
    BASE.make_raw_prepared_data
)

_original_append_progress = (
    BASE.append_progress
)

_original_build_final_report = (
    BASE.build_final_report
)


# ============================================================
# Same 4532 Samples, Baseline Features
# ============================================================

def prepare_aligned_baseline_dataset():

    result = list(
        _original_prepare_candidate_dataset()
    )

    eligible = result[3]
    model_context = result[7]
    audit = dict(result[8])

    if len(eligible) != 4532:
        raise RuntimeError(
            f"Baseline 必須使用 4532 samples，"
            f"目前={len(eligible)}"
        )

    baseline_context = {}

    # --------------------------------------------------------
    # RF / XGBoost
    # Feature Set A only
    # --------------------------------------------------------

    for model_name in [
        "random_forest",
        "xgboost",
    ]:

        baseline_context[
            model_name
        ] = (
            np.empty(
                (
                    len(eligible),
                    0,
                ),
                dtype=np.float32,
            ),
            [],
        )

    # --------------------------------------------------------
    # GRU
    # A + original volume2
    # --------------------------------------------------------

    gru_values, gru_names = (
        model_context["gru"]
    )

    gru_names = list(gru_names)

    missing = [
        name
        for name in GRU_BASELINE_CONTEXT
        if name not in gru_names
    ]

    if missing:
        raise RuntimeError(
            "GRU Baseline 找不到原始 Context："
            + ", ".join(missing)
        )

    indices = [
        gru_names.index(name)
        for name in GRU_BASELINE_CONTEXT
    ]

    baseline_context["gru"] = (
        np.asarray(
            gru_values[
                :,
                indices,
            ],
            dtype=np.float32,
        ),
        list(
            GRU_BASELINE_CONTEXT
        ),
    )

    audit.update({
        "status":
            "READY_FOR_ALIGNED_BASELINE_WALK_FORWARD",

        "comparison_variant":
            "baseline",

        "same_candidate_intersection":
            True,

        "samples":
            4532,

        "rf_context":
            [],

        "xgb_context":
            [],

        "gru_context":
            list(
                GRU_BASELINE_CONTEXT
            ),

        "round1_context_features_used":
            False,

        "gru_fold4_inner_validation_start":
            BASE.GRU_INNER_START[4],
    })

    result[7] = baseline_context
    result[8] = audit

    return tuple(result)


BASE.prepare_candidate_dataset = (
    prepare_aligned_baseline_dataset
)


# ============================================================
# Raw PreparedData
#
# RF / XGB 直接把 context_X 設成 None，
# 避免空的 0-column Context 經過 scaler。
# ============================================================

def make_baseline_raw_prepared_data(
    X,
    y,
    metadata,
    context_values,
    context_names,
    mask,
    industry,
    model_name,
):

    raw = _original_make_raw_prepared_data(
        X=X,
        y=y,
        metadata=metadata,
        context_values=context_values,
        context_names=context_names,
        mask=mask,
        industry=industry,
        model_name=model_name,
    )

    if model_name == "gru":
        return raw

    return BASE.PreparedData(
        X={
            "train":
                raw.X["train"]
                .astype(np.float32)
        },

        y={
            "train":
                raw.y["train"]
                .astype(np.int64)
        },

        metadata={
            "train":
                raw.metadata["train"]
                .reset_index(drop=True)
        },

        feature_names=list(
            raw.feature_names
        ),

        scaler_mean=np.asarray(
            raw.scaler_mean
        ),

        scaler_scale=np.asarray(
            raw.scaler_scale
        ),

        audit={
            **raw.audit,

            "comparison_variant":
                "baseline",

            "baseline_feature_policy":
                "Feature Set A only",

            "round1_context_features_used":
                False,
        },

        context_X=None,

        context_feature_names=[],

        context_scaler_mean=None,
        context_scaler_scale=None,
    )


BASE.make_raw_prepared_data = (
    make_baseline_raw_prepared_data
)


# ============================================================
# Progress Label
# ============================================================

def append_baseline_progress(item):

    item = dict(item)

    item[
        "experiment"
    ] = "aligned_baseline_walk_forward"

    item[
        "comparison_variant"
    ] = "baseline"

    _original_append_progress(
        item
    )


BASE.append_progress = (
    append_baseline_progress
)


# ============================================================
# Final Report
# ============================================================

def build_baseline_report(
    dataset_audit,
):

    _original_build_final_report(
        dataset_audit
    )

    if not BASE.RESULT_JSON.exists():
        return

    report = json.loads(
        BASE.RESULT_JSON.read_text(
            encoding="utf-8"
        )
    )

    report[
        "experiment"
    ] = (
        "ML V1 Improvement Round 1 "
        "Aligned Baseline Walk-forward"
    )

    report[
        "stage"
    ] = (
        "ALIGNED_BASELINE_WALK_FORWARD"
    )

    report[
        "comparison_variant"
    ] = (
        "baseline_aligned_to_"
        "context_round1_4532"
    )

    report[
        "baseline_feature_policy"
    ] = {
        "random_forest":
            "Feature Set A only",

        "xgboost":
            "Feature Set A only",

        "gru": (
            "Feature Set A + "
            "volume_trend_5d + "
            "volume_trend_20d"
        ),
    }

    report[
        "round1_context_features_used"
    ] = False

    report[
        "gru_fold4_inner_validation_start"
    ] = BASE.GRU_INNER_START[4]

    BASE.RESULT_JSON.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


BASE.build_final_report = (
    build_baseline_report
)


# ============================================================
# CLI
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--prepare-only",
        action="store_true",
    )

    parser.add_argument(
        "--industry",
        choices=[
            "technology",
            "financial",
        ],
        default=None,
    )

    parser.add_argument(
        "--model",
        choices=[
            "random_forest",
            "xgboost",
            "gru",
        ],
        default=None,
    )

    args = parser.parse_args()

    if args.prepare_only:

        (
            _config,
            _plan,
            _protocol,
            eligible,
            _X,
            _y,
            _metadata,
            model_context,
            audit,
        ) = (
            BASE.prepare_candidate_dataset()
        )

        print()
        print("=" * 72)
        print(
            "ALIGNED BASELINE "
            "WALK-FORWARD PREPARE：PASS"
        )
        print("=" * 72)

        print(
            f"Samples：{len(eligible)}"
        )

        print(
            "RF Context："
            f"{model_context['random_forest'][1]}"
        )

        print(
            "XGB Context："
            f"{model_context['xgboost'][1]}"
        )

        print(
            "GRU Context："
            f"{model_context['gru'][1]}"
        )

        print(
            "GRU Fold4 Inner Start："
            f"{BASE.GRU_INNER_START[4]}"
        )

        print()
        print(
            "沒有訓練任何模型。"
        )

        return 0

    BASE.run_candidate_walk_forward(
        industry_filter=
            args.industry,

        model_filter=
            args.model,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
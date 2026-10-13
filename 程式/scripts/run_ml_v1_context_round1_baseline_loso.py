"""
ML V1 Improvement Round 1
Aligned Baseline LOSO

目的：
使用與 Context Candidate 完全相同的 4532 samples，
重新執行 Baseline LOSO，作為公平 paired comparison。

Baseline：
- RF       = Feature Set A only
- XGBoost  = Feature Set A only
- GRU      = Feature Set A
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


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ============================================================
# 載入原本已經驗證可跑的 LOSO Runner
# ============================================================

LOSO_SCRIPT = (
    ROOT
    / "scripts"
    / "run_ml_v1_context_round1_loso.py"
)

spec = importlib.util.spec_from_file_location(
    "context_loso_runner",
    LOSO_SCRIPT,
)

if spec is None or spec.loader is None:
    raise RuntimeError(
        "找不到 run_ml_v1_context_round1_loso.py"
    )

BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)


# ============================================================
# Baseline 輸出位置
# 不覆蓋 Candidate 120/120
# ============================================================

BASE.PROGRESS_PATH = (
    BASE.OUTPUT_DIR
    / "baseline_aligned_loso_progress.jsonl"
)

BASE.RESULT_JSON = (
    BASE.OUTPUT_DIR
    / "baseline_aligned_loso_results.json"
)

BASE.RESULT_CSV = (
    BASE.OUTPUT_DIR
    / "baseline_aligned_loso_results.csv"
)

BASE.AUDIT_JSON = (
    BASE.OUTPUT_DIR
    / "baseline_aligned_loso_audit.json"
)


# ============================================================
# Baseline Feature Policy
# ============================================================

GRU_BASELINE_CONTEXT = [
    "volume_trend_5d",
    "volume_trend_20d",
]


# 保存原本函式
_original_prepare_candidate_dataset = (
    BASE.EXP.prepare_candidate_dataset
)


def prepare_aligned_baseline_dataset():
    """
    先使用 Context Round 1 已建立好的 4532 common intersection，
    然後只修改 Model Context。

    X / y / metadata / label / folds 都不改。
    """

    result = list(
        _original_prepare_candidate_dataset()
    )

    eligible = result[3]
    model_context = result[7]
    dataset_audit = dict(result[8])

    if len(eligible) != 4532:
        raise RuntimeError(
            "Baseline 必須使用與 Candidate "
            "相同的 4532 samples。"
        )

    baseline_context = {}

    for model_name, (
        context_values,
        context_names,
    ) in model_context.items():

        values = np.asarray(
            context_values,
            dtype=np.float32,
        )

        names = list(context_names)

        # ----------------------------------------
        # RF / XGB：
        # Baseline = A only
        # ----------------------------------------

        if model_name in {
            "random_forest",
            "xgboost",
        }:

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

        # ----------------------------------------
        # GRU：
        # Baseline =
        # A + 原本 volume2
        # ----------------------------------------

        elif model_name == "gru":

            missing = [
                name
                for name
                in GRU_BASELINE_CONTEXT
                if name not in names
            ]

            if missing:
                raise RuntimeError(
                    "GRU Baseline 找不到："
                    + ", ".join(missing)
                )

            indices = [
                names.index(name)
                for name
                in GRU_BASELINE_CONTEXT
            ]

            baseline_context[
                model_name
            ] = (
                values[
                    :,
                    indices,
                ].astype(
                    np.float32
                ),
                list(
                    GRU_BASELINE_CONTEXT
                ),
            )

        else:
            raise RuntimeError(
                f"未知 Model：{model_name}"
            )

    dataset_audit.update({
        "comparison_variant":
            "baseline",

        "same_candidate_intersection":
            True,

        "common_samples":
            4532,

        "rf_baseline":
            "Feature Set A only",

        "xgb_baseline":
            "Feature Set A only",

        "gru_baseline":
            (
                "Feature Set A + "
                "volume_trend_5d + "
                "volume_trend_20d"
            ),

        "round1_context_features_used":
            False,
    })

    result[7] = baseline_context
    result[8] = dataset_audit

    return tuple(result)


# 用我們的 Baseline Dataset 取代 Candidate Context
BASE.EXP.prepare_candidate_dataset = (
    prepare_aligned_baseline_dataset
)


# ============================================================
# 修改 Progress 標記
# ============================================================

_original_append_progress = (
    BASE.append_progress
)


def append_baseline_progress(item):

    item = dict(item)

    item[
        "experiment"
    ] = "aligned_baseline_loso"

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
# 修改 Prepare Audit
# ============================================================

_original_prepare_loso = (
    BASE.prepare_loso
)


def prepare_baseline_loso():

    result = list(
        _original_prepare_loso()
    )

    audit = dict(
        result[-1]
    )

    audit.update({
        "status":
            "READY_FOR_ALIGNED_BASELINE_LOSO",

        "comparison_variant":
            "baseline",

        "samples":
            4532,

        "same_candidate_intersection":
            True,

        "rf_xgb_context":
            [],

        "gru_context":
            list(
                GRU_BASELINE_CONTEXT
            ),

        "round1_context_features_used":
            False,
    })

    BASE.AUDIT_JSON.write_text(
        json.dumps(
            audit,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result[-1] = audit

    return tuple(result)


BASE.prepare_loso = (
    prepare_baseline_loso
)


# ============================================================
# 修改最終 Report 標記
# ============================================================

_original_build_report = (
    BASE.build_report
)


def build_baseline_report(
    dataset_audit,
    loso_audit,
):

    _original_build_report(
        dataset_audit,
        loso_audit,
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
        "Aligned Baseline LOSO"
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

    BASE.RESULT_JSON.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


BASE.build_report = (
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

    parser.add_argument(
        "--heldout",
        default=None,
    )

    args = parser.parse_args()

    if args.prepare_only:

        (
            _protocol,
            _X,
            _y,
            _metadata,
            model_context,
            _dataset_audit,
            audit,
        ) = BASE.prepare_loso()

        print()
        print("=" * 72)
        print(
            "ALIGNED BASELINE LOSO "
            "PREPARE：PASS"
        )
        print("=" * 72)

        print(
            f"Samples："
            f"{audit['samples']}"
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

        print()
        print(
            "沒有訓練任何模型。"
        )

        return 0

    BASE.run_loso(
        industry_filter=
            args.industry,

        model_filter=
            args.model,

        heldout_filter=
            args.heldout,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
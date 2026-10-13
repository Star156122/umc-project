from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 載入剛剛已經 PASS 的 Round 2 runner
RUNNER = ROOT / "scripts" / "run_ml_v1_temporal_round2_walk_forward.py"

spec = importlib.util.spec_from_file_location("round2_runner", RUNNER)
if spec is None or spec.loader is None:
    raise RuntimeError("找不到 Round 2 runner")

R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

from ml.data_pipeline import PreparedData
from ml.models import run_multiclass_model
from ml.walk_forward import (
    build_walk_forward_fold,
    load_walk_forward_protocol,
)


OUT = (
    ROOT
    / "exports"
    / "ml_v1_temporal_round2_training_20261012"
    / "smoke_rf_technology_fold1.json"
)


def make_raw_data(X, y, metadata, feature_names, note):

    return PreparedData(
        X={
            "train": X.astype(np.float32),
        },
        y={
            "train": y.astype(np.int64),
        },
        metadata={
            "train": metadata.reset_index(drop=True),
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
                "temporal_round2_smoke_raw",
            "note": note,
        },

        context_X=None,
        context_feature_names=[],
        context_scaler_mean=None,
        context_scaler_scale=None,
    )


def main():

    print("Preparing 4447 aligned samples...")

    config = R.load_training_config()

    phase, research, plan = (
        R.load_phase_b_config()
    )

    common = R.load_common_samples(
        config
    )

    feature_names = list(
        research["feature_policy"]
        ["core_features"]
    )

    # 只讀 2023~2024
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

    # Baseline 24
    X_baseline = (
        R.build_baseline_sequences(
            featured,
            common,
            feature_names,
        )
    )

    # Round 2 72
    X_round2 = (
        R.build_round2_sequences(
            featured,
            common,
            feature_names,
        )
    )

    # 共用 Label
    y, metadata = (
        R.build_labels_and_metadata(
            raw,
            common,
            research,
            config,
        )
    )

    technology = set(
        config["stock_universe"]
        ["technology"]
    )

    mask = (
        metadata["stock_code"]
        .isin(technology)
        .to_numpy()
    )

    tech_meta = (
        metadata.loc[mask]
        .reset_index(drop=True)
    )

    tech_y = y[mask]

    baseline_raw = make_raw_data(
        X_baseline[mask],
        tech_y,
        tech_meta,
        feature_names,
        "Baseline 24 timesteps",
    )

    round2_raw = make_raw_data(
        X_round2[mask],
        tech_y,
        tech_meta,
        feature_names,
        "Round2 72 timesteps",
    )

    protocol = (
        load_walk_forward_protocol(
            ROOT
            / "configs"
            / "ml_training_protocol_v3.json"
        )
    )

    fold1 = (
        protocol["walk_forward"]
        ["folds"][0]
    )

    print(
        f"Technology samples: "
        f"{len(tech_y)}"
    )

    print("Building Fold 1...")

    baseline_fold = (
        build_walk_forward_fold(
            baseline_raw,
            fold1,
        )
    )

    round2_fold = (
        build_walk_forward_fold(
            round2_raw,
            fold1,
        )
    )

    # 必須完全相同的 train / validation samples
    if (
        len(baseline_fold.y["train"])
        != len(round2_fold.y["train"])
    ):
        raise RuntimeError(
            "Train samples 不一致"
        )

    if (
        len(baseline_fold.y["validation"])
        != len(round2_fold.y["validation"])
    ):
        raise RuntimeError(
            "Validation samples 不一致"
        )

    if not np.array_equal(
        baseline_fold.y["train"],
        round2_fold.y["train"],
    ):
        raise RuntimeError(
            "Train labels 不一致"
        )

    if not np.array_equal(
        baseline_fold.y["validation"],
        round2_fold.y["validation"],
    ):
        raise RuntimeError(
            "Validation labels 不一致"
        )

    params = (
        research["models"]
        ["random_forest"]
    )

    print()
    print("Training Baseline RF...")

    _, baseline_metrics, _ = (
        run_multiclass_model(
            "random_forest",
            baseline_fold,
            params,
            evaluation_splits=(
                "validation",
            ),
        )
    )

    print("Training Round 2 RF...")

    _, round2_metrics, _ = (
        run_multiclass_model(
            "random_forest",
            round2_fold,
            params,
            evaluation_splits=(
                "validation",
            ),
        )
    )

    b = (
        baseline_metrics["splits"]
        ["validation"]
    )

    r = (
        round2_metrics["splits"]
        ["validation"]
    )

    report = {
        "status":
            "SMOKE_TEST_PASS_NOT_FORMAL_DECISION",

        "industry":
            "technology",

        "model":
            "random_forest",

        "fold":
            1,

        "train_samples":
            len(
                baseline_fold.y["train"]
            ),

        "validation_samples":
            len(
                baseline_fold.y[
                    "validation"
                ]
            ),

        "baseline": {
            "timesteps": 24,
            "macro_f1":
                b["macro_f1"],
            "macro_roc_auc_ovr":
                b["macro_roc_auc_ovr"],
        },

        "round2": {
            "timesteps": 72,
            "macro_f1":
                r["macro_f1"],
            "macro_roc_auc_ovr":
                r["macro_roc_auc_ovr"],
        },

        "guardrails": {
            "same_samples": True,
            "same_labels": True,
            "same_model_params": True,
            "fold_train_scaler_only": True,
            "2025_used": False,
            "2026_used": False,
            "formal_decision_allowed":
                False,
        },
    }

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUT.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 70)
    print("ROUND 2 RF SMOKE TEST: PASS")
    print("=" * 70)

    print(
        "Train samples:",
        report["train_samples"],
    )

    print(
        "Validation samples:",
        report["validation_samples"],
    )

    print()

    print(
        "Baseline Macro F1:",
        round(
            report["baseline"]
            ["macro_f1"],
            6,
        ),
    )

    print(
        "Baseline Macro AUC:",
        round(
            report["baseline"]
            ["macro_roc_auc_ovr"],
            6,
        ),
    )

    print()

    print(
        "Round2 Macro F1:",
        round(
            report["round2"]
            ["macro_f1"],
            6,
        ),
    )

    print(
        "Round2 Macro AUC:",
        round(
            report["round2"]
            ["macro_roc_auc_ovr"],
            6,
        ),
    )

    print()
    print(
        "Result:",
        OUT,
    )

    print()
    print(
        "This is a pipeline smoke test. "
        "Do not make a research decision "
        "from Fold 1 alone."
    )


if __name__ == "__main__":
    main()
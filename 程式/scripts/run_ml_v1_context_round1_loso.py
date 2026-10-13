"""
ML V1 Improvement Round 1
Context Candidate LOSO

設計：
- 只使用 2023~2024 Training
- Technology / Financial 分開
- 每個產業 5-stock Leave-One-Stock-Out
- 每個 Held-out Stock 跑 4 個 chronological folds
- RF / XGBoost / GRU
- Fixed ±1% Label
- 3-session horizon
- Exact 24 bars：11:35~13:30
- 不碰 2025 / 2026
- 不調 Hyperparameter
- 不調 Probability Threshold
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ============================================================
# 路徑
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXPERIMENT_SCRIPT = (
    ROOT
    / "scripts"
    / "run_ml_v1_context_round1_experiment.py"
)

OUTPUT_DIR = (
    ROOT
    / "exports"
    / "ml_v1_context_round1_loso_20261012"
)

PROGRESS_PATH = OUTPUT_DIR / "candidate_loso_progress.jsonl"
RESULT_JSON = OUTPUT_DIR / "candidate_loso_results.json"
RESULT_CSV = OUTPUT_DIR / "candidate_loso_results.csv"
AUDIT_JSON = OUTPUT_DIR / "candidate_loso_audit.json"


# ============================================================
# 載入剛剛已成功的 Context Experiment Runner
# ============================================================

spec = importlib.util.spec_from_file_location(
    "round1_experiment",
    EXPERIMENT_SCRIPT,
)

if spec is None or spec.loader is None:
    raise RuntimeError(
        "找不到 scripts/run_ml_v1_context_round1_experiment.py"
    )

EXP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(EXP)


from ml.data_pipeline import PreparedData
from ml.models import run_multiclass_model


TECHNOLOGY = list(EXP.TECHNOLOGY)
FINANCIAL = list(EXP.FINANCIAL)

INDUSTRIES = {
    "technology": TECHNOLOGY,
    "financial": FINANCIAL,
}

MODELS = list(EXP.MODELS)
MODEL_PARAMS = EXP.MODEL_PARAMS


# ============================================================
# 基本工具
# ============================================================

def check_walk_forward_completed() -> dict[str, Any]:

    path = EXP.RESULT_JSON

    if not path.exists():
        raise RuntimeError(
            "找不到 Candidate Walk-forward 結果。\n"
            "請先完成 24 / 24 Walk-forward。"
        )

    report = json.loads(
        path.read_text(encoding="utf-8")
    )

    completed = int(
        report.get("completed_runs", 0)
    )

    expected = int(
        report.get(
            "expected_full_walk_forward_runs",
            24,
        )
    )

    if completed != 24 or expected != 24:
        raise RuntimeError(
            f"Walk-forward 尚未完成：{completed} / {expected}"
        )

    return report


def subset_raw_data(
    data: PreparedData,
    mask: np.ndarray,
    note: str,
) -> PreparedData:

    context = None

    if data.context_X is not None:
        context = {
            "train":
                data.context_X["train"][mask]
                .astype(np.float32)
        }

    return PreparedData(
        X={
            "train":
                data.X["train"][mask]
                .astype(np.float32)
        },

        y={
            "train":
                data.y["train"][mask]
                .astype(np.int64)
        },

        metadata={
            "train":
                data.metadata["train"]
                .loc[mask]
                .reset_index(drop=True)
        },

        feature_names=list(
            data.feature_names
        ),

        scaler_mean=np.zeros(
            len(data.feature_names),
            dtype=np.float64,
        ),

        scaler_scale=np.ones(
            len(data.feature_names),
            dtype=np.float64,
        ),

        audit={
            "loaded_roles": ["train"],
            "scaler_fit_on": None,
            "workflow_stage":
                "context_round1_loso_raw",
            "note": note,
        },

        context_X=context,

        context_feature_names=list(
            data.context_feature_names
        ),

        context_scaler_mean=None,
        context_scaler_scale=None,
    )


# ============================================================
# 建立 LOSO Fold
# ============================================================

def build_loso_fold(
    raw_data: PreparedData,
    fold: dict[str, Any],
    heldout_stock: str,
) -> PreparedData:

    X = raw_data.X["train"]
    y = raw_data.y["train"]

    meta = (
        raw_data.metadata["train"]
        .reset_index(drop=True)
        .copy()
    )

    meta["stock_code"] = (
        meta["stock_code"]
        .astype(str)
        .str.zfill(4)
    )

    stocks = meta["stock_code"]

    signal_dates = pd.to_datetime(
        meta["signal_date"]
    )

    target_dates = pd.to_datetime(
        meta["target_date"]
    )

    train_start = pd.Timestamp(
        fold["train_start"]
    )

    train_end = pd.Timestamp(
        fold["train_end"]
    )

    validation_start = pd.Timestamp(
        fold["validation_start"]
    )

    validation_end = pd.Timestamp(
        fold["validation_end"]
    )

    # -----------------------------
    # Training：
    # 只能是同產業另外四支股票
    # -----------------------------

    peer_mask = (
        stocks != heldout_stock
    )

    train_signal_mask = (
        signal_dates.between(
            train_start,
            train_end,
        )
    )

    train_target_mask = (
        target_dates.between(
            train_start,
            train_end,
        )
    )

    train_mask = (
        peer_mask
        & train_signal_mask
        & train_target_mask
    )

    # -----------------------------
    # Validation：
    # 只能是 held-out 股票
    # -----------------------------

    heldout_mask = (
        stocks == heldout_stock
    )

    validation_signal_mask = (
        signal_dates.between(
            validation_start,
            validation_end,
        )
    )

    validation_target_mask = (
        target_dates.between(
            validation_start,
            validation_end,
        )
    )

    validation_mask = (
        heldout_mask
        & validation_signal_mask
        & validation_target_mask
    )

    train_indices = np.flatnonzero(
        train_mask.to_numpy()
    )

    validation_indices = np.flatnonzero(
        validation_mask.to_numpy()
    )

    if not len(train_indices):
        raise RuntimeError(
            f"Fold {fold['fold']} 沒有 Peer Training 樣本。"
        )

    if not len(validation_indices):
        raise RuntimeError(
            f"Fold {fold['fold']} 沒有 Held-out "
            f"{heldout_stock} 樣本。"
        )

    # ========================================================
    # 防洩漏檢查
    # ========================================================

    train_meta = (
        meta.iloc[train_indices]
        .reset_index(drop=True)
    )

    validation_meta = (
        meta.iloc[validation_indices]
        .reset_index(drop=True)
    )

    if (
        train_meta["stock_code"]
        == heldout_stock
    ).any():
        raise RuntimeError(
            "LOSO 洩漏：Held-out Stock 出現在 Training。"
        )

    if not (
        validation_meta["stock_code"]
        == heldout_stock
    ).all():
        raise RuntimeError(
            "LOSO Validation 含有非 Held-out 股票。"
        )

    peer_stocks = sorted(
        train_meta["stock_code"]
        .unique()
        .tolist()
    )

    if len(peer_stocks) != 4:
        raise RuntimeError(
            f"LOSO 應該有 4 個 Peer Stocks，"
            f"實際={peer_stocks}"
        )

    # ========================================================
    # Intraday Feature Scaler
    # 只能 fit Peer Training
    # ========================================================

    feature_count = X.shape[2]

    fit_values = (
        X[train_indices]
        .reshape(-1, feature_count)
        .astype(np.float64)
    )

    mean = fit_values.mean(axis=0)
    scale = fit_values.std(axis=0)

    scale[scale == 0] = 1.0

    X_train = (
        (X[train_indices] - mean)
        / scale
    ).astype(np.float32)

    X_validation = (
        (X[validation_indices] - mean)
        / scale
    ).astype(np.float32)

    # ========================================================
    # Context Scaler
    # 也只能 fit Peer Training
    # ========================================================

    context_X = None
    context_mean = None
    context_scale = None

    if raw_data.context_X is not None:

        source_context = (
            raw_data.context_X["train"]
        )

        context_fit = (
            source_context[train_indices]
            .astype(np.float64)
        )

        context_mean = (
            context_fit.mean(axis=0)
        )

        context_scale = (
            context_fit.std(axis=0)
        )

        context_scale[
            context_scale == 0
        ] = 1.0

        context_X = {
            "train": (
                (
                    source_context[
                        train_indices
                    ]
                    - context_mean
                )
                / context_scale
            ).astype(np.float32),

            "validation": (
                (
                    source_context[
                        validation_indices
                    ]
                    - context_mean
                )
                / context_scale
            ).astype(np.float32),
        }

    y_train = y[train_indices]
    y_validation = y[validation_indices]

    audit = {
        "workflow_stage":
            "context_round1_loso_fold",

        "fold":
            int(fold["fold"]),

        "heldout_stock":
            heldout_stock,

        "peer_stocks":
            peer_stocks,

        "periods":
            dict(fold),

        "scaler_fit_on":
            "peer_training_only",

        "context_scaler_fit_on":
            (
                "peer_training_only"
                if context_X is not None
                else None
            ),

        "class_weight_source":
            "peer_training_only",

        "heldout_stock_in_training":
            False,

        "train_samples":
            int(len(train_indices)),

        "validation_samples":
            int(len(validation_indices)),

        "shuffle":
            False,
    }

    return PreparedData(
        X={
            "train":
                X_train,

            "validation":
                X_validation,
        },

        y={
            "train":
                y_train,

            "validation":
                y_validation,
        },

        metadata={
            "train":
                train_meta,

            "validation":
                validation_meta,
        },

        feature_names=list(
            raw_data.feature_names
        ),

        scaler_mean=mean,
        scaler_scale=scale,

        audit=audit,

        context_X=context_X,

        context_feature_names=list(
            raw_data.context_feature_names
        ),

        context_scaler_mean=context_mean,
        context_scaler_scale=context_scale,
    )


# ============================================================
# GRU Inner Chronological Validation
# ============================================================

def select_gru_epochs_peer_only(
    raw_data: PreparedData,
    fold: dict[str, Any],
    heldout_stock: str,
) -> tuple[int, dict[str, Any]]:

    meta = raw_data.metadata[
        "train"
    ]

    peer_mask = (
        meta["stock_code"]
        .astype(str)
        .str.zfill(4)
        != heldout_stock
    ).to_numpy()

    peer_raw = subset_raw_data(
        raw_data,
        peer_mask,
        note=(
            f"GRU inner validation "
            f"excluding heldout={heldout_stock}"
        ),
    )

    # 沿用 Context Walk-forward 已使用的
    # chronological inner validation 規則
    selected_epochs, inner_audit = (
        EXP.select_gru_epochs(
            peer_raw,
            fold,
        )
    )

    # 再確認 held-out 完全不在 inner data
    peer_codes = set(
        peer_raw.metadata["train"][
            "stock_code"
        ]
        .astype(str)
        .str.zfill(4)
    )

    if heldout_stock in peer_codes:
        raise RuntimeError(
            "GRU Early Stopping 洩漏 Held-out Stock。"
        )

    inner_audit = {
        **inner_audit,
        "heldout_stock":
            heldout_stock,
        "heldout_used":
            False,
        "source":
            "peer-stock inner chronological validation only",
    }

    return (
        selected_epochs,
        inner_audit,
    )


# ============================================================
# Progress Resume
# ============================================================

def load_completed() -> set[
    tuple[str, str, str, int]
]:

    completed = set()

    if not PROGRESS_PATH.exists():
        return completed

    for line in PROGRESS_PATH.read_text(
        encoding="utf-8"
    ).splitlines():

        if not line.strip():
            continue

        item = json.loads(line)

        completed.add((
            str(item["industry"]),
            str(item["heldout_stock"]),
            str(item["model"]),
            int(item["fold"]),
        ))

    return completed


def append_progress(
    item: dict[str, Any],
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
                item,
                ensure_ascii=False,
            )
            + "\n"
        )


def load_progress_rows() -> list[
    dict[str, Any]
]:

    if not PROGRESS_PATH.exists():
        return []

    rows = []

    for line in PROGRESS_PATH.read_text(
        encoding="utf-8"
    ).splitlines():

        if line.strip():
            rows.append(
                json.loads(line)
            )

    return rows


# ============================================================
# 準備資料
# ============================================================

def prepare_loso():

    # 必須先確認 Walk-forward 已跑完
    wf_report = (
        check_walk_forward_completed()
    )

    (
        config,
        plan,
        protocol,
        eligible,
        X,
        y,
        metadata,
        model_context,
        dataset_audit,
    ) = EXP.prepare_candidate_dataset()

    if len(eligible) != 4532:
        raise RuntimeError(
            f"LOSO 必須沿用 4532 samples，"
            f"目前={len(eligible)}"
        )

    if dataset_audit.get(
        "database_modified"
    ):
        raise RuntimeError(
            "Raw Database 已被修改。"
        )

    audit = {
        "status":
            "READY_FOR_CONTEXT_CANDIDATE_LOSO",

        "walk_forward_completed":
            int(
                wf_report[
                    "completed_runs"
                ]
            ),

        "walk_forward_required":
            24,

        "samples":
            int(len(eligible)),

        "training_period": [
            "2023-01-01",
            "2024-12-31",
        ],

        "method":
            "5-stock LOSO x 4-fold expanding-window",

        "industries":
            INDUSTRIES,

        "models":
            MODELS,

        "candidate_context_features":
            list(
                EXP.CONTEXT_CANDIDATE_FEATURES
            ),

        "heldout_stock_never_training_sample":
            True,

        "heldout_stock_never_gru_early_stopping_sample":
            True,

        "scaler_fit_on_peer_training_only":
            True,

        "context_scaler_fit_on_peer_training_only":
            True,

        "controlled_validation_used":
            False,

        "development_used":
            False,

        "holdout_period_used":
            False,

        "final_oos_used":
            False,

        "hyperparameter_search":
            False,

        "probability_threshold_search":
            False,

        "trading_return_selection":
            False,
    }

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    AUDIT_JSON.write_text(
        json.dumps(
            audit,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return (
        protocol,
        X,
        y,
        metadata,
        model_context,
        dataset_audit,
        audit,
    )


# ============================================================
# LOSO
# ============================================================

def run_loso(
    industry_filter: str | None = None,
    model_filter: str | None = None,
    heldout_filter: str | None = None,
) -> None:

    (
        protocol,
        X,
        y,
        metadata,
        model_context,
        dataset_audit,
        loso_audit,
    ) = prepare_loso()

    folds = (
        protocol[
            "walk_forward"
        ]["folds"]
    )

    completed = load_completed()

    for industry, stocks in (
        INDUSTRIES.items()
    ):

        if (
            industry_filter
            and industry
            != industry_filter
        ):
            continue

        industry_mask = (
            metadata["stock_code"]
            .astype(str)
            .str.zfill(4)
            .isin(stocks)
            .to_numpy()
        )

        print()
        print("=" * 72)
        print(
            f"Industry：{industry}"
        )
        print("=" * 72)

        for heldout_stock in stocks:

            if (
                heldout_filter
                and heldout_stock
                != heldout_filter
            ):
                continue

            print()
            print(
                f"Held-out Stock："
                f"{heldout_stock}"
            )

            peer_stocks = [
                code
                for code in stocks
                if code
                != heldout_stock
            ]

            print(
                "Peer Stocks："
                + ", ".join(
                    peer_stocks
                )
            )

            for model_name in MODELS:

                if (
                    model_filter
                    and model_name
                    != model_filter
                ):
                    continue

                (
                    context_values,
                    context_names,
                ) = model_context[
                    model_name
                ]

                # 整個產業的 Raw Data
                raw_data = (
                    EXP.make_raw_prepared_data(
                        X=X,
                        y=y,
                        metadata=metadata,
                        context_values=
                            context_values,
                        context_names=
                            context_names,
                        mask=
                            industry_mask,
                        industry=
                            industry,
                        model_name=
                            model_name,
                    )
                )

                print(
                    f"  Model：{model_name} "
                    f"Context={len(context_names)}"
                )

                for fold_spec in folds:

                    fold_number = int(
                        fold_spec["fold"]
                    )

                    key = (
                        industry,
                        heldout_stock,
                        model_name,
                        fold_number,
                    )

                    if key in completed:
                        print(
                            f"    Fold {fold_number}："
                            "已完成，跳過"
                        )
                        continue

                    print(
                        f"    Fold {fold_number}：開始"
                    )

                    loso_fold = (
                        build_loso_fold(
                            raw_data=
                                raw_data,
                            fold=
                                fold_spec,
                            heldout_stock=
                                heldout_stock,
                        )
                    )

                    selected_epochs = None
                    gru_inner = None

                    if model_name == "gru":

                        (
                            selected_epochs,
                            gru_inner,
                        ) = (
                            select_gru_epochs_peer_only(
                                raw_data=
                                    raw_data,
                                fold=
                                    fold_spec,
                                heldout_stock=
                                    heldout_stock,
                            )
                        )

                        print(
                            "      GRU Epoch="
                            f"{selected_epochs}"
                        )

                    (
                        _model,
                        model_metrics,
                        _probabilities,
                    ) = run_multiclass_model(
                        model_name,
                        loso_fold,
                        MODEL_PARAMS[
                            model_name
                        ],
                        fixed_epochs=
                            selected_epochs,
                        evaluation_splits=(
                            "validation",
                        ),
                    )

                    validation = (
                        EXP.add_direction_metrics(
                            model_metrics[
                                "splits"
                            ]["validation"]
                        )
                    )

                    item = {
                        "experiment":
                            "context_candidate_loso",

                        "industry":
                            industry,

                        "heldout_stock":
                            heldout_stock,

                        "peer_stocks":
                            peer_stocks,

                        "model":
                            model_name,

                        "fold":
                            fold_number,

                        "train_samples":
                            int(
                                len(
                                    loso_fold.X[
                                        "train"
                                    ]
                                )
                            ),

                        "validation_samples":
                            int(
                                len(
                                    loso_fold.X[
                                        "validation"
                                    ]
                                )
                            ),

                        "context_feature_count":
                            len(
                                context_names
                            ),

                        "context_features":
                            context_names,

                        "selected_epochs":
                            selected_epochs,

                        "gru_inner":
                            gru_inner,

                        "metrics":
                            validation,

                        "fold_audit":
                            loso_fold.audit,
                    }

                    append_progress(
                        item
                    )

                    completed.add(
                        key
                    )

                    print(
                        "      Macro F1 = "
                        f"{validation['macro_f1']:.4f}"
                    )

                    auc = validation.get(
                        "macro_roc_auc_ovr"
                    )

                    print(
                        "      Macro AUC = "
                        + (
                            f"{auc:.4f}"
                            if auc
                            is not None
                            else "N/A"
                        )
                    )

                    print(
                        "      BUY Precision = "
                        f"{validation['buy_precision']:.4f}"
                    )

                    print(
                        "      BUY Recall = "
                        f"{validation['buy_recall']:.4f}"
                    )

                    gc.collect()

    build_report(
        dataset_audit=
            dataset_audit,
        loso_audit=
            loso_audit,
    )


# ============================================================
# 報告
# ============================================================

def build_report(
    dataset_audit: dict[str, Any],
    loso_audit: dict[str, Any],
) -> None:

    rows = load_progress_rows()

    if not rows:
        print(
            "目前沒有 LOSO 結果。"
        )
        return

    heldout_results: dict[
        str,
        Any,
    ] = {}

    stability: dict[
        str,
        Any,
    ] = {}

    for industry, stocks in (
        INDUSTRIES.items()
    ):

        heldout_results[
            industry
        ] = {}

        stability[
            industry
        ] = {}

        for stock in stocks:

            heldout_results[
                industry
            ][stock] = {}

            for model_name in MODELS:

                selected = [
                    row
                    for row in rows
                    if (
                        row["industry"]
                        == industry
                        and
                        row[
                            "heldout_stock"
                        ]
                        == stock
                        and
                        row["model"]
                        == model_name
                    )
                ]

                if not selected:
                    continue

                fold_metrics = [
                    {
                        "fold":
                            row["fold"],
                        **row["metrics"],
                    }
                    for row in sorted(
                        selected,
                        key=lambda x:
                            x["fold"],
                    )
                ]

                heldout_results[
                    industry
                ][stock][
                    model_name
                ] = EXP.summarize_folds(
                    fold_metrics
                )

        # ----------------------------
        # 各模型跨 Held-out Stock Stability
        # ----------------------------

        for model_name in MODELS:

            stock_summaries = []

            for stock in stocks:

                result = (
                    heldout_results[
                        industry
                    ][stock].get(
                        model_name
                    )
                )

                if result:
                    stock_summaries.append(
                        result
                    )

            if not stock_summaries:
                continue

            f1_values = [
                item[
                    "macro_f1"
                ]["mean"]
                for item
                in stock_summaries
            ]

            auc_values = [
                item[
                    "macro_roc_auc_ovr"
                ]["mean"]
                for item
                in stock_summaries
            ]

            buy_precision = [
                item[
                    "buy_precision"
                ]["mean"]
                for item
                in stock_summaries
            ]

            buy_recall = [
                item[
                    "buy_recall"
                ]["mean"]
                for item
                in stock_summaries
            ]

            stability[
                industry
            ][
                model_name
            ] = {
                "completed_heldout_stocks":
                    len(stock_summaries),

                "macro_f1_mean_by_stock":
                    EXP.stats(
                        f1_values
                    ),

                "macro_auc_mean_by_stock":
                    EXP.stats(
                        auc_values
                    ),

                "buy_precision_mean_by_stock":
                    EXP.stats(
                        buy_precision
                    ),

                "buy_recall_mean_by_stock":
                    EXP.stats(
                        buy_recall
                    ),
            }

    report = {
        "experiment":
            "ML V1 Improvement Round 1 Context Candidate LOSO",

        "stage":
            "TRAINING_ONLY_INDUSTRY_LOSO",

        "design": {
            "method":
                "5-stock LOSO x 4-fold expanding-window",

            "industries":
                INDUSTRIES,

            "models":
                MODEL_PARAMS,

            "training_period": [
                "2023-01-01",
                "2024-12-31",
            ],

            "label":
                "Fixed ±1%",

            "classes": [
                "SELL",
                "HOLD",
                "BUY",
            ],

            "horizon_sessions":
                3,

            "decision_time":
                "13:30",

            "sequence":
                "Exact 24 bars 11:35..13:30",

            "expected_runs":
                120,
        },

        "dataset_audit":
            dataset_audit,

        "loso_audit":
            loso_audit,

        "heldout_results":
            heldout_results,

        "heldout_stock_stability":
            stability,

        "completed_runs":
            len(rows),

        "expected_runs":
            120,
    }

    RESULT_JSON.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    flat_rows = []

    for row in rows:

        m = row["metrics"]

        flat_rows.append({
            "industry":
                row["industry"],

            "heldout_stock":
                row[
                    "heldout_stock"
                ],

            "model":
                row["model"],

            "fold":
                row["fold"],

            "train_samples":
                row[
                    "train_samples"
                ],

            "validation_samples":
                row[
                    "validation_samples"
                ],

            "macro_f1":
                m["macro_f1"],

            "macro_auc":
                m.get(
                    "macro_roc_auc_ovr"
                ),

            "buy_precision":
                m[
                    "buy_precision"
                ],

            "buy_recall":
                m[
                    "buy_recall"
                ],

            "buy_coverage":
                m[
                    "buy_coverage"
                ],

            "sell_precision":
                m[
                    "sell_precision"
                ],

            "sell_recall":
                m[
                    "sell_recall"
                ],

            "selected_epochs":
                row.get(
                    "selected_epochs"
                ),
        })

    (
        pd.DataFrame(
            flat_rows
        )
        .sort_values(
            [
                "industry",
                "heldout_stock",
                "model",
                "fold",
            ]
        )
        .to_csv(
            RESULT_CSV,
            index=False,
            encoding="utf-8-sig",
        )
    )

    print()
    print("=" * 72)
    print("LOSO 結果已輸出")
    print("=" * 72)

    print(
        RESULT_JSON
    )

    print(
        RESULT_CSV
    )

    print()

    print(
        f"Completed Runs："
        f"{len(rows)} / 120"
    )

    if len(rows) == 120:

        print()
        print(
            "Context Candidate LOSO "
            "已全部完成。"
        )

        print(
            "先停止，不要進 2025。"
        )

        print(
            "把 candidate_loso_results.json "
            "傳給 ChatGPT。"
        )


# ============================================================
# CLI
# ============================================================

def main() -> int:

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
        choices=[
            *TECHNOLOGY,
            *FINANCIAL,
        ],
        default=None,
    )

    args = parser.parse_args()

    if args.heldout:

        if (
            args.heldout
            in TECHNOLOGY
        ):
            expected_industry = (
                "technology"
            )
        else:
            expected_industry = (
                "financial"
            )

        if (
            args.industry
            and args.industry
            != expected_industry
        ):
            raise RuntimeError(
                f"{args.heldout} 不屬於 "
                f"{args.industry}"
            )

    if args.prepare_only:

        (
            _protocol,
            _X,
            _y,
            _metadata,
            _model_context,
            _dataset_audit,
            audit,
        ) = prepare_loso()

        print()
        print("=" * 72)
        print("LOSO PREPARE ONLY：PASS")
        print("=" * 72)

        print(
            json.dumps(
                audit,
                ensure_ascii=False,
                indent=2,
            )
        )

        print()
        print(
            "沒有訓練任何 LOSO 模型。"
        )

        return 0

    run_loso(
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
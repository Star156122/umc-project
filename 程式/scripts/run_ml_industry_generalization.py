"""Training-only Industry Generalization.

設計：
- 每個產業固定 5 檔股票
- LOSO / Leave-One-Stock-Out（留一股票驗證）
- 4-fold expanding-window Walk-forward（四折時間滾動驗證）

Frozen Feature（已凍結特徵）：
- Random Forest：Feature Set A
- XGBoost：Feature Set A
- GRU：Feature Set A + VOLUME

資料限制：
- 只讀取 2023-2024 Training
- Held-out Stock（留出股票）不可參與訓練
- Held-out Stock 不可參與 GRU Early Stopping
- 不調參
- 不重新找 Feature
- 不使用 Trading Return 選模型
- 不碰 Controlled Validation / Development / Holdout / Final OOS
"""

from __future__ import annotations

import copy
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from ml.data_pipeline import PreparedData
from ml.models import run_multiclass_model
from ml.research_features import build_daily_stock_features
from ml.trading_pipeline import (
    _intraday_features,
    _read_split,
    _samples_for_split,
    coverage_report,
    load_trading_plan,
)
from ml.walk_forward import (
    aggregate_fold_metrics,
    load_walk_forward_protocol,
)


CONFIG_PATH = ROOT / "configs/ml_industry_generalization_20261005.json"

DATABASE = ROOT / "data/market_data.sqlite3"

OUT = ROOT / "exports/ml_industry_generalization_20261005"

REQUIRED_CLASSES = {0, 1, 2}


# ============================================================
# Config
# ============================================================

def load_config(path: Path) -> dict[str, Any]:

    config = json.loads(
        path.read_text(encoding="utf-8")
    )

    if config.get("data_role") != "train":
        raise ValueError(
            "Industry Generalization 只允許使用 Training（訓練資料）。"
        )

    expected_period = {
        "start": "2023-01-01",
        "end": "2024-12-31",
    }

    if config.get("training_period") != expected_period:
        raise ValueError(
            "Training 期間必須固定為 2023-01-01～2024-12-31。"
        )

    rules = config["rules"]

    forbidden_true = [
        "parameter_search",
        "feature_search",
        "trading_return_selection",
        "controlled_validation_used",
        "development_used",
        "holdout_used",
        "final_oos_used",
    ]

    if any(
        bool(rules.get(name))
        for name in forbidden_true
    ):
        raise ValueError(
            "禁止調參、Feature Mining 或讀取非 Training 資料。"
        )

    minimum = int(
        rules["minimum_industry_members"]
    )

    for industry, stocks in config["industries"].items():

        codes = list(map(str, stocks))

        if len(codes) < minimum:
            raise ValueError(
                f"{industry} 股票數不足：{codes}"
            )

        if len(codes) != len(set(codes)):
            raise ValueError(
                f"{industry} 有重複股票：{codes}"
            )

        if not all(
            code.isdigit() and len(code) == 4
            for code in codes
        ):
            raise ValueError(
                f"{industry} 含無效股票代碼。"
            )

    technology = set(
        map(str, config["industries"]["technology"])
    )

    financial = set(
        map(str, config["industries"]["financial"])
    )

    overlap = technology & financial

    if overlap:
        raise ValueError(
            f"科技與金融群組有重複股票：{sorted(overlap)}"
        )

    expected_context = {

        "random_forest": [],

        "xgboost": [],

        "gru": [
            "volume_trend_5d",
            "volume_trend_20d",
        ],
    }

    for model_name, expected in expected_context.items():

        actual = config[
            "model_feature_policy"
        ][model_name]["context_features"]

        if actual != expected:
            raise ValueError(
                f"{model_name} Feature Freeze（特徵凍結）遭到修改。"
            )

    fraction = float(
        config["gru_inner_validation_fraction"]
    )

    if not 0.10 <= fraction <= 0.40:
        raise ValueError(
            "GRU Inner Validation Fraction 不合理。"
        )

    return config


# ============================================================
# Utility
# ============================================================

def stats(
    values: list[float],
) -> dict[str, Any]:

    array = np.asarray(
        values,
        dtype=float,
    )

    return {

        "mean":
            float(array.mean())
            if len(array)
            else None,

        "standard_deviation":
            float(array.std(ddof=1))
            if len(array) > 1
            else 0.0
            if len(array)
            else None,

        "worst":
            float(array.min())
            if len(array)
            else None,

        "count":
            int(len(array)),
    }


def require_three_classes(
    y: np.ndarray,
    where: str,
) -> None:

    classes = set(
        np.unique(y)
        .astype(int)
        .tolist()
    )

    if classes != REQUIRED_CLASSES:

        raise RuntimeError(
            f"{where} 缺少三分類，"
            f"實際 classes={sorted(classes)}"
        )


# ============================================================
# Load Training
# ============================================================

def prepare_raw_training(
    database: Path,
    plan: dict[str, Any],
    config: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:

    all_codes = sorted({

        str(code)

        for stocks
        in config["industries"].values()

        for code
        in stocks
    })

    local_plan = copy.deepcopy(plan)

    local_plan["stock_codes"] = all_codes


    # --------------------------------------------------------
    # Coverage Audit（資料涵蓋檢查）
    # --------------------------------------------------------

    coverage = coverage_report(
        database,
        local_plan,
        roles=("train",),
    )

    missing = [

        row

        for row in coverage.rows

        if not row["available"]
    ]

    if missing:

        raise RuntimeError(
            f"Training database 缺少股票資料：{missing}"
        )


    # --------------------------------------------------------
    # Load only Training
    # --------------------------------------------------------

    raw = _read_split(
        database,
        local_plan,
        "train",
    )


    # --------------------------------------------------------
    # Intraday Feature
    # --------------------------------------------------------

    featured, cleaning = _intraday_features(
        raw,
        local_plan["features"],
    )


    # --------------------------------------------------------
    # Build samples
    # --------------------------------------------------------

    samples, labels, metadata, _, rejected = (
        _samples_for_split(
            featured,
            local_plan,
        )
    )

    X = np.asarray(
        samples,
        dtype=np.float32,
    )

    y = np.asarray(
        labels,
        dtype=np.int64,
    )

    meta = pd.DataFrame(
        metadata
    )


    # --------------------------------------------------------
    # GRU Frozen VOLUME Context
    # --------------------------------------------------------

    daily = build_daily_stock_features(
        featured
    )

    context_names = [
        "volume_trend_5d",
        "volume_trend_20d",
    ]

    daily["trade_date"] = (
        pd.to_datetime(
            daily["trade_date"]
        ).dt.date
    )

    lookup = meta.copy()

    lookup["trade_date"] = (
        pd.to_datetime(
            lookup["signal_date"]
        ).dt.date
    )

    joined = (
        lookup[
            [
                "stock_code",
                "trade_date",
            ]
        ]
        .merge(
            daily[
                [
                    "stock_code",
                    "trade_date",
                    *context_names,
                ]
            ],
            on=[
                "stock_code",
                "trade_date",
            ],
            how="left",
            validate="many_to_one",
        )
    )

    context = joined[
        context_names
    ].to_numpy(
        dtype=np.float32
    )


    # --------------------------------------------------------
    # Common Alignment（共同樣本對齊）
    #
    # GRU 的 volume_trend_20d 前期會 NaN。
    # RF/XGB 也跟著使用同一批 sample，
    # 才能公平比較。
    # --------------------------------------------------------

    finite = np.isfinite(
        context
    ).all(
        axis=1
    )

    X = X[finite]

    y = y[finite]

    meta = (
        meta
        .loc[finite]
        .reset_index(
            drop=True
        )
    )

    context = context[
        finite
    ]


    audit = {

        "loaded_role":
            "train",

        "stock_codes":
            all_codes,

        "coverage":
            coverage.rows,

        "cleaning":
            cleaning,

        "sample_rejections_before_context_alignment":
            int(rejected),

        "samples_after_common_alignment":
            int(len(X)),

        "context_features":
            context_names,

        "common_sample_alignment_across_models":
            True,
    }


    return {

        "X":
            X,

        "y":
            y,

        "meta":
            meta,

        "context":
            context,

        "feature_names":
            list(
                local_plan["features"]
            ),

        "context_feature_names":
            context_names,

    }, audit


# ============================================================
# Outer LOSO + Walk-forward
# ============================================================

def indices_for_outer_fold(
    meta: pd.DataFrame,
    peer_codes: list[str],
    heldout: str,
    fold: dict[str, Any],
) -> tuple[
    np.ndarray,
    np.ndarray,
]:

    stock = (
        meta["stock_code"]
        .astype(str)
    )

    signal = pd.to_datetime(
        meta["signal_date"]
    )

    target = pd.to_datetime(
        meta["target_date"]
    )


    train_start = pd.Timestamp(
        fold["train_start"]
    )

    train_end = pd.Timestamp(
        fold["train_end"]
    )

    test_start = pd.Timestamp(
        fold["validation_start"]
    )

    test_end = pd.Timestamp(
        fold["validation_end"]
    )


    peer_mask = stock.isin(
        peer_codes
    )

    heldout_mask = stock.eq(
        heldout
    )


    # Training：
    # 只能是其他四檔股票
    # 且 signal / target 都必須在 training period 裡。
    train_mask = (

        peer_mask

        & signal.between(
            train_start,
            train_end,
        )

        & target.between(
            train_start,
            train_end,
        )
    )


    # Test：
    # 只能是 held-out stock。
    test_mask = (

        heldout_mask

        & signal.between(
            test_start,
            test_end,
        )

        & target.between(
            test_start,
            test_end,
        )
    )


    train_idx = np.flatnonzero(
        train_mask.to_numpy()
    )

    test_idx = np.flatnonzero(
        test_mask.to_numpy()
    )


    if (
        not len(train_idx)
        or not len(test_idx)
    ):

        raise RuntimeError(
            f"Fold {fold['fold']} / "
            f"Held-out {heldout} "
            f"沒有足夠樣本。"
        )


    return (
        train_idx,
        test_idx,
    )


# ============================================================
# GRU Inner Validation
# ============================================================

def split_gru_inner_validation(
    raw: dict[str, Any],
    outer_train_idx: np.ndarray,
    fraction: float,
    fold_number: int,
    heldout: str,
) -> tuple[
    np.ndarray,
    np.ndarray,
    str,
]:

    subset = (
        raw["meta"]
        .iloc[
            outer_train_idx
        ]
        .copy()
    )

    signal = pd.to_datetime(
        subset["signal_date"]
    )

    target = pd.to_datetime(
        subset["target_date"]
    )


    dates = pd.Index(
        sorted(
            pd.to_datetime(
                signal
                .dt
                .normalize()
                .unique()
            )
        )
    )


    if len(dates) < 20:

        raise RuntimeError(
            f"Fold {fold_number} / "
            f"{heldout} 日期太少，"
            f"無法做 GRU Inner Validation。"
        )


    split_pos = int(
        np.floor(
            len(dates)
            * (1.0 - fraction)
        )
    )

    split_pos = min(
        max(
            split_pos,
            1,
        ),
        len(dates) - 1,
    )


    validation_start = pd.Timestamp(
        dates[
            split_pos
        ]
    )


    # Inner Train：
    # target 也不能跨進 inner validation。
    inner_train_local = (

        (signal < validation_start)

        & (target < validation_start)
    )


    # Inner Validation：
    # 還是只有 peer stocks。
    inner_val_local = (
        signal >= validation_start
    )


    inner_train_idx = (
        outer_train_idx[
            np.flatnonzero(
                inner_train_local
                .to_numpy()
            )
        ]
    )


    inner_val_idx = (
        outer_train_idx[
            np.flatnonzero(
                inner_val_local
                .to_numpy()
            )
        ]
    )


    if (
        not len(inner_train_idx)
        or not len(inner_val_idx)
    ):

        raise RuntimeError(
            f"Fold {fold_number} / "
            f"{heldout} "
            f"GRU Inner Split 為空。"
        )


    require_three_classes(

        raw["y"][
            inner_train_idx
        ],

        (
            f"Fold {fold_number} / "
            f"{heldout} "
            f"GRU Inner Train"
        ),
    )


    return (
        inner_train_idx,
        inner_val_idx,
        validation_start.date().isoformat(),
    )


# ============================================================
# Scaling + PreparedData
# ============================================================

def make_scaled_data(
    raw: dict[str, Any],
    train_idx: np.ndarray,
    eval_indices: dict[
        str,
        np.ndarray,
    ],
    *,
    include_context: bool,
    stage: str,
    audit_extra: dict[str, Any],
) -> PreparedData:

    X_source = raw["X"]

    y_source = raw["y"]

    meta_source = raw["meta"]


    # --------------------------------------------------------
    # Intraday Scaler
    # fit ONLY on current peer-stock training.
    # --------------------------------------------------------

    feature_count = (
        X_source.shape[2]
    )

    fit_flat = (
        X_source[
            train_idx
        ]
        .reshape(
            -1,
            feature_count,
        )
        .astype(
            np.float64
        )
    )


    mean = fit_flat.mean(
        axis=0
    )

    scale = fit_flat.std(
        axis=0
    )

    scale[
        scale == 0
    ] = 1.0


    X = {

        "train":

            (
                (
                    X_source[
                        train_idx
                    ]
                    - mean
                )
                / scale
            )
            .astype(
                np.float32
            )
    }


    y = {

        "train":
            y_source[
                train_idx
            ]
    }


    metadata = {

        "train":

            meta_source
            .iloc[
                train_idx
            ]
            .reset_index(
                drop=True
            )
    }


    for (
        split,
        idx,
    ) in eval_indices.items():

        X[split] = (

            (
                X_source[idx]
                - mean
            )
            / scale

        ).astype(
            np.float32
        )


        y[split] = (
            y_source[idx]
        )


        metadata[split] = (

            meta_source
            .iloc[idx]
            .reset_index(
                drop=True
            )
        )


    # --------------------------------------------------------
    # GRU static VOLUME context
    # --------------------------------------------------------

    context_X = None

    context_mean = None

    context_scale = None


    if include_context:

        source_context = (
            raw["context"]
        )

        fit_context = (

            source_context[
                train_idx
            ]

            .astype(
                np.float64
            )
        )


        context_mean = (
            fit_context.mean(
                axis=0
            )
        )


        context_scale = (
            fit_context.std(
                axis=0
            )
        )


        context_scale[
            context_scale == 0
        ] = 1.0


        context_X = {

            "train":

                (
                    (
                        source_context[
                            train_idx
                        ]
                        - context_mean
                    )
                    / context_scale
                )
                .astype(
                    np.float32
                )
        }


        for (
            split,
            idx,
        ) in eval_indices.items():

            context_X[split] = (

                (
                    source_context[
                        idx
                    ]
                    - context_mean
                )
                / context_scale

            ).astype(
                np.float32
            )


    audit = {

        "workflow_stage":
            stage,

        "loaded_roles":
            ["train"],

        "scaler_fit_on":
            "peer_stock_training_only",

        "preprocessing_fit_on":
            "peer_stock_training_only",

        "context_scaler_fit_on":
            (
                "peer_stock_training_only"
                if include_context
                else None
            ),

        "class_weight_source":
            "peer_stock_training_only",

        "shuffle":
            False,

        **audit_extra,
    }


    return PreparedData(

        X=X,

        y=y,

        metadata=metadata,

        feature_names=list(
            raw[
                "feature_names"
            ]
        ),

        scaler_mean=mean,

        scaler_scale=scale,

        audit=audit,

        context_X=context_X,

        context_feature_names=(
            list(
                raw[
                    "context_feature_names"
                ]
            )
            if include_context
            else []
        ),

        context_scaler_mean=
            context_mean,

        context_scaler_scale=
            context_scale,
    )


# ============================================================
# Leakage Audit
# ============================================================

def verify_no_heldout_leakage(
    data: PreparedData,
    heldout: str,
) -> None:

    train_codes = set(

        data
        .metadata[
            "train"
        ][
            "stock_code"
        ]
        .astype(str)
    )


    if heldout in train_codes:

        raise RuntimeError(

            f"資料洩漏："
            f"Held-out {heldout} "
            f"出現在 Training。"
        )


    if (
        "validation"
        in data.metadata
    ):

        validation_codes = set(

            data
            .metadata[
                "validation"
            ][
                "stock_code"
            ]
            .astype(str)
        )


        if heldout in validation_codes:

            raise RuntimeError(

                f"資料洩漏："
                f"Held-out {heldout} "
                f"出現在 GRU Early Stopping。"
            )


# ============================================================
# Industry aggregation
# ============================================================

def aggregate_industry(
    heldout_results:
        dict[str, dict[str, Any]],
    model_names:
        list[str],
) -> dict[str, Any]:

    output: dict[
        str,
        Any,
    ] = {}


    for model_name in model_names:

        all_outer_metrics = []

        heldout_f1_means = []

        heldout_auc_means = []


        for (
            heldout,
            model_results,
        ) in heldout_results.items():

            summary = (
                model_results[
                    model_name
                ]
            )


            all_outer_metrics.extend(
                summary["folds"]
            )


            f1_mean = (
                summary[
                    "macro_f1"
                ][
                    "mean"
                ]
            )


            auc_mean = (
                summary[
                    "macro_roc_auc_ovr"
                ][
                    "mean"
                ]
            )


            if f1_mean is not None:

                heldout_f1_means.append(
                    float(
                        f1_mean
                    )
                )


            if auc_mean is not None:

                heldout_auc_means.append(
                    float(
                        auc_mean
                    )
                )


        output[
            model_name
        ] = {

            "outer_fold_aggregate":

                aggregate_fold_metrics(
                    all_outer_metrics
                ),

            "heldout_stock_stability": {

                "macro_f1_mean_by_stock":

                    stats(
                        heldout_f1_means
                    ),

                "macro_auc_mean_by_stock":

                    stats(
                        heldout_auc_means
                    ),
            },
        }


    return output


# ============================================================
# HTML Report
# ============================================================

def render(
    report: dict[str, Any],
) -> str:

    def table(
        headers: list[str],
        rows: list[list[Any]],
    ) -> str:

        head = "".join(

            f"<th>{html.escape(str(value))}</th>"

            for value
            in headers
        )


        body = "".join(

            "<tr>"

            + "".join(

                f"<td>{html.escape(str(value))}</td>"

                for value
                in row
            )

            + "</tr>"

            for row
            in rows
        )


        return (
            f"<table>"
            f"<tr>{head}</tr>"
            f"{body}"
            f"</table>"
        )


    industry_rows = []


    for (
        industry,
        models,
    ) in report[
        "industry_summary"
    ].items():

        for (
            model_name,
            payload,
        ) in models.items():

            s = payload[
                "outer_fold_aggregate"
            ]


            auc_mean = (
                s[
                    "macro_roc_auc_ovr"
                ][
                    "mean"
                ]
            )

            auc_std = (
                s[
                    "macro_roc_auc_ovr"
                ][
                    "standard_deviation"
                ]
            )

            auc_worst = (
                s[
                    "macro_roc_auc_ovr"
                ][
                    "worst"
                ]
            )


            industry_rows.append([

                industry,

                model_name,

                f"{s['macro_f1']['mean']:.4f}",

                f"{s['macro_f1']['standard_deviation']:.4f}",

                f"{s['macro_f1']['worst']:.4f}",

                (
                    "NA"
                    if auc_mean is None
                    else f"{auc_mean:.4f}"
                ),

                (
                    "NA"
                    if auc_std is None
                    else f"{auc_std:.4f}"
                ),

                (
                    "NA"
                    if auc_worst is None
                    else f"{auc_worst:.4f}"
                ),
            ])


    heldout_rows = []


    for (
        industry,
        stocks,
    ) in report[
        "heldout_results"
    ].items():

        for (
            heldout,
            models,
        ) in stocks.items():

            for (
                model_name,
                s,
            ) in models.items():

                auc_mean = (
                    s[
                        "macro_roc_auc_ovr"
                    ][
                        "mean"
                    ]
                )

                auc_worst = (
                    s[
                        "macro_roc_auc_ovr"
                    ][
                        "worst"
                    ]
                )


                heldout_rows.append([

                    industry,

                    heldout,

                    model_name,

                    f"{s['macro_f1']['mean']:.4f}",

                    f"{s['macro_f1']['worst']:.4f}",

                    (
                        "NA"
                        if auc_mean is None
                        else f"{auc_mean:.4f}"
                    ),

                    (
                        "NA"
                        if auc_worst is None
                        else f"{auc_worst:.4f}"
                    ),
                ])


    design_json = html.escape(
        json.dumps(
            report["design"],
            ensure_ascii=False,
            indent=2,
        )
    )


    audit_json = html.escape(
        json.dumps(
            report["audit"],
            ensure_ascii=False,
            indent=2,
        )
    )


    return f"""
<!doctype html>

<html lang="zh-Hant">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width"
>

<title>
ML Industry Generalization
</title>

<style>

body {{
    font-family:
        system-ui,
        'Noto Sans TC';
    max-width: 1500px;
    margin: 28px auto;
    padding: 0 18px;
    background: #f6f8fb;
    color: #182536;
}}

section {{
    background: #fff;
    border: 1px solid #dae3ea;
    border-radius: 12px;
    padding: 18px;
    margin: 16px 0;
}}

h1,
h2 {{
    color: #0b527a;
}}

table {{
    border-collapse: collapse;
    width: 100%;
    font-size: 13px;
}}

th,
td {{
    border: 1px solid #d6e0e8;
    padding: 6px;
    text-align: right;
}}

th:first-child,
td:first-child {{
    text-align: left;
}}

th {{
    background: #eaf3f8;
}}

pre {{
    white-space: pre-wrap;
}}

</style>

</head>

<body>

<h1>
Training-only Industry Generalization
</h1>

<section>

<h2>
1. Design
</h2>

<pre>{design_json}</pre>

</section>


<section>

<h2>
2. Industry Aggregate
</h2>

{
    table(
        [
            "Industry",
            "Model",
            "F1 mean",
            "F1 std",
            "F1 worst",
            "AUC mean",
            "AUC std",
            "AUC worst",
        ],
        industry_rows,
    )
}

</section>


<section>

<h2>
3. Held-out Stock Summary
</h2>

{
    table(
        [
            "Industry",
            "Held-out",
            "Model",
            "F1 mean",
            "F1 worst",
            "AUC mean",
            "AUC worst",
        ],
        heldout_rows,
    )
}

</section>


<section>

<h2>
4. Audit
</h2>

<pre>{audit_json}</pre>

</section>


<section>

<h2>
5. Decision Status
</h2>

<p>
本報告只做 Training-only（僅訓練資料）
產業泛化診斷。
不自動選產業、不調 Feature、不調參數，
不啟動 Controlled Validation。
</p>

</section>

</body>

</html>
"""


# ============================================================
# Main
# ============================================================

def main() -> int:

    config = load_config(
        CONFIG_PATH
    )


    plan = load_trading_plan(

        ROOT
        / config[
            "base_plan"
        ]
    )


    protocol = (
        load_walk_forward_protocol(

            ROOT
            / config[
                "walk_forward_protocol"
            ]
        )
    )


    raw, load_audit = (
        prepare_raw_training(

            DATABASE,

            plan,

            config,
        )
    )


    heldout_results = {}

    leakage_checks = []

    gru_epoch_records = []

    model_names = list(
        config[
            "models"
        ].keys()
    )


    # ========================================================
    # Industry loop
    # ========================================================

    for (
        industry,
        stocks0,
    ) in config[
        "industries"
    ].items():

        stocks = list(
            map(
                str,
                stocks0,
            )
        )


        heldout_results[
            industry
        ] = {}


        print()

        print(
            "=" * 72
        )

        print(
            f"Industry: {industry}"
        )

        print(
            "Stocks: "
            + ", ".join(
                stocks
            )
        )

        print(
            "=" * 72
        )


        # ====================================================
        # LOSO
        # ====================================================

        for heldout in stocks:

            peer_codes = [

                code

                for code
                in stocks

                if code != heldout
            ]


            model_fold_metrics = {

                model_name: []

                for model_name
                in model_names
            }


            print()

            print(
                f"[Held-out {heldout}] "
                f"Train peers: "
                f"{', '.join(peer_codes)}"
            )


            # ================================================
            # Walk-forward folds
            # ================================================

            for fold in protocol[
                "walk_forward"
            ][
                "folds"
            ]:

                fold_number = int(
                    fold[
                        "fold"
                    ]
                )


                (
                    outer_train_idx,
                    outer_test_idx,
                ) = (
                    indices_for_outer_fold(

                        raw[
                            "meta"
                        ],

                        peer_codes,

                        heldout,

                        fold,
                    )
                )


                require_three_classes(

                    raw[
                        "y"
                    ][
                        outer_train_idx
                    ],

                    (
                        f"{industry} / "
                        f"{heldout} / "
                        f"Fold {fold_number} "
                        f"Outer Train"
                    ),
                )


                common_audit = {

                    "industry":
                        industry,

                    "heldout_stock":
                        heldout,

                    "peer_stocks":
                        peer_codes,

                    "fold":
                        fold_number,

                    "periods":
                        dict(
                            fold
                        ),

                    "heldout_stock_in_training":
                        False,

                    "heldout_stock_in_gru_early_stopping":
                        False,

                    "outer_train_samples":
                        int(
                            len(
                                outer_train_idx
                            )
                        ),

                    "outer_generalization_samples":
                        int(
                            len(
                                outer_test_idx
                            )
                        ),
                }


                # ============================================
                # Random Forest + XGBoost
                # Feature A
                # ============================================

                for model_name in (
                    "random_forest",
                    "xgboost",
                ):

                    data = make_scaled_data(

                        raw,

                        outer_train_idx,

                        {
                            "generalization":
                                outer_test_idx
                        },

                        include_context=False,

                        stage=
                            "industry_generalization_fold",

                        audit_extra={

                            **common_audit,

                            "feature_set":

                                config[
                                    "model_feature_policy"
                                ][
                                    model_name
                                ][
                                    "feature_set"
                                ],
                        },
                    )


                    verify_no_heldout_leakage(
                        data,
                        heldout,
                    )


                    leakage_checks.append(
                        True
                    )


                    _, metrics, _ = (
                        run_multiclass_model(

                            model_name,

                            data,

                            config[
                                "models"
                            ][
                                model_name
                            ][
                                "params"
                            ],

                            evaluation_splits=(
                                "generalization",
                            ),
                        )
                    )


                    row = {

                        "fold":
                            fold_number,

                        "heldout_stock":
                            heldout,

                        "peer_stocks":
                            peer_codes,

                        **metrics[
                            "splits"
                        ][
                            "generalization"
                        ],
                    }


                    model_fold_metrics[
                        model_name
                    ].append(
                        row
                    )


                # ============================================
                # GRU
                #
                # 先在 peer stocks outer training 裡
                # 再切一個 inner validation，
                # 用來 Early Stopping。
                #
                # Held-out stock 完全不參與。
                # ============================================

                (
                    inner_train_idx,
                    inner_val_idx,
                    inner_val_start,
                ) = split_gru_inner_validation(

                    raw,

                    outer_train_idx,

                    float(
                        config[
                            "gru_inner_validation_fraction"
                        ]
                    ),

                    fold_number,

                    heldout,
                )


                gru_inner = make_scaled_data(

                    raw,

                    inner_train_idx,

                    {
                        "validation":
                            inner_val_idx
                    },

                    include_context=True,

                    stage=
                        "walk_forward_fold",

                    audit_extra={

                        **common_audit,

                        "feature_set":

                            config[
                                "model_feature_policy"
                            ][
                                "gru"
                            ][
                                "feature_set"
                            ],

                        "gru_inner_validation_start":
                            inner_val_start,

                        "gru_inner_train_samples":
                            int(
                                len(
                                    inner_train_idx
                                )
                            ),

                        "gru_inner_validation_samples":
                            int(
                                len(
                                    inner_val_idx
                                )
                            ),
                    },
                )


                verify_no_heldout_leakage(
                    gru_inner,
                    heldout,
                )


                leakage_checks.append(
                    True
                )


                (
                    _,
                    inner_metrics,
                    _,
                ) = run_multiclass_model(

                    "gru",

                    gru_inner,

                    config[
                        "models"
                    ][
                        "gru"
                    ][
                        "params"
                    ],

                    evaluation_splits=(
                        "validation",
                    ),
                )


                selected_epochs = int(

                    inner_metrics[
                        "epochs_completed"
                    ]
                )


                if selected_epochs < 1:

                    raise RuntimeError(
                        "GRU selected_epochs 不可小於 1。"
                    )


                # ============================================
                # GRU final fit on ALL peer outer training
                #
                # Epoch 已由 inner validation 決定。
                # 不再看 Held-out Stock。
                # ============================================

                gru_final = make_scaled_data(

                    raw,

                    outer_train_idx,

                    {
                        "generalization":
                            outer_test_idx
                    },

                    include_context=True,

                    stage=
                        "industry_generalization_refit",

                    audit_extra={

                        **common_audit,

                        "feature_set":

                            config[
                                "model_feature_policy"
                            ][
                                "gru"
                            ][
                                "feature_set"
                            ],

                        "gru_fixed_epochs_from_peer_inner_validation":
                            selected_epochs,

                        "gru_inner_validation_start":
                            inner_val_start,
                    },
                )


                verify_no_heldout_leakage(
                    gru_final,
                    heldout,
                )


                leakage_checks.append(
                    True
                )


                (
                    _,
                    gru_metrics,
                    _,
                ) = run_multiclass_model(

                    "gru",

                    gru_final,

                    config[
                        "models"
                    ][
                        "gru"
                    ][
                        "params"
                    ],

                    fixed_epochs=
                        selected_epochs,

                    evaluation_splits=(
                        "generalization",
                    ),
                )


                gru_epoch_records.append({

                    "industry":
                        industry,

                    "heldout_stock":
                        heldout,

                    "fold":
                        fold_number,

                    "selected_epochs":
                        selected_epochs,

                    "inner_validation_start":
                        inner_val_start,
                })


                gru_row = {

                    "fold":
                        fold_number,

                    "heldout_stock":
                        heldout,

                    "peer_stocks":
                        peer_codes,

                    "selected_epochs":
                        selected_epochs,

                    **gru_metrics[
                        "splits"
                    ][
                        "generalization"
                    ],
                }


                model_fold_metrics[
                    "gru"
                ].append(
                    gru_row
                )


                print(

                    f"  Fold {fold_number}: "

                    f"train="
                    f"{len(outer_train_idx):,}, "

                    f"test="
                    f"{len(outer_test_idx):,}, "

                    f"GRU epochs="
                    f"{selected_epochs}"
                )


            # ================================================
            # Aggregate 4 Walk-forward folds for held-out stock
            # ================================================

            heldout_results[
                industry
            ][
                heldout
            ] = {

                model_name:

                    aggregate_fold_metrics(

                        model_fold_metrics[
                            model_name
                        ]
                    )

                for model_name
                in model_names
            }


    # ========================================================
    # Industry Summary
    # ========================================================

    industry_summary = {

        industry:

            aggregate_industry(

                heldout_results[
                    industry
                ],

                model_names,
            )

        for industry
        in config[
            "industries"
        ]
    }


    # ========================================================
    # Audit
    # ========================================================

    audit = {

        "training_only":
            True,

        "training_period":
            config[
                "training_period"
            ],

        "controlled_validation_used":
            False,

        "development_used":
            False,

        "holdout_used":
            False,

        "final_oos_used":
            False,

        "parameter_search":
            False,

        "feature_search":
            False,

        "trading_return_selection":
            False,

        "heldout_stock_never_in_training":
            all(
                leakage_checks
            ),

        "heldout_stock_never_in_gru_early_stopping":
            all(
                leakage_checks
            ),

        "scaler_fit_on_peer_training_only":
            True,

        "context_scaler_fit_on_peer_training_only":
            True,

        "common_sample_alignment_across_models":
            True,

        "gru_early_stopping_source":
            (
                "peer-stock inner "
                "chronological validation only"
            ),

        "gru_epoch_records":
            gru_epoch_records,

        "data_load":
            load_audit,
    }


    # ========================================================
    # Report
    # ========================================================

    report = {

        "report_name":
            "ML Industry Generalization Training-only",

        "generated_at":

            datetime
            .now()
            .astimezone()
            .isoformat(
                timespec="seconds"
            ),

        "design": {

            "method":
                (
                    "5-stock LOSO "
                    "x 4-fold expanding-window"
                ),

            "industries":
                config[
                    "industries"
                ],

            "model_feature_policy":
                config[
                    "model_feature_policy"
                ],

            "models":
                config[
                    "models"
                ],

            "gru_inner_validation_fraction":
                config[
                    "gru_inner_validation_fraction"
                ],

            "selection":
                (
                    "diagnostic_only_"
                    "no_candidate_selection"
                ),
        },

        "heldout_results":
            heldout_results,

        "industry_summary":
            industry_summary,

        "audit":
            audit,

        "decision_status":
            (
                "Training-only Industry Generalization completed. "
                "Do not start Controlled Validation until results "
                "are reviewed and Candidate is frozen."
            ),
    }


    # ========================================================
    # Save
    # ========================================================

    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )


    json_path = (
        OUT
        / "industry_generalization_results.json"
    )


    html_path = (
        OUT
        / "industry_generalization_report.html"
    )


    audit_path = (
        OUT
        / "industry_generalization_audit.json"
    )


    json_path.write_text(

        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),

        encoding="utf-8",
    )


    html_path.write_text(

        render(
            report
        ),

        encoding="utf-8",
    )


    audit_path.write_text(

        json.dumps(
            audit,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),

        encoding="utf-8",
    )


    # ========================================================
    # Console Summary
    # ========================================================

    print()

    print(
        "=" * 72
    )

    print(
        "Industry Generalization Completed"
    )

    print(
        "=" * 72
    )


    for (
        industry,
        models,
    ) in industry_summary.items():

        print()

        print(
            f"[{industry}]"
        )


        for (
            model_name,
            payload,
        ) in models.items():

            s = payload[
                "outer_fold_aggregate"
            ]


            f1_mean = (
                s[
                    "macro_f1"
                ][
                    "mean"
                ]
            )


            f1_worst = (
                s[
                    "macro_f1"
                ][
                    "worst"
                ]
            )


            auc_mean = (
                s[
                    "macro_roc_auc_ovr"
                ][
                    "mean"
                ]
            )


            auc_worst = (
                s[
                    "macro_roc_auc_ovr"
                ][
                    "worst"
                ]
            )


            auc_mean_text = (

                "NA"

                if auc_mean is None

                else f"{auc_mean:.4f}"
            )


            auc_worst_text = (

                "NA"

                if auc_worst is None

                else f"{auc_worst:.4f}"
            )


            print(

                f"- {model_name}: "

                f"F1 mean="
                f"{f1_mean:.4f}, "

                f"AUC mean="
                f"{auc_mean_text}, "

                f"F1 worst="
                f"{f1_worst:.4f}, "

                f"AUC worst="
                f"{auc_worst_text}"
            )


    print()

    print(
        f"JSON: {json_path}"
    )

    print(
        f"HTML: {html_path}"
    )

    print(
        f"AUDIT: {audit_path}"
    )


    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
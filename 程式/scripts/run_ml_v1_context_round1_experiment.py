"""
ML V1 Improvement Round 1
Candidate Context Walk-forward Experiment

目的：
只測試「新增 9 個 Context Features」是否改善 ML。

本程式：
- 只使用 2023~2024 Training
- Fixed ±1% 三分類
- Exact 24 bars：11:35~13:30
- Technology / Financial 分開
- RF / XGBoost / GRU 參數固定
- 只跑 Candidate Walk-forward
- 不跑 LOSO
- 不讀 2025 / 2026
- 不做 Hyperparameter Search
- 不做 Probability Threshold Search

Baseline 直接使用既有 Phase B 結果，
避免重新浪費運算資源。
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import inspect
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

CONTEXT_CONFIG = ROOT / "configs/ml_v1_context_round1.json"
PLAN_PATH = ROOT / "configs/ml_trading_v3_20261004.json"
PROTOCOL_PATH = ROOT / "configs/ml_training_protocol_v3.json"

PREP_SCRIPT = ROOT / "scripts/run_ml_v1_context_round1.py"

OUTPUT_DIR = ROOT / "exports/ml_v1_context_round1_experiment_20261012"

PROGRESS_PATH = OUTPUT_DIR / "candidate_walk_forward_progress.jsonl"
RESULT_JSON = OUTPUT_DIR / "candidate_walk_forward_results.json"
RESULT_CSV = OUTPUT_DIR / "candidate_walk_forward_results.csv"
DATASET_AUDIT = OUTPUT_DIR / "candidate_dataset_audit.json"


# ============================================================
# 匯入目前專案模組
# ============================================================

from ml.data_pipeline import PreparedData
from ml.models import run_multiclass_model
from ml.research_features import (
    CONTEXT_CANDIDATE_FEATURES,
    build_daily_stock_features,
)
from ml.trading_pipeline import (
    _daily_market,
    _intraday_features,
)
from ml.walk_forward import (
    aggregate_fold_metrics,
    build_walk_forward_fold,
    load_walk_forward_protocol,
)


# ============================================================
# 載入 Context Preflight Script
# ============================================================

SPEC = importlib.util.spec_from_file_location(
    "run_ml_v1_context_round1",
    PREP_SCRIPT,
)

if SPEC is None or SPEC.loader is None:
    raise RuntimeError("找不到 scripts/run_ml_v1_context_round1.py")

PREP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREP)


# ============================================================
# 固定研究設定
# ============================================================

TECHNOLOGY = ["2303", "2330", "2379", "2454", "3034"]
FINANCIAL = ["2881", "2882", "2884", "2886", "2891"]

INDUSTRIES = {
    "technology": TECHNOLOGY,
    "financial": FINANCIAL,
}

MODELS = ["random_forest", "xgboost", "gru"]


# Phase B 固定模型參數
MODEL_PARAMS = {
    "random_forest": {
        "n_estimators": 200,
        "max_depth": 10,
        "min_samples_leaf": 5,
        "class_weight": "balanced",
        "random_state": 42,
    },
    "xgboost": {
        "n_estimators": 150,
        "max_depth": 4,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "random_state": 42,
    },
    "gru": {
        "hidden_size": 16,
        "num_layers": 1,
        "dropout": 0.0,
        "batch_size": 128,
        "max_epochs": 10,
        "patience": 2,
        "learning_rate": 0.001,
        "random_state": 42,
    },
}


# 原本 24 個 Intraday Features
EXPECTED_INTRADAY_FEATURES = [
    "open_vs_prev_close",
    "high_vs_prev_close",
    "low_vs_prev_close",
    "return_1",
    "return_3",
    "return_6",
    "return_12",
    "volume_ratio_5",
    "volume_ratio_20",
    "volume_zscore_20",
    "close_vs_ma5",
    "close_vs_ma20",
    "price_zscore_20",
    "rsi14_scaled",
    "macd_vs_close",
    "macd_signal_vs_close",
    "macd_hist_vs_close",
    "range_vs_close",
    "body_return",
    "close_location",
    "volatility_12",
    "volatility_24",
    "trend_slope_12",
    "trend_slope_24",
]


# Exact 24 bars
EXACT_TIMES = [
    "11:35",
    "11:40",
    "11:45",
    "11:50",
    "11:55",
    "12:00",
    "12:05",
    "12:10",
    "12:15",
    "12:20",
    "12:25",
    "12:30",
    "12:35",
    "12:40",
    "12:45",
    "12:50",
    "12:55",
    "13:00",
    "13:05",
    "13:10",
    "13:15",
    "13:20",
    "13:25",
    "13:30",
]


# GRU 原本 Phase B 已有的 Context
GRU_BASE_CONTEXT = [
    "volume_trend_5d",
    "volume_trend_20d",
]


# Phase B 使用的 Training Fold 內部 GRU Early Stopping 起點
# 外層 Validation 不允許用來決定 epoch
GRU_INNER_START = {
    1: "2023-07-20",
    2: "2023-10-26",
    3: "2024-01-22",
    4: "2024-05-09",
}


# ============================================================
# 基本工具
# ============================================================

def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_project_support() -> None:
    """
    確認目前 ml.models / ml.walk_forward
    是支援 static context 的版本。
    """
    model_source = inspect.getsource(run_multiclass_model)
    fold_source = inspect.getsource(build_walk_forward_fold)

    if "_context_for" not in model_source:
        raise RuntimeError(
            "目前 ml/models.py 似乎不是支援 static context 的版本。"
        )

    if "context_X" not in fold_source:
        raise RuntimeError(
            "目前 ml/walk_forward.py 似乎不支援 context scaler。"
        )


def validate_fixed_design(
    config: dict[str, Any],
    plan: dict[str, Any],
) -> None:

    fixed = config["fixed_settings"]

    if fixed["label"] != "fixed_1pct":
        raise RuntimeError("Label 被修改，不允許執行。")

    if fixed["classes"] != ["SELL", "HOLD", "BUY"]:
        raise RuntimeError("三分類設定被修改。")

    if int(fixed["prediction_horizon_sessions"]) != 3:
        raise RuntimeError("Prediction Horizon 被修改。")

    if fixed["decision_time"] != "13:30:00":
        raise RuntimeError("Decision Time 被修改。")

    if fixed["sequence_policy"] != "EXACT_24_BARS_11_35_TO_13_30":
        raise RuntimeError("Sequence Policy 被修改。")

    if int(fixed["sequence_bars"]) != 24:
        raise RuntimeError("Sequence Bars 必須為 24。")

    if list(config["candidate_features"]) != CONTEXT_CANDIDATE_FEATURES:
        raise RuntimeError("Context Feature 清單與 Preflight 不一致。")

    if len(CONTEXT_CANDIDATE_FEATURES) != 9:
        raise RuntimeError("Context Candidate 必須剛好 9 個 Feature。")

    if list(plan["features"]) != EXPECTED_INTRADAY_FEATURES:
        raise RuntimeError(
            "ml_trading_v3 的 24 個 Intraday Feature 與 Phase B 不一致。"
        )

    if int(plan["sequence_bars"]) != 24:
        raise RuntimeError("Base Plan sequence_bars 不是 24。")

    target = plan["target"]

    if target.get("classification") != "multiclass":
        raise RuntimeError("Base Plan 不是三分類。")

    if int(target["holding_sessions"]) != 3:
        raise RuntimeError("Base Plan Holding Sessions 被修改。")

    if float(target["buy_net_return_threshold"]) != 0.01:
        raise RuntimeError("BUY Threshold 不是 +1%。")

    if float(target["sell_net_return_threshold"]) != -0.01:
        raise RuntimeError("SELL Threshold 不是 -1%。")


# ============================================================
# Exact 24-bar Sequence
# ============================================================

def build_exact_sequences(
    featured: pd.DataFrame,
    eligible: pd.DataFrame,
) -> np.ndarray:

    frame = featured.copy()

    frame["stock_code"] = frame["stock_code"].astype(str)
    frame["date_key"] = pd.to_datetime(frame["trade_date"]).dt.date.astype(str)

    dt = pd.to_datetime(frame["datetime"])
    frame["time_key"] = dt.dt.strftime("%H:%M")

    grouped: dict[tuple[str, str], pd.DataFrame] = {}

    for (code, date), group in frame.groupby(
        ["stock_code", "date_key"],
        sort=False,
    ):
        grouped[(str(code), str(date))] = group.sort_values("kbar_timestamp")

    sequences: list[np.ndarray] = []

    for row in eligible.itertuples(index=False):

        code = str(row.stock_code)
        date = str(row.decision_date)

        key = (code, date)

        if key not in grouped:
            raise RuntimeError(
                f"找不到 Eligible Sample 對應行情：{code} {date}"
            )

        day = grouped[key]

        selected = day.loc[
            day["time_key"].isin(EXACT_TIMES)
        ].copy()

        selected = selected.sort_values("kbar_timestamp")

        actual_times = selected["time_key"].tolist()

        if actual_times != EXACT_TIMES:
            raise RuntimeError(
                f"{code} {date} 不符合 Exact 24 Bars。\n"
                f"Expected={EXACT_TIMES}\n"
                f"Actual={actual_times}"
            )

        values = selected[
            EXPECTED_INTRADAY_FEATURES
        ].to_numpy(dtype=np.float64)

        if values.shape != (24, 24):
            raise RuntimeError(
                f"{code} {date} Sequence Shape 錯誤：{values.shape}"
            )

        if not np.isfinite(values).all():
            raise RuntimeError(
                f"{code} {date} Intraday Feature 含非有限值。"
            )

        sequences.append(values.astype(np.float32))

    result = np.asarray(sequences, dtype=np.float32)

    if result.shape != (len(eligible), 24, 24):
        raise RuntimeError(
            f"Sequence Tensor Shape 異常：{result.shape}"
        )

    return result


# ============================================================
# Label / Metadata
# ============================================================

def build_labels_and_metadata(
    raw: pd.DataFrame,
    eligible: pd.DataFrame,
    plan: dict[str, Any],
) -> tuple[np.ndarray, pd.DataFrame]:

    daily = _daily_market(raw).copy()

    daily["stock_code"] = daily["stock_code"].astype(str)
    daily["date_key"] = pd.to_datetime(
        daily["trade_date"]
    ).dt.date.astype(str)

    commission = float(plan["trading"]["commission_rate"])
    tax = float(plan["trading"]["transaction_tax_rate"])

    labels: list[int] = []
    metadata: list[dict[str, Any]] = []

    daily_by_stock = {}

    for code, group in daily.groupby("stock_code", sort=False):
        g = group.sort_values("trade_date").reset_index(drop=True)
        positions = {
            str(date): index
            for index, date in enumerate(
                pd.to_datetime(g["trade_date"]).dt.date.astype(str)
            )
        }
        daily_by_stock[str(code)] = (g, positions)

    for row in eligible.itertuples(index=False):

        code = str(row.stock_code)
        signal_date = str(row.decision_date)

        if code not in daily_by_stock:
            raise RuntimeError(f"找不到股票 Daily Data：{code}")

        days, positions = daily_by_stock[code]

        if signal_date not in positions:
            raise RuntimeError(
                f"找不到 Signal Date：{code} {signal_date}"
            )

        i = positions[signal_date]

        if i + 3 >= len(days):
            raise RuntimeError(
                f"{code} {signal_date} 沒有完整未來三個交易日。"
            )

        entry_row = days.iloc[i + 1]
        target_row = days.iloc[i + 3]

        entry_open = float(entry_row["open"])
        target_close = float(target_row["close"])

        future_net_return = (
            target_close * (1.0 - commission - tax)
            / (entry_open * (1.0 + commission))
            - 1.0
        )

        if future_net_return >= 0.01:
            label = 2       # BUY
        elif future_net_return <= -0.01:
            label = 0       # SELL
        else:
            label = 1       # HOLD

        # 如果 Preflight CSV 已經有 future_net_return，
        # 再做一次一致性確認
        if hasattr(row, "future_net_return"):
            old_value = getattr(row, "future_net_return")

            if pd.notna(old_value):
                if abs(float(old_value) - future_net_return) > 1e-8:
                    raise RuntimeError(
                        f"{code} {signal_date} future_net_return "
                        f"與 Phase B Manifest 不一致。"
                    )

        labels.append(label)

        metadata.append({
            "stock_code": code,
            "signal_date": signal_date,
            "decision_date": signal_date,
            "entry_date": str(
                pd.to_datetime(entry_row["trade_date"]).date()
            ),
            "target_date": str(
                pd.to_datetime(target_row["trade_date"]).date()
            ),
            "entry_open": entry_open,
            "target_close": target_close,
            "future_net_return": future_net_return,
        })

    return (
        np.asarray(labels, dtype=np.int64),
        pd.DataFrame(metadata),
    )


# ============================================================
# GRU 原本 Volume Context
# ============================================================

def build_volume_context(
    featured: pd.DataFrame,
    eligible: pd.DataFrame,
) -> pd.DataFrame:

    daily = build_daily_stock_features(featured).copy()

    daily["stock_code"] = daily["stock_code"].astype(str)
    daily["decision_date"] = pd.to_datetime(
        daily["trade_date"]
    ).dt.date.astype(str)

    context = daily[
        [
            "stock_code",
            "decision_date",
            "volume_trend_5d",
            "volume_trend_20d",
        ]
    ].copy()

    merged = eligible[
        ["stock_code", "decision_date"]
    ].merge(
        context,
        on=["stock_code", "decision_date"],
        how="left",
        validate="one_to_one",
    )

    values = merged[
        GRU_BASE_CONTEXT
    ].to_numpy(dtype=np.float64)

    if not np.isfinite(values).all():
        bad = merged.loc[
            ~np.isfinite(values).all(axis=1),
            ["stock_code", "decision_date"],
        ]
        raise RuntimeError(
            "GRU 原本 Volume Context 有非有限值：\n"
            + bad.to_string(index=False)
        )

    return merged


# ============================================================
# PreparedData
# ============================================================

def make_raw_prepared_data(
    X: np.ndarray,
    y: np.ndarray,
    metadata: pd.DataFrame,
    context_values: np.ndarray,
    context_names: list[str],
    mask: np.ndarray,
    industry: str,
    model_name: str,
) -> PreparedData:

    selected_X = X[mask]
    selected_y = y[mask]

    selected_meta = (
        metadata.loc[mask]
        .reset_index(drop=True)
    )

    selected_context = (
        context_values[mask]
        .astype(np.float32)
    )

    if not np.isfinite(selected_X).all():
        raise RuntimeError(
            f"{industry}/{model_name} X 含非有限值。"
        )

    if not np.isfinite(selected_context).all():
        raise RuntimeError(
            f"{industry}/{model_name} Context 含非有限值。"
        )

    return PreparedData(
        X={
            "train": selected_X.astype(np.float32),
        },
        y={
            "train": selected_y.astype(np.int64),
        },
        metadata={
            "train": selected_meta,
        },

        feature_names=list(EXPECTED_INTRADAY_FEATURES),

        # 這裡只是 Raw Source Placeholder；
        # 真正 scaler 會在每個 Fold Training 內 fit。
        scaler_mean=np.zeros(
            len(EXPECTED_INTRADAY_FEATURES),
            dtype=np.float64,
        ),
        scaler_scale=np.ones(
            len(EXPECTED_INTRADAY_FEATURES),
            dtype=np.float64,
        ),

        audit={
            "loaded_roles": ["train"],
            "scaler_fit_on": None,
            "workflow_stage": "context_round1_candidate_raw",
            "industry": industry,
            "model": model_name,
            "training_only": True,
            "common_sample_alignment": True,
        },

        context_X={
            "train": selected_context,
        },

        context_feature_names=list(context_names),

        context_scaler_mean=None,
        context_scaler_scale=None,
    )


# ============================================================
# Metrics
# ============================================================

def safe_div(a: float, b: float) -> float:
    return float(a / b) if b else 0.0


def add_direction_metrics(
    metrics: dict[str, Any],
) -> dict[str, Any]:

    output = dict(metrics)

    cm = np.asarray(
        metrics["confusion_matrix"],
        dtype=float,
    )

    # class:
    # 0 SELL
    # 1 HOLD
    # 2 BUY

    buy_tp = cm[2, 2]
    buy_pred = cm[:, 2].sum()
    buy_actual = cm[2, :].sum()

    sell_tp = cm[0, 0]
    sell_pred = cm[:, 0].sum()
    sell_actual = cm[0, :].sum()

    samples = float(metrics["samples"])

    output["buy_precision"] = safe_div(
        buy_tp,
        buy_pred,
    )

    output["buy_recall"] = safe_div(
        buy_tp,
        buy_actual,
    )

    output["buy_coverage"] = safe_div(
        buy_pred,
        samples,
    )

    output["buy_prediction_count"] = int(
        buy_pred
    )

    output["sell_precision"] = safe_div(
        sell_tp,
        sell_pred,
    )

    output["sell_recall"] = safe_div(
        sell_tp,
        sell_actual,
    )

    output["sell_coverage"] = safe_div(
        sell_pred,
        samples,
    )

    return output


def stats(
    values: list[float],
) -> dict[str, Any]:

    array = np.asarray(
        [
            value
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
        "mean": float(array.mean()),
        "standard_deviation": (
            float(array.std(ddof=1))
            if len(array) > 1
            else 0.0
        ),
        "worst": float(array.min()),
        "available_folds": int(len(array)),
    }


def summarize_folds(
    folds: list[dict[str, Any]],
) -> dict[str, Any]:

    base = aggregate_fold_metrics(folds)

    for name in [
        "buy_precision",
        "buy_recall",
        "buy_coverage",
        "sell_precision",
        "sell_recall",
        "sell_coverage",
    ]:
        base[name] = stats([
            float(item[name])
            for item in folds
        ])

    return base


# ============================================================
# GRU Inner Chronological Validation
# ============================================================

def select_gru_epochs(
    raw_data: PreparedData,
    outer_fold_spec: dict[str, Any],
) -> tuple[int, dict[str, Any]]:

    fold_number = int(
        outer_fold_spec["fold"]
    )

    if fold_number not in GRU_INNER_START:
        raise RuntimeError(
            f"沒有 Fold {fold_number} 的 GRU Inner Split。"
        )

    inner_validation_start = pd.Timestamp(
        GRU_INNER_START[fold_number]
    )

    inner_train_end = (
        inner_validation_start
        - pd.Timedelta(days=1)
    )

    inner_spec = {
        "fold": fold_number,
        "train_start": outer_fold_spec["train_start"],
        "train_end": inner_train_end.strftime("%Y-%m-%d"),
        "validation_start": inner_validation_start.strftime("%Y-%m-%d"),
        "validation_end": outer_fold_spec["train_end"],
    }

    inner_fold = build_walk_forward_fold(
        raw_data,
        inner_spec,
    )

    _, metrics, _ = run_multiclass_model(
        "gru",
        inner_fold,
        MODEL_PARAMS["gru"],
        evaluation_splits=("validation",),
    )

    selected_epochs = int(
        metrics["epochs_completed"]
    )

    if not 1 <= selected_epochs <= int(
        MODEL_PARAMS["gru"]["max_epochs"]
    ):
        raise RuntimeError(
            f"GRU selected_epochs 異常：{selected_epochs}"
        )

    audit = {
        "selected_epochs": selected_epochs,
        "inner_validation_start":
            inner_spec["validation_start"],
        "inner_validation_end":
            inner_spec["validation_end"],
        "inner_train_end":
            inner_spec["train_end"],
        "inner_best_validation_loss":
            metrics.get("best_validation_loss"),
    }

    return selected_epochs, audit


# ============================================================
# Progress
# ============================================================

def load_completed() -> set[tuple[str, str, int]]:

    completed: set[
        tuple[str, str, int]
    ] = set()

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


def load_progress_rows() -> list[dict[str, Any]]:

    if not PROGRESS_PATH.exists():
        return []

    rows = []

    for line in PROGRESS_PATH.read_text(
        encoding="utf-8"
    ).splitlines():

        if line.strip():
            rows.append(json.loads(line))

    return rows


# ============================================================
# 準備資料
# ============================================================

def prepare_candidate_dataset():

    check_project_support()

    config = PREP.load_config(
        CONTEXT_CONFIG
    )

    plan = load_json(
        PLAN_PATH
    )

    protocol = load_walk_forward_protocol(
        PROTOCOL_PATH
    )

    validate_fixed_design(
        config,
        plan,
    )

    eligible_path = (
        ROOT
        / config["output"]["directory"]
        / config["output"]["eligible_samples"]
    )

    if not eligible_path.exists():
        raise RuntimeError(
            "找不到 context_eligible_samples.csv。\n"
            "請先執行：\n"
            "uv run python scripts/run_ml_v1_context_round1.py"
        )

    eligible = pd.read_csv(
        eligible_path,
        dtype={
            "stock_code": str,
        },
    )

    eligible["stock_code"] = (
        eligible["stock_code"]
        .astype(str)
        .str.zfill(4)
    )

    eligible["decision_date"] = (
        pd.to_datetime(
            eligible["decision_date"]
        )
        .dt.date
        .astype(str)
    )

    if len(eligible) != 4532:
        raise RuntimeError(
            f"Expected 4,532 samples，實際 {len(eligible)}。"
        )

    if eligible[
        ["stock_code", "decision_date"]
    ].duplicated().any():
        raise RuntimeError(
            "context_eligible_samples.csv 有重複 Stock-Date。"
        )

    # 9 個 Candidate Context 必須全部存在
    missing_context = sorted(
        set(CONTEXT_CANDIDATE_FEATURES)
        - set(eligible.columns)
    )

    if missing_context:
        raise RuntimeError(
            f"Eligible CSV 缺少 Context：{missing_context}"
        )

    candidate_context = eligible[
        CONTEXT_CANDIDATE_FEATURES
    ].to_numpy(
        dtype=np.float64
    )

    if not np.isfinite(
        candidate_context
    ).all():
        raise RuntimeError(
            "9 個 Candidate Context 含非有限值。"
        )

    # 只載入 2023~2024
    raw, database_hash_before, database_hash_after = (
        PREP.load_training_bars_read_only(
            config
        )
    )

    if database_hash_before != database_hash_after:
        raise RuntimeError(
            "Raw Database 在準備期間被修改。"
        )

    # 建立原本 24 個 Intraday Features
    featured, cleaning_audit = (
        _intraday_features(
            raw,
            EXPECTED_INTRADAY_FEATURES,
        )
    )

    X = build_exact_sequences(
        featured,
        eligible,
    )

    y, metadata = build_labels_and_metadata(
        raw,
        eligible,
        plan,
    )

    # 必須跟 Eligible 順序完全一致
    keys_a = list(zip(
        eligible["stock_code"],
        eligible["decision_date"],
    ))

    keys_b = list(zip(
        metadata["stock_code"],
        metadata["decision_date"],
    ))

    if keys_a != keys_b:
        raise RuntimeError(
            "Eligible 與 Metadata Sample Order 不一致。"
        )

    # GRU 舊有 Context
    volume_context_df = build_volume_context(
        featured,
        eligible,
    )

    volume_context = volume_context_df[
        GRU_BASE_CONTEXT
    ].to_numpy(
        dtype=np.float64
    )

    # Candidate Context：
    # RF / XGB = 新9個
    # GRU = 原2個 + 新9個
    model_context = {
        "random_forest": (
            candidate_context.astype(
                np.float32
            ),
            list(CONTEXT_CANDIDATE_FEATURES),
        ),

        "xgboost": (
            candidate_context.astype(
                np.float32
            ),
            list(CONTEXT_CANDIDATE_FEATURES),
        ),

        "gru": (
            np.concatenate(
                [
                    volume_context,
                    candidate_context,
                ],
                axis=1,
            ).astype(np.float32),

            [
                *GRU_BASE_CONTEXT,
                *CONTEXT_CANDIDATE_FEATURES,
            ],
        ),
    }

    # Audit
    audit = {
        "status": "READY_FOR_CANDIDATE_WALK_FORWARD",
        "samples": int(len(eligible)),
        "sequence_shape": list(X.shape),
        "label_counts": {
            "SELL": int((y == 0).sum()),
            "HOLD": int((y == 1).sum()),
            "BUY": int((y == 2).sum()),
        },
        "candidate_context_features":
            list(CONTEXT_CANDIDATE_FEATURES),
        "rf_context_count":
            model_context["random_forest"][0].shape[1],
        "xgb_context_count":
            model_context["xgboost"][0].shape[1],
        "gru_context_count":
            model_context["gru"][0].shape[1],
        "gru_context_features":
            model_context["gru"][1],
        "database_sha256_before":
            database_hash_before,
        "database_sha256_after":
            database_hash_after,
        "database_modified":
            database_hash_before
            != database_hash_after,
        "cleaning_audit":
            cleaning_audit,
        "training_period":
            ["2023-01-01", "2024-12-31"],
        "controlled_validation_used": False,
        "development_used": False,
        "holdout_used": False,
        "final_oos_used": False,
        "loso_run": False,
        "hyperparameter_search": False,
        "threshold_search": False,
    }

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    DATASET_AUDIT.write_text(
        json.dumps(
            audit,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return (
        config,
        plan,
        protocol,
        eligible,
        X,
        y,
        metadata,
        model_context,
        audit,
    )


# ============================================================
# Walk-forward
# ============================================================

def run_candidate_walk_forward(
    industry_filter: str | None = None,
    model_filter: str | None = None,
) -> None:

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
    ) = prepare_candidate_dataset()

    completed = load_completed()

    folds = protocol[
        "walk_forward"
    ]["folds"]

    for industry, stocks in INDUSTRIES.items():

        if (
            industry_filter
            and industry != industry_filter
        ):
            continue

        mask = metadata[
            "stock_code"
        ].isin(
            stocks
        ).to_numpy()

        print()
        print("=" * 70)
        print(
            f"Industry：{industry}"
        )
        print(
            f"Samples：{int(mask.sum())}"
        )
        print("=" * 70)

        for model_name in MODELS:

            if (
                model_filter
                and model_name != model_filter
            ):
                continue

            context_values, context_names = (
                model_context[model_name]
            )

            raw_data = make_raw_prepared_data(
                X=X,
                y=y,
                metadata=metadata,
                context_values=context_values,
                context_names=context_names,
                mask=mask,
                industry=industry,
                model_name=model_name,
            )

            print()
            print(
                f"[{industry}] "
                f"{model_name} "
                f"Context={len(context_names)}"
            )

            for fold_spec in folds:

                fold_number = int(
                    fold_spec["fold"]
                )

                progress_key = (
                    industry,
                    model_name,
                    fold_number,
                )

                if progress_key in completed:
                    print(
                        f"  Fold {fold_number}："
                        f"已完成，跳過。"
                    )
                    continue

                print(
                    f"  Fold {fold_number}：開始"
                )

                outer_fold = build_walk_forward_fold(
                    raw_data,
                    fold_spec,
                )

                gru_audit = None
                fixed_epochs = None

                if model_name == "gru":

                    fixed_epochs, gru_audit = (
                        select_gru_epochs(
                            raw_data,
                            fold_spec,
                        )
                    )

                    print(
                        "    GRU Inner Validation "
                        f"選擇 Epoch={fixed_epochs}"
                    )

                _, model_metrics, _ = (
                    run_multiclass_model(
                        model_name,
                        outer_fold,
                        MODEL_PARAMS[
                            model_name
                        ],
                        fixed_epochs=fixed_epochs,
                        evaluation_splits=(
                            "validation",
                        ),
                    )
                )

                validation = add_direction_metrics(
                    model_metrics[
                        "splits"
                    ]["validation"]
                )

                item = {
                    "experiment":
                        "context_candidate",
                    "industry":
                        industry,
                    "model":
                        model_name,
                    "fold":
                        fold_number,
                    "train_samples":
                        int(
                            len(
                                outer_fold.X[
                                    "train"
                                ]
                            )
                        ),
                    "validation_samples":
                        int(
                            len(
                                outer_fold.X[
                                    "validation"
                                ]
                            )
                        ),
                    "context_feature_count":
                        len(context_names),
                    "context_features":
                        context_names,
                    "selected_epochs":
                        fixed_epochs,
                    "gru_inner":
                        gru_audit,
                    "metrics":
                        validation,
                    "fold_audit":
                        outer_fold.audit,
                }

                append_progress(
                    item
                )

                completed.add(
                    progress_key
                )

                print(
                    f"    Macro F1 = "
                    f"{validation['macro_f1']:.4f}"
                )

                auc = validation.get(
                    "macro_roc_auc_ovr"
                )

                print(
                    "    Macro AUC = "
                    + (
                        f"{auc:.4f}"
                        if auc is not None
                        else "N/A"
                    )
                )

                print(
                    f"    BUY Precision = "
                    f"{validation['buy_precision']:.4f}"
                )

                print(
                    f"    BUY Recall = "
                    f"{validation['buy_recall']:.4f}"
                )

                print(
                    f"    BUY Coverage = "
                    f"{validation['buy_coverage']:.4f}"
                )

                gc.collect()

    build_final_report(
        dataset_audit
    )


# ============================================================
# 最終輸出
# ============================================================

def build_final_report(
    dataset_audit: dict[str, Any],
) -> None:

    rows = load_progress_rows()

    if not rows:
        print(
            "目前沒有任何訓練結果。"
        )
        return

    summaries: dict[str, Any] = {}

    for industry in INDUSTRIES:

        summaries[industry] = {}

        for model_name in MODELS:

            model_rows = [
                row
                for row in rows
                if row["industry"]
                == industry
                and row["model"]
                == model_name
            ]

            if not model_rows:
                continue

            fold_metrics = [
                {
                    "fold": row["fold"],
                    **row["metrics"],
                }
                for row in sorted(
                    model_rows,
                    key=lambda x: x["fold"],
                )
            ]

            summaries[
                industry
            ][
                model_name
            ] = summarize_folds(
                fold_metrics
            )

    report = {
        "experiment":
            "ML V1 Improvement Round 1 Context Candidate",
        "stage":
            "CANDIDATE_WALK_FORWARD_ONLY",
        "dataset_audit":
            dataset_audit,
        "design": {
            "baseline":
                "Existing Phase B Fixed ±1% results",
            "candidate":
                "Phase B inputs + preregistered 9 Context Features",
            "technology":
                TECHNOLOGY,
            "financial":
                FINANCIAL,
            "models":
                MODEL_PARAMS,
            "training_only":
                "2023-01-01..2024-12-31",
            "label":
                "Fixed ±1%",
            "classes":
                ["SELL", "HOLD", "BUY"],
            "horizon_sessions":
                3,
            "decision_time":
                "13:30",
            "sequence":
                "Exact 24 bars 11:35..13:30",
            "loso_run":
                False,
            "trading_return_used":
                False,
            "parameter_search":
                False,
            "probability_threshold_search":
                False,
        },
        "summaries":
            summaries,
        "completed_runs":
            len(rows),
        "expected_full_walk_forward_runs":
            24,
    }

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

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
            "model":
                row["model"],
            "fold":
                row["fold"],
            "train_samples":
                row["train_samples"],
            "validation_samples":
                row["validation_samples"],
            "context_feature_count":
                row["context_feature_count"],
            "macro_f1":
                m["macro_f1"],
            "macro_auc":
                m.get(
                    "macro_roc_auc_ovr"
                ),
            "buy_precision":
                m["buy_precision"],
            "buy_recall":
                m["buy_recall"],
            "buy_coverage":
                m["buy_coverage"],
            "sell_precision":
                m["sell_precision"],
            "sell_recall":
                m["sell_recall"],
            "sell_coverage":
                m["sell_coverage"],
            "selected_epochs":
                row.get(
                    "selected_epochs"
                ),
        })

    pd.DataFrame(
        flat_rows
    ).sort_values(
        [
            "industry",
            "model",
            "fold",
        ]
    ).to_csv(
        RESULT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 70)
    print("目前結果已輸出")
    print("=" * 70)
    print(RESULT_JSON)
    print(RESULT_CSV)
    print()
    print(
        f"Completed Runs：{len(rows)} / 24"
    )

    if len(rows) == 24:
        print(
            "Candidate Walk-forward 已全部完成。"
        )
        print(
            "現在先停止，不要跑 LOSO。"
        )
        print(
            "把 candidate_walk_forward_results.json "
            "傳給 ChatGPT 分析。"
        )


# ============================================================
# CLI
# ============================================================

def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只檢查 Dataset，不訓練模型",
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
            _eligible,
            _X,
            _y,
            _metadata,
            _model_context,
            audit,
        ) = prepare_candidate_dataset()

        print()
        print("=" * 70)
        print("PREPARE ONLY：PASS")
        print("=" * 70)
        print(
            json.dumps(
                audit,
                ensure_ascii=False,
                indent=2,
            )
        )

        print()
        print(
            "沒有訓練任何模型。"
        )

        return 0

    run_candidate_walk_forward(
        industry_filter=
            args.industry,
        model_filter=
            args.model,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
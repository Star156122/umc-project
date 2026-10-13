"""
ML V1 Improvement Round 2
Aligned Baseline vs Temporal 72-timestep Candidate

目前階段：
- 只準備資料
- 不訓練模型
- 不跑 Walk-forward
- 不跑 LOSO

比較：
Baseline = Day T exact 24 bars
Round 2  = T-2 / T-1 / T each exact 24 bars = 72 timesteps

兩者必須使用完全相同的 4447 stock-date samples。
"""

from __future__ import annotations

import argparse
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
# Project imports
# ============================================================

from ml.ml_v1_sequence import (
    SEQUENCE_COMPLETE,
    select_exact_sequence,
)

from ml.research_features import build_daily_stock_features

from ml.temporal_round2 import (
    build_three_session_intersection,
)

from ml.trading_pipeline import (
    _daily_market,
    _intraday_features,
)

from scripts.audit_ml_v1_labels import load_training_kbars
from scripts.run_ml_v1_phase_b import load_phase_b_config


# ============================================================
# Paths
# ============================================================

CONFIG_PATH = (
    ROOT
    / "configs"
    / "ml_v1_temporal_round2_training.json"
)

OUTPUT_DIR = (
    ROOT
    / "exports"
    / "ml_v1_temporal_round2_training_20261012"
)

PREPARE_AUDIT_PATH = (
    OUTPUT_DIR
    / "round2_training_prepare_audit.json"
)


# ============================================================
# Fixed design
# ============================================================

EXPECTED_COMMON_SAMPLES = 4447

GRU_STATIC_FEATURES = [
    "volume_trend_5d",
    "volume_trend_20d",
]

EXPECTED_GRU_INNER_START = {
    "1": "2023-07-20",
    "2": "2023-10-26",
    "3": "2024-01-22",
    "4": "2024-05-09",
}


# ============================================================
# Config
# ============================================================

def load_training_config() -> dict[str, Any]:

    config = json.loads(
        CONFIG_PATH.read_text(
            encoding="utf-8"
        )
    )

    if (
        config["status"]
        != "ROUND2_APPROVED_FOR_TRAINING"
    ):
        raise RuntimeError(
            "Round 2 Training 尚未正式核准。"
        )

    if (
        config["candidate"]["design_status"]
        != "APPROVED_3_SESSION_72_TIMESTEPS"
    ):
        raise RuntimeError(
            "Round 2 72-timestep 設計尚未核准。"
        )

    if config["candidate"]["historical_sessions"] != 3:
        raise RuntimeError(
            "Round 2 historical_sessions 必須是 3。"
        )

    if config["candidate"]["timesteps"] != 72:
        raise RuntimeError(
            "Round 2 timesteps 必須是 72。"
        )

    if (
        config["fixed_contract"]
        ["context_round1_features_used"]
        is not False
    ):
        raise RuntimeError(
            "禁止使用 Context Round 1 features。"
        )

    if (
        config["validation"]
        ["gru_inner_validation_start_by_fold"]
        != EXPECTED_GRU_INNER_START
    ):
        raise RuntimeError(
            "GRU Inner Validation 日期不一致。"
        )

    if (
        int(
            config["sample_alignment"]
            ["confirmed_common_samples"]
        )
        != EXPECTED_COMMON_SAMPLES
    ):
        raise RuntimeError(
            "Common sample count 不是 4447。"
        )

    return config


# ============================================================
# Common samples
# ============================================================

def load_common_samples(
    config: dict[str, Any],
) -> pd.DataFrame:

    path = (
        ROOT
        / config["sample_alignment"]
        ["common_samples_file"]
    )

    if not path.exists():
        raise RuntimeError(
            f"找不到 Common Sample 檔案：{path}"
        )

    common = pd.read_csv(
        path,
        dtype={"stock_code": str},
    )

    required = {
        "stock_code",
        "decision_date",
    }

    if not required.issubset(common.columns):
        raise RuntimeError(
            "Common Sample 缺少 stock_code / decision_date。"
        )

    common["stock_code"] = (
        common["stock_code"]
        .astype(str)
        .str.zfill(4)
    )

    common["decision_date"] = (
        pd.to_datetime(
            common["decision_date"]
        )
        .dt.date
        .astype(str)
    )

    if common[
        ["stock_code", "decision_date"]
    ].duplicated().any():
        raise RuntimeError(
            "Common Sample 存在重複 stock-date key。"
        )

    if len(common) != EXPECTED_COMMON_SAMPLES:
        raise RuntimeError(
            f"Common Samples 應為 4447，"
            f"實際 {len(common)}。"
        )

    return common.reset_index(drop=True)


# ============================================================
# Baseline 24-bar tensor
# ============================================================

def build_baseline_sequences(
    featured: pd.DataFrame,
    common: pd.DataFrame,
    feature_names: list[str],
) -> np.ndarray:

    work = featured.copy()

    work["stock_code"] = (
        work["stock_code"]
        .astype(str)
        .str.zfill(4)
    )

    work["trade_date"] = (
        pd.to_datetime(
            work["trade_date"]
        )
        .dt.date
        .astype(str)
    )

    grouped = {
        (str(code), str(day)): group
        for (code, day), group
        in work.groupby(
            ["stock_code", "trade_date"],
            sort=False,
        )
    }

    sequences: list[np.ndarray] = []

    for row in common.itertuples(index=False):

        key = (
            str(row.stock_code),
            str(row.decision_date),
        )

        group = grouped.get(key)

        if group is None:
            raise RuntimeError(
                f"Baseline 找不到行情：{key}"
            )

        selected, eligibility = (
            select_exact_sequence(group)
        )

        if (
            selected is None
            or eligibility.status
            != SEQUENCE_COMPLETE
        ):
            raise RuntimeError(
                f"Baseline 24-bar 不完整：{key}"
            )

        values = selected[
            feature_names
        ].to_numpy(
            dtype=np.float32
        )

        if values.shape != (
            24,
            len(feature_names),
        ):
            raise RuntimeError(
                f"Baseline Shape 異常："
                f"{key} {values.shape}"
            )

        if not np.isfinite(values).all():
            raise RuntimeError(
                f"Baseline Feature 非有限值：{key}"
            )

        sequences.append(values)

    result = np.asarray(
        sequences,
        dtype=np.float32,
    )

    expected_shape = (
        EXPECTED_COMMON_SAMPLES,
        24,
        len(feature_names),
    )

    if result.shape != expected_shape:
        raise RuntimeError(
            f"Baseline Tensor Shape 異常："
            f"{result.shape}"
        )

    return result


# ============================================================
# Round 2 72-bar tensor
# ============================================================

def build_round2_sequences(
    featured: pd.DataFrame,
    common: pd.DataFrame,
    feature_names: list[str],
) -> np.ndarray:

    result = build_three_session_intersection(
        baseline_samples=common[
            ["stock_code", "decision_date"]
        ],
        featured=featured,
        feature_names=feature_names,
    )

    if len(result.common_samples) != EXPECTED_COMMON_SAMPLES:
        raise RuntimeError(
            "Round 2 重新建立後不再是 4447 samples。"
        )

    sequence_rows: list[np.ndarray] = []

    for row in common.itertuples(index=False):

        key = (
            str(row.stock_code),
            str(row.decision_date),
        )

        if key not in result.sequences:
            raise RuntimeError(
                f"Round 2 缺少 Sequence：{key}"
            )

        value = result.sequences[key]

        if value.shape != (
            72,
            len(feature_names),
        ):
            raise RuntimeError(
                f"Round 2 Shape 異常："
                f"{key} {value.shape}"
            )

        sequence_rows.append(value)

    X = np.asarray(
        sequence_rows,
        dtype=np.float32,
    )

    expected_shape = (
        EXPECTED_COMMON_SAMPLES,
        72,
        len(feature_names),
    )

    if X.shape != expected_shape:
        raise RuntimeError(
            f"Round 2 Tensor Shape 異常：{X.shape}"
        )

    if not np.isfinite(X).all():
        raise RuntimeError(
            "Round 2 Tensor 含非有限值。"
        )

    return X


# ============================================================
# Labels / metadata
# ============================================================

def build_labels_and_metadata(
    raw: pd.DataFrame,
    common: pd.DataFrame,
    research: dict[str, Any],
    config: dict[str, Any],
) -> tuple[np.ndarray, pd.DataFrame]:

    daily = _daily_market(raw).copy()

    daily["stock_code"] = (
        daily["stock_code"]
        .astype(str)
        .str.zfill(4)
    )

    daily["date_key"] = (
        pd.to_datetime(
            daily["trade_date"]
        )
        .dt.date
        .astype(str)
    )

    commission = float(
        research["target"]["commission_rate"]
    )

    tax = float(
        research["target"]["transaction_tax_rate"]
    )
    sell_threshold = float(
        config["fixed_contract"]
        ["label_thresholds"]["SELL"]
    )

    buy_threshold = float(
        config["fixed_contract"]
        ["label_thresholds"]["BUY"]
    )

    daily_by_stock: dict[
        str,
        tuple[pd.DataFrame, dict[str, int]],
    ] = {}

    for code, group in daily.groupby(
        "stock_code",
        sort=False,
    ):

        g = (
            group
            .sort_values("trade_date")
            .reset_index(drop=True)
        )

        positions = {
            str(day): index
            for index, day
            in enumerate(g["date_key"])
        }

        daily_by_stock[str(code)] = (
            g,
            positions,
        )

    labels: list[int] = []
    metadata: list[dict[str, Any]] = []

    for row in common.itertuples(index=False):

        code = str(row.stock_code)
        decision = str(row.decision_date)

        if code not in daily_by_stock:
            raise RuntimeError(
                f"找不到 Daily Data：{code}"
            )

        days, positions = (
            daily_by_stock[code]
        )

        if decision not in positions:
            raise RuntimeError(
                f"找不到 Decision Date："
                f"{code} {decision}"
            )

        index = positions[decision]

        if index + 3 >= len(days):
            raise RuntimeError(
                f"未來 3 sessions 不完整："
                f"{code} {decision}"
            )

        entry = days.iloc[index + 1]
        target = days.iloc[index + 3]

        entry_open = float(entry["open"])
        target_close = float(target["close"])

        future_net_return = (
            target_close
            * (1.0 - commission - tax)
            / (
                entry_open
                * (1.0 + commission)
            )
            - 1.0
        )

        if future_net_return >= buy_threshold:
            label = 2
        elif future_net_return <= sell_threshold:
            label = 0
        else:
            label = 1

        labels.append(label)

        metadata.append({
            "stock_code": code,
            "signal_date": decision,
            "decision_date": decision,
            "entry_date": str(
                pd.to_datetime(
                    entry["trade_date"]
                ).date()
            ),
            "target_date": str(
                pd.to_datetime(
                    target["trade_date"]
                ).date()
            ),
            "entry_open": entry_open,
            "target_close": target_close,
            "future_net_return":
                future_net_return,
        })

    y = np.asarray(
        labels,
        dtype=np.int64,
    )

    meta = pd.DataFrame(metadata)

    if len(y) != EXPECTED_COMMON_SAMPLES:
        raise RuntimeError(
            "Label count 不是 4447。"
        )

    expected_keys = list(zip(
        common["stock_code"],
        common["decision_date"],
    ))

    actual_keys = list(zip(
        meta["stock_code"],
        meta["decision_date"],
    ))

    if expected_keys != actual_keys:
        raise RuntimeError(
            "Label / Common Sample Order 不一致。"
        )

    return y, meta


# ============================================================
# GRU original volume context
# ============================================================

def build_gru_static_context(
    featured: pd.DataFrame,
    common: pd.DataFrame,
) -> np.ndarray:

    daily = (
        build_daily_stock_features(
            featured
        )
        .copy()
    )

    daily["stock_code"] = (
        daily["stock_code"]
        .astype(str)
        .str.zfill(4)
    )

    daily["decision_date"] = (
        pd.to_datetime(
            daily["trade_date"]
        )
        .dt.date
        .astype(str)
    )

    merged = common[
        ["stock_code", "decision_date"]
    ].merge(
        daily[
            [
                "stock_code",
                "decision_date",
                *GRU_STATIC_FEATURES,
            ]
        ],
        on=[
            "stock_code",
            "decision_date",
        ],
        how="left",
        validate="one_to_one",
    )

    values = merged[
        GRU_STATIC_FEATURES
    ].to_numpy(
        dtype=np.float32
    )

    if values.shape != (
        EXPECTED_COMMON_SAMPLES,
        2,
    ):
        raise RuntimeError(
            f"GRU Static Context Shape 異常："
            f"{values.shape}"
        )

    if not np.isfinite(values).all():
        raise RuntimeError(
            "GRU volume_trend Context 有非有限值。"
        )

    return values


# ============================================================
# Prepare
# ============================================================

def prepare_dataset() -> dict[str, Any]:

    config = load_training_config()

    phase, research, plan = (
        load_phase_b_config()
    )

    # ------------------------------
    # Guardrails
    # ------------------------------

    if (
        research["data"]["start"]
        != "2023-01-01"
        or research["data"]["end"]
        != "2024-12-31"
    ):
        raise RuntimeError(
            "Training period 被修改。"
        )

    if (
        research["prediction"]
        ["decision_time"]
        != "13:30:00"
    ):
        raise RuntimeError(
            "Decision Time 被修改。"
        )

    if (
        int(
            research["prediction"]
            ["prediction_horizon_sessions"]
        )
        != 3
    ):
        raise RuntimeError(
            "Prediction Horizon 被修改。"
        )

    feature_names = list(
        research["feature_policy"]
        ["core_features"]
    )

    if len(feature_names) != 24:
        raise RuntimeError(
            "Feature Set A 不是 24 features。"
        )

    common = load_common_samples(config)

    # ------------------------------
    # Training-only raw data
    # ------------------------------

    raw = load_training_kbars(
        research
    )

    raw = raw.loc[
        raw["datetime"].dt.dayofweek < 5
    ].copy()

    raw["trade_date"] = (
        raw["datetime"].dt.date
    )

    # ------------------------------
    # Feature Set A
    # ------------------------------

    featured, cleaning = (
        _intraday_features(
            raw,
            feature_names,
        )
    )

    # ------------------------------
    # Baseline / Round 2 tensors
    # ------------------------------

    X_baseline = build_baseline_sequences(
        featured,
        common,
        feature_names,
    )

    X_round2 = build_round2_sequences(
        featured,
        common,
        feature_names,
    )

    # ------------------------------
    # Same labels
    # ------------------------------

    y, metadata = (
        build_labels_and_metadata(
            raw,
            common,
            research,
            config,
        )
    )

    # ------------------------------
    # Same GRU static volume2
    # ------------------------------

    gru_static = (
        build_gru_static_context(
            featured,
            common,
        )
    )

    # ------------------------------
    # Final checks
    # ------------------------------

    if not (
        len(X_baseline)
        == len(X_round2)
        == len(y)
        == len(metadata)
        == len(gru_static)
        == EXPECTED_COMMON_SAMPLES
    ):
        raise RuntimeError(
            "Baseline / Round2 / Label "
            "/ Metadata / GRU Context 數量不一致。"
        )

    class_counts = {
        "SELL": int((y == 0).sum()),
        "HOLD": int((y == 1).sum()),
        "BUY": int((y == 2).sum()),
    }

    per_stock = (
        metadata
        .groupby("stock_code")
        .size()
        .astype(int)
        .sort_index()
        .to_dict()
    )

    audit = {
        "status":
            "READY_FOR_ALIGNED_WALK_FORWARD",

        "experiment":
            "ML V1 Temporal Round 2",

        "samples":
            EXPECTED_COMMON_SAMPLES,

        "sample_keys_identical":
            True,

        "baseline_sequence_shape":
            list(X_baseline.shape),

        "round2_sequence_shape":
            list(X_round2.shape),

        "rf_xgb_baseline_flat_features":
            24 * 24,

        "rf_xgb_round2_flat_features":
            72 * 24,

        "gru_static_context_shape":
            list(gru_static.shape),

        "gru_static_features":
            list(GRU_STATIC_FEATURES),

        "label_counts":
            class_counts,

        "per_stock_samples":
            per_stock,

        "core_feature_count":
            len(feature_names),

        "core_features":
            feature_names,

        "cleaning":
            cleaning,

        "label_unchanged":
            True,

        "horizon_unchanged":
            True,

        "decision_time_unchanged":
            True,

        "universe_unchanged":
            True,

        "context_round1_used":
            False,

        "year_2025_used":
            False,

        "year_2026_used":
            False,

        "models_trained":
            False,

        "walk_forward_run":
            False,

        "loso_run":
            False,

        "gru_fold4_inner_validation_start":
            "2024-05-09",
    }

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    PREPARE_AUDIT_PATH.write_text(
        json.dumps(
            audit,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return audit


# ============================================================
# CLI
# ============================================================

def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--prepare-only",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.prepare_only:
        raise SystemExit(
            "目前只允許 --prepare-only；"
            "Walk-forward Training 尚未加入。"
        )

    audit = prepare_dataset()

    print()
    print("=" * 70)
    print("TEMPORAL ROUND 2 TRAINING PREPARE: PASS")
    print("=" * 70)

    print(
        f"Samples: {audit['samples']}"
    )

    print(
        "Baseline Shape: "
        f"{audit['baseline_sequence_shape']}"
    )

    print(
        "Round2 Shape: "
        f"{audit['round2_sequence_shape']}"
    )

    print(
        "GRU Static Shape: "
        f"{audit['gru_static_context_shape']}"
    )

    print(
        "Labels: "
        f"{audit['label_counts']}"
    )

    print(
        "Fold4 Inner Start: "
        f"{audit['gru_fold4_inner_validation_start']}"
    )

    print(
        f"Audit: {PREPARE_AUDIT_PATH}"
    )

    print()
    print("No model was trained.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""V3 Training 內部的 expanding-window Walk-forward 工具。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.data_pipeline import PreparedData
from ml.data_roles import assert_ml_read_period

LABEL_NAMES = {0: "SELL", 1: "HOLD", 2: "BUY"}


def load_walk_forward_protocol(path: Path) -> dict[str, Any]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("base_plan") != "configs/ml_trading_v3_20261004.json":
        raise ValueError("Walk-forward 只能使用既有 V3 設定。")
    walk = protocol.get("walk_forward", {})
    folds = walk.get("folds", [])
    if walk.get("type") != "expanding_window" or walk.get("shuffle") is not False or len(folds) != 4:
        raise ValueError("V3 必須使用固定四折 expanding-window，且 shuffle=false。")
    training = protocol["training_role"]
    assert_ml_read_period(training["start"], training["end"])
    previous_validation_end: pd.Timestamp | None = None
    for expected_number, fold in enumerate(folds, 1):
        if int(fold["fold"]) != expected_number or fold["train_start"] != training["start"]:
            raise ValueError("Fold 編號必須連續，Training 起點必須固定以形成 expanding-window。")
        train_start = pd.Timestamp(fold["train_start"])
        train_end = pd.Timestamp(fold["train_end"])
        validation_start = pd.Timestamp(fold["validation_start"])
        validation_end = pd.Timestamp(fold["validation_end"])
        assert_ml_read_period(train_start.date(), validation_end.date())
        if not train_start <= train_end < validation_start <= validation_end:
            raise ValueError(f"Fold {expected_number} 沒有保持時間順序。")
        if previous_validation_end is not None and validation_start <= previous_validation_end:
            raise ValueError("Walk-forward Validation 不可互相重疊或倒退。")
        previous_validation_end = validation_end
    if protocol["candidate_selection"].get("automatic_parameter_search") is not False:
        raise ValueError("禁止自動參數搜尋。")
    if protocol["candidate_selection"].get("return_optimization") is not False:
        raise ValueError("禁止依最高報酬選 Candidate。")
    if set(protocol.get("forbidden_roles", [])) != {"holdout", "final_out_of_sample"}:
        raise ValueError("兩個保留角色必須維持 forbidden。")
    return protocol


def _dates(metadata: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    return pd.to_datetime(metadata["signal_date"]), pd.to_datetime(metadata["target_date"])


def build_walk_forward_fold(raw_training: PreparedData, fold: dict[str, Any]) -> PreparedData:
    """從未標準化的 Training 樣本建立單一 Fold，並只以 Fold Training fit scaler。"""
    if raw_training.audit.get("loaded_roles") != ["train"] or raw_training.audit.get("scaler_fit_on") is not None:
        raise ValueError("Walk-forward 輸入必須是只載入 train 的未標準化資料。")
    source_X = raw_training.X["train"]
    source_y = raw_training.y["train"]
    source_meta = raw_training.metadata["train"].reset_index(drop=True)
    signal_dates, target_dates = _dates(source_meta)
    train_start, train_end = pd.Timestamp(fold["train_start"]), pd.Timestamp(fold["train_end"])
    validation_start, validation_end = pd.Timestamp(fold["validation_start"]), pd.Timestamp(fold["validation_end"])

    train_signal = signal_dates.between(train_start, train_end)
    validation_signal = signal_dates.between(validation_start, validation_end)
    train_mask = train_signal & target_dates.between(train_start, train_end)
    validation_mask = validation_signal & target_dates.between(validation_start, validation_end)
    train_indices = np.flatnonzero(train_mask.to_numpy())
    validation_indices = np.flatnonzero(validation_mask.to_numpy())
    if not len(train_indices) or not len(validation_indices):
        raise RuntimeError(f"Fold {fold['fold']} 沒有足夠樣本。")

    feature_count = source_X.shape[2]
    fit_values = source_X[train_indices].reshape(-1, feature_count).astype(np.float64)
    mean = fit_values.mean(axis=0)
    scale = fit_values.std(axis=0)
    scale[scale == 0] = 1.0
    X_train = ((source_X[train_indices] - mean) / scale).astype(np.float32)
    X_validation = ((source_X[validation_indices] - mean) / scale).astype(np.float32)
    context_X = None
    context_mean = None
    context_scale = None
    if raw_training.context_X is not None:
        source_context = raw_training.context_X["train"]
        context_fit = source_context[train_indices].astype(np.float64)
        context_mean = context_fit.mean(axis=0)
        context_scale = context_fit.std(axis=0)
        context_scale[context_scale == 0] = 1.0
        context_X = {
            "train": ((source_context[train_indices] - context_mean) / context_scale).astype(np.float32),
            "validation": ((source_context[validation_indices] - context_mean) / context_scale).astype(np.float32),
        }
    y_train = source_y[train_indices]
    y_validation = source_y[validation_indices]
    train_meta = source_meta.iloc[train_indices].reset_index(drop=True)
    validation_meta = source_meta.iloc[validation_indices].reset_index(drop=True)
    class_distribution = {
        "train": {LABEL_NAMES[label]: int((y_train == label).sum()) for label in LABEL_NAMES},
        "validation": {LABEL_NAMES[label]: int((y_validation == label).sum()) for label in LABEL_NAMES},
    }
    audit = {
        "workflow_stage": "walk_forward_fold",
        "fold": int(fold["fold"]),
        "periods": dict(fold),
        "scaler_fit_on": "fold_train_only",
        "preprocessing_fit_on": "fold_train_only",
        "scaler_fit_samples": int(len(train_indices)),
        "context_scaler_fit_on": "fold_train_only" if context_X is not None else None,
        "context_scaler_fit_samples": int(len(train_indices)) if context_X is not None else 0,
        "class_weight_source": "fold_train_only",
        "train_boundary_targets_removed": int((train_signal & ~train_mask).sum()),
        "validation_boundary_targets_removed": int((validation_signal & ~validation_mask).sum()),
        "samples": {"train": int(len(train_indices)), "validation": int(len(validation_indices))},
        "class_distribution": class_distribution,
        "shuffle": False,
    }
    return PreparedData(
        X={"train": X_train, "validation": X_validation},
        y={"train": y_train, "validation": y_validation},
        metadata={"train": train_meta, "validation": validation_meta},
        feature_names=list(raw_training.feature_names),
        scaler_mean=mean,
        scaler_scale=scale,
        audit=audit,
        context_X=context_X,
        context_feature_names=list(raw_training.context_feature_names),
        context_scaler_mean=context_mean,
        context_scaler_scale=context_scale,
    )


def fold_validation_market(training_market: pd.DataFrame, fold: dict[str, Any]) -> pd.DataFrame:
    dates = pd.to_datetime(training_market["trade_date"])
    mask = dates.between(pd.Timestamp(fold["validation_start"]), pd.Timestamp(fold["validation_end"]))
    return training_market.loc[mask].copy()


def build_full_training_data(raw_training: PreparedData) -> PreparedData:
    """Candidate 決定後，以完整 2023–2024 Training fit 唯一 scaler。"""
    if raw_training.audit.get("loaded_roles") != ["train"] or raw_training.audit.get("scaler_fit_on") is not None:
        raise ValueError("Candidate refit 必須使用只載入 train 的未標準化資料。")
    X = raw_training.X["train"]
    feature_count = X.shape[2]
    flat = X.reshape(-1, feature_count).astype(np.float64)
    mean, scale = flat.mean(axis=0), flat.std(axis=0)
    scale[scale == 0] = 1.0
    context_X = None
    context_mean = None
    context_scale = None
    if raw_training.context_X is not None:
        raw_context = raw_training.context_X["train"].astype(np.float64)
        context_mean, context_scale = raw_context.mean(axis=0), raw_context.std(axis=0)
        context_scale[context_scale == 0] = 1.0
        context_X = {"train": ((raw_context - context_mean) / context_scale).astype(np.float32)}
    return PreparedData(
        X={"train": ((X - mean) / scale).astype(np.float32)},
        y={"train": raw_training.y["train"]},
        metadata={"train": raw_training.metadata["train"].copy()},
        feature_names=list(raw_training.feature_names), scaler_mean=mean, scaler_scale=scale,
        audit={"workflow_stage": "candidate_refit", "loaded_roles": ["train"],
               "scaler_fit_on": "full_training_2023_2024",
               "preprocessing_fit_on": "full_training_2023_2024", "shuffle": False,
               "context_scaler_fit_on": "full_training_2023_2024" if context_X is not None else None},
        context_X=context_X,
        context_feature_names=list(raw_training.context_feature_names),
        context_scaler_mean=context_mean,
        context_scaler_scale=context_scale,
    )


def aggregate_fold_metrics(folds: list[dict[str, Any]]) -> dict[str, Any]:
    if not folds:
        raise ValueError("沒有 Fold 指標可彙總。")
    output: dict[str, Any] = {"completed_folds": len(folds), "folds": folds}
    keys = ["macro_f1", "macro_roc_auc_ovr"]
    for key in keys:
        values = np.asarray([float(item[key]) for item in folds if item.get(key) is not None], dtype=float)
        output[key] = {
            "mean": float(values.mean()) if len(values) else None,
            "standard_deviation": float(values.std(ddof=1)) if len(values) > 1 else 0.0 if len(values) else None,
            "worst": float(values.min()) if len(values) else None,
            "available_folds": int(len(values)),
        }
    for label in LABEL_NAMES.values():
        values = [item["individual_roc_auc_ovr"].get(label) for item in folds]
        usable = np.asarray([float(value) for value in values if value is not None], dtype=float)
        output.setdefault("individual_roc_auc_ovr", {})[label] = {
            "mean": float(usable.mean()) if len(usable) else None,
            "standard_deviation": float(usable.std(ddof=1)) if len(usable) > 1 else 0.0 if len(usable) else None,
            "worst": float(usable.min()) if len(usable) else None,
            "available_folds": int(len(usable)),
        }
    return output


def select_candidate_model(summary_by_model: dict[str, dict[str, Any]], protocol: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """以最差 Fold、平均及穩定度排序；完全不使用報酬率。"""
    required = int(protocol["candidate_selection"]["required_completed_folds"])
    order = protocol["candidate_selection"]["tie_break_model_order"]
    eligible = {
        name: summary for name, summary in summary_by_model.items()
        if int(summary.get("completed_folds", 0)) == required
    }
    if not eligible:
        raise RuntimeError("沒有完成所有 Walk-forward Fold 的模型，不能建立 Candidate。")
    rank = {name: -index for index, name in enumerate(order)}
    def score(item: tuple[str, dict[str, Any]]) -> tuple[float, ...]:
        name, summary = item
        f1 = summary["macro_f1"]
        auc = summary["macro_roc_auc_ovr"]
        safe = lambda value, default=-1.0: default if value is None else float(value)
        return (
            safe(f1["worst"]), safe(f1["mean"]),
            safe(auc["worst"]), safe(auc["mean"]),
            -safe(f1["standard_deviation"], 999.0), -safe(auc["standard_deviation"], 999.0),
            float(rank.get(name, -999)),
        )
    selected_name, selected = max(eligible.items(), key=score)
    decision = {
        "selected_model": selected_name,
        "selection_basis": list(protocol["candidate_selection"]["ordered_criteria"]),
        "return_used_for_selection": False,
        "score_components": score((selected_name, selected)),
    }
    return selected_name, decision

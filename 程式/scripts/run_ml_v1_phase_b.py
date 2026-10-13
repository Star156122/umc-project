"""ML V1 Phase B: Training-only confirmatory label comparison.

The only experimental variable is the label definition.  The script reads the
formal 4,542-row model-eligible manifest, uses the same features/folds/stocks
for both labels, and never evaluates trading returns.
"""
from __future__ import annotations

import hashlib
import html
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.ml_v1_labels import aggregate_daily_market, build_label_frame, eligible_common_samples
from ml.ml_v1_sequence import EXACT_SEQUENCE_TIMES, SEQUENCE_COMPLETE, select_exact_sequence
from ml.models import run_multiclass_model
from ml.research_features import build_daily_stock_features
from ml.trading_pipeline import _intraday_features
from ml.walk_forward import load_walk_forward_protocol
from scripts.audit_ml_v1_labels import load_config as load_research_config, load_training_kbars
from scripts.run_ml_industry_generalization import (
    indices_for_outer_fold,
    make_scaled_data,
    require_three_classes,
    split_gru_inner_validation,
)

CONFIG_PATH = ROOT / "configs/ml_v1_phase_b.json"
OUTPUT = ROOT / "exports/ml_v1_phase_b_20261012"
LABEL_COLUMNS = {"fixed_1pct": "fixed_label", "volatility_k_0_50": "volatility_label"}
LABEL_IDS = {"SELL": 0, "HOLD": 1, "BUY": 2}
CLASS_NAMES = ("SELL", "HOLD", "BUY")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_phase_b_config() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    phase = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    research = load_research_config()
    protocol = load_walk_forward_protocol(ROOT / phase["walk_forward_protocol"])
    if phase["status"] != "AUTHORIZED" or phase["data_role"] != "train":
        raise RuntimeError("Phase B is not authorized for Training-only execution")
    if phase["labels"] != ["fixed_1pct", "volatility_k_0_50"]:
        raise RuntimeError("Phase B may compare only the two preregistered labels")
    if phase["models"] != ["random_forest", "xgboost", "gru"]:
        raise RuntimeError("Phase B model set changed")
    if phase["training_period"] != {"start": "2023-01-01", "end": "2024-12-31"}:
        raise RuntimeError("Phase B may use only 2023-2024 Training")
    if not all(bool(value) for value in phase["forbidden"].values()):
        raise RuntimeError("All Phase B forbidden operations must remain locked")
    if research["prediction"]["sequence_policy"] != phase["sequence_policy"]:
        raise RuntimeError("Sequence policy mismatch")
    if research["prediction"]["missing_sequence_policy"] != phase["missing_sequence_policy"]:
        raise RuntimeError("Missing sequence policy mismatch")
    return phase, research, protocol


def build_raw_inputs(phase: dict[str, Any], research: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = ROOT / phase["eligible_manifest"]
    manifest = pd.read_csv(manifest_path, dtype={"stock_code": str})
    manifest["decision_date"] = manifest["decision_date"].astype(str)
    if len(manifest) != int(phase["expected_sequence_eligible_samples"]):
        raise RuntimeError(
            f"Expected {phase['expected_sequence_eligible_samples']} sequence-eligible rows, found {len(manifest)}"
        )
    if set(manifest["sequence_status"]) != {SEQUENCE_COMPLETE}:
        raise RuntimeError("Manifest contains an ineligible sequence")
    if manifest[["stock_code", "decision_date"]].duplicated().any():
        raise RuntimeError("Duplicate stock-date in eligible manifest")
    manifest_keys = set(zip(manifest["stock_code"], manifest["decision_date"]))

    database = ROOT / research["data"]["database"]
    db_hash_before = sha256(database)
    raw = load_training_kbars(research)
    raw = raw.loc[raw["datetime"].dt.dayofweek < 5].copy()
    raw["trade_date"] = raw["datetime"].dt.date
    featured, cleaning = _intraday_features(raw, research["feature_policy"]["core_features"])

    sequence_lookup: dict[tuple[str, str], np.ndarray] = {}
    nonfinite_rows: list[dict[str, Any]] = []
    for (code, day), group in featured.groupby(["stock_code", "trade_date"], sort=True):
        key = (str(code), str(day))
        if key not in manifest_keys:
            continue
        selected, eligibility = select_exact_sequence(group)
        if selected is None or eligibility.status != SEQUENCE_COMPLETE:
            raise RuntimeError(f"Formal eligible row is no longer exact-sequence complete: {key}")
        values = selected[research["feature_policy"]["core_features"]].to_numpy(dtype=np.float32)
        if values.shape != (24, 24):
            raise RuntimeError(f"Invalid Feature Set A sequence shape for {key}: {values.shape}")
        if not np.isfinite(values).all():
            bad_features = [
                research["feature_policy"]["core_features"][index]
                for index in range(values.shape[1])
                if not np.isfinite(values[:, index]).all()
            ]
            nonfinite_rows.append({
                "stock_code": key[0], "decision_date": key[1],
                "reason": "EXCLUDE_NONFINITE_FEATURE_NO_FILL",
                "nonfinite_features": "|".join(bad_features),
            })
            continue
        sequence_lookup[key] = values

    target = research["target"]
    candidate = target["candidate"]
    daily = aggregate_daily_market(raw)
    label_frame = eligible_common_samples(build_label_frame(
        daily,
        holding_sessions=research["prediction"]["prediction_horizon_sessions"],
        volatility_lookback=candidate["volatility_lookback_sessions"],
        commission_rate=target["commission_rate"],
        transaction_tax_rate=target["transaction_tax_rate"],
        k=candidate["k"],
    ))
    label_frame["decision_date"] = label_frame["trade_date"].astype(str)
    target_meta = label_frame[["stock_code", "decision_date", "entry_date", "target_date"]].copy()
    target_meta["stock_code"] = target_meta["stock_code"].astype(str)
    merged = manifest.merge(target_meta, on=["stock_code", "decision_date"], how="left", validate="one_to_one")
    if merged[["entry_date", "target_date"]].isna().any().any():
        raise RuntimeError("Eligible manifest cannot be joined to target dates")

    finite_keys = set(sequence_lookup)
    merged = merged.loc[
        [(str(code), str(day)) in finite_keys for code, day in zip(merged["stock_code"], merged["decision_date"])]
    ].copy()
    daily_context = build_daily_stock_features(featured)
    daily_context["stock_code"] = daily_context["stock_code"].astype(str)
    daily_context["decision_date"] = pd.to_datetime(daily_context["trade_date"]).dt.date.astype(str)
    context_names = research["feature_policy"]["gru"]["auxiliary_context"]
    merged = merged.merge(
        daily_context[["stock_code", "decision_date", *context_names]],
        on=["stock_code", "decision_date"], how="left", validate="one_to_one",
    )
    if merged[context_names].isna().any().any():
        raise RuntimeError("GRU volume context is missing from the common eligible sample set")

    merged = merged.sort_values(["decision_date", "stock_code"]).reset_index(drop=True)
    keys = list(zip(merged["stock_code"].astype(str), merged["decision_date"].astype(str)))
    if set(keys) != set(sequence_lookup):
        missing = sorted(set(keys) - set(sequence_lookup))[:10]
        extra = sorted(set(sequence_lookup) - set(keys))[:10]
        raise RuntimeError(f"Sequence/manifest mismatch, missing={missing}, extra={extra}")
    X = np.stack([sequence_lookup[key] for key in keys]).astype(np.float32)
    context = merged[context_names].to_numpy(dtype=np.float32)
    labels: dict[str, np.ndarray] = {}
    for name, column in LABEL_COLUMNS.items():
        mapped = merged[column].map(LABEL_IDS)
        if mapped.isna().any():
            raise RuntimeError(f"Unknown label name in manifest column {column}")
        labels[name] = mapped.to_numpy(dtype=np.int64)
    meta = merged[["stock_code", "decision_date", "entry_date", "target_date", "industry", "fold_id"]].copy()
    meta = meta.rename(columns={"decision_date": "signal_date"})
    for column in ("signal_date", "entry_date", "target_date"):
        meta[column] = pd.to_datetime(meta[column]).dt.date.astype(str)

    db_hash_after = sha256(database)
    if db_hash_before != db_hash_after:
        raise RuntimeError("market_data.sqlite3 changed during Phase B input preparation")
    return {
        "X": X, "context": context, "labels": labels, "meta": meta,
        "feature_names": list(research["feature_policy"]["core_features"]),
        "context_feature_names": list(context_names),
    }, {
        "sequence_eligible_samples": int(len(manifest)),
        "nonfinite_feature_exclusions": int(len(nonfinite_rows)),
        "model_ready_samples": int(len(X)), "shape": list(X.shape), "context_shape": list(context.shape),
        "nonfinite_feature_rows": nonfinite_rows,
        "cleaning": cleaning, "database_sha256_before": db_hash_before,
        "database_sha256_after": db_hash_after, "database_modified": False,
        "manifest_sha256": sha256(manifest_path),
        "exact_times": list(EXACT_SEQUENCE_TIMES),
    }


def raw_for_label(base: dict[str, Any], label: str) -> dict[str, Any]:
    return {
        "X": base["X"], "y": base["labels"][label], "meta": base["meta"],
        "context": base["context"], "feature_names": base["feature_names"],
        "context_feature_names": base["context_feature_names"],
    }


def indices_for_industry_fold(meta: pd.DataFrame, stocks: list[str], fold: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    stock = meta["stock_code"].astype(str).isin(stocks)
    signal = pd.to_datetime(meta["signal_date"])
    target = pd.to_datetime(meta["target_date"])
    train = stock & signal.between(fold["train_start"], fold["train_end"]) & target.between(fold["train_start"], fold["train_end"])
    validation = stock & signal.between(fold["validation_start"], fold["validation_end"]) & target.between(fold["validation_start"], fold["validation_end"])
    train_idx, validation_idx = np.flatnonzero(train.to_numpy()), np.flatnonzero(validation.to_numpy())
    if not len(train_idx) or not len(validation_idx):
        raise RuntimeError(f"Fold {fold['fold']} has an empty industry split")
    return train_idx, validation_idx


def add_buy_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    result = dict(metrics)
    matrix = np.asarray(result["confusion_matrix"], dtype=int)
    true_buy = int(matrix[2, 2])
    predicted_buy = int(matrix[:, 2].sum())
    actual_buy = int(matrix[2, :].sum())
    samples = int(result["samples"])
    result["buy_precision"] = float(true_buy / predicted_buy) if predicted_buy else 0.0
    result["buy_recall"] = float(true_buy / actual_buy) if actual_buy else 0.0
    result["buy_prediction_count"] = predicted_buy
    result["buy_prediction_proportion"] = float(predicted_buy / samples) if samples else 0.0
    result["per_stock"] = {code: add_buy_metrics(value) for code, value in result.get("per_stock", {}).items()}
    return result


def metric_stats(values: list[float | None]) -> dict[str, Any]:
    usable = np.asarray([float(value) for value in values if value is not None and np.isfinite(value)], dtype=float)
    return {
        "mean": float(usable.mean()) if len(usable) else None,
        "standard_deviation": float(usable.std(ddof=1)) if len(usable) > 1 else (0.0 if len(usable) else None),
        "worst": float(usable.min()) if len(usable) else None,
        "count": int(len(usable)),
    }


def aggregate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [record["metrics"] for record in records]
    matrix = np.sum([np.asarray(item["confusion_matrix"], dtype=int) for item in metrics], axis=0)
    predicted_buy, actual_buy, true_buy = int(matrix[:, 2].sum()), int(matrix[2, :].sum()), int(matrix[2, 2])
    samples = int(sum(item["samples"] for item in metrics))
    output = {
        "evaluations": len(records), "samples": samples,
        "macro_f1": metric_stats([item["macro_f1"] for item in metrics]),
        "macro_auc": metric_stats([item["macro_roc_auc_ovr"] for item in metrics]),
        "buy_precision": metric_stats([item["buy_precision"] for item in metrics]),
        "buy_recall": metric_stats([item["buy_recall"] for item in metrics]),
        "buy_coverage": metric_stats([item["buy_prediction_proportion"] for item in metrics]),
        "individual_auc": {
            name: metric_stats([item["individual_roc_auc_ovr"].get(name) for item in metrics])
            for name in CLASS_NAMES
        },
        "confusion_matrix_sum": matrix.astype(int).tolist(),
        "pooled_buy_precision": float(true_buy / predicted_buy) if predicted_buy else 0.0,
        "pooled_buy_recall": float(true_buy / actual_buy) if actual_buy else 0.0,
        "pooled_buy_coverage": float(predicted_buy / samples) if samples else 0.0,
        "buy_prediction_count": predicted_buy,
    }
    return output


def record_row(kind: str, industry: str, label: str, model: str, fold: int, metrics: dict[str, Any], **extra: Any) -> dict[str, Any]:
    row = {"evaluation": kind, "industry": industry, "label": label, "model": model, "fold": int(fold), **extra}
    row["metrics"] = add_buy_metrics(metrics)
    return row


def append_progress(record: dict[str, Any]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "phase_b_progress.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def run_walk_forward(base: dict[str, Any], phase: dict[str, Any], research: dict[str, Any], protocol: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = len(phase["industries"]) * len(phase["labels"]) * len(phase["models"]) * 4
    completed = 0
    for industry, stocks in phase["industries"].items():
        for label in phase["labels"]:
            raw = raw_for_label(base, label)
            for model in phase["models"]:
                for fold in protocol["walk_forward"]["folds"]:
                    train_idx, validation_idx = indices_for_industry_fold(raw["meta"], stocks, fold)
                    require_three_classes(raw["y"][train_idx], f"{industry}/{label}/{model}/fold{fold['fold']} train")
                    selected_epochs = None
                    inner_start = None
                    inner_loss = None
                    if model == "gru":
                        # Keep the outer Fold Validation untouched for scoring.
                        # Early stopping uses a chronological inner split formed
                        # only from the current Fold Training partition.
                        inner_train, inner_val, inner_start = split_gru_inner_validation(
                            raw, train_idx, float(phase["gru_inner_validation_fraction"]),
                            int(fold["fold"]), f"{industry}_all",
                        )
                        inner_data = make_scaled_data(
                            raw, inner_train, {"validation": inner_val}, include_context=True,
                            stage="walk_forward_fold",
                            audit_extra={"fold": int(fold["fold"]), "industry": industry, "label": label,
                                         "heldout_stock": None, "inner_validation_start": inner_start},
                        )
                        _, inner_result, _ = run_multiclass_model(
                            "gru", inner_data, research["models"]["gru"], evaluation_splits=("validation",)
                        )
                        selected_epochs = int(inner_result["epochs_completed"])
                        inner_loss = inner_result.get("best_validation_loss")
                    data = make_scaled_data(
                        raw, train_idx, {"validation": validation_idx}, include_context=model == "gru",
                        stage="walk_forward_outer_refit" if model == "gru" else "walk_forward_fold",
                        audit_extra={"fold": int(fold["fold"]), "industry": industry, "label": label,
                                     "scaler_fit_samples": int(len(train_idx)), "heldout_stock": None},
                    )
                    _, result, _ = run_multiclass_model(
                        model, data, research["models"][model], fixed_epochs=selected_epochs,
                        evaluation_splits=("validation",)
                    )
                    record = record_row(
                        "walk_forward", industry, label, model, fold["fold"], result["splits"]["validation"],
                        train_samples=int(len(train_idx)), validation_samples=int(len(validation_idx)),
                        selected_epochs=selected_epochs,
                        inner_validation_start=inner_start,
                        inner_best_validation_loss=inner_loss,
                        outer_epochs_completed=result.get("epochs_completed"),
                    )
                    records.append(record); append_progress(record)
                    completed += 1
                    print(f"[walk-forward {completed}/{total}] {industry} {label} {model} fold={fold['fold']}", flush=True)
    return records


def run_loso(base: dict[str, Any], phase: dict[str, Any], research: dict[str, Any], protocol: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = len(phase["industries"]) * len(phase["labels"]) * len(phase["models"]) * 5 * 4
    completed = 0
    for industry, stocks in phase["industries"].items():
        for label in phase["labels"]:
            raw = raw_for_label(base, label)
            for model in phase["models"]:
                for heldout in stocks:
                    peers = [code for code in stocks if code != heldout]
                    for fold in protocol["walk_forward"]["folds"]:
                        train_idx, test_idx = indices_for_outer_fold(raw["meta"], peers, heldout, fold)
                        require_three_classes(raw["y"][train_idx], f"{industry}/{label}/{model}/{heldout}/fold{fold['fold']}")
                        selected_epochs = None
                        inner_start = None
                        inner_loss = None
                        if model == "gru":
                            inner_train, inner_val, inner_start = split_gru_inner_validation(
                                raw, train_idx, float(phase["gru_inner_validation_fraction"]), int(fold["fold"]), heldout
                            )
                            inner_data = make_scaled_data(
                                raw, inner_train, {"validation": inner_val}, include_context=True,
                                stage="walk_forward_fold",
                                audit_extra={"fold": int(fold["fold"]), "industry": industry, "label": label,
                                             "heldout_stock": heldout, "inner_validation_start": inner_start},
                            )
                            _, inner_result, _ = run_multiclass_model(
                                "gru", inner_data, research["models"]["gru"], evaluation_splits=("validation",)
                            )
                            selected_epochs = int(inner_result["epochs_completed"])
                            inner_loss = inner_result.get("best_validation_loss")
                        data = make_scaled_data(
                            raw, train_idx, {"validation": test_idx}, include_context=model == "gru",
                            stage="loso_outer_fold",
                            audit_extra={"fold": int(fold["fold"]), "industry": industry, "label": label,
                                         "heldout_stock": heldout, "peer_stocks": peers,
                                         "scaler_fit_samples": int(len(train_idx))},
                        )
                        _, result, _ = run_multiclass_model(
                            model, data, research["models"][model], fixed_epochs=selected_epochs,
                            evaluation_splits=("validation",),
                        )
                        record = record_row(
                            "loso", industry, label, model, fold["fold"], result["splits"]["validation"],
                            heldout_stock=heldout, peer_stocks="|".join(peers),
                            train_samples=int(len(train_idx)), validation_samples=int(len(test_idx)),
                            selected_epochs=selected_epochs, inner_validation_start=inner_start,
                            inner_best_validation_loss=inner_loss,
                        )
                        records.append(record); append_progress(record)
                        completed += 1
                        print(f"[LOSO {completed}/{total}] {industry} {label} {model} heldout={heldout} fold={fold['fold']}", flush=True)
    return records


def grouped_summary(records: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, Any]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[tuple(str(record[key]) for key in keys)].append(record)
    root: dict[str, Any] = {}
    for group_key, rows in groups.items():
        cursor = root
        for value in group_key[:-1]:
            cursor = cursor.setdefault(value, {})
        cursor[group_key[-1]] = aggregate_records(rows)
    return root


def per_stock_walk_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    expanded = []
    for record in records:
        for code, metrics in record["metrics"].get("per_stock", {}).items():
            expanded.append({**{key: record[key] for key in ("industry", "label", "model", "fold")},
                             "stock_code": str(code), "metrics": metrics})
    return grouped_summary(expanded, ("industry", "label", "model", "stock_code"))


def value(summary: dict[str, Any], metric: str, field: str = "mean") -> float:
    result = summary[metric][field]
    return float(result) if result is not None else float("nan")


def compare_model(fixed_wf: dict[str, Any], vol_wf: dict[str, Any], fixed_loso: dict[str, Any], vol_loso: dict[str, Any],
                  fixed_stocks: dict[str, Any], vol_stocks: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    deltas = {
        "walk_macro_f1": value(vol_wf, "macro_f1") - value(fixed_wf, "macro_f1"),
        "walk_macro_auc": value(vol_wf, "macro_auc") - value(fixed_wf, "macro_auc"),
        "loso_macro_f1": value(vol_loso, "macro_f1") - value(fixed_loso, "macro_f1"),
        "loso_macro_auc": value(vol_loso, "macro_auc") - value(fixed_loso, "macro_auc"),
        "loso_buy_precision": vol_loso["pooled_buy_precision"] - fixed_loso["pooled_buy_precision"],
        "loso_buy_recall": vol_loso["pooled_buy_recall"] - fixed_loso["pooled_buy_recall"],
        "loso_buy_coverage": vol_loso["pooled_buy_coverage"] - fixed_loso["pooled_buy_coverage"],
    }
    nonworse, positive = 0, 0
    stock_deltas: dict[str, Any] = {}
    for stock in sorted(fixed_stocks):
        f, v = fixed_stocks[stock], vol_stocks[stock]
        f1_delta = value(v, "macro_f1") - value(f, "macro_f1")
        auc_delta = value(v, "macro_auc") - value(f, "macro_auc")
        okay = f1_delta >= -rule["macro_mean_max_drop"] and auc_delta >= -rule["macro_mean_max_drop"]
        better = f1_delta >= rule["minimum_positive_delta"] or auc_delta >= rule["minimum_positive_delta"]
        nonworse += int(okay); positive += int(better)
        stock_deltas[stock] = {"macro_f1_delta": f1_delta, "macro_auc_delta": auc_delta,
                               "nonworse": okay, "positive": better}
    checks = {
        "buy_precision_not_worse": deltas["loso_buy_precision"] >= -rule["buy_precision_max_drop"],
        "buy_recall_not_collapsed": deltas["loso_buy_recall"] >= -rule["buy_recall_max_drop"],
        "buy_coverage_not_collapsed": deltas["loso_buy_coverage"] >= -rule["buy_coverage_max_drop"],
        "walk_means_not_worse": deltas["walk_macro_f1"] >= -rule["macro_mean_max_drop"] and deltas["walk_macro_auc"] >= -rule["macro_mean_max_drop"],
        "loso_means_not_worse": deltas["loso_macro_f1"] >= -rule["macro_mean_max_drop"] and deltas["loso_macro_auc"] >= -rule["macro_mean_max_drop"],
        "worst_folds_not_worse": all(
            value(vol, metric, "worst") - value(fixed, metric, "worst") >= -rule["macro_worst_max_drop"]
            for fixed, vol in ((fixed_wf, vol_wf), (fixed_loso, vol_loso)) for metric in ("macro_f1", "macro_auc")
        ),
        "standard_deviation_not_worse": all(
            value(vol, metric, "standard_deviation") - value(fixed, metric, "standard_deviation") <= rule["macro_std_max_increase"]
            for fixed, vol in ((fixed_wf, vol_wf), (fixed_loso, vol_loso)) for metric in ("macro_f1", "macro_auc")
        ),
        "cross_stock_not_single_name": nonworse >= int(rule["minimum_nonworse_heldout_stocks"]),
        "has_positive_evidence": positive >= int(rule["minimum_nonworse_heldout_stocks"]) and any(
            delta >= rule["minimum_positive_delta"] for delta in deltas.values()
        ),
    }
    return {"supports_volatility_k_0_50": bool(all(checks.values())), "checks": checks,
            "deltas": deltas, "nonworse_heldout_stocks": nonworse,
            "positive_heldout_stocks": positive, "per_stock_deltas": stock_deltas}


def build_recommendation(phase: dict[str, Any], wf_summary: dict[str, Any], loso_summary: dict[str, Any],
                         loso_stock_summary: dict[str, Any]) -> dict[str, Any]:
    by_industry: dict[str, Any] = {}
    for industry in phase["industries"]:
        model_results: dict[str, Any] = {}
        for model in phase["models"]:
            model_results[model] = compare_model(
                wf_summary[industry]["fixed_1pct"][model], wf_summary[industry]["volatility_k_0_50"][model],
                loso_summary[industry]["fixed_1pct"][model], loso_summary[industry]["volatility_k_0_50"][model],
                loso_stock_summary[industry]["fixed_1pct"][model], loso_stock_summary[industry]["volatility_k_0_50"][model],
                phase["selection"]["model_support_rule"],
            )
        count = sum(int(item["supports_volatility_k_0_50"]) for item in model_results.values())
        required = int(phase["selection"][f"{industry}_required_support"])
        by_industry[industry] = {"supporting_models": count, "required_models": required,
                                 "supports_volatility_k_0_50": count >= required, "models": model_results}
    replace = all(item["supports_volatility_k_0_50"] for item in by_industry.values())
    return {
        "recommended_label": "volatility_k_0_50" if replace else "fixed_1pct",
        "evidence_sufficient_to_replace_fixed": replace,
        "freeze_label_now": True,
        "freeze_rationale": "Confirmatory comparison complete; no further K search is permitted."
        if replace else "Evidence is mixed or insufficient; preregistered fallback keeps Fixed ±1% and no further K search is permitted.",
        "selection_basis": "Prediction Quality + Generalization + Stability only",
        "trading_metrics_used": False,
        "by_industry": by_industry,
    }


def records_to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for record in records:
        metrics = record["metrics"]
        row = {key: value for key, value in record.items() if key != "metrics"}
        row.update({
            "samples": metrics["samples"], "macro_f1": metrics["macro_f1"],
            "macro_auc": metrics["macro_roc_auc_ovr"], "buy_precision": metrics["buy_precision"],
            "buy_recall": metrics["buy_recall"], "buy_prediction_count": metrics["buy_prediction_count"],
            "buy_prediction_proportion": metrics["buy_prediction_proportion"],
            "sell_auc": metrics["individual_roc_auc_ovr"]["SELL"],
            "hold_auc": metrics["individual_roc_auc_ovr"]["HOLD"],
            "buy_auc": metrics["individual_roc_auc_ovr"]["BUY"],
            "confusion_matrix": json.dumps(metrics["confusion_matrix"]),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def render_html(report: dict[str, Any]) -> str:
    recommendation = report["recommendation"]
    rows = []
    for industry, industry_item in recommendation["by_industry"].items():
        for model, item in industry_item["models"].items():
            delta = item["deltas"]
            rows.append(
                f"<tr><td>{industry}</td><td>{model}</td><td>{'支持 K=0.5' if item['supports_volatility_k_0_50'] else '不支持'}</td>"
                f"<td>{delta['loso_buy_precision']:+.4f}</td><td>{delta['loso_buy_recall']:+.4f}</td>"
                f"<td>{delta['loso_buy_coverage']:+.4f}</td><td>{delta['loso_macro_f1']:+.4f}</td>"
                f"<td>{delta['loso_macro_auc']:+.4f}</td><td>{item['nonworse_heldout_stocks']}/5</td></tr>"
            )
    return f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><title>ML V1 Phase B</title>
<style>body{{font-family:system-ui;max-width:1300px;margin:30px auto;color:#18202a}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd;padding:7px;text-align:right}}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){{text-align:left}}th{{background:#eaf2ff}}.box{{padding:16px;border:1px solid #aac;border-radius:10px;background:#f8fbff;margin:16px 0}}code{{background:#eef;padding:2px 5px}}</style></head><body>
<h1>ML V1 Phase B：Label Confirmatory Experiment</h1>
<div class='box'><b>建議 Label：</b>{html.escape(recommendation['recommended_label'])}<br>
<b>是否足以取代 Fixed：</b>{recommendation['evidence_sufficient_to_replace_fixed']}<br>
<b>是否 Freeze：</b>{recommendation['freeze_label_now']}<br>
<b>依據：</b>{html.escape(recommendation['freeze_rationale'])}</div>
<h2>LOSO：K=0.5 相對 Fixed 的差異</h2><table><tr><th>產業</th><th>模型</th><th>判定</th><th>BUY Precision Δ</th><th>BUY Recall Δ</th><th>BUY Coverage Δ</th><th>Macro F1 Δ</th><th>Macro AUC Δ</th><th>非劣化股票</th></tr>{''.join(rows)}</table>
<h2>研究限制確認</h2><pre>{html.escape(json.dumps(report['guardrails'], ensure_ascii=False, indent=2))}</pre>
<h2>輸出檔案</h2><ul><li>label_comparison_summary.json / csv</li><li>technology_results.json</li><li>financial_results.json</li><li>walk_forward_results.json / csv</li><li>loso_results.json / csv</li><li>buy_coverage.csv</li><li>recommendation.json</li></ul>
</body></html>"""


def main() -> int:
    phase, research, protocol = load_phase_b_config()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    progress = OUTPUT / "phase_b_progress.jsonl"
    if progress.exists():
        progress.unlink()
    started = time.perf_counter()
    base, input_audit = build_raw_inputs(phase, research)
    if input_audit["sequence_eligible_samples"] != int(phase["expected_sequence_eligible_samples"]):
        raise RuntimeError("The source sequence count differs from the formal manifest")
    if input_audit["model_ready_samples"] != (
        input_audit["sequence_eligible_samples"] - input_audit["nonfinite_feature_exclusions"]
    ):
        raise RuntimeError("Model-ready sample accounting is inconsistent")
    print(
        f"Prepared {input_audit['model_ready_samples']} shared model-ready samples "
        f"({input_audit['nonfinite_feature_exclusions']} nonfinite Feature Set A rows excluded without fill)",
        flush=True,
    )

    walk_records = run_walk_forward(base, phase, research, protocol)
    loso_records = run_loso(base, phase, research, protocol)
    wf_summary = grouped_summary(walk_records, ("industry", "label", "model"))
    wf_stock_summary = per_stock_walk_summary(walk_records)
    loso_summary = grouped_summary(loso_records, ("industry", "label", "model"))
    loso_stock_summary = grouped_summary(loso_records, ("industry", "label", "model", "heldout_stock"))
    recommendation = build_recommendation(phase, wf_summary, loso_summary, loso_stock_summary)

    walk_payload = {"summary": wf_summary, "per_stock": wf_stock_summary, "records": walk_records}
    loso_payload = {"summary": loso_summary, "per_heldout_stock": loso_stock_summary, "records": loso_records}
    label_summary = {
        "fixed_1pct": {industry: {model: {"walk_forward": wf_summary[industry]["fixed_1pct"][model],
                                                   "loso": loso_summary[industry]["fixed_1pct"][model]}
                                     for model in phase["models"]} for industry in phase["industries"]},
        "volatility_k_0_50": {industry: {model: {"walk_forward": wf_summary[industry]["volatility_k_0_50"][model],
                                                         "loso": loso_summary[industry]["volatility_k_0_50"][model]}
                                           for model in phase["models"]} for industry in phase["industries"]},
    }
    guardrails = {
        "controlled_validation_used": False, "development_used": False,
        "additional_holdout_used": False, "final_oos_used": False,
        "forbidden_roles_used": False, "trading_metrics_used_for_selection": False,
        "k_search": False, "probability_threshold_search": False,
        "feature_search": False, "hyperparameter_search": False,
        "hybrid_backtest_run": False, "technical_modified": False,
        "candidate_overwritten": False,
        "sequence_eligible_samples": int(phase["expected_sequence_eligible_samples"]),
        "model_ready_samples": int(len(base["X"])),
        "labels_share_identical_X": True, "labels_share_identical_folds": True,
    }
    report = {
        "experiment_id": phase["experiment_id"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_seconds": time.perf_counter() - started,
        "input_audit": input_audit, "guardrails": guardrails,
        "recommendation": recommendation,
        "remaining_decisions": ["Technology final model", "Financial final model", "probability threshold", "Hybrid integration"],
    }

    walk_frame, loso_frame = records_to_frame(walk_records), records_to_frame(loso_records)
    coverage_frame = pd.concat([
        walk_frame[["evaluation", "industry", "label", "model", "fold", "buy_prediction_count", "buy_prediction_proportion"]],
        loso_frame[["evaluation", "industry", "label", "model", "fold", "heldout_stock", "buy_prediction_count", "buy_prediction_proportion"]],
    ], ignore_index=True, sort=False)
    summary_rows = []
    for industry in phase["industries"]:
        for label in phase["labels"]:
            for model in phase["models"]:
                for evaluation, summary in (("walk_forward", wf_summary[industry][label][model]), ("loso", loso_summary[industry][label][model])):
                    summary_rows.append({
                        "industry": industry, "label": label, "model": model, "evaluation": evaluation,
                        "macro_f1_mean": value(summary, "macro_f1"), "macro_f1_std": value(summary, "macro_f1", "standard_deviation"),
                        "macro_f1_worst": value(summary, "macro_f1", "worst"), "macro_auc_mean": value(summary, "macro_auc"),
                        "macro_auc_std": value(summary, "macro_auc", "standard_deviation"), "macro_auc_worst": value(summary, "macro_auc", "worst"),
                        "buy_precision_pooled": summary["pooled_buy_precision"], "buy_recall_pooled": summary["pooled_buy_recall"],
                        "buy_coverage_pooled": summary["pooled_buy_coverage"],
                    })

    files = {
        "label_comparison_summary.json": label_summary,
        "technology_results.json": {"walk_forward": wf_summary["technology"], "walk_forward_per_stock": wf_stock_summary["technology"],
                                    "loso": loso_summary["technology"], "loso_per_stock": loso_stock_summary["technology"]},
        "financial_results.json": {"walk_forward": wf_summary["financial"], "walk_forward_per_stock": wf_stock_summary["financial"],
                                   "loso": loso_summary["financial"], "loso_per_stock": loso_stock_summary["financial"]},
        "walk_forward_results.json": walk_payload,
        "loso_results.json": loso_payload,
        "recommendation.json": recommendation,
        "experiment_manifest.json": report,
    }
    for name, payload in files.items():
        (OUTPUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    pd.DataFrame(summary_rows).to_csv(OUTPUT / "label_comparison_summary.csv", index=False, encoding="utf-8-sig")
    walk_frame.to_csv(OUTPUT / "walk_forward_results.csv", index=False, encoding="utf-8-sig")
    loso_frame.to_csv(OUTPUT / "loso_results.csv", index=False, encoding="utf-8-sig")
    coverage_frame.to_csv(OUTPUT / "buy_coverage.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(input_audit["nonfinite_feature_rows"]).to_csv(
        OUTPUT / "excluded_nonfinite_features.csv", index=False, encoding="utf-8-sig"
    )
    (OUTPUT / "phase_b_report.html").write_text(render_html(report), encoding="utf-8")
    print(json.dumps({"recommendation": recommendation["recommended_label"], "guardrails": guardrails,
                      "output": str(OUTPUT)}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

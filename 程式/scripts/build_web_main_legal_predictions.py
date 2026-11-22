"""Build causal RF/XGBoost/GRU predictions for the bounded website dataset.

2024 H2 uses the frozen ML V1 expanding-window folds.  2025 H1 uses the
existing full-Training refits whose latest target is 2024-12-31.  No target
labels from the prediction period are loaded, no parameter is searched, and
no strategy performance is calculated.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.ml_v1_sequence import select_exact_sequence
from ml.models import build_gru_multiclass_model, fit_multiclass_model
from ml.research_features import build_daily_stock_features
from ml.trading_pipeline import _intraday_features
from scripts import run_ml_v1_phase_b as phase_b
from scripts.run_ml_industry_generalization import make_scaled_data
from trading_system.research_guard import assert_development_period, assert_timestamps


DATA = ROOT / "data/web_main_bounded_2024h2_2025h1"
DATABASE = DATA / "history.sqlite3"
INDEX = DATA / "index.json"
MODELS = ("random_forest", "xgboost", "gru")
STOCKS = ("2303", "2330", "2379", "2454", "3034", "2357", "3231", "2892", "4938")
INDUSTRY = {
    "technology": {
        "training_stocks": ("2303", "2330", "2379", "2454", "3034"),
        "targets": ("2303", "2330", "2379", "2454", "3034", "2357", "3231", "4938"),
        "full_artifacts": ROOT / "獨立實驗/華碩2357_2025上半年/模型",
    },
    "financial": {
        "training_stocks": ("2881", "2882", "2884", "2886", "2891"),
        "targets": ("2892",),
        "full_artifacts": ROOT / "獨立實驗/第一金2892_2025上半年/模型",
    },
}
FOLDS = {
    "ML_WF3": {
        "train_start": "2023-01-01", "train_end": "2024-04-30",
        "validation_start": "2024-05-01", "validation_end": "2024-08-31",
    },
    "ML_WF4": {
        "train_start": "2023-01-01", "train_end": "2024-08-31",
        "validation_start": "2024-09-01", "validation_end": "2024-12-31",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parameter_fingerprint(params: dict[str, Any], industry: str, fold_id: str) -> str:
    payload = json.dumps(
        {"params": params, "industry": industry, "fold_id": fold_id},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def load_target_features(research: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not DATABASE.is_file() or not INDEX.is_file():
        raise RuntimeError("Run fill_web_main_dataset_gaps.py successfully before predictions")
    with closing(sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        raw = pd.read_sql_query(
            "SELECT stock_code,kbar_timestamp,open,high,low,close,volume "
            "FROM market_kbars ORDER BY stock_code,kbar_timestamp", connection,
        )
    assert_timestamps(raw["kbar_timestamp"])
    raw["stock_code"] = raw["stock_code"].astype(str)
    raw["datetime"] = pd.to_datetime(raw["kbar_timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Taipei")
    raw = raw.loc[
        raw["stock_code"].isin(STOCKS)
        & (raw["datetime"].dt.dayofweek < 5)
        & raw["datetime"].dt.strftime("%H:%M").between("09:00", "13:30")
    ].copy()
    raw["trade_date"] = raw["datetime"].dt.date
    features = research["feature_policy"]["core_features"]
    contexts = research["feature_policy"]["gru"]["auxiliary_context"]
    featured, cleaning = _intraday_features(raw, features)
    daily = build_daily_stock_features(featured)
    daily["stock_code"] = daily["stock_code"].astype(str)
    daily["day"] = pd.to_datetime(daily["trade_date"]).dt.date.astype(str)
    context_map = {
        (row.stock_code, row.day): np.asarray([getattr(row, name) for name in contexts], dtype=np.float32)
        for row in daily.itertuples()
    }
    calendar = [
        str(row["date"]) for row in json.loads(INDEX.read_text(encoding="utf8"))
        if "2024-05-01" <= str(row["date"]) <= "2025-06-30"
    ]
    position = {day: index for index, day in enumerate(calendar)}
    target: dict[str, dict[str, Any]] = {}
    exclusions: dict[str, Counter] = defaultdict(Counter)
    for (stock, day_value), group in featured.groupby(["stock_code", "trade_date"], sort=True):
        stock, day = str(stock), str(day_value)
        index = position.get(day)
        if stock not in STOCKS or index is None or index + 1 >= len(calendar):
            continue
        entry = calendar[index + 1]
        if not "2024-07-01" <= entry <= "2025-06-30":
            continue
        sequence, _ = select_exact_sequence(group)
        if sequence is None:
            exclusions[stock]["missing_exact_24_bar_sequence"] += 1
            continue
        values = sequence[features].to_numpy(dtype=np.float32)
        context = context_map.get((stock, day))
        if values.shape != (24, len(features)) or not np.isfinite(values).all():
            exclusions[stock]["nonfinite_or_wrong_shape_features"] += 1
            continue
        if context is None or not np.isfinite(context).all():
            exclusions[stock]["missing_or_nonfinite_context"] += 1
            continue
        key = f"{stock}|{entry}"
        target[key] = {"stock_code": stock, "signal_date": day, "entry_date": entry,
                       "X": values, "context": context}
    return target, {"cleaning": cleaning, "exclusions": {k: dict(v) for k, v in exclusions.items()}}


def training_raw(base: dict[str, Any]) -> dict[str, Any]:
    return {
        "X": base["X"], "y": base["labels"]["fixed_1pct"], "meta": base["meta"],
        "context": base["context"], "feature_names": base["feature_names"],
        "context_feature_names": base["context_feature_names"],
    }


def train_fold_models(base: dict[str, Any], research: dict[str, Any], temporary: Path):
    raw = training_raw(base)
    trained: dict[tuple[str, str, str], tuple[Callable, np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]] = {}
    audit: dict[str, Any] = {}
    meta = base["meta"]
    for industry, spec in INDUSTRY.items():
        for fold_id, fold in FOLDS.items():
            stock_ok = meta["stock_code"].astype(str).isin(spec["training_stocks"])
            date_ok = meta["signal_date"].between(fold["train_start"], fold["train_end"])
            target_ok = meta["target_date"].between(fold["train_start"], fold["train_end"])
            idx = np.flatnonzero((stock_ok & date_ok & target_ok).to_numpy())
            if not len(idx) or set(np.unique(raw["y"][idx])) != {0, 1, 2}:
                raise RuntimeError(f"Invalid three-class fold training set: {industry} {fold_id}")
            training_meta = meta.iloc[idx]
            key = f"{industry}/{fold_id}"
            audit[key] = {
                "training_rows": int(len(idx)),
                "training_stocks": sorted(set(training_meta["stock_code"].astype(str))),
                "training_signal_max": str(training_meta["signal_date"].max()),
                "training_target_max": str(training_meta["target_date"].max()),
                "target_stocks_outside_training_dates": True,
            }
            for name in MODELS:
                data = make_scaled_data(
                    raw, idx, {}, include_context=name == "gru",
                    stage="web_main_causal_walk_forward_inference",
                    audit_extra={"industry": industry, "fold_id": fold_id, "performance_calculated": False},
                )
                model, seconds, predict, extra = fit_multiclass_model(
                    name, data, research["models"][name], fixed_epochs=10 if name == "gru" else None,
                )
                folder = temporary / industry / fold_id / name
                folder.mkdir(parents=True, exist_ok=False)
                np.savez(
                    folder / "scaler.npz", mean=data.scaler_mean, scale=data.scaler_scale,
                    context_mean=data.context_scaler_mean if name == "gru" else np.array([]),
                    context_scale=data.context_scaler_scale if name == "gru" else np.array([]),
                )
                if name == "gru":
                    import torch
                    torch.save(model.state_dict(), folder / "model.pt")
                else:
                    joblib.dump(model, folder / "model.joblib")
                trained[(industry, fold_id, name)] = (
                    predict, data.scaler_mean, data.scaler_scale,
                    data.context_scaler_mean, data.context_scaler_scale,
                )
                audit[key][name] = {"training_seconds": seconds, "extra": extra}
    return trained, audit


def full_model_predictor(industry: str, name: str, research: dict[str, Any]):
    root = INDUSTRY[industry]["full_artifacts"]
    audit = json.loads((root / "training_audit.json").read_text(encoding="utf8"))
    if audit["training_target_max"] > "2024-12-31":
        raise RuntimeError(f"Full model leaks past Training: {industry}")
    scaler = np.load(root / name / "scaler.npz")
    if name == "gru":
        import torch
        model = build_gru_multiclass_model(
            24, research["models"]["gru"], len(research["feature_policy"]["gru"]["auxiliary_context"]),
        )
        model.load_state_dict(torch.load(root / name / "model.pt", map_location="cpu", weights_only=True))
        model.eval()

        def predict(X, context):
            with torch.no_grad():
                return torch.softmax(model(torch.from_numpy(X), torch.from_numpy(context)), dim=1).numpy()
    else:
        model = joblib.load(root / name / "model.joblib")

        def predict(X, context):
            return model.predict_proba(X.reshape(len(X), -1))
    return predict, scaler, audit


def prediction_row(meta: dict[str, Any], model: str, probability: np.ndarray, **extra) -> dict:
    probability = np.asarray(probability, dtype=float)
    if probability.shape != (3,) or not np.isfinite(probability).all():
        raise RuntimeError("Invalid prediction probability shape/value")
    probability = probability / probability.sum()
    return {
        "stock_code": meta["stock_code"], "model": model,
        "signal_date": meta["signal_date"], "entry_date": meta["entry_date"],
        "decision_time": meta["signal_date"] + "T13:31:00+08:00",
        "prediction": ("SELL", "HOLD", "BUY")[int(np.argmax(probability))],
        "prob_sell": float(probability[0]), "prob_hold": float(probability[1]),
        "prob_buy": float(probability[2]), **extra,
    }


def build_predictions(target: dict[str, dict[str, Any]], base: dict[str, Any], research: dict[str, Any], model_temp: Path):
    fold_models, training_audit = train_fold_models(base, research, model_temp)
    outputs = {stock: {name: [] for name in MODELS} for stock in STOCKS}
    full_cache = {}
    for key in sorted(target, key=lambda value: (target[value]["entry_date"], value)):
        item = target[key]
        industry = "financial" if item["stock_code"] == "2892" else "technology"
        if item["entry_date"] <= "2024-12-31":
            fold_id = next((name for name, fold in FOLDS.items()
                            if fold["validation_start"] <= item["signal_date"] <= fold["validation_end"]), None)
            if fold_id is None:
                raise RuntimeError(f"No causal fold for {key} signal={item['signal_date']}")
            fold = FOLDS[fold_id]
            for name in MODELS:
                predict, mean, scale, context_mean, context_scale = fold_models[(industry, fold_id, name)]
                X = ((item["X"] - mean) / scale).astype(np.float32)[None, ...]
                context = None if name != "gru" else (
                    (item["context"] - context_mean) / context_scale
                ).astype(np.float32)[None, ...]
                probability = predict(X, context)[0]
                outputs[item["stock_code"]][name].append(prediction_row(
                    item, name, probability, is_oof=True, data_role="training",
                    label_version="ml_v1_fixed_1pct",
                    prediction_source="ml_v1_causal_walk_forward_web_inference",
                    fold_id=fold_id, fold_train_start=fold["train_start"], fold_train_end=fold["train_end"],
                    fold_validation_start=fold["validation_start"], fold_validation_end=fold["validation_end"],
                    model_parameter_fingerprint=parameter_fingerprint(research["models"][name], industry, fold_id),
                ))
        else:
            for name in MODELS:
                cache_key = (industry, name)
                if cache_key not in full_cache:
                    full_cache[cache_key] = full_model_predictor(industry, name, research)
                predict, scaler, audit = full_cache[cache_key]
                X = ((item["X"] - scaler["mean"]) / scaler["scale"]).astype(np.float32)[None, ...]
                context = None if name != "gru" else (
                    (item["context"] - scaler["context_mean"]) / scaler["context_scale"]
                ).astype(np.float32)[None, ...]
                probability = predict(X, context)[0]
                outputs[item["stock_code"]][name].append(prediction_row(
                    item, name, probability, data_role="validation",
                    training_target_max=audit["training_target_max"],
                    prediction_source="fixed_2023_2024_new_stock_inference",
                ))
    return outputs, training_audit


def main() -> int:
    assert_development_period("2023-01-01", "2025-06-30")
    if not DATABASE.is_file() or not INDEX.is_file():
        raise RuntimeError("Run fill_web_main_dataset_gaps.py successfully before predictions")
    prediction_dir = DATA / "predictions"
    prediction_temp = DATA / "predictions.incomplete"
    model_dir = DATA / "walk_forward_models"
    model_temp = DATA / "walk_forward_models.incomplete"
    audit_path = DATA / "prediction_audit.json"
    if any(path.exists() for path in (prediction_dir, prediction_temp, model_dir, model_temp, audit_path)):
        raise RuntimeError("Prediction/model output already exists; preserve and inspect it before retrying")
    phase, research, _ = phase_b.load_phase_b_config()
    base, training_input_audit = phase_b.build_raw_inputs(phase, research)
    database_hash_before = sha256(DATABASE)
    target, target_audit = load_target_features(research)
    expected = set(STOCKS)
    if set(item["stock_code"] for item in target.values()) != expected:
        raise RuntimeError("One or more target stocks have no eligible inference rows")
    model_temp.mkdir(parents=True)
    outputs, fold_training_audit = build_predictions(target, base, research, model_temp)
    prediction_temp.mkdir(parents=True)
    counts = {}
    for stock, payload in outputs.items():
        for name in MODELS:
            payload[name].sort(key=lambda row: row["entry_date"])
            if len({row["entry_date"] for row in payload[name]}) != len(payload[name]):
                raise RuntimeError(f"Duplicate prediction date: {stock} {name}")
            for row in payload[name]:
                assert_development_period(row["signal_date"], row["entry_date"])
        (prediction_temp / f"{stock}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf8",
        )
        counts[stock] = {name: len(payload[name]) for name in MODELS}
    if database_hash_before != sha256(DATABASE):
        raise RuntimeError("Bounded market database changed during inference")
    model_temp.replace(model_dir)
    prediction_temp.replace(prediction_dir)
    audit = {
        "experiment_id": "web-main-data-completion-20261122",
        "status": "LEGAL_PREDICTIONS_BUILT_PENDING_PREFLIGHT",
        "database": str(DATABASE.relative_to(ROOT)),
        "database_sha256": database_hash_before,
        "prediction_counts": counts,
        "target_feature_audit": target_audit,
        "fold_training_audit": fold_training_audit,
        "training_input_audit": training_input_audit,
        "period_roles": {"2024-07-01..2024-12-31": "training", "2025-01-01..2025-06-30": "validation"},
        "performance_calculated": False,
        "parameter_search": False,
        "additional_holdout_accessed": False,
        "final_oos_accessed": False,
    }
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf8")
    print(json.dumps({"status": audit["status"], "prediction_counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

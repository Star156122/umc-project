"""Inference-only 2025H1 predictions for 2303/2330 with the frozen 2357 RF/XGB/GRU models.

Experiment: web-2303-2330-2025h1-onboarding-20261120
- No labels, no fitting, no parameter choice; models are reused byte-for-byte (hashes recorded).
- 2303/2330 WERE training stocks of these models (2023-2024 targets), so predictions are
  time-out-of-sample only and are labelled `fixed_2023_2024_training_stock_time_oos_inference`,
  never as new-stock inference.
- Market data is read with a SQL upper bound strictly before 2025-07-01 (Additional Holdout stays locked).
"""
import hashlib, json, sqlite3, sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

H = Path(__file__).resolve().parents[1]
R = H.parents[1]
sys.path.insert(0, str(R))
from ml.trading_pipeline import _intraday_features
from ml.research_features import build_daily_stock_features
from ml.ml_v1_sequence import select_exact_sequence
from ml.models import build_gru_multiclass_model
from trading_system.research_guard import assert_timestamps, assert_development_period

EXPERIMENT_ID = "web-2303-2330-2025h1-onboarding-20261120"
SOURCE = "fixed_2023_2024_training_stock_time_oos_inference"
MODEL_HOME = R / "獨立實驗/華碩2357_2025上半年/模型"
INDEX = R / "獨立實驗/華碩2357_2025上半年/資料/index_daily.json"
DATABASE = R / "data/market_data.sqlite3"
TZ = timezone(timedelta(hours=8))
WARMUP_START, PERIOD_START, PERIOD_END = "2024-11-01", "2025-01-01", "2025-06-30"
LO = int(datetime.fromisoformat(WARMUP_START).replace(tzinfo=TZ).timestamp())
HI = int(datetime.fromisoformat("2025-07-01").replace(tzinfo=TZ).timestamp())  # exclusive
assert_development_period(WARMUP_START, PERIOD_END)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


research = json.loads((R / "configs/ml_v1_research.json").read_text(encoding="utf8"))
audit = json.loads((MODEL_HOME / "training_audit.json").read_text(encoding="utf8"))
assert audit["training_target_max"] <= "2024-12-31"
assert not audit["validation_2025_used_for_training_or_selection"]
features = research["feature_policy"]["core_features"]
contexts = research["feature_policy"]["gru"]["auxiliary_context"]
calendar = [r["date"] for r in json.loads(INDEX.read_text(encoding="utf8"))]
assert calendar == sorted(set(calendar)) and calendar[-1] <= PERIOD_END
model_files = {name: sorted(p for p in (MODEL_HOME / name).iterdir() if p.is_file())
               for name in ["random_forest", "xgboost", "gru"]}
model_hashes = {f"{name}/{p.name}": sha(p) for name, files in model_files.items() for p in files}

summary = {}
for code in ["2303", "2330"]:
    assert code in audit["training_stocks"]  # documents why SOURCE is not "new_stock"
    with sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        raw = pd.read_sql_query(
            "SELECT stock_code,kbar_timestamp,open,high,low,close,volume FROM market_kbars "
            "WHERE stock_code=? AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? "
            "ORDER BY kbar_timestamp", con, params=(code, LO, HI))
    assert_timestamps(raw.kbar_timestamp)
    raw["datetime"] = pd.to_datetime(raw.kbar_timestamp, unit="s", utc=True).dt.tz_convert("Asia/Taipei")
    clock = raw.datetime.dt.strftime("%H:%M")
    raw = raw[(raw.datetime.dt.dayofweek < 5) & (clock >= "09:00") & (clock <= "13:30")].copy()
    raw["trade_date"] = raw.datetime.dt.date
    featured, cleaning = _intraday_features(raw, features)
    daily = build_daily_stock_features(featured)
    daily["day"] = pd.to_datetime(daily.trade_date).dt.date.astype(str)
    context_map = {r.day: np.array([getattr(r, k) for k in contexts], dtype=np.float32) for r in daily.itertuples()}
    xs, cs, meta, excluded = [], [], [], Counter()
    for day, group in featured.groupby("trade_date", sort=True):
        day = str(day)
        if day not in calendar:
            continue
        i = calendar.index(day)
        if i + 1 >= len(calendar):
            continue
        entry = calendar[i + 1]
        if not PERIOD_START <= entry <= PERIOD_END:
            continue
        sequence, _state = select_exact_sequence(group)
        if sequence is None:
            excluded["missing_exact_sequence"] += 1
            continue
        x = sequence[features].to_numpy(dtype=np.float32)
        ctx = context_map.get(day)
        if not np.isfinite(x).all():
            excluded["nonfinite_features"] += 1
            continue
        if ctx is None or not np.isfinite(ctx).all():
            excluded["missing_context"] += 1
            continue
        xs.append(x); cs.append(ctx); meta.append((day, entry))
    if not xs:
        raise RuntimeError(f"No valid inference sequences for {code}")
    X, context, outputs = np.stack(xs), np.stack(cs), {}
    for name in ["random_forest", "xgboost", "gru"]:
        folder = MODEL_HOME / name
        scaler = np.load(folder / "scaler.npz")
        xx = ((X - scaler["mean"]) / scaler["scale"]).astype(np.float32)
        if name == "gru":
            import torch
            ctx = ((context - scaler["context_mean"]) / scaler["context_scale"]).astype(np.float32)
            model = build_gru_multiclass_model(24, research["models"]["gru"], len(contexts))
            model.load_state_dict(torch.load(folder / "model.pt", map_location="cpu", weights_only=True))
            model.eval()
            with torch.no_grad():
                prob = torch.softmax(model(torch.from_numpy(xx), torch.from_numpy(ctx)), dim=1).numpy()
        else:
            prob = joblib.load(folder / "model.joblib").predict_proba(xx.reshape(len(xx), -1))
        assert prob.shape == (len(meta), 3) and np.isfinite(prob).all()
        rows = []
        for (day, entry), p in zip(meta, prob):
            assert_development_period(day, day); assert_development_period(entry, entry)
            rows.append({"stock_code": code, "model": name, "signal_date": day, "entry_date": entry,
                         "decision_time": day + "T23:59:59+08:00",
                         "prediction": ("SELL", "HOLD", "BUY")[int(np.argmax(p))],
                         "prob_sell": float(p[0]), "prob_hold": float(p[1]), "prob_buy": float(p[2]),
                         "training_target_max": audit["training_target_max"], "data_role": "validation",
                         "prediction_source": SOURCE})
        outputs[name] = rows
    out = H / f"資料/{code}_predictions.json"
    out.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf8")
    summary[code] = {"eligible_rows": len(meta), "exclusions": dict(excluded), "cleaning": cleaning,
                     "first_entry": meta[0][1], "last_entry": meta[-1][1], "predictions_sha256": sha(out)}
    print("INFERENCE", code, len(meta), dict(excluded), flush=True)

(H / "資料/inference_audit.json").write_text(json.dumps({
    "experiment_id": EXPERIMENT_ID, "inference_only": True, "labels_created": False, "retrained": False,
    "prediction_source": SOURCE, "stock_was_in_model_training": True,
    "model_home": str(MODEL_HOME.relative_to(R)), "model_training_stocks": audit["training_stocks"],
    "training_target_max": audit["training_target_max"], "model_file_sha256": model_hashes,
    "database": str(DATABASE.relative_to(R)), "database_query_window": [WARMUP_START, "<2025-07-01"],
    "index": str(INDEX.relative_to(R)), "features": features, "context": contexts,
    "performance_calculated": False, "locked_data_used": False, "stocks": summary,
    "decision_time_note": "End-of-signal-day availability; always before next-session entry.",
}, ensure_ascii=False, indent=2, default=str), encoding="utf8")

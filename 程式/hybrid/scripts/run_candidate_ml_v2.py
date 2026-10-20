"""Training-only Candidate-Specific ML V2 on frozen Technical V2-B events."""
from __future__ import annotations

import hashlib
import html
import contextlib
import io
import json
import math
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                             precision_recall_fscore_support, roc_auc_score)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid.scripts.run_hybrid_common_coverage import POC_CONFIG_PATH, read_json
from hybrid.scripts.run_ml_v2_exp001 import (TECH_CANDIDATE_AUDIT, TECH_BATCH_DIR,
    TECH_V2_CONFIG, OOF_PATH, POC_RUN, _run_alone_one, _prior_session_map,
    TechnicalV2AloneStrategy)
from hybrid.scripts.run_strategy_v2_batch import build_regime_context, _entry_key
from hybrid.scripts.run_ml_v2_exp002 import (MODELS, STOCKS)
from scripts import run_ml_v1_phase_b as mlv1
from scripts.run_ml_industry_generalization import make_scaled_data, split_gru_inner_validation
from ml.models import run_multiclass_model
from ml.walk_forward import load_walk_forward_protocol
from trading_system import backtest as bt
from trading_system import technical_v1 as tv1
from hybrid.scripts.run_strategy_v2_batch import simulate_one, summarize_rows

TAIPEI = ZoneInfo("Asia/Taipei")
CLASS_NAMES = ("SELL", "HOLD", "BUY")
LABEL_NAMES = {0: "SELL", 1: "HOLD", 2: "BUY"}
CLASS_INDEX = {v: k for k, v in LABEL_NAMES.items()}
TECH_CONFIG_PATH = ROOT / "configs/technical_v1_research.json"
RISK_CONFIG_PATH = ROOT / "hybrid/config/risk_v1.json"
COMMON_DIR = ROOT / "exports/hybrid_common_coverage_20261018T121416162413"
TECH_GATE_PATH = ROOT / "exports/strategy_v2_technical_candidate_20261019T103707108020/technical_v2_candidate_gate_decisions.csv"
ML_V1_AUDIT_DIR = ROOT / "exports/ml_candidate_error_audit_20261019T134230804119"
TECH_FOLDS = {
    "WF1": {"train_start": "2023-01-01", "train_end": "2023-06-30", "evaluate_start": "2023-07-01", "evaluate_end": "2023-12-31"},
    "WF2": {"train_start": "2023-01-01", "train_end": "2023-12-31", "evaluate_start": "2024-01-01", "evaluate_end": "2024-06-30"},
    "WF3": {"train_start": "2023-01-01", "train_end": "2024-06-30", "evaluate_start": "2024-07-01", "evaluate_end": "2024-12-31"},
}


def sha256(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            d.update(b)
    return d.hexdigest()


def _session_set(connection: sqlite3.Connection) -> set[str]:
    lower, upper = tv1._timestamp_bounds("2023-01-01", "2024-12-31")
    rows = connection.execute("SELECT DISTINCT kbar_timestamp FROM market_kbars WHERE stock_code IN (?,?,?,?,?) AND freq_minutes=5 AND kbar_timestamp>=? AND kbar_timestamp<? ORDER BY kbar_timestamp", (*STOCKS, lower, upper)).fetchall()
    return {datetime.fromtimestamp(int(r[0]), TAIPEI).date().isoformat() for r in rows}


def _all_signal_times(frame: Any) -> set[str]:
    # TechnicalV2AloneStrategy matches candidate_schedule against the engine's
    # next-tradable-bar decision time, not the preceding bar's final microsecond.
    return {((pd.Timestamp(v).to_pydatetime().replace(tzinfo=TAIPEI) if pd.Timestamp(v).tzinfo is None else pd.Timestamp(v).to_pydatetime().astimezone(TAIPEI))
             + timedelta(minutes=5)).isoformat(timespec="microseconds")
            for v in frame["kbar_time"].to_list()}


def collect_technical_b_events(connection: sqlite3.Connection, config: dict[str, Any],
                               family: dict[str, Any], candidate: dict[str, Any],
                               stock: str, end_date: str, regime_context: dict[str, Any],
                               *, fold_id: str, evaluate_start: str = "2023-01-01",
                               candidate_schedule: set[str] | None = None) -> list[dict[str, Any]]:
    fold = {"id": fold_id, "train_start": "2023-01-01", "train_end": end_date,
            "evaluate_start": evaluate_start, "evaluate_end": end_date}
    full, evaluation, app, _warmup, eval_lower = tv1._load_fold_frame(connection, config, candidate, stock, fold)
    strategy = TechnicalV2AloneStrategy(
        config=app, study_config=config, family_name="breakout", family=family,
        candidate=candidate, data_role="training", fold_id=fold_id,
        is_backtest=True, is_simulation=True, local_only=True, ml_model="technical_only",
        prediction_lookup={}, candidate_schedule=_all_signal_times(full) if candidate_schedule is None else candidate_schedule,
        regime_context=regime_context,
    )
    strategy.configure_local_account(app)
    strategy.evaluation_start_timestamp = eval_lower
    stats = bt.MarketDataStats(tick_count=full.height,
        first_timestamp=float(full["kbar_timestamp"][0]), last_timestamp=float(full["kbar_timestamp"][-1]))
    with contextlib.redirect_stdout(io.StringIO()):
        bt.replay_kbars(strategy, full, stats)
    return [dict(r) for r in strategy.hybrid_decisions]


def source_validation_events() -> pd.DataFrame:
    source = pd.read_csv(TECH_GATE_PATH, dtype={"stock_code": str})
    source = source[(source.version == "TECH_V2_B") & source.technical_signal.eq("BUY_CANDIDATE")].copy()
    source = source.drop_duplicates(["stock_code", "technical_fold_id", "technical_signal_time"])
    source["stock_code"] = source.stock_code.astype(str).str.zfill(4)
    source["fold_id"] = source.technical_fold_id.astype(str)
    source["candidate_date"] = pd.to_datetime(source.technical_candidate_date).dt.strftime("%Y-%m-%d")
    source["feature_date"] = pd.to_datetime(source.ml_signal_date).dt.strftime("%Y-%m-%d")
    source["entry_time"] = source.technical_earliest_entry_time.astype(str)
    source["candidate_key"] = source.stock_code + "|" + source.fold_id + "|" + source.technical_signal_time.astype(str)
    if source.duplicated("candidate_key").any():
        raise RuntimeError("Frozen Technical V2-B validation candidates are duplicated")
    return source[["stock_code", "fold_id", "candidate_date", "feature_date", "entry_time", "candidate_key", "technical_signal_time"]]


def build_event_dataset(base: dict[str, Any], events: list[dict[str, Any]], prior_map: dict[tuple[str, str], str | None],
                        *, origin: str) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    meta = base["meta"].copy().reset_index(drop=True)
    meta["stock_code"] = meta.stock_code.astype(str).str.zfill(4)
    meta["signal_date"] = meta.signal_date.astype(str).str[:10]
    feature_index = {(r.stock_code, r.signal_date): i for i, r in enumerate(meta.itertuples(index=False))}
    out: list[dict[str, Any]] = []
    idxs: list[int] = []
    for e in events:
        stock = str(e.get("stock_code", "")).zfill(4)
        cand_date = str(e.get("technical_candidate_date", e.get("candidate_date", "")))[:10]
        prior = e.get("feature_date") or e.get("previous_actual_session") or prior_map.get((stock, cand_date))
        if not prior or str(prior)[:10] >= cand_date:
            continue
        prior = str(prior)[:10]
        index = feature_index.get((stock, prior))
        if index is None:
            continue
        m = meta.iloc[index]
        target = str(m.target_date)[:10]
        if str(m.entry_date)[:10] != cand_date:
            # Existing frozen label's T+1 open must coincide with the Candidate session.
            continue
        out.append({"stock_code": stock, "candidate_date": cand_date, "feature_date": prior,
            "signal_date": cand_date, "entry_date": str(m.entry_date)[:10], "target_date": target,
            "fold_id": str(e.get("fold_id", "")), "candidate_key": str(e.get("candidate_key", "")),
            "entry_time": str(e.get("entry_time", "")), "candidate_source": origin,
            "actual_class": LABEL_NAMES[int(base["labels"]["fixed_1pct"][index])],
            "feature_index": index, "feature_time_cutoff": prior + "T13:30:00+08:00",
            "no_future_leakage_check": True})
        idxs.append(index)
    df = pd.DataFrame(out)
    if df.empty:
        return df, {"X": np.empty((0, 24, 24), dtype=np.float32), "y": np.empty(0, dtype=np.int64),
                    "context": np.empty((0, 2), dtype=np.float32),
                    "feature_names": list(base["feature_names"]),
                    "context_feature_names": list(base["context_feature_names"])}
    labels = np.asarray([CLASS_INDEX[x] for x in df.actual_class], dtype=np.int64)
    return df.reset_index(drop=True), {
        "X": base["X"][idxs].astype(np.float32), "y": labels,
        "context": base["context"][idxs].astype(np.float32),
        "feature_names": list(base["feature_names"]),
        "context_feature_names": list(base["context_feature_names"]),
    }


def _class_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {name: int(frame.actual_class.eq(name).sum()) for name in CLASS_NAMES}


def _train_candidate_fold(model_name: str, train: pd.DataFrame, train_arrays: dict[str, np.ndarray],
                          validation: pd.DataFrame, validation_arrays: dict[str, np.ndarray],
                          params: dict[str, Any], fold_id: str, inner_fraction: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    if len(train) < 3 or set(train.actual_class) != set(CLASS_NAMES):
        raise RuntimeError("INSUFFICIENT_CANDIDATE_SAMPLE: training candidates do not contain all 3 classes")
    if validation.empty:
        raise RuntimeError("INSUFFICIENT_CANDIDATE_SAMPLE: no validation candidates")
    if model_name == "gru":
        raw = {"X": np.concatenate([train_arrays["X"], validation_arrays["X"]]),
               "y": np.concatenate([train_arrays["y"], validation_arrays["y"]]),
               "context": np.concatenate([train_arrays["context"], validation_arrays["context"]]),
               "meta": pd.concat([train.reset_index(drop=True), validation.reset_index(drop=True)], ignore_index=True),
               "feature_names": list(train_arrays["feature_names"]),
               "context_feature_names": list(train_arrays["context_feature_names"])}
        t_idx = np.arange(len(train), dtype=int)
        v_idx = np.arange(len(train), len(train) + len(validation), dtype=int)
        inner_train, inner_val, inner_start = split_gru_inner_validation(
            raw, t_idx, inner_fraction, int(fold_id[-1]), "all_technology_candidate_events")
        inner_data = make_scaled_data(raw, inner_train, {"validation": inner_val}, include_context=True,
            stage="walk_forward_fold", audit_extra={"fold": fold_id, "sample_basis": "technical_candidate_only"})
        _, inner_result, _ = run_multiclass_model("gru", inner_data, params, evaluation_splits=("validation",))
        selected_epochs = int(inner_result["epochs_completed"])
        outer_data = make_scaled_data(raw, t_idx, {"validation": v_idx}, include_context=True,
            stage="walk_forward_fold", audit_extra={"fold": fold_id, "scaler_fit_samples": int(len(train)), "sample_basis": "technical_candidate_only"})
        _, result, probs = run_multiclass_model("gru", outer_data, params, fixed_epochs=selected_epochs, evaluation_splits=("validation",))
        extra = {"gru_inner_validation_start": inner_start, "gru_selected_epochs": selected_epochs,
                 "gru_inner_best_validation_loss": inner_result.get("best_validation_loss")}
    else:
        raw = {"X": np.concatenate([train_arrays["X"], validation_arrays["X"]]),
               "y": np.concatenate([train_arrays["y"], validation_arrays["y"]]),
               "context": np.concatenate([train_arrays["context"], validation_arrays["context"]]),
               "meta": pd.concat([train.reset_index(drop=True), validation.reset_index(drop=True)], ignore_index=True),
               "feature_names": list(train_arrays["feature_names"]),
               "context_feature_names": list(train_arrays["context_feature_names"])}
        t_idx = np.arange(len(train), dtype=int)
        v_idx = np.arange(len(train), len(train) + len(validation), dtype=int)
        data = make_scaled_data(raw, t_idx, {"validation": v_idx}, include_context=False,
            stage="walk_forward_fold", audit_extra={"fold": fold_id, "scaler_fit_samples": int(len(train)), "sample_basis": "technical_candidate_only"})
        _, result, probs = run_multiclass_model(model_name, data, params, evaluation_splits=("validation",))
        extra = {}
    prediction = np.asarray(probs["validation"], dtype=float)
    pred_ids = prediction.argmax(axis=1)
    output = validation.reset_index(drop=True).copy()
    output["model"] = model_name
    output["prediction"] = [LABEL_NAMES[int(v)] for v in pred_ids]
    output["prob_sell"], output["prob_hold"], output["prob_buy"] = prediction[:, 0], prediction[:, 1], prediction[:, 2]
    output["is_oof"] = True
    output["data_role"] = "training"
    output["feature_version"] = "ml_v1_frozen_feature_pipeline"
    output["no_future_leakage_check"] = (output.feature_date < output.candidate_date) & (output.target_date <= "2024-12-31")
    return output, {"training_seconds": result.get("training_seconds"), "validation_metrics": result["splits"]["validation"], **extra}


def classification_table(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    def row(g: pd.DataFrame, model: str, scope: str, val: str) -> dict[str, Any]:
        yt, yp = g.actual_class.astype(str), g.prediction.astype(str)
        p, r, f, _ = precision_recall_fscore_support(yt, yp, labels=CLASS_NAMES, zero_division=0)
        output = {"model": model, "scope": scope, "value": val, "samples": int(len(g)),
            "accuracy": float(accuracy_score(yt, yp)), "macro_f1": float(f1_score(yt, yp, labels=CLASS_NAMES, average="macro", zero_division=0)),
            "macro_precision": float(p.mean()), "macro_recall": float(r.mean())}
        for i, c in enumerate(CLASS_NAMES):
            output[f"{c.lower()}_precision"] = float(p[i]); output[f"{c.lower()}_recall"] = float(r[i]); output[f"{c.lower()}_f1"] = float(f[i])
            output[f"actual_{c.lower()}_count"] = int(yt.eq(c).sum()); output[f"predicted_{c.lower()}_count"] = int(yp.eq(c).sum())
            if yt.eq(c).nunique() == 2:
                output[f"{c.lower()}_auc"] = float(roc_auc_score(yt.eq(c).astype(int), g[f"prob_{c.lower()}"]))
            else: output[f"{c.lower()}_auc"] = None
        try:
            yid = yt.map(CLASS_INDEX).to_numpy(); pp = g[["prob_sell", "prob_hold", "prob_buy"]].to_numpy()
            output["macro_auc"] = float(roc_auc_score(yid, pp, labels=[0, 1, 2], multi_class="ovr", average="macro"))
        except ValueError: output["macro_auc"] = None
        return output
    overall = pd.DataFrame([row(g, m, "overall", "ALL") for m, g in predictions.groupby("model")])
    per_stock = pd.DataFrame([row(g, m, "stock", s) for (m, s), g in predictions.groupby(["model", "stock_code"])])
    per_fold = pd.DataFrame([row(g, m, "fold", f) for (m, f), g in predictions.groupby(["model", "fold_id"])])
    return overall, per_stock, per_fold


def write_user_html(audit: dict[str, Any], overall: pd.DataFrame, comparison: pd.DataFrame, path: Path) -> None:
    payload = json.dumps({"audit": audit, "overall": overall.to_dict("records"), "comparison": comparison.to_dict("records")}, ensure_ascii=False, allow_nan=False, default=str)
    page = f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Candidate-specific ML V2</title><style>body{{margin:0;background:#09131d;color:#e8f0f8;font:16px/1.55 system-ui,'Microsoft JhengHei',sans-serif}}main{{max-width:1100px;margin:35px auto;padding:0 18px}}header,section{{background:#102230;border:1px solid #29485e;border-radius:16px;padding:22px;margin:16px 0}}h1,h2{{margin-top:0}}select{{background:#0a1a26;color:#fff;border:1px solid #43627a;padding:9px;border-radius:8px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #29485e}}th{{color:#9bcaff}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}.card{{background:#0a1b28;padding:14px;border-radius:12px}}.muted{{color:#a7bdce}}.scroll{{overflow:auto}}</style><main><header><h1>候選股專屬 ML V2</h1><p>模型只用歷史上 Technical V2-B 真正出現候選的時點訓練，特徵取候選日前一交易日；它用來回答「在策略已發出候選時，ML 能否協助過濾？」</p><p>此為 2023–2024 Training-only 研究。候選出現後的資訊不會作為模型輸入。</p><label>模型　<select id="model"><option value="random_forest">Random Forest</option><option value="xgboost">XGBoost</option><option value="gru">GRU</option></select></label></header><section><h2>分類結果</h2><div id="cards" class="grid"></div><div class="scroll"><table id="metrics"></table></div></section><section><h2>與 ML V1 / Technical Alone 比較</h2><div class="scroll"><table id="comparison"></table></div></section><section><h2>覆蓋和限制</h2><p id="coverage"></p><p class="muted">顯示候選事件的相同 Common Coverage。若某 Fold 無足夠歷史 Candidate 樣本，該 Fold 不補資料也不出預測。</p></section></main><script>const D={payload};const f=x=>x==null||!Number.isFinite(Number(x))?'—':(Number(x)*100).toFixed(2)+'%';const n=x=>x==null?'—':Number(x).toLocaleString();function draw(){{const m=document.querySelector('#model').value;const o=D.overall.find(x=>x.model===m)||{{}};document.querySelector('#cards').innerHTML=[['樣本數',n(o.samples)],['Accuracy',f(o.accuracy)],['Macro F1',f(o.macro_f1)],['BUY Precision',f(o.buy_precision)],['BUY Recall',f(o.buy_recall)],['Macro AUC',f(o.macro_auc)]].map(x=>`<div class="card"><b>${{x[0]}}</b><br><strong>${{x[1]}}</strong></div>`).join('');const c=D.comparison.filter(x=>x.model===m);document.querySelector('#comparison').innerHTML='<tr><th>版本</th><th>分類</th><th>報酬</th><th>Worst MDD</th><th>PF</th><th>Sharpe</th><th>交易數</th><th>Net PnL</th><th>成本</th></tr>'+c.map(x=>`<tr><td>${{x.version}}</td><td>${{x.classification||''}}</td><td>${{f(x.average_return)}}</td><td>${{f(x.worst_mdd)}}</td><td>${{x.profit_factor==null?'—':Number(x.profit_factor).toFixed(2)}}</td><td>${{x.sharpe==null?'—':Number(x.sharpe).toFixed(2)}}</td><td>${{n(x.trade_count)}}</td><td>${{Number(x.net_pnl||0).toFixed(0)}}</td><td>${{Number(x.transaction_cost||0).toFixed(0)}}</td></tr>`).join('');document.querySelector('#coverage').textContent='有效 Candidate OOF：'+D.audit.candidate_oof_count+'；完整技術候選樣本數：'+D.audit.technical_candidate_count+'。';}}document.querySelector('#model').addEventListener('change',draw);draw();</script></main></html>"""
    path.write_text(page, encoding="utf-8")


def run() -> Path:
    phase, research, ml_protocol = mlv1.load_phase_b_config()
    ml_config = json.loads((ROOT / "configs/ml_v1_research.json").read_text(encoding="utf-8"))
    tech_config = read_json(TECH_CONFIG_PATH)
    v2_config = read_json(TECH_V2_CONFIG)
    poc = read_json(POC_CONFIG_PATH)
    risk = read_json(RISK_CONFIG_PATH)
    if (v2_config["strategy_version"] != "technical_v2" or v2_config["status"] != "FROZEN_CANDIDATE"
            or v2_config["base_candidate"] != "BR_SENS_RANGE55_BB20_2"):
        raise RuntimeError("Frozen Technical V2 candidate identity changed")
    if risk["risk_per_trade"] != 0.01 or risk["max_position_fraction"] != 0.4 or risk["stop_loss_pct"] != 0.025:
        raise RuntimeError("Risk V1 fixed contract changed")
    params = ml_config["models"]
    for k, v in {"random_forest": {"n_estimators": 200, "max_depth": 10, "min_samples_leaf": 5, "class_weight": "balanced", "random_state": 42},
                 "xgboost": {"n_estimators": 150, "max_depth": 4, "learning_rate": 0.05, "subsample": 0.8, "colsample_bytree": 0.8, "random_state": 42},
                 "gru": {"hidden_size": 16, "num_layers": 1, "dropout": 0.0, "batch_size": 128, "max_epochs": 10, "patience": 2, "learning_rate": 0.001, "random_state": 42}}.items():
        if any(params[k].get(p) != value for p, value in v.items()):
            raise RuntimeError(f"Frozen parameters differ for {k}; stop without substitution")
    base, input_audit = mlv1.build_raw_inputs(phase, research)
    validation_events = source_validation_events()
    technical_config_hash = sha256(TECH_CONFIG_PATH)
    if technical_config_hash != v2_config.get("source_config_sha256") and v2_config.get("source_config_sha256"):
        raise RuntimeError("Technical source config hash no longer matches frozen V2 candidate")
    family_name, family, candidate = tv1.candidate_by_id(tech_config, v2_config["base_candidate"])
    if family_name != "breakout":
        raise RuntimeError("Frozen V2 candidate is no longer a breakout")
    market_db = ROOT / research["data"]["database"]
    technical_db = ROOT / "data/technical_v1_mltech_clean.sqlite3"
    db_hashes_before = {str(market_db.relative_to(ROOT)): sha256(market_db), str(technical_db.relative_to(ROOT)): sha256(technical_db)}
    all_dates = set()
    with tv1._readonly_connection(market_db) as conn:
        all_dates = _session_set(conn)
        regime_context, regime_audit = build_regime_context(conn, tuple(STOCKS), all_dates)
    # Frozen Technical B validation population must be reproducible from the current frozen engine.
    generated_validation: list[dict[str, Any]] = []
    history_by_fold: dict[str, list[dict[str, Any]]] = {}
    common_candidates = pd.read_csv(COMMON_DIR / "common_coverage_candidates.csv", dtype={"stock_code": str})
    with tv1._readonly_connection(technical_db) as conn:
        for fold_id, spec in TECH_FOLDS.items():
            start, end = spec["evaluate_start"], spec["evaluate_end"]
            for stock in STOCKS:
                common_rows = common_candidates[(common_candidates.fold_id.astype(str) == fold_id)
                    & (common_candidates.stock_code.astype(str).str.zfill(4) == stock)]
                schedule = {_entry_key(stock, fold_id, row.earliest_entry_time)[2]
                    for row in common_rows.itertuples(index=False)}
                generated_validation.extend(collect_technical_b_events(
                    conn, tech_config, family, candidate, stock, end, regime_context,
                    fold_id=fold_id, evaluate_start=start, candidate_schedule=schedule))
            history_by_fold[fold_id] = []
            # Historical Candidate event pool is created only from data available before this fold's validation start.
            train_end = spec["train_end"]
            if train_end >= "2023-01-01":
                for stock in STOCKS:
                    history_by_fold[fold_id].extend(collect_technical_b_events(conn, tech_config, family, candidate, stock, train_end, regime_context, fold_id=fold_id + "_TRAIN"))
    generated_keys = {(str(r["stock_code"]), str(r["technical_fold_id"]), str(r["technical_signal_time"])) for r in generated_validation}
    expected_keys = set(zip(validation_events.stock_code.astype(str), validation_events.fold_id.astype(str), validation_events.technical_signal_time.astype(str)))
    validation_reproduction = {"generated_count": len(generated_keys), "frozen_count": len(expected_keys),
        "matching_event_count": len(generated_keys & expected_keys), "missing": len(expected_keys - generated_keys),
        "extra": len(generated_keys - expected_keys), "status": "PASS" if generated_keys == expected_keys else "FAIL"}
    if validation_reproduction["status"] != "PASS":
        raise RuntimeError(f"Frozen Technical V2-B Candidate event reproduction failed: {validation_reproduction}")
    # Prior-session join and features are strictly the already-defined ML V1 sample at P.
    with tv1._readonly_connection(technical_db) as conn:
        prior_map = _prior_session_map(conn, validation_events.rename(columns={"technical_signal_time": "signal_time", "entry_time": "earliest_entry_time"}))
        for events in history_by_fold.values():
            mini = pd.DataFrame([{"stock_code": r["stock_code"], "signal_time": r["technical_signal_time"], "earliest_entry_time": r["technical_earliest_entry_time"]} for r in events])
            if not mini.empty:
                prior_map.update(_prior_session_map(conn, mini))
    valid_rows = validation_events.to_dict("records")
    for r in valid_rows:
        r["technical_candidate_date"] = r["candidate_date"]
        r["feature_date"] = r["feature_date"]
        r["fold_id"] = r["fold_id"]
    eval_df, eval_arrays = build_event_dataset(base, valid_rows, prior_map, origin="FROZEN_TECH_V2_B_VALIDATION")
    if eval_df.empty:
        raise RuntimeError("No frozen Candidate validation features are model-eligible")
    # History candidates may not use the current fold's validation window.
    train_frames: dict[str, pd.DataFrame] = {}
    train_arrays: dict[str, dict[str, np.ndarray]] = {}
    for fold_id, generated in history_by_fold.items():
        mapped = []
        for r in generated:
            mapped.append({"stock_code": r["stock_code"], "technical_candidate_date": str(r["technical_candidate_date"]),
                "feature_date": prior_map.get((str(r["stock_code"]).zfill(4), str(r["technical_candidate_date"]))),
                "fold_id": fold_id, "candidate_key": f"{r['stock_code']}|{fold_id}|{r['technical_signal_time']}",
                "entry_time": r["technical_earliest_entry_time"]})
        frame, arrays = build_event_dataset(base, mapped, prior_map, origin="REPLAYED_FROZEN_TECH_V2_B_TRAINING_CANDIDATE")
        train_frames[fold_id] = frame
        train_arrays[fold_id] = arrays
    fold_specs = {f: s for f, s in TECH_FOLDS.items()}
    preflight = []
    for fold_id, spec in fold_specs.items():
        train = train_frames[fold_id]
        train = train[(train.candidate_date < spec["evaluate_start"]) & (train.target_date <= spec["train_end"])].copy() if not train.empty else train
        val = eval_df[eval_df.fold_id.eq(fold_id)].copy()
        preflight.append({"fold_id": fold_id, "train_candidate_count": int(len(train)),
            "validation_candidate_count": int(len(val)), "train_class_counts": _class_counts(train),
            "validation_class_counts": _class_counts(val),
            "per_stock_train": train.stock_code.value_counts().sort_index().astype(int).to_dict() if len(train) else {},
            "per_stock_validation": val.stock_code.value_counts().sort_index().astype(int).to_dict() if len(val) else {},
            "train_has_all_classes": set(train.actual_class) == set(CLASS_NAMES),
            "status": "READY" if len(train) >= 3 and set(train.actual_class) == set(CLASS_NAMES) and len(val) else "INSUFFICIENT_CANDIDATE_SAMPLE"})
    # Cache candidate-level train/evaluation arrays by fold; train samples are strictly target-boundary safe.
    predictions: list[pd.DataFrame] = []
    model_fold_results: list[dict[str, Any]] = []
    inner_fraction = float(phase["gru_inner_validation_fraction"])
    for fold_id, spec in fold_specs.items():
        tr = train_frames[fold_id]
        if len(tr):
            tr = tr[(tr.candidate_date < spec["evaluate_start"]) & (tr.target_date <= spec["train_end"])].copy().reset_index(drop=True)
            # Build arrays from the same selected event rows using their feature_index.
            tridx = tr.feature_index.astype(int).to_numpy()
            trdata = {"X": base["X"][tridx], "y": np.asarray([CLASS_INDEX[v] for v in tr.actual_class]),
                "context": base["context"][tridx], "feature_names": base["feature_names"],
                "context_feature_names": base["context_feature_names"]}
        else:
            tr = tr.reset_index(drop=True); trdata = train_arrays[fold_id]
        val = eval_df[eval_df.fold_id.eq(fold_id)].copy().reset_index(drop=True)
        vidx = val.feature_index.astype(int).to_numpy()
        valdata = {"X": base["X"][vidx], "y": np.asarray([CLASS_INDEX[v] for v in val.actual_class]),
            "context": base["context"][vidx], "feature_names": base["feature_names"],
            "context_feature_names": base["context_feature_names"]}
        for model in MODELS:
            counts = _class_counts(tr)
            try:
                out, result = _train_candidate_fold(model, tr, trdata, val, valdata, params[model], fold_id, inner_fraction)
                predictions.append(out)
                model_fold_results.append({"model": model, "fold_id": fold_id, "status": "COMPLETED",
                    "train_candidate_count": len(tr), "validation_candidate_count": len(val),
                    "train_class_counts": counts, "validation_class_counts": _class_counts(val), **result})
            except Exception as exc:
                if "INSUFFICIENT_CANDIDATE_SAMPLE" not in str(exc):
                    raise
                model_fold_results.append({"model": model, "fold_id": fold_id, "status": "INSUFFICIENT_CANDIDATE_SAMPLE",
                    "reason": str(exc), "train_candidate_count": len(tr), "validation_candidate_count": len(val),
                    "train_class_counts": counts, "validation_class_counts": _class_counts(val)})
    if not predictions:
        raise RuntimeError("No Candidate-Specific ML V2 Fold produced legal OOF predictions")
    candidate_predictions = pd.concat(predictions, ignore_index=True)
    overall_cls, per_stock_cls, per_fold_cls = classification_table(candidate_predictions)
    # Candidate-specific OOF has no random split and is always emitted only for evaluation candidate events.
    baseline_oof = pd.read_csv(OOF_PATH, dtype={"stock_code": str})
    baseline_oof["stock_code"] = baseline_oof.stock_code.astype(str).str.zfill(4)
    baseline_oof["signal_date"] = pd.to_datetime(baseline_oof.signal_date).dt.strftime("%Y-%m-%d")
    baseline_oof["entry_date"] = pd.to_datetime(baseline_oof.entry_date).dt.strftime("%Y-%m-%d")
    baseline_oof["model"] = baseline_oof.model.astype(str).str.lower()
    candidate_predictions["stock_code"] = candidate_predictions.stock_code.astype(str).str.zfill(4)
    candidate_predictions["candidate_date"] = candidate_predictions.candidate_date.astype(str).str[:10]
    v1rows = []
    for r in candidate_predictions.itertuples(index=False):
        hit = baseline_oof[(baseline_oof.model == r.model) & (baseline_oof.stock_code == r.stock_code) & (baseline_oof.signal_date == r.feature_date)]
        if len(hit) != 1: continue
        h = hit.iloc[0]
        v1rows.append({"model": r.model, "stock_code": r.stock_code, "candidate_date": r.candidate_date,
            "fold_id": r.fold_id, "actual_class": str(h.actual_label).upper(), "prediction": str(h.prediction).upper(),
            "prob_sell": float(h.prob_sell), "prob_hold": float(h.prob_hold), "prob_buy": float(h.prob_buy), "version": "ML_V1"})
    v1pred = pd.DataFrame(v1rows)
    c2pred = candidate_predictions.copy(); c2pred["version"] = "CANDIDATE_ML_V2"
    # Fair coverage is the exact key intersection of V1 and Candidate-Specific V2 predictions per model.
    fair_keys = ["model", "stock_code", "fold_id", "candidate_date"]
    c2keys = set(map(tuple, c2pred[fair_keys].astype(str).itertuples(index=False, name=None)))
    v1keys = set(map(tuple, v1pred[fair_keys].astype(str).itertuples(index=False, name=None)))
    fair_keyset = c2keys & v1keys
    c2fair = c2pred[[tuple(map(str, row)) in fair_keyset for row in c2pred[fair_keys].itertuples(index=False, name=None)]].copy()
    v1fair = v1pred[[tuple(map(str, row)) in fair_keyset for row in v1pred[fair_keys].itertuples(index=False, name=None)]].copy()
    fair_summary = {"candidate_oof_count": int(len(c2pred)), "fair_model_rows": int(len(c2fair)),
        "all_model_common_coverage": int(c2fair.groupby(["stock_code", "fold_id", "candidate_date"]).filter(lambda g: set(g.model) == set(MODELS)).groupby(["stock_code", "fold_id", "candidate_date"]).ngroups),
        "coverage_by_model": c2fair.groupby("model").size().astype(int).to_dict(),
        "coverage_by_model_fold": {f"{model}|{fold}": int(count)
            for (model, fold), count in c2fair.groupby(["model", "fold_id"]).size().items()}}
    cls_compare_rows = []
    for model in MODELS:
        for version, frame in (("ML_V1", v1fair[v1fair.model == model]), ("CANDIDATE_ML_V2", c2fair[c2fair.model == model])):
            if len(frame):
                y, p = frame.actual_class, frame.prediction
                precision, recall, f1, _ = precision_recall_fscore_support(y, p, labels=CLASS_NAMES, zero_division=0)
                cls_compare_rows.append({"model": model, "version": version, "samples": len(frame),
                    "accuracy": accuracy_score(y, p), "macro_f1": f1_score(y, p, labels=CLASS_NAMES, average="macro", zero_division=0),
                    "buy_precision": precision[2], "buy_recall": recall[2], "buy_f1": f1[2]})
    classification_compare = pd.DataFrame(cls_compare_rows)
    # Trading comparison: identical model-specific candidate intersections and unchanged Risk V1 / Technical V2-B.
    poc_cfg = read_json(POC_CONFIG_PATH)
    risk_cfg = risk
    old_lookup = {}
    for model in MODELS:
        old_lookup[model] = {(str(r.stock_code), str(r.entry_date)): r._asdict() for r in baseline_oof[baseline_oof.model.eq(model)].itertuples(index=False)}
    new_lookup = {}
    for model in MODELS:
        new_lookup[model] = {(str(r.stock_code), str(r.candidate_date)): {"prediction": r.prediction,
            "signal_date": r.feature_date, "entry_date": r.candidate_date, "target_date": r.target_date,
            "fold_id": r.fold_id, "is_oof": True, "data_role": "training",
            "prob_sell": r.prob_sell, "prob_hold": r.prob_hold, "prob_buy": r.prob_buy}
            for r in c2fair[c2fair.model.eq(model)].itertuples(index=False)}
    technical_cfg = read_json(TECH_CONFIG_PATH)
    alone_inputs = []; model_sim_rows = []; all_trade_rows = []
    with tv1._readonly_connection(technical_db) as conn:
        coverage_dates = set(c2fair.candidate_date.astype(str))
        regime_for_fair, _ = build_regime_context(conn, tuple(STOCKS), coverage_dates)
        volume_context = {}
        for model in MODELS:
            model_rows = c2fair[c2fair.model.eq(model)]
            if model_rows.empty: continue
            for fold_id in TECH_FOLDS:
                fold_cfg = TECH_FOLDS[fold_id]
                for stock in STOCKS:
                    subset = model_rows[(model_rows.fold_id == fold_id) & (model_rows.stock_code == stock)]
                    if subset.empty: continue
                    schedule = {_entry_key(stock, fold_id, t)[2] for t in subset.entry_time}
                    # The technical-only control and both ML versions use this same event schedule.
                    alone, alone_trades, _, _ = _run_alone_one(connection=conn, config=technical_cfg, family_name=family_name,
                        family=family, candidate=candidate, stock=stock, fold={"id": fold_id, **fold_cfg}, schedule=schedule, regime_context=regime_for_fair)
                    alone["version"] = "TECHNICAL_V2_ALONE"; alone_inputs.append(alone)
                    all_trade_rows.extend({"version": "TECHNICAL_V2_ALONE", "model": model, "stock_code": stock, "fold_id": fold_id, **r} for r in alone_trades)
                    for version, lookup in (("ML_V1", old_lookup[model]), ("CANDIDATE_ML_V2", new_lookup[model])):
                        result, trades, gates, risks, _audit = simulate_one(connection=conn, config=technical_cfg,
                            family_name=family_name, family=family, candidate=candidate, stock=stock,
                            fold={"id": fold_id, **fold_cfg}, version="TECH_V2_B", model=model,
                            lookup=lookup, schedule=schedule, volume_context=volume_context, regime_context=regime_for_fair)
                        result["version"] = version; result["model"] = model; result["stock_code"] = stock; result["fold_id"] = fold_id
                        model_sim_rows.append(result)
                        all_trade_rows.extend({"version": version, "model": model, "stock_code": stock, "fold_id": fold_id, **r} for r in trades)
    trade_metrics = pd.DataFrame(model_sim_rows)
    alone_df = pd.DataFrame(alone_inputs)
    if not trade_metrics.empty and not alone_df.empty:
        alone_df = alone_df.drop_duplicates(["model", "stock_code", "fold_id"])
        alone_expanded = []
        for m in MODELS:
            rows_m = alone_df.copy(); rows_m["model"] = m; rows_m["version"] = "TECHNICAL_V2_ALONE"; alone_expanded.append(rows_m)
        trade_metrics = pd.concat([trade_metrics, *alone_expanded], ignore_index=True, sort=False)
    # Aggregate the identical per-stock/fold rows per model and method.
    trade_overall_rows = []
    if not trade_metrics.empty:
        for (model, version), g in trade_metrics.groupby(["model", "version"], sort=False):
            trade_overall_rows.append({"model": model, "version": version, **summarize_rows(g.rename(columns={"total_return": "total_return", "max_drawdown": "max_drawdown"}))})
    trade_overall = pd.DataFrame(trade_overall_rows)
    comparison_rows = []
    statuses = {}
    for model in MODELS:
        rows = trade_overall[trade_overall.model.eq(model)] if not trade_overall.empty else pd.DataFrame()
        tech = rows[rows.version.eq("TECHNICAL_V2_ALONE")]
        v1 = rows[rows.version.eq("ML_V1")]
        c2 = rows[rows.version.eq("CANDIDATE_ML_V2")]
        if not len(c2) or not len(v1) or not len(tech):
            statuses[model] = "MIXED"
            continue
        base, cand = v1.iloc[0], c2.iloc[0]
        # Cross-stock/fold criteria are computed from the paired Stock × Fold rows.
        paired = trade_metrics[trade_metrics.model.eq(model)].pivot_table(index=["stock_code", "fold_id"], columns="version", values="total_return", aggfunc="first")
        stock_improved = int((paired["CANDIDATE_ML_V2"].groupby(level=0).mean() > paired["ML_V1"].groupby(level=0).mean()).sum()) if set(["CANDIDATE_ML_V2", "ML_V1"]).issubset(paired.columns) else 0
        fold_improved = int((paired["CANDIDATE_ML_V2"].groupby(level=1).mean() > paired["ML_V1"].groupby(level=1).mean()).sum()) if set(["CANDIDATE_ML_V2", "ML_V1"]).issubset(paired.columns) else 0
        needed_stock = max(1, math.ceil(3 * len(paired.index.get_level_values(0).unique()) / 5))
        needed_folds = max(1, math.ceil(2 * len(paired.index.get_level_values(1).unique()) / 3))
        supported = (cand["average_return"] >= base["average_return"] and cand["profit_factor"] >= base["profit_factor"]
            and cand["worst_mdd"] <= base["worst_mdd"] and stock_improved >= needed_stock and fold_improved >= needed_folds
            and cand["trade_count"] >= 0.5 * base["trade_count"])
        clearly_bad = (cand["average_return"] < base["average_return"] and cand["profit_factor"] < base["profit_factor"]
            and cand["worst_mdd"] > base["worst_mdd"] and stock_improved <= 2 and fold_improved <= 1)
        statuses[model] = "SUPPORTED" if supported else ("NOT_SUPPORTED" if clearly_bad else "MIXED")
        comparison_rows.append({"model": model, "classification": statuses[model],
            "fair_candidate_oof_count": fair_summary["coverage_by_model"].get(model, 0),
            **{k: float(tech.iloc[0][k]) for k in ("average_return", "worst_mdd", "profit_factor", "sharpe", "trade_count", "net_pnl", "transaction_cost")} ,
            "version": "TECHNICAL_V2_ALONE"})
        comparison_rows.append({"model": model, "classification": statuses[model],
            "fair_candidate_oof_count": fair_summary["coverage_by_model"].get(model, 0),
            **{k: float(base[k]) for k in ("average_return", "worst_mdd", "profit_factor", "sharpe", "trade_count", "net_pnl", "transaction_cost")},
            "version": "ML_V1", "return_improved_stocks": stock_improved, "return_improved_folds": fold_improved})
        comparison_rows.append({"model": model, "classification": statuses[model],
            "fair_candidate_oof_count": fair_summary["coverage_by_model"].get(model, 0),
            **{k: float(cand[k]) for k in ("average_return", "worst_mdd", "profit_factor", "sharpe", "trade_count", "net_pnl", "transaction_cost")},
            "version": "CANDIDATE_ML_V2", "return_improved_stocks": stock_improved, "return_improved_folds": fold_improved})
    comparison = pd.DataFrame(comparison_rows)
    trade_per_stock_rows = []
    trade_per_fold_rows = []
    for (model, version, stock), group in trade_metrics.groupby(["model", "version", "stock_code"], sort=False):
        trade_per_stock_rows.append({"model": model, "version": version, "stock_code": stock, **summarize_rows(group)})
    for (model, version, fold_id), group in trade_metrics.groupby(["model", "version", "fold_id"], sort=False):
        trade_per_fold_rows.append({"model": model, "version": version, "fold_id": fold_id, **summarize_rows(group)})
    trade_per_stock = pd.DataFrame(trade_per_stock_rows)
    trade_per_fold = pd.DataFrame(trade_per_fold_rows)
    outdir = ROOT / "exports" / f"candidate_ml_v2_{datetime.now(TAIPEI).strftime('%Y%m%dT%H%M%S%f')}"
    outdir.mkdir(parents=True, exist_ok=False)
    db_hashes_after = {str(market_db.relative_to(ROOT)): sha256(market_db), str(technical_db.relative_to(ROOT)): sha256(technical_db)}
    if db_hashes_before != db_hashes_after:
        raise RuntimeError("A source database changed during Candidate ML V2")
    audit = {"status": "COMPLETED", "experiment_id": "strategy-v2-candidate-specific-ml-v2",
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "preregistration": "research/ml_v2_candidate_specific_preregistration.md", "preregistration_sha256": sha256(ROOT / "research/ml_v2_candidate_specific_preregistration.md"),
        "phase_a_audit": str(ML_V1_AUDIT_DIR.relative_to(ROOT)), "phase_a_status": json.loads((ML_V1_AUDIT_DIR / "candidate_error_audit.json").read_text(encoding="utf-8"))["status"],
        "technical_candidate": "BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER", "technical_candidate_frozen": True,
        "technical_validation_reproduction": validation_reproduction, "technical_candidate_count": int(len(validation_events)),
        "candidate_training_pool_policy": "Replayed frozen V2-B events through each WF training cutoff; only previous-session V1 feature sample, Fixed ±1% label, and target end no later than cutoff.",
        "training_period": ["2023-01-01", "2024-12-31"], "data_role": "TRAINING_ONLY",
        "stocks": list(STOCKS), "models": list(MODELS), "folds": list(TECH_FOLDS),
        "feature_policy": "Complete ML V1 frozen feature pipeline; no feature selection or lookback change.",
        "label_policy": "ML V1 Fixed ±1%; T+1 Open to T+3 Close after frozen one-round-trip cost.",
        "fold_preflight": preflight, "model_fold_results": model_fold_results,
        "insufficient_folds": [r for r in model_fold_results if r["status"] != "COMPLETED"],
        "candidate_oof_count": int(len(candidate_predictions)), "fair_coverage": fair_summary,
        "probability_source": "candidate model native predict_proba/softmax; evaluation only; no threshold search",
        "gru_inner_validation": "Chronological 20% of fold training candidate dates; scaler and early stopping only within fold training; outer fold validation not used for early stopping.",
        "source_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in [TECH_CONFIG_PATH, TECH_V2_CONFIG, TECH_GATE_PATH, OOF_PATH, market_db, technical_db]},
        "model_parameters_tuned": False, "feature_selection_performed": False, "label_changed": False,
        "technical_changed": False, "risk_changed": False, "threshold_changed": False,
        "2025_used": False, "2026_used": False, "final_oos_used": False,
        "database_modified": False, "backtest_scope": "fair candidate coverage only", "parameter_search": False}
    for name, frame in (("candidate_ml_v2_oof_predictions.csv", candidate_predictions),
        ("candidate_ml_v2_dataset_summary.csv", pd.DataFrame(preflight)),
        ("candidate_ml_v2_classification_metrics.csv", pd.concat([overall_cls.assign(version="CANDIDATE_ML_V2"), per_stock_cls.assign(version="CANDIDATE_ML_V2"), per_fold_cls.assign(version="CANDIDATE_ML_V2")], ignore_index=True)),
        ("candidate_ml_v2_overall.csv", trade_overall), ("candidate_ml_v2_per_stock.csv", trade_per_stock),
        ("candidate_ml_v2_per_fold.csv", trade_per_fold),
        ("candidate_ml_v2_stock_fold.csv", trade_metrics), ("candidate_ml_v2_trades.csv", pd.DataFrame(all_trade_rows)),
        ("candidate_ml_v2_comparison.csv", comparison), ("candidate_ml_v2_classification_comparison.csv", classification_compare)):
        frame.to_csv(outdir / name, index=False, encoding="utf-8-sig")
    (outdir / "candidate_ml_v2_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")
    write_user_html(audit, overall_cls, comparison, outdir / "candidate_ml_v2_user_analysis.html")
    report = "<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><title>Candidate ML V2 Report</title><body><h1>Candidate-Specific ML V2 Research Report</h1><pre>" + html.escape(json.dumps(audit, ensure_ascii=False, indent=2, default=str)) + "</pre>" + overall_cls.to_html(index=False) + comparison.to_html(index=False) + "</body></html>"
    (outdir / "candidate_ml_v2_report.html").write_text(report, encoding="utf-8")
    return outdir


if __name__ == "__main__":
    print(run())

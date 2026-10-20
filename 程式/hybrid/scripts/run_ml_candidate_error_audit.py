"""Read-only audit of frozen ML V1 predictions on Strategy V2 candidate dates."""
from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                             precision_recall_fscore_support, roc_auc_score)

ROOT = Path(__file__).resolve().parents[2]
STOCKS = ("2303", "2330", "2379", "2454", "3034")
MODELS = {
    "random_forest": ("rf", "Random Forest"),
    "xgboost": ("xgb", "XGBoost"),
    "gru": ("gru", "GRU"),
}
CLASSES = ("SELL", "HOLD", "BUY")
TECH_FOLDS = {
    "WF1": ("2023-07-01", "2023-12-31"),
    "WF2": ("2024-01-01", "2024-06-30"),
    "WF3": ("2024-07-01", "2024-12-31"),
}
POC = ROOT / "exports/hybrid_poc_v1_20261013T041540804059"
EXP1 = ROOT / "exports/strategy_v2_ml_exp001_20261019T123507275634"
EXP2 = ROOT / "exports/strategy_v2_hybrid_exp002_20261019T132413877488"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def normalize_class(value: object) -> str:
    return str(value).strip().upper()


def safe_auc(actual: pd.Series, probabilities: pd.DataFrame) -> dict[str, object]:
    result: dict[str, object] = {"macro_auc": None}
    actual = actual.map(normalize_class)
    for label in CLASSES:
        binary = actual.eq(label).astype(int)
        result[f"{label.lower()}_auc"] = (
            float(roc_auc_score(binary, probabilities[label.lower()]))
            if binary.nunique() == 2 else None
        )
    try:
        y = pd.Categorical(actual, categories=CLASSES).codes
        p = probabilities[[c.lower() for c in CLASSES]].to_numpy(dtype=float)
        result["macro_auc"] = float(roc_auc_score(y, p, multi_class="ovr", average="macro", labels=[0, 1, 2]))
    except (ValueError, TypeError):
        pass
    return result


def metric_row(frame: pd.DataFrame, *, model: str, scope: str, value: str) -> dict[str, object]:
    y_true = frame.actual_label.map(normalize_class)
    y_pred = frame.prediction.map(normalize_class)
    p, r, f, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=CLASSES, zero_division=0
    )
    row: dict[str, object] = {
        "model": model, "scope": scope, "value": value, "count": int(len(frame)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_precision": float(np.mean(p)), "macro_recall": float(np.mean(r)),
        "macro_f1": float(f1_score(y_true, y_pred, labels=CLASSES, average="macro", zero_division=0)),
        "actual_sell": int((y_true == "SELL").sum()), "actual_hold": int((y_true == "HOLD").sum()),
        "actual_buy": int((y_true == "BUY").sum()),
        "predicted_sell": int((y_pred == "SELL").sum()), "predicted_hold": int((y_pred == "HOLD").sum()),
        "predicted_buy": int((y_pred == "BUY").sum()),
    }
    for idx, label in enumerate(CLASSES):
        row[f"{label.lower()}_precision"] = float(p[idx])
        row[f"{label.lower()}_recall"] = float(r[idx])
        row[f"{label.lower()}_f1"] = float(f[idx])
    probability_cols = [f"prob_{x.lower()}" for x in CLASSES]
    if set(probability_cols).issubset(frame.columns):
        probs = frame[probability_cols].rename(columns={f"prob_{x.lower()}": x.lower() for x in CLASSES})
        probs = probs.astype(float)
        row.update(safe_auc(y_true, probs))
        row["probability_analysis"] = "AVAILABLE_FROM_FROZEN_OOF"
        row["mean_prob_sell"] = float(probs.sell.mean())
        row["mean_prob_hold"] = float(probs.hold.mean())
        row["mean_prob_buy"] = float(probs.buy.mean())
        row["mean_max_probability"] = float(probs.max(axis=1).mean())
        # Reliability summary is descriptive; no recalibration is performed.
        row["buy_calibration_bins"] = json.dumps(_calibration_bins(y_true, probs.buy), ensure_ascii=False)
    else:
        row["probability_analysis"] = "PROBABILITY_ANALYSIS_UNAVAILABLE"
    return row


def _calibration_bins(actual: pd.Series, p_buy: pd.Series) -> list[dict[str, object]]:
    rows = []
    bins = pd.cut(p_buy, bins=np.linspace(0, 1, 6), include_lowest=True, duplicates="drop")
    for interval, group in pd.DataFrame({"bin": bins, "p": p_buy, "y": actual.eq("BUY").astype(int)}).groupby("bin", observed=True):
        rows.append({"bin": str(interval), "count": int(len(group)), "mean_probability": float(group.p.mean()),
                     "observed_buy_rate": float(group.y.mean())})
    return rows


def attach_realized_trade_outcomes(candidates: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Match frozen Technical-alone completed round trips to candidate entry timestamps."""
    t = trades[(trades.variant == "TECH_V2_ALONE") & (trades.model == "technical_only")].copy()
    if t.empty:
        out = candidates.copy()
        out["strategy_trade_matched"] = False
        return out
    t["stock_code"] = t.stock_code.astype(str)
    t["fold_id"] = t.fold_id.astype(str)
    t["datetime_norm"] = pd.to_datetime(t.datetime).dt.strftime("%Y-%m-%d %H:%M:%S")
    pairs: dict[tuple[str, str, str], dict[str, object]] = {}
    for (stock, fold), group in t.sort_values("timestamp").groupby(["stock_code", "fold_id"], sort=False):
        pending = None
        for row in group.to_dict("records"):
            action = str(row["action"]).lower()
            if action == "buy":
                pending = row
            elif action == "sell" and pending is not None:
                net = float(pending["net_cash_flow"]) + float(row["net_cash_flow"])
                gross = float(pending["gross_amount"])
                key = (stock, fold, pending["datetime_norm"])
                pairs[key] = {
                    "strategy_trade_matched": True,
                    "strategy_net_pnl": net,
                    "strategy_return_on_entry_cash": net / max(abs(float(pending["net_cash_flow"])), 1e-12),
                    "strategy_win": net > 0,
                    "strategy_entry_time": pending["datetime_norm"],
                    "strategy_exit_time": row["datetime_norm"],
                    "strategy_gross_entry_amount": gross,
                    "strategy_transaction_cost": float(pending.get("fee", 0)) + float(row.get("fee", 0)) + float(row.get("tax", 0)),
                }
                pending = None
    out = candidates.copy()
    out["entry_key"] = pd.to_datetime(out.earliest_entry_time).dt.strftime("%Y-%m-%d %H:%M:%S")
    keys = list(zip(out.stock_code.astype(str), out.fold_id.astype(str), out.entry_key.astype(str)))
    mapped = [pairs.get(k, {"strategy_trade_matched": False}) for k in keys]
    extra = pd.DataFrame(mapped, index=out.index)
    return pd.concat([out, extra], axis=1).drop(columns=["entry_key"])


def render_html(audit: dict[str, object], metrics: pd.DataFrame, confusion: pd.DataFrame,
                outcomes: pd.DataFrame, dest: Path, *, user_facing: bool) -> None:
    title = "Technical 候選日 ML 錯誤分析" if user_facing else "ML Candidate Error Audit"
    intro = ("這份報告只看 Technical 已經出現候選訊號的日期，檢查 ML 的分類和原策略成交結果是否一致。" if user_facing
             else "Read-only audit of the frozen V1 OOF predictions on the preregistered candidate-only coverage.")
    metric_html = metrics.to_html(index=False, escape=True, border=0, classes="data")
    confusion_html = confusion.to_html(index=False, escape=True, border=0, classes="data")
    outcomes_html = outcomes.to_html(index=False, escape=True, border=0, classes="data")
    issues = "<li>沒有發現資料時序、標籤或 OOF 對齊問題。</li>" if audit["status"] == "PASS" else "<li>檢查未通過；依 preregistration 停止後續訓練。</li>"
    html_text = f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{margin:0;background:#08131d;color:#e8f0f8;font:16px/1.55 system-ui,'Microsoft JhengHei',sans-serif}}main{{max-width:1200px;margin:36px auto;padding:0 20px}}header,section{{background:#102230;border:1px solid #29485e;border-radius:16px;padding:22px;margin:18px 0}}h1,h2{{margin-top:0}}.badge{{display:inline-block;padding:5px 11px;border-radius:20px;background:#123b35;color:#9be4c0}}.warn{{background:#351a1e;border-color:#81363d}}.table-wrap{{overflow:auto}}table.data{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:8px;border-bottom:1px solid #29485e;text-align:left;white-space:nowrap}}th{{color:#8fc5ff}}code{{color:#a8d7ff}}</style><main><header><h1>{html.escape(title)}</h1><p>{html.escape(intro)}</p><span class="badge">Audit status: {html.escape(str(audit['status']))}</span><p>Candidate rows: {audit.get('candidate_count')} · Models: RF / XGBoost / GRU · Scope: 2023–2024 Training only</p></header>
<section><h2>資料與時間檢查</h2><ul><li>Common Coverage rows: {audit.get('candidate_count')}</li><li>Expected prior actual session matched: {audit.get('previous_session_match_count')}/{audit.get('candidate_count')}</li><li>Frozen OOF mapping matched: {audit.get('oof_match_count')}/{audit.get('candidate_model_rows')}</li><li>OOF prediction flagged out-of-fold: {audit.get('oof_true_count')}/{audit.get('candidate_model_rows')}</li><li>Technical fold/date assignments matched: {audit.get('technical_fold_match_count')}/{audit.get('candidate_count')}</li><li>OOF candidate predictions matched: {audit.get('candidate_prediction_match_count')}/{audit.get('candidate_model_rows')}</li><li>Frozen Fixed ±1% label rows loaded with candidate entry date: {audit.get('frozen_label_rows_loaded')}/{audit.get('candidate_model_rows')}</li><li>Forbidden-period rows: {audit.get('forbidden_period_rows')}</li></ul><ul>{issues}</ul></section>
<section><h2>分類指標</h2><div class="table-wrap">{metric_html}</div></section><section><h2>Candidate-only Confusion Matrix</h2><p>列為實際類別，欄為預測類別，順序 SELL / HOLD / BUY。</p><div class="table-wrap">{confusion_html}</div></section><section><h2>依預測類別分組的原策略成交結果</h2><p>僅統計既有 Technical-alone 已完成的 round trip；未匹配到完成交易的候選日仍留在分類稽核，但不虛構交易績效。</p><div class="table-wrap">{outcomes_html}</div></section><section class="warn"><h2>解讀限制</h2><p>這是診斷，不會更改 Gate、模型、技術規則或參數。分類表現和已實現交易結果分開呈現；不可由單一指標推導調參或移除股票。</p></section><section><h2>輸入身分</h2><pre>{html.escape(json.dumps(audit.get('inputs'), ensure_ascii=False, indent=2))}</pre></section></main></html>"""
    dest.write_text(html_text, encoding="utf-8")


def run(output: Path | None = None) -> Path:
    coverage_path = EXP1 / "ml_v2_common_coverage.csv"
    exp2_coverage_path = EXP2 / "hybrid_v2_exp002_common_coverage.csv"
    oof_path = POC / "ml_v1_baseline_oof_predictions.csv"
    trade_path = EXP1 / "ml_v2_trades.csv"
    prereg_path = ROOT / "research/ml_v2_candidate_specific_preregistration.md"
    paths = [coverage_path, exp2_coverage_path, oof_path, trade_path, prereg_path]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise RuntimeError("Required frozen inputs missing: " + ", ".join(missing))
    coverage = pd.read_csv(coverage_path, dtype={"stock_code": str})
    exp2_coverage = pd.read_csv(exp2_coverage_path, dtype={"stock_code": str})
    oof = pd.read_csv(oof_path, dtype={"stock_code": str})
    trades = pd.read_csv(trade_path, dtype={"stock_code": str})
    if len(coverage) != 138 or len(exp2_coverage) != 138:
        raise RuntimeError(f"Expected preregistered 138-row coverage; got E1={len(coverage)}, E2={len(exp2_coverage)}")
    coverage["stock_code"] = coverage.stock_code.astype(str).str.zfill(4)
    exp2_coverage["stock_code"] = exp2_coverage.stock_code.astype(str).str.zfill(4)
    oof["stock_code"] = oof.stock_code.astype(str).str.zfill(4)
    key = ["stock_code", "fold_id", "signal_time", "earliest_entry_time"]
    if not set(key).issubset(exp2_coverage.columns):
        raise RuntimeError("Experiment 002 coverage lacks required matching keys")
    # The E1 and E2 common coverage must be the same frozen event set.
    e1_keys = set(zip(coverage.stock_code.astype(str), coverage.fold_id.astype(str),
                      pd.to_datetime(coverage.signal_time).astype(str), pd.to_datetime(coverage.earliest_entry_time).astype(str)))
    e2_keys = set(zip(exp2_coverage.stock_code.astype(str), exp2_coverage.fold_id.astype(str),
                      pd.to_datetime(exp2_coverage.signal_time).astype(str), pd.to_datetime(exp2_coverage.earliest_entry_time).astype(str)))
    e1_e2_match = e1_keys == e2_keys
    if not e1_e2_match:
        raise RuntimeError("Experiment 001 / 002 frozen common coverage mismatch")
    if coverage.duplicated(["stock_code", "fold_id", "technical_candidate_key"]).any():
        raise RuntimeError("Duplicate candidate event keys")
    coverage["candidate_date"] = pd.to_datetime(coverage.candidate_date).dt.strftime("%Y-%m-%d")
    coverage["previous_actual_session"] = pd.to_datetime(coverage.previous_actual_session).dt.strftime("%Y-%m-%d")
    coverage["expected_previous_trading_day"] = pd.to_datetime(coverage.expected_previous_trading_day).dt.strftime("%Y-%m-%d")
    coverage["stock_code"] = coverage.stock_code.astype(str).str.zfill(4)
    coverage["fold_id"] = coverage.fold_id.astype(str)
    candidate_count = len(coverage)
    prev_ok = (coverage.previous_actual_session == coverage.expected_previous_trading_day) & (coverage.rf_signal_date == coverage.previous_actual_session) & (coverage.xgb_signal_date == coverage.previous_actual_session) & (coverage.gru_signal_date == coverage.previous_actual_session)
    fold_ok = coverage.apply(lambda r: r.candidate_date >= TECH_FOLDS.get(str(r.fold_id), ("~", "!"))[0] and r.candidate_date <= TECH_FOLDS.get(str(r.fold_id), ("~", "!"))[1], axis=1)
    is_forbidden = coverage.candidate_date.str.startswith(("2025", "2026"))
    oof["signal_date"] = pd.to_datetime(oof.signal_date).dt.strftime("%Y-%m-%d")
    oof["target_date"] = pd.to_datetime(oof.target_date).dt.strftime("%Y-%m-%d")
    oof["entry_date"] = pd.to_datetime(oof.entry_date).dt.strftime("%Y-%m-%d")
    oof["model"] = oof.model.astype(str).str.lower()
    oof["stock_code"] = oof.stock_code.astype(str).str.zfill(4)
    oof["prediction"] = oof.prediction.map(normalize_class)
    oof["actual_label"] = oof.actual_label.map(normalize_class)
    oof_lookup = {(r.model, r.stock_code, r.signal_date): r for r in oof.itertuples(index=False)}
    records: list[dict[str, object]] = []
    oof_match = oof_true = prediction_match = label_rows_loaded = 0
    for row in coverage.to_dict("records"):
        stock, date, candidate_date = str(row["stock_code"]), str(row["previous_actual_session"]), str(row["candidate_date"])
        for model, (prefix, label) in MODELS.items():
            found = oof_lookup.get((model, stock, date))
            if found is None:
                continue
            oof_match += 1
            oof_true += int(str(found.is_oof).lower() == "true" and str(found.data_role).lower() == "training")
            cov_pred = normalize_class(row[f"{prefix}_prediction"] if f"{prefix}_prediction" in row else row[f"{model}_prediction"])
            if prefix == "rf": cov_pred = normalize_class(row["random_forest_prediction"])
            if prefix == "xgb": cov_pred = normalize_class(row["xgboost_prediction"])
            if prefix == "gru": cov_pred = normalize_class(row["gru_prediction"])
            prediction_match += int(cov_pred == normalize_class(found.prediction))
            label_rows_loaded += int(str(found.entry_date) == candidate_date
                                     and str(found.data_role).lower() == "training"
                                     and str(found.label_version) == "ml_v1_fixed_1pct")
            records.append({
                "stock_code": stock, "candidate_date": candidate_date,
                "previous_actual_session": date, "technical_fold": str(row["fold_id"]),
                "ml_fold": str(found.fold_id), "model": model, "model_label": label,
                "prediction": normalize_class(found.prediction), "actual_label": normalize_class(found.actual_label),
                "prob_sell": float(found.prob_sell), "prob_hold": float(found.prob_hold), "prob_buy": float(found.prob_buy),
                "is_oof": str(found.is_oof).lower() == "true", "data_role": str(found.data_role),
                "label_version": str(found.label_version), "entry_date": str(found.entry_date),
                "target_date": str(found.target_date), "technical_candidate_key": row.get("technical_candidate_key"),
                "earliest_entry_time": row.get("earliest_entry_time"),
                "previous_session_match": bool(prev_ok.loc[coverage.index[coverage.technical_candidate_key == row["technical_candidate_key"]][0]]),
                "technical_fold_match": bool(fold_ok.loc[coverage.index[coverage.technical_candidate_key == row["technical_candidate_key"]][0]]),
                "coverage_e1_e2_match": True,
            })
    cand_models = candidate_count * len(MODELS)
    rows = pd.DataFrame(records)
    if rows.empty:
        raise RuntimeError("No candidate OOF rows could be matched")
    if rows.duplicated(["stock_code", "candidate_date", "technical_fold", "model"]).any():
        raise RuntimeError("Duplicate candidate/model OOF rows")
    base_candidates = coverage[["stock_code", "candidate_date", "fold_id", "earliest_entry_time"]].copy()
    trade_rows = trades.copy()
    enriched_candidates = attach_realized_trade_outcomes(base_candidates, trade_rows)
    trade_key = ["stock_code", "candidate_date", "technical_fold"]
    enriched_candidates = enriched_candidates.rename(columns={"fold_id": "technical_fold"})
    rows = rows.merge(enriched_candidates.drop(columns=["earliest_entry_time"], errors="ignore"), on=trade_key, how="left", validate="many_to_one")
    metrics = pd.DataFrame([metric_row(g, model=m, scope="overall", value="ALL") for m, g in rows.groupby("model", sort=False)])
    by_stock = pd.DataFrame([metric_row(g, model=m, scope="stock", value=s) for (m, s), g in rows.groupby(["model", "stock_code"], sort=False)])
    by_fold = pd.DataFrame([metric_row(g, model=m, scope="technical_fold", value=f) for (m, f), g in rows.groupby(["model", "technical_fold"], sort=False)])
    confusion_rows = []
    for model in MODELS:
        for scope, col, values in (("overall", None, ["ALL"]), ("stock", "stock_code", list(STOCKS)), ("technical_fold", "technical_fold", list(TECH_FOLDS))):
            for value in values:
                part = rows[rows.model.eq(model)] if col is None else rows[(rows.model.eq(model)) & (rows[col].astype(str).eq(value))]
                mat = confusion_matrix(part.actual_label, part.prediction, labels=CLASSES)
                confusion_rows.append({"model": model, "scope": scope, "value": value,
                    **{f"actual_{a.lower()}_pred_{p.lower()}": int(mat[i, j]) for i, a in enumerate(CLASSES) for j, p in enumerate(CLASSES)}})
    confusion = pd.DataFrame(confusion_rows)
    fp = rows[(rows.prediction == "BUY") & (rows.actual_label != "BUY")].copy()
    fp["error_type"] = "PREDICT_BUY_ACTUAL_NOT_BUY"
    fp2 = rows[(rows.prediction == "BUY") & rows.strategy_trade_matched.fillna(False)
               & rows.strategy_net_pnl.notna() & (rows.strategy_net_pnl < 0)].copy()
    fp2["error_type"] = "PREDICT_BUY_STRATEGY_NET_PNL_NONPOSITIVE"
    fp = pd.concat([fp, fp2], ignore_index=True)
    fn = rows[(rows.prediction.isin(["HOLD", "SELL"])) & (rows.actual_label == "BUY")].copy()
    fn["error_type"] = "PREDICT_HOLD_SELL_ACTUAL_BUY"
    fn2 = rows[(rows.prediction.isin(["HOLD", "SELL"])) & rows.strategy_net_pnl.fillna(0).gt(0)].copy()
    fn2["error_type"] = "PREDICT_HOLD_SELL_STRATEGY_POSITIVE"
    fn = pd.concat([fn, fn2], ignore_index=True)
    outcome_records = []
    for (model, pred), g in rows.groupby(["model", "prediction"], sort=False):
        done = g[g.strategy_trade_matched.fillna(False) & g.strategy_net_pnl.notna()]
        gains = done.loc[done.strategy_net_pnl > 0, "strategy_net_pnl"].sum()
        losses = -done.loc[done.strategy_net_pnl < 0, "strategy_net_pnl"].sum()
        outcome_records.append({"model": model, "predicted_class": pred, "candidate_count": int(len(g)),
            "completed_strategy_trade_count": int(len(done)),
            "average_strategy_return": float(done.strategy_return_on_entry_cash.mean()) if len(done) else None,
            "median_strategy_return": float(done.strategy_return_on_entry_cash.median()) if len(done) else None,
            "win_rate": float(done.strategy_win.mean()) if len(done) else None,
            "net_pnl": float(done.strategy_net_pnl.sum()) if len(done) else 0.0,
            "profit_factor": float(gains / losses) if losses else (float("inf") if gains else None),
            "worst_loss": float(done.strategy_net_pnl.min()) if len(done) else None,
            "best_gain": float(done.strategy_net_pnl.max()) if len(done) else None})
    outcomes = pd.DataFrame(outcome_records)
    per_stock_outcomes = rows.groupby(["model", "stock_code", "prediction"], dropna=False).apply(
        lambda g: pd.Series({"candidate_count": len(g), "completed_strategy_trade_count": int(g.strategy_trade_matched.fillna(False).sum()),
            "average_strategy_return": g.loc[g.strategy_trade_matched.fillna(False), "strategy_return_on_entry_cash"].mean(),
            "net_pnl": g.loc[g.strategy_trade_matched.fillna(False), "strategy_net_pnl"].sum()}), include_groups=False).reset_index()
    per_fold_outcomes = rows.groupby(["model", "technical_fold", "prediction"], dropna=False).apply(
        lambda g: pd.Series({"candidate_count": len(g), "completed_strategy_trade_count": int(g.strategy_trade_matched.fillna(False).sum()),
            "average_strategy_return": g.loc[g.strategy_trade_matched.fillna(False), "strategy_return_on_entry_cash"].mean(),
            "net_pnl": g.loc[g.strategy_trade_matched.fillna(False), "strategy_net_pnl"].sum()}), include_groups=False).reset_index()
    integrity = {
        "status": "PASS" if (e1_e2_match and len(rows) == cand_models and oof_match == cand_models and oof_true == cand_models and prediction_match == cand_models and label_rows_loaded == cand_models and bool(prev_ok.all()) and bool(fold_ok.all()) and not bool(is_forbidden.any()) and bool(rows.target_date.le("2024-12-31").all())) else "FAIL",
        "candidate_count": int(candidate_count), "candidate_model_rows": int(cand_models),
        "common_coverage_e1_e2_same": bool(e1_e2_match), "previous_session_match_count": int(prev_ok.sum()),
        "technical_fold_match_count": int(fold_ok.sum()), "oof_match_count": int(oof_match),
        "oof_true_count": int(oof_true), "candidate_prediction_match_count": int(prediction_match),
        "frozen_label_rows_loaded": int(label_rows_loaded),
        "forbidden_period_rows": int(is_forbidden.sum()),
        "unique_candidate_model_rows": int(len(rows)),
        "candidate_only_dataset_note": "Conditional-on-Technical-Candidate. Candidate occurrence only selects this gate-use population and is never an ML input feature.",
        "leakage_checks": {"feature_date_is_previous_actual_session": bool(prev_ok.all()),
            "prediction_is_frozen_oof_training_role": bool(oof_true == cand_models),
            "candidate_date_equals_frozen_label_entry_date": bool((rows.candidate_date == rows.entry_date).all()),
            "target_date_within_training_period": bool(rows.target_date.le("2024-12-31").all()),
            "no_2025_or_2026_rows": bool(not is_forbidden.any()),
            "no_same_day_or_future_feature": bool((rows.previous_actual_session < rows.candidate_date).all())},
        "inputs": {str(p.relative_to(ROOT)): sha256(p) for p in paths},
        "source_coverage": str(coverage_path.relative_to(ROOT)), "oof_source": str(oof_path.relative_to(ROOT)),
        "technical_folds": {f: {"start": a, "end": b} for f, (a, b) in TECH_FOLDS.items()},
        "ml_fold_definition": "ML V1 frozen 4-fold expanding-window OOF; exact fold is recorded per row and is not equated to Technical WF1-WF3 by name.",
        "probabilities": "read from existing frozen OOF artifact; no probability reconstructed",
        "new_training": False,
    }
    now = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%dT%H%M%S%f")
    outdir = output or (ROOT / "exports" / f"ml_candidate_error_audit_{now}")
    outdir.mkdir(parents=True, exist_ok=False)
    integrity["output_directory"] = str(outdir.relative_to(ROOT))
    integrity["metrics_overall"] = metrics.to_dict(orient="records")
    rows.to_csv(outdir / "candidate_error_rows.csv", index=False, encoding="utf-8-sig")
    confusion.to_csv(outdir / "candidate_confusion_matrix.csv", index=False, encoding="utf-8-sig")
    metrics.to_csv(outdir / "candidate_metrics_overall.csv", index=False, encoding="utf-8-sig")
    by_stock.to_csv(outdir / "candidate_metrics_per_stock.csv", index=False, encoding="utf-8-sig")
    by_fold.to_csv(outdir / "candidate_metrics_per_fold.csv", index=False, encoding="utf-8-sig")
    outcomes.to_csv(outdir / "candidate_prediction_outcome.csv", index=False, encoding="utf-8-sig")
    per_stock_outcomes.to_csv(outdir / "candidate_prediction_outcome_per_stock.csv", index=False, encoding="utf-8-sig")
    per_fold_outcomes.to_csv(outdir / "candidate_prediction_outcome_per_fold.csv", index=False, encoding="utf-8-sig")
    fp.to_csv(outdir / "candidate_false_positive.csv", index=False, encoding="utf-8-sig")
    fn.to_csv(outdir / "candidate_false_negative.csv", index=False, encoding="utf-8-sig")
    (outdir / "candidate_error_audit.json").write_text(json.dumps(integrity, ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")
    render_html(integrity, metrics, confusion, outcomes, outdir / "candidate_error_audit_report.html", user_facing=False)
    render_html(integrity, metrics, confusion, outcomes, outdir / "candidate_error_user_analysis.html", user_facing=True)
    return outdir


if __name__ == "__main__":
    print(run())

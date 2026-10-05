"""Training-only complete diagnosis for the frozen two-industry ML design.

Runs no feature search / parameter search and does not touch 2025 H1 or later.
Outputs classification diagnostics + a diagnostic BUY-only backtest using the
same LOSO x 4-fold predictions.
"""
from __future__ import annotations

import html
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.models import evaluate_multiclass, run_multiclass_model
from ml.trading_pipeline import load_trading_plan
from ml.walk_forward import aggregate_fold_metrics, load_walk_forward_protocol
from scripts.run_ml_industry_generalization import (
    indices_for_outer_fold,
    load_config as load_industry_config,
    make_scaled_data,
    prepare_raw_training,
    split_gru_inner_validation,
    verify_no_heldout_leakage,
)

INDUSTRY_CONFIG = ROOT / "configs/ml_industry_generalization_20261005.json"
DATABASE = ROOT / "data/market_data.sqlite3"
SOURCE_RESULTS = ROOT / "exports/ml_industry_generalization_20261005/industry_generalization_results.json"
OUT = ROOT / "exports/ml_training_diagnosis_20261005"
LABELS = {0: "SELL", 1: "HOLD", 2: "BUY"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(v: Any, digits: int = 4) -> str:
    return "NA" if v is None else f"{float(v):.{digits}f}"


def pct(v: Any) -> str:
    return "NA" if v is None else f"{float(v) * 100:.2f}%"


def load_bars(stock_codes: list[str]) -> pd.DataFrame:
    marks = ",".join("?" for _ in stock_codes)
    sql = f"""
        SELECT stock_code, kbar_timestamp, open, high, low, close
        FROM market_kbars
        WHERE stock_code IN ({marks})
          AND freq_minutes=5
          AND range_start='2023-01-01'
          AND range_end='2024-12-31'
        ORDER BY stock_code, kbar_timestamp
    """
    with sqlite3.connect(DATABASE) as conn:
        frame = pd.read_sql_query(sql, conn, params=stock_codes)
    if frame.empty:
        raise RuntimeError("找不到 2023-2024 Training 5 分 K。")
    frame["stock_code"] = frame["stock_code"].astype(str)
    ts = pd.to_datetime(frame["kbar_timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Taipei")
    frame["trade_date"] = ts.dt.date
    return frame


def stop_exit(
    bars: pd.DataFrame,
    stock: str,
    entry_date: str,
    target_date: str,
    entry_price: float,
    stop_loss: float,
    target_close: float,
) -> tuple[float, str, str]:
    start = pd.Timestamp(entry_date).date()
    end = pd.Timestamp(target_date).date()
    window = bars[
        (bars["stock_code"] == stock)
        & (bars["trade_date"] >= start)
        & (bars["trade_date"] <= end)
    ].sort_values("kbar_timestamp")
    stop_price = entry_price * (1.0 - stop_loss)
    for row in window.itertuples(index=False):
        if float(row.low) <= stop_price:
            price = float(row.open) if float(row.open) <= stop_price else stop_price
            return price, str(row.trade_date), "stop_loss"
    return float(target_close), str(target_date), "target_close"


def stock_backtest(rows: list[dict[str, Any]], bars: pd.DataFrame, rules: dict[str, Any]):
    initial = float(rules["initial_cash"])
    cash = initial
    fee = float(rules["commission_rate"])
    tax = float(rules["transaction_tax_rate"])
    capital_fraction = float(rules["capital_fraction"])
    max_shares = int(rules["max_shares"])
    stop_loss = float(rules["stop_loss"])

    candidates = sorted(
        (r for r in rows if int(r["predicted_class"]) == 2),
        key=lambda r: (r["entry_date"], r["signal_date"]),
    )
    trades = []
    equity = [initial]
    last_exit = None

    for row in candidates:
        entry_date = pd.Timestamp(row["entry_date"]).date()
        if last_exit is not None and entry_date <= last_exit:
            continue

        entry = float(row["entry_open"])
        shares = min(max_shares, int((cash * capital_fraction) / (entry * (1 + fee))))
        if shares < 1:
            continue

        exit_price, exit_date, exit_reason = stop_exit(
            bars,
            str(row["stock_code"]),
            str(row["entry_date"]),
            str(row["target_date"]),
            entry,
            stop_loss,
            float(row["target_close"]),
        )
        buy_cost = shares * entry * (1 + fee)
        sell_net = shares * exit_price * (1 - fee - tax)
        profit = sell_net - buy_cost
        trade_return = profit / buy_cost
        cash = cash - buy_cost + sell_net
        equity.append(cash)
        last_exit = pd.Timestamp(exit_date).date()

        trades.append({
            "industry": row["industry"],
            "model": row["model"],
            "stock_code": str(row["stock_code"]),
            "fold": int(row["fold"]),
            "signal_date": row["signal_date"],
            "entry_date": row["entry_date"],
            "exit_date": exit_date,
            "target_date": row["target_date"],
            "entry_price": entry,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "shares": shares,
            "p_sell": float(row["p_sell"]),
            "p_hold": float(row["p_hold"]),
            "p_buy": float(row["p_buy"]),
            "trade_return": trade_return,
            "profit": profit,
            "equity_after_trade": cash,
        })

    eq = np.asarray(equity, dtype=float)
    peaks = np.maximum.accumulate(eq)
    mdd = float((eq / peaks - 1.0).min()) if len(eq) else 0.0
    profits = np.asarray([t["profit"] for t in trades], dtype=float)
    gains = float(profits[profits > 0].sum()) if len(profits) else 0.0
    losses = float(-profits[profits < 0].sum()) if len(profits) else 0.0
    pf = gains / losses if losses > 0 else None

    metrics = {
        "initial_equity": initial,
        "final_equity": cash,
        "net_profit": cash - initial,
        "total_return": cash / initial - 1.0,
        "trade_count": len(trades),
        "win_rate": float((profits > 0).mean()) if len(profits) else None,
        "max_drawdown_closed_equity": mdd,
        "profit_factor": pf,
    }
    return metrics, trades


def add_prediction_rows(
    output: list[dict[str, Any]],
    industry: str,
    model: str,
    heldout: str,
    fold_number: int,
    data,
    probabilities: np.ndarray,
) -> None:
    meta = data.metadata["generalization"].reset_index(drop=True)
    pred = probabilities.argmax(axis=1)
    for i in range(len(meta)):
        row = meta.iloc[i].to_dict()
        output.append({
            "industry": industry,
            "model": model,
            "heldout_stock": heldout,
            "fold": fold_number,
            **row,
            "actual_class": int(data.y["generalization"][i]),
            "predicted_class": int(pred[i]),
            "p_sell": float(probabilities[i, 0]),
            "p_hold": float(probabilities[i, 1]),
            "p_buy": float(probabilities[i, 2]),
        })


def baseline_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    return aggregate_fold_metrics(items)


def html_table(df: pd.DataFrame) -> str:
    return df.to_html(index=False, border=0, classes="report-table", escape=True)


def main() -> int:
    config = load_industry_config(INDUSTRY_CONFIG)
    plan = load_trading_plan(ROOT / config["base_plan"])
    protocol = load_walk_forward_protocol(ROOT / config["walk_forward_protocol"])
    existing = load_json(SOURCE_RESULTS)

    if existing["audit"].get("training_only") is not True:
        raise RuntimeError("來源 Industry Generalization 不是 Training-only。")
    if any(existing["audit"].get(k) for k in (
        "controlled_validation_used", "development_used", "holdout_used", "final_oos_used"
    )):
        raise RuntimeError("來源結果曾讀取非 Training 資料，停止。")

    raw, audit = prepare_raw_training(DATABASE, plan, config)
    stocks = sorted({str(c) for g in config["industries"].values() for c in g})
    bars = load_bars(stocks)
    models = list(config["models"].keys())

    # 1) 直接整理已完成 LOSO 的分類指標。
    classification_rows = []
    stock_rows = []
    for industry, model_map in existing["industry_summary"].items():
        for model, payload in model_map.items():
            s = payload["outer_fold_aggregate"]
            st = payload["heldout_stock_stability"]
            classification_rows.append({
                "industry": industry,
                "model": model,
                "macro_f1_mean": s["macro_f1"]["mean"],
                "macro_f1_std": s["macro_f1"]["standard_deviation"],
                "macro_f1_worst_fold": s["macro_f1"]["worst"],
                "macro_auc_mean": s["macro_roc_auc_ovr"]["mean"],
                "macro_auc_std": s["macro_roc_auc_ovr"]["standard_deviation"],
                "macro_auc_worst_fold": s["macro_roc_auc_ovr"]["worst"],
                "sell_auc_mean": s["individual_roc_auc_ovr"]["SELL"]["mean"],
                "hold_auc_mean": s["individual_roc_auc_ovr"]["HOLD"]["mean"],
                "buy_auc_mean": s["individual_roc_auc_ovr"]["BUY"]["mean"],
                "stock_f1_std": st["macro_f1_mean_by_stock"]["standard_deviation"],
                "stock_f1_worst": st["macro_f1_mean_by_stock"]["worst"],
                "stock_auc_std": st["macro_auc_mean_by_stock"]["standard_deviation"],
                "stock_auc_worst": st["macro_auc_mean_by_stock"]["worst"],
            })

    for industry, stocks_map in existing["heldout_results"].items():
        for stock, model_map in stocks_map.items():
            for model, s in model_map.items():
                stock_rows.append({
                    "industry": industry,
                    "stock_code": stock,
                    "model": model,
                    "macro_f1_mean": s["macro_f1"]["mean"],
                    "macro_f1_worst_fold": s["macro_f1"]["worst"],
                    "macro_auc_mean": s["macro_roc_auc_ovr"]["mean"],
                    "macro_auc_worst_fold": s["macro_roc_auc_ovr"]["worst"],
                    "sell_auc_mean": s["individual_roc_auc_ovr"]["SELL"]["mean"],
                    "hold_auc_mean": s["individual_roc_auc_ovr"]["HOLD"]["mean"],
                    "buy_auc_mean": s["individual_roc_auc_ovr"]["BUY"]["mean"],
                })

    # 2) 重跑同一套 LOSO，保留 probabilities，供 Baseline + Trading Diagnostic。
    prediction_rows: list[dict[str, Any]] = []
    baseline_folds: dict[str, list[dict[str, Any]]] = defaultdict(list)

    print("========== Training Diagnosis ==========")
    print("Training-only / no tuning / no 2025 H1")

    for industry, stock_list0 in config["industries"].items():
        stock_list = list(map(str, stock_list0))
        print(f"\n[{industry}]")
        for heldout in stock_list:
            peers = [s for s in stock_list if s != heldout]
            print(f"Held-out {heldout}")
            for fold in protocol["walk_forward"]["folds"]:
                fn = int(fold["fold"])
                train_idx, test_idx = indices_for_outer_fold(raw["meta"], peers, heldout, fold)

                priors = np.bincount(raw["y"][train_idx], minlength=3).astype(float)
                priors /= priors.sum()
                base_prob = np.tile(priors, (len(test_idx), 1))
                baseline_folds[industry].append({
                    "fold": fn,
                    "heldout_stock": heldout,
                    **evaluate_multiclass(raw["y"][test_idx], base_prob),
                })

                common = {
                    "industry": industry,
                    "heldout_stock": heldout,
                    "peer_stocks": peers,
                    "fold": fn,
                    "periods": dict(fold),
                    "heldout_stock_in_training": False,
                    "heldout_stock_in_gru_early_stopping": False,
                }

                for model in ("random_forest", "xgboost"):
                    data = make_scaled_data(
                        raw, train_idx, {"generalization": test_idx},
                        include_context=False,
                        stage="industry_generalization_fold",
                        audit_extra={**common, "feature_set": config["model_feature_policy"][model]["feature_set"]},
                    )
                    verify_no_heldout_leakage(data, heldout)
                    _, _, probs = run_multiclass_model(
                        model, data, config["models"][model]["params"],
                        evaluation_splits=("generalization",),
                    )
                    add_prediction_rows(prediction_rows, industry, model, heldout, fn, data, probs["generalization"])

                inner_train, inner_val, inner_start = split_gru_inner_validation(
                    raw, train_idx, float(config["gru_inner_validation_fraction"]), fn, heldout
                )
                gru_inner = make_scaled_data(
                    raw, inner_train, {"validation": inner_val}, include_context=True,
                    stage="walk_forward_fold",
                    audit_extra={**common, "feature_set": config["model_feature_policy"]["gru"]["feature_set"],
                                 "gru_inner_validation_start": inner_start},
                )
                verify_no_heldout_leakage(gru_inner, heldout)
                _, inner_metrics, _ = run_multiclass_model(
                    "gru", gru_inner, config["models"]["gru"]["params"],
                    evaluation_splits=("validation",),
                )
                epochs = int(inner_metrics["epochs_completed"])
                gru_final = make_scaled_data(
                    raw, train_idx, {"generalization": test_idx}, include_context=True,
                    stage="industry_generalization_refit",
                    audit_extra={**common, "feature_set": config["model_feature_policy"]["gru"]["feature_set"],
                                 "gru_fixed_epochs_from_peer_inner_validation": epochs,
                                 "gru_inner_validation_start": inner_start},
                )
                verify_no_heldout_leakage(gru_final, heldout)
                _, _, probs = run_multiclass_model(
                    "gru", gru_final, config["models"]["gru"]["params"],
                    fixed_epochs=epochs,
                    evaluation_splits=("generalization",),
                )
                add_prediction_rows(prediction_rows, industry, "gru", heldout, fn, gru_final, probs["generalization"])
                print(f"  Fold {fn}: train={len(train_idx):,}, test={len(test_idx):,}, GRU epochs={epochs}")

    pred_df = pd.DataFrame(prediction_rows)

    # 3) Prediction distribution + aggregated confusion matrix.
    distribution_rows = []
    confusion_rows = []
    for industry in config["industries"]:
        for model in models:
            d = pred_df[(pred_df["industry"] == industry) & (pred_df["model"] == model)]
            for kind, column in (("actual", "actual_class"), ("predicted", "predicted_class")):
                for label_id, label in LABELS.items():
                    count = int((d[column] == label_id).sum())
                    distribution_rows.append({
                        "industry": industry, "model": model, "type": kind,
                        "class": label, "count": count,
                        "proportion": count / len(d) if len(d) else 0.0,
                    })
            cm = np.zeros((3, 3), dtype=int)
            for a, p in zip(d["actual_class"].astype(int), d["predicted_class"].astype(int)):
                cm[a, p] += 1
            for a in range(3):
                for p in range(3):
                    confusion_rows.append({
                        "industry": industry, "model": model,
                        "actual": LABELS[a], "predicted": LABELS[p], "count": int(cm[a, p])
                    })

    # 4) Trading Diagnostic：Argmax BUY only，不調 threshold。
    trading_rows = []
    trading_stock_rows = []
    all_trades = []
    rules = plan["trading"]

    for industry, stock_list0 in config["industries"].items():
        stock_list = list(map(str, stock_list0))
        for model in models:
            per_stock = {}
            model_trades = []
            for stock in stock_list:
                rows = pred_df[
                    (pred_df["industry"] == industry)
                    & (pred_df["model"] == model)
                    & (pred_df["stock_code"].astype(str) == stock)
                ].to_dict("records")
                metrics, trades = stock_backtest(rows, bars, rules)
                per_stock[stock] = metrics
                model_trades.extend(trades)
                all_trades.extend(trades)
                trading_stock_rows.append({"industry": industry, "model": model, "stock_code": stock, **metrics})

            initial = sum(v["initial_equity"] for v in per_stock.values())
            final = sum(v["final_equity"] for v in per_stock.values())
            profits = np.asarray([t["profit"] for t in model_trades], dtype=float)
            gains = float(profits[profits > 0].sum()) if len(profits) else 0.0
            losses = float(-profits[profits < 0].sum()) if len(profits) else 0.0
            mdds = [v["max_drawdown_closed_equity"] for v in per_stock.values()]
            trading_rows.append({
                "industry": industry,
                "model": model,
                "aggregation": "5 independent equal-weight 100k subaccounts",
                "initial_equity": initial,
                "final_equity": final,
                "net_profit": final - initial,
                "total_return": final / initial - 1.0,
                "trade_count": int(sum(v["trade_count"] for v in per_stock.values())),
                "win_rate": float((profits > 0).mean()) if len(profits) else None,
                "mean_stock_max_drawdown": float(np.mean(mdds)),
                "worst_stock_max_drawdown": float(np.min(mdds)),
                "profit_factor": gains / losses if losses > 0 else None,
            })

    baseline_rows = []
    for industry, items in baseline_folds.items():
        s = baseline_summary(items)
        baseline_rows.append({
            "industry": industry,
            "baseline": "peer-training class-prior argmax",
            "macro_f1_mean": s["macro_f1"]["mean"],
            "macro_f1_worst": s["macro_f1"]["worst"],
            "macro_auc_mean": s["macro_roc_auc_ovr"]["mean"],
            "macro_auc_worst": s["macro_roc_auc_ovr"]["worst"],
        })

    # 5) Save outputs.
    OUT.mkdir(parents=True, exist_ok=True)
    cdf = pd.DataFrame(classification_rows)
    sdf = pd.DataFrame(stock_rows)
    ddf = pd.DataFrame(distribution_rows)
    cmf = pd.DataFrame(confusion_rows)
    bdf = pd.DataFrame(baseline_rows)
    tdf = pd.DataFrame(trading_rows)
    tsdf = pd.DataFrame(trading_stock_rows)
    trades_df = pd.DataFrame(all_trades)

    cdf.to_csv(OUT / "classification_summary.csv", index=False, encoding="utf-8-sig")
    sdf.to_csv(OUT / "stock_summary.csv", index=False, encoding="utf-8-sig")
    ddf.to_csv(OUT / "class_distribution.csv", index=False, encoding="utf-8-sig")
    cmf.to_csv(OUT / "confusion_matrix.csv", index=False, encoding="utf-8-sig")
    bdf.to_csv(OUT / "baseline_summary.csv", index=False, encoding="utf-8-sig")
    tdf.to_csv(OUT / "trading_summary.csv", index=False, encoding="utf-8-sig")
    tsdf.to_csv(OUT / "trading_per_stock.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(OUT / "trades.csv", index=False, encoding="utf-8-sig")

    report = {
        "report_name": "ML Training-only Complete Diagnosis",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": {
            "period": ["2023-01-01", "2024-12-31"],
            "method": "5-stock LOSO x 4-fold expanding-window",
            "industries": config["industries"],
            "feature_policy": config["model_feature_policy"],
            "trading_signal": "argmax class == BUY",
            "trading_rule_source": "configs/ml_trading_v3_20261004.json",
            "trading_metrics_are_diagnostic_only": True,
        },
        "guardrails": {
            "training_only": True,
            "controlled_validation_used": False,
            "development_used": False,
            "holdout_used": False,
            "final_oos_used": False,
            "feature_search": False,
            "parameter_search": False,
            "probability_threshold_search": False,
            "trading_metrics_used_for_model_selection": False,
            "heldout_stock_never_in_training": True,
            "heldout_stock_never_in_gru_early_stopping": True,
        },
        "data_audit": audit,
        "classification_summary": classification_rows,
        "baseline_summary": baseline_rows,
        "trading_summary": trading_rows,
    }
    (OUT / "training_diagnosis_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )

    # 簡單 HTML，重點是讓你可以直接開來看表格。
    html_text = f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>
    <title>ML Training Diagnosis</title><style>
    body{{font-family:system-ui,'Noto Sans TC';max-width:1600px;margin:28px auto;padding:0 18px;background:#f6f8fb;color:#182536}}
    section{{background:white;border:1px solid #d9e3eb;border-radius:12px;padding:18px;margin:16px 0;overflow:auto}}
    table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d5dfe8;padding:6px;text-align:right}}th{{background:#eaf3f8}}td:first-child,th:first-child{{text-align:left}}
    h1,h2{{color:#0b527a}}.note{{background:#fff8e8}}</style></head><body>
    <h1>ML Training-only 完整診斷</h1>
    <section class='note'><h2>使用原則</h2><p>本報告只用 2023-2024 Training。Trading Metrics 只做診斷，不可拿來重新調 Feature、模型參數、Label 或 BUY 機率門檻。Max Drawdown 為平倉後權益序列的 Training 診斷值；最終 Hybrid Backtest 再做完整每日 Mark-to-Market。</p></section>
    <section><h2>1. Macro F1 / AUC / Worst Fold / Stock Stability</h2>{html_table(cdf)}</section>
    <section><h2>2. Baseline</h2>{html_table(bdf)}</section>
    <section><h2>3. BUY / HOLD / SELL Distribution</h2>{html_table(ddf)}</section>
    <section><h2>4. Confusion Matrix</h2>{html_table(cmf)}</section>
    <section><h2>5. Per-stock Generalization</h2>{html_table(sdf)}</section>
    <section><h2>6. Training Backtest Diagnostic</h2>{html_table(tdf)}</section>
    <section><h2>7. Trading Per Stock</h2>{html_table(tsdf)}</section>
    </body></html>"""
    (OUT / "training_diagnosis_report.html").write_text(html_text, encoding="utf-8")

    print("\n========== 完成 ==========")
    print(f"HTML: {OUT / 'training_diagnosis_report.html'}")
    print(f"JSON: {OUT / 'training_diagnosis_results.json'}")
    print(f"Classification: {OUT / 'classification_summary.csv'}")
    print(f"Trading: {OUT / 'trading_summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

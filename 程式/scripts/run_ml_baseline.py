"""執行已事先登記且不調參的 RF／XGBoost／GRU baseline。"""
from __future__ import annotations

import csv
import html
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.data_pipeline import load_plan, prepare
from ml.models import run_model
from trading_system.research_guard import assert_payload

PLAN_PATH = ROOT / "configs/ml_baseline_20260927.json"
OUTPUT_DIR = ROOT / "exports/ml_baseline_20260927"
DATABASE = ROOT / "data/ml_research.sqlite3"


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def save_database(plan, audit, results):
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS ml_experiments(
          experiment_id TEXT PRIMARY KEY, status TEXT NOT NULL, data_role TEXT NOT NULL,
          period_start TEXT NOT NULL, period_end TEXT NOT NULL, target TEXT NOT NULL,
          created_at TEXT NOT NULL, plan_json TEXT NOT NULL, audit_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ml_model_metrics(
          experiment_id TEXT NOT NULL, model TEXT NOT NULL, split TEXT NOT NULL,
          samples INTEGER NOT NULL, accuracy REAL, precision REAL, recall REAL, f1 REAL,
          roc_auc REAL, training_seconds REAL, prediction_seconds REAL,
          confusion_matrix_json TEXT NOT NULL, PRIMARY KEY(experiment_id, model, split));
        CREATE TABLE IF NOT EXISTS ml_stock_metrics(
          experiment_id TEXT NOT NULL, model TEXT NOT NULL, split TEXT NOT NULL, stock_code TEXT NOT NULL,
          samples INTEGER NOT NULL, accuracy REAL, precision REAL, recall REAL, f1 REAL, roc_auc REAL,
          confusion_matrix_json TEXT NOT NULL, PRIMARY KEY(experiment_id, model, split, stock_code));
        """)
        exp = plan["experiment_id"]
        db.execute("DELETE FROM ml_stock_metrics WHERE experiment_id=?", (exp,))
        db.execute("DELETE FROM ml_model_metrics WHERE experiment_id=?", (exp,))
        db.execute("DELETE FROM ml_experiments WHERE experiment_id=?", (exp,))
        db.execute("INSERT INTO ml_experiments VALUES(?,?,?,?,?,?,?,?,?)", (
            exp, "completed", plan["data_role"], plan["period"]["start"], plan["period"]["end"],
            plan["target"]["name"], results["generated_at"], _json(plan), _json(audit)))
        for model, payload in results["models"].items():
            for split, metrics in payload["splits"].items():
                db.execute("INSERT INTO ml_model_metrics VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                    exp, model, split, metrics["samples"], metrics["accuracy"], metrics["precision"], metrics["recall"], metrics["f1"], metrics["roc_auc"], payload["training_seconds"], metrics["prediction_seconds"], _json(metrics["confusion_matrix"])))
                for code, stock in metrics["per_stock"].items():
                    db.execute("INSERT INTO ml_stock_metrics VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                        exp, model, split, code, stock["samples"], stock["accuracy"], stock["precision"], stock["recall"], stock["f1"], stock["roc_auc"], _json(stock["confusion_matrix"])))


def write_csv(results):
    rows = []
    stock_rows = []
    for model, payload in results["models"].items():
        for split, m in payload["splits"].items():
            rows.append({k: v for k, v in {"model": model, "split": split, "training_seconds": payload["training_seconds"], **m}.items() if k not in {"per_stock", "confusion_matrix"}})
            for code, item in m["per_stock"].items():
                stock_rows.append({k: v for k, v in {"model": model, "split": split, "stock_code": code, **item}.items() if k != "confusion_matrix"})
    for name, data in (("model_summary.csv", rows), ("per_stock_metrics.csv", stock_rows)):
        with (OUTPUT_DIR / name).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0])); writer.writeheader(); writer.writerows(data)


def render_html(plan, audit, results):
    development_rows = []
    stock_rows = []
    for name, payload in results["models"].items():
        m = payload["splits"]["development"]
        development_rows.append(f"<tr><td>{html.escape(name)}</td><td>{m['samples']:,}</td><td>{m['accuracy']:.3f}</td><td>{m['precision']:.3f}</td><td>{m['recall']:.3f}</td><td>{m['f1']:.3f}</td><td>{m['roc_auc']:.3f}</td><td>{payload['training_seconds']:.1f} 秒</td></tr>")
        for code, s in m["per_stock"].items():
            auc_text = "—" if s["roc_auc"] is None else f"{s['roc_auc']:.3f}"
            stock_rows.append(f"<tr><td>{html.escape(name)}</td><td>{code}</td><td>{s['samples']:,}</td><td>{s['accuracy']:.3f}</td><td>{s['f1']:.3f}</td><td>{auc_text}</td></tr>")
    benchmark = results["benchmarks"]["always_predict_not_up"]["development"]
    return f"""<!doctype html><html lang='zh-Hant'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>ML Baseline 模型比較</title>
<style>body{{font-family:system-ui,'Noto Sans TC',sans-serif;margin:32px;color:#223}}.card{{border:1px solid #dbe2ea;border-radius:12px;padding:18px;margin:16px 0}}table{{border-collapse:collapse;width:100%}}th,td{{padding:9px;border:1px solid #dde3ea;text-align:right}}th:first-child,td:first-child,td:nth-child(2){{text-align:left}}.warn{{background:#fff4cc}}.ok{{background:#e9f7ef}}code{{background:#eef2f6;padding:2px 5px}}</style>
<h1>機器學習 Baseline：RF／XGBoost／GRU</h1>
<div class='card warn'><b>資料身分：</b>已看過的開發資料（ml_development_seen），不是獨立驗證。<br><b>Holdout：</b>2025/07/01～2025/12/31 仍為 locked / forbidden，本次完全未讀取。<br><b>限制：</b>這是方向預測模型比較，尚未轉成買賣策略或報酬率。</div>
<div class='card'><h2>大家比的是同一道題目</h2><p>使用前 24 根完成的 5 分 K，預測 12 根後（約 60 分鐘）收盤是否高於目前收盤。這是歷史 baseline，三段都位於已看過的 2026H1 Development；標準化只用其中的 train 段估計。</p><p>訓練段 {audit['samples']['train']:,} 筆、驗證段 {audit['samples']['validation']:,} 筆、Development 評估段 {audit['samples']['development']:,} 筆。</p></div>
<h2>Development 區間結果</h2><table><thead><tr><th>模型</th><th>樣本</th><th>正確率</th><th>精確率</th><th>召回率</th><th>F1</th><th>ROC AUC</th><th>訓練時間</th></tr></thead><tbody>{''.join(development_rows)}<tr><td>永遠猜不漲（簡單基準）</td><td>{benchmark['samples']:,}</td><td>{benchmark['accuracy']:.3f}</td><td>0.000</td><td>0.000</td><td>0.000</td><td>0.500</td><td>0 秒</td></tr></tbody></table>
<h2>各股票 Development 結果</h2><table><thead><tr><th>模型</th><th>股票</th><th>樣本</th><th>正確率</th><th>F1</th><th>ROC AUC</th></tr></thead><tbody>{''.join(stock_rows)}</tbody></table>
<div class='card ok'><h2>怎麼看</h2><p>正確率看整體猜對比例；F1 同時考慮「猜上漲時準不準」與「真正上漲抓到多少」；ROC AUC 看模型排序能力，0.5 附近代表接近隨機。因為樣本中「不上漲」較多，永遠猜不漲也有 {benchmark['accuracy']:.1%} 正確率，所以不能只看正確率。這些數值不能直接當成投資報酬。</p></div>
<p>實驗：<code>{html.escape(plan['experiment_id'])}</code>｜產生：{html.escape(results['generated_at'])}</p></html>"""


def main():
    plan = load_plan(PLAN_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare(ROOT / "data/market_data.sqlite3", plan)
    results = {"experiment_id": plan["experiment_id"], "generated_at": datetime.now().isoformat(timespec="seconds"), "data_role": plan["data_role"], "period": plan["period"], "target": plan["target"], "models": {}, "benchmarks": {"always_predict_not_up": {}}}
    for split in ("validation", "development"):
        positives = int(data.y[split].sum()); samples = int(len(data.y[split])); negatives = samples - positives
        results["benchmarks"]["always_predict_not_up"][split] = {"samples": samples, "accuracy": negatives / samples, "precision": 0.0, "recall": 0.0, "f1": 0.0, "roc_auc": 0.5, "confusion_matrix": [[negatives, 0], [positives, 0]]}
    for name in ("random_forest", "xgboost", "gru"):
        print(f"訓練 {name}...", flush=True)
        _, metrics, _ = run_model(name, data, plan["models"][name])
        results["models"][name] = metrics
    assert_payload(results)
    (OUTPUT_DIR / "data_audit.json").write_text(_json(data.audit), encoding="utf-8")
    (OUTPUT_DIR / "scaler.json").write_text(_json({"feature_names": data.feature_names, "mean": data.scaler_mean.tolist(), "scale": data.scaler_scale.tolist(), "fit_on": "train_only"}), encoding="utf-8")
    (OUTPUT_DIR / "results.json").write_text(_json(results), encoding="utf-8")
    write_csv(results)
    html_text = render_html(plan, data.audit, results)
    (OUTPUT_DIR / "report.html").write_text(html_text, encoding="utf-8")
    (ROOT / "exports/ml_baseline_latest.html").write_text(html_text, encoding="utf-8")
    save_database(plan, data.audit, results)
    print(f"完成：{OUTPUT_DIR / 'report.html'}")


if __name__ == "__main__":
    main()

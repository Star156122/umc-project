"""Build a documentation snapshot from already completed Strategy V2 outputs.

This script does not load market data, train models, or execute a backtest. It
filters the existing Risk V2 audit bundle to its reproduced RISK_V1 control.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "exports/risk_v2_exp001_20261019T180446688808"
BASELINE_MANIFEST = ROOT / "research/strategy_v2_baseline_manifest.json"
TECH_CONFIG = ROOT / "configs/technical_v2_candidate.json"
TECH_SOURCE = ROOT / "trading_system/technical_v1.py"
ML_OOF = ROOT / "exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv"
RISK_CONFIG = ROOT / "hybrid/config/risk_v1.json"
RISK_AUDIT = SOURCE_DIR / "risk_v2_exp001_audit.json"
STOCKS = ["2303", "2330", "2379", "2454", "3034"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_rows(path: Path, variant: str | None = None) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if variant is not None:
            rows = [row for row in rows if row.get("variant") == variant]
        return list(reader.fieldnames or []), rows


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def pct(value: str | float) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def money(value: str | float) -> str:
    try:
        return f"NT$ {float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def esc(value: object) -> str:
    return html.escape(str(value))


def main() -> None:
    required = [SOURCE_DIR / name for name in (
        "risk_v2_exp001_overall.csv", "risk_v2_exp001_per_stock.csv",
        "risk_v2_exp001_per_fold.csv", "risk_v2_exp001_audit.json",
    )]
    required += [BASELINE_MANIFEST, TECH_CONFIG, TECH_SOURCE, ML_OOF, RISK_CONFIG]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing required frozen evidence: {missing}")

    audit = read_json(RISK_AUDIT)
    baseline = read_json(BASELINE_MANIFEST)
    risk_audit = audit.get("audit", audit)
    if risk_audit.get("risk_v1_reproduction") != "PASS":
        raise SystemExit("Existing RISK_V1 control reproduction evidence is not PASS")
    if risk_audit.get("final_oos_used") is True or risk_audit.get("2025_used") is True or risk_audit.get("2026_used") is True:
        raise SystemExit("Source audit indicates a forbidden data role was used")

    fields, overall = read_csv_rows(SOURCE_DIR / "risk_v2_exp001_overall.csv", "RISK_V1")
    stock_fields, per_stock = read_csv_rows(SOURCE_DIR / "risk_v2_exp001_per_stock.csv", "RISK_V1")
    fold_fields, per_fold = read_csv_rows(SOURCE_DIR / "risk_v2_exp001_per_fold.csv", "RISK_V1")
    if len(overall) != 1 or len(per_stock) != 5 or len(per_fold) != 3:
        raise SystemExit("Existing RISK_V1 evidence rows are incomplete")
    if {row.get("stock_code") for row in per_stock} != set(STOCKS):
        raise SystemExit("Existing RISK_V1 per-stock set does not match the frozen universe")

    now = datetime.now(ZoneInfo("Asia/Taipei"))
    run_id = now.strftime("%Y%m%dT%H%M%S%f")
    out_dir = ROOT / "exports" / f"strategy_v2_development_freeze_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=False)

    technical = json.loads(TECH_CONFIG.read_text(encoding="utf-8"))
    risk = read_json(RISK_CONFIG)
    technical_research = read_json(ROOT / "configs/technical_v1_research.json")
    folds = technical.get("walk_forward_folds", [])
    db_hashes = baseline.get("v2_control", {}).get("current_v2_control_definition", {})
    ml_meta = baseline.get("ml", {})
    freeze_manifest = {
        "freeze_id": f"strategy-v2-single-stock-development-freeze-{run_id}",
        "created_at": now.isoformat(),
        "strategy_v2_status": "FROZEN_DEVELOPMENT_CANDIDATE",
        "scope": "Single-stock, long-only, exposed 2023-2024 development evidence; not Final OOS.",
        "git": {
            "commit": git_output("rev-parse", "HEAD"),
            "working_tree_status": "DIRTY" if git_output("status", "--porcelain") else "CLEAN",
            "working_tree_note": "Working tree was already dirty before this freeze; hashes below identify the frozen current files. Freeze files themselves also make the tree dirty.",
        },
        "candidate": {
            "technical": "BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER",
            "technical_candidate_id": technical.get("base_candidate"),
            "technical_parameters": technical.get("candidate_parameters"),
            "ml_model": "ML V1 Random Forest OOF",
            "ml_gate": {"BUY": "ALLOW_LONG_ENTRY", "HOLD": "BLOCK", "SELL": "BLOCK", "short": False},
            "risk": {"version": "Risk V1", "risk_per_trade": risk["risk_per_trade"], "stop_loss_pct": risk["stop_loss_pct"], "max_position_fraction": risk["max_position_fraction"]},
            "execution_contract": "COMPLETED_5_MINUTE_SIGNAL_TO_NEXT_TRADABLE_5_MINUTE_BAR_OPEN",
            "short": False,
            "portfolio": False,
        },
        "freeze_invariants": {
            "technical_parameters_frozen": True,
            "ml_model_frozen": True,
            "ml_gate_frozen": True,
            "risk_v1_frozen": True,
            "execution_frozen": True,
            "transaction_cost_frozen": True,
            "stock_universe_frozen": True,
            "no_short": True,
            "no_portfolio": True,
            "candidate_ml_v2_included": False,
            "sell_veto_included": False,
            "risk_v2_atr_included": False,
            "final_oos_used": False,
        },
        "research_data": {
            "role": "EXPOSED_DEVELOPMENT",
            "period": {"start": "2023-01-01", "end": "2024-12-31"},
            "stock_universe": STOCKS,
            "folds": folds,
            "walk_forward_fold_ids": [fold.get("id") for fold in folds],
        },
        "transaction_cost": technical.get("preserved_common_backtest", {}).get("stock_fee_rate"),
        "transaction_costs": technical.get("preserved_common_backtest", {}).get("stock_fee_rate") is not None and {
            key: technical.get("preserved_common_backtest", {}).get(key)
            for key in ("stock_fee_rate", "stock_tax_rate", "day_trade_tax_rate", "apply_day_trade_tax", "slippage_rate")
        } or technical_research.get("costs", {}),
        "source_identity": {
            "technical_config": {"path": "configs/technical_v2_candidate.json", "sha256": sha256(TECH_CONFIG)},
            "technical_source": {"path": "trading_system/technical_v1.py", "sha256": sha256(TECH_SOURCE)},
            "ml_oof": {"path": "exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv", "sha256": sha256(ML_OOF)},
            "risk_config": {"path": "hybrid/config/risk_v1.json", "sha256": sha256(RISK_CONFIG)},
            "technical_database": {"path": "data/technical_v1_mltech_clean.sqlite3", "sha256": db_hashes.get("technical_database_sha256"), "hash_source": "existing parity audit snapshot; raw DB not opened by this freeze"},
            "ml_database": {"path": "data/market_data.sqlite3", "sha256": db_hashes.get("ml_database_sha256"), "hash_source": "existing parity audit snapshot; raw DB not opened by this freeze"},
        },
        "data_exposure": baseline.get("data_exposure", {}),
        "validation_data_status": {
            "2025_H1": "EXPOSED",
            "2025_H2": "UNKNOWN",
            "2026_H1": "EXPOSED",
            "2026_H2_later": "PARTIALLY_EXPOSED",
        },
        "rejected_or_non_adopted": [
            {"experiment": "Technical A Volume Confirmation", "classification": "MIXED", "decision": "NOT_ADOPTED", "reason": "Evidence was mixed; frozen Technical V2-B is the supported structural component."},
            {"experiment": "Technical C EMA20 Exit", "classification": "MIXED", "decision": "NOT_ADOPTED", "reason": "Evidence was mixed and did not justify replacing the existing exit."},
            {"experiment": "Hybrid SELL-Veto", "classification_by_model": {"random_forest": "BALANCED_GATE_MIXED", "xgboost": "BALANCED_GATE_NOT_SUPPORTED", "gru": "BALANCED_GATE_MIXED"}, "decision": "NOT_ADOPTED", "reason": "No model was classified supported, so evidence did not justify changing the BUY-only gate."},
            {"experiment": "Candidate-specific ML V2", "classification_by_model": {"random_forest": "NOT_SUPPORTED", "xgboost": "NOT_SUPPORTED", "gru": "MIXED"}, "decision": "NOT_ADOPTED", "reason": "The candidate-specific comparison did not support replacing ML V1 RF."},
            {"experiment": "Risk V2 ATR Adaptive", "classification": "RISK_V2_MIXED", "decision": "NOT_ADOPTED", "reason": "Worst drawdown improved, but average return and Sharpe fell relative to Risk V1."},
        ],
        "reproduction_evidence": {
            "status": "VERIFIED_FROM_EXISTING_REPRODUCTION_AUDITS",
            "technical_candidate_audit": "exports/strategy_v2_technical_candidate_20261019T103707108020/technical_v2_candidate_audit.json",
            "risk_v1_reproduction_audit": "exports/risk_v2_exp001_20261019T180446688808/risk_v2_exp001_audit.json",
            "risk_v1_reproduction": risk_audit.get("risk_v1_reproduction"),
            "metric_match_checks": risk_audit.get("risk_v1_reproduction_metrics_match"),
            "new_backtest_run_this_freeze": False,
        },
        "final_oos": {"available_now": False, "status": "NO_EXISTING_PROVEN_CLEAN_UNEXPOSED_RANGE", "used": False},
        "immutability": {"strategy_changed": False, "ml_retrained": False, "parameter_search": False, "new_experiment": False, "forbidden_period_data_read": False},
    }

    # Copy only the already generated RISK_V1 rows; original result files remain untouched.
    write_csv(out_dir / "strategy_v2_development_summary.csv", fields, overall)
    write_csv(out_dir / "strategy_v2_per_stock_summary.csv", stock_fields, per_stock)
    write_csv(out_dir / "strategy_v2_per_fold_summary.csv", fold_fields, per_fold)
    (out_dir / "strategy_v2_freeze_manifest.json").write_text(json.dumps(freeze_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "research/strategy_v2_freeze_manifest.json").write_text(
        json.dumps(freeze_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = overall[0]
    def table(headers: list[str], rows: list[list[object]]) -> str:
        head = "".join(f"<th>{esc(item)}</th>" for item in headers)
        body = "".join("<tr>" + "".join(f"<td>{esc(item)}</td>" for item in row) + "</tr>" for row in rows)
        return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"

    audit_data = {
        "status": "COMPLETED_FROM_EXISTING_EVIDENCE",
        "freeze_id": freeze_manifest["freeze_id"],
        "generated_at": now.isoformat(),
        "input_source_directory": str(SOURCE_DIR.relative_to(ROOT)),
        "input_files": {name: sha256(SOURCE_DIR / name) for name in (
            "risk_v2_exp001_overall.csv", "risk_v2_exp001_per_stock.csv", "risk_v2_exp001_per_fold.csv", "risk_v2_exp001_audit.json")},
        "rows_copied": {"overall": len(overall), "per_stock": len(per_stock), "per_fold": len(per_fold)},
        "risk_v1_reproduction": risk_audit.get("risk_v1_reproduction"),
        "data_period": ["2023-01-01", "2024-12-31"],
        "data_roles_opened": ["EXPOSED_DEVELOPMENT_2023_2024_EXISTING_EXPORTS_ONLY"],
        "raw_database_opened": False,
        "forbidden_period_data_read": False,
        "final_oos_used": False,
        "ml_retrained": False,
        "backtest_run": False,
        "parameter_search_performed": False,
        "strategy_changed": False,
        "source_hashes": freeze_manifest["source_identity"],
    }
    (out_dir / "strategy_v2_freeze_audit.json").write_text(json.dumps(audit_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    overall_rows = [[
        "Overall", pct(summary.get("average_return")), pct(summary.get("worst_mdd")), pct(summary.get("win_rate")),
        f"{float(summary.get('profit_factor', 0)):.3f}", f"{float(summary.get('sharpe', 0)):.3f}",
        summary.get("trade_count"), money(summary.get("net_pnl")), money(summary.get("transaction_cost")),
    ]]
    stock_rows = [[row["stock_code"], pct(row.get("average_return")), pct(row.get("worst_mdd")), f"{float(row.get('profit_factor', 0)):.2f}", row.get("trade_count"), money(row.get("net_pnl"))] for row in per_stock]
    fold_rows = [[row["fold_id"], pct(row.get("average_return")), pct(row.get("worst_mdd")), f"{float(row.get('profit_factor', 0)):.2f}", row.get("trade_count"), money(row.get("net_pnl"))] for row in per_fold]
    styles = """
    :root{color-scheme:dark;--bg:#07111b;--panel:#0e1e2b;--line:#243b4e;--blue:#1478f8;--text:#e8f1f8;--muted:#9bb1c3}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 system-ui,'Microsoft JhengHei',sans-serif}
    main{max-width:1100px;margin:auto;padding:32px 20px 60px}.hero,.card{border:1px solid var(--line);border-radius:20px;background:var(--panel);padding:24px;margin-bottom:18px}
    .hero{background:#0a2b42;border-color:#125274}.tag{display:inline-block;border:1px solid #4897d0;border-radius:999px;padding:3px 12px;color:#9bd3ff;font-size:14px}
    h1{margin:8px 0;font-size:30px}h2{font-size:21px;margin:0 0 14px}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
    .metric{border:1px solid var(--line);border-radius:14px;padding:14px}.metric b{display:block;font-size:21px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:#a9c9e2}.table-wrap{overflow:auto}
    .notice{border-left:4px solid #ffbf47;padding:10px 14px;background:#302712;border-radius:6px}.blue{color:#75baff}@media(max-width:640px){main{padding:16px 12px}h1{font-size:24px}}
    """
    html_doc = f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Strategy V2 Development Freeze</title><style>{styles}</style><main>
    <section class="hero"><span class="tag">FROZEN DEVELOPMENT CANDIDATE</span><h1>Strategy V2 單股開發凍結</h1><p>目前固定版本：Technical V2-B + RF ML V1 BUY-only + Risk V1。</p><p class="muted">資料：2023–2024 已曝光開發資料　｜　NOT Final OOS</p></section>
    <section class="card"><h2>策略如何決定進場</h2><p>Technical V2-B 產生 BUY Candidate；只有前一交易日 Random Forest OOF 預測為 BUY 時才允許做多。HOLD 或 SELL 都不進場；SELL 不代表放空。</p><p>Risk V1 每筆風險預算 1%，停損 2.5%，部位上限 40%。訊號在 5 分 K 完成後確認，下一個可交易 5 分 K 開盤模擬進場。</p></section>
    <section class="card"><h2>既有開發結果（Risk V1 Control）</h2><div class="grid">
    <div class="metric">平均報酬<b>{pct(summary.get('average_return'))}</b></div><div class="metric">最差回撤<b>{pct(summary.get('worst_mdd'))}</b></div><div class="metric">勝率<b>{pct(summary.get('win_rate'))}</b></div><div class="metric">Profit Factor<b>{float(summary.get('profit_factor',0)):.3f}</b></div><div class="metric">Sharpe<b>{float(summary.get('sharpe',0)):.3f}</b></div><div class="metric">交易次數<b>{summary.get('trade_count')}</b></div><div class="metric">Net PnL<b>{money(summary.get('net_pnl'))}</b></div><div class="metric">交易成本<b>{money(summary.get('transaction_cost'))}</b></div></div>
    <p class="muted">來自既有 Risk V2 實驗中通過重現檢查的 RISK_V1 Control；本次只整理既有輸出，沒有重跑回測。</p></section>
    <section class="card"><h2>各股票結果</h2>{table(["股票","平均報酬","最差回撤","PF","交易數","Net PnL"], stock_rows)}</section>
    <section class="card"><h2>各時間 Fold 結果</h2>{table(["Fold","平均報酬","最差回撤","PF","交易數","Net PnL"], fold_rows)}</section>
    <section class="card"><h2>研究決策</h2><p>保留 Technical V2-B，因其是 Technical V2 中獲得 Supported 的結構性元件；保留 ML V1 RF BUY-only，既有比較支持其增量價值；Risk V2 ATR 結果為 Mixed，故維持 Risk V1。Technical A、Technical C、SELL-Veto 與 Candidate-specific ML V2 均不納入凍結候選。</p></section>
    <section class="card"><h2>驗證資料狀態</h2><p>目前沒有可由專案紀錄證明完全未曝光的現存 Final OOS。2025 H1 與 2026 H1 已曝光；2025 H2 為 Unknown；2026 下半年以後已有部分股票資料曝光，因此維持 Partially Exposed。未來 Final OOS 只能使用凍結後新累積且曝光狀態可證明乾淨的資料。</p><div class="notice">本頁是開發階段紀錄，不代表策略已通過獨立未來資料驗證，也不保證未來績效。</div></section>
    <section class="card"><h2>凍結檔案</h2><p class="muted">Freeze ID：{esc(freeze_manifest['freeze_id'])}</p><p class="muted">Manifest：strategy_v2_freeze_manifest.json　｜　Audit：strategy_v2_freeze_audit.json</p></section>
    </main></html>"""
    (out_dir / "strategy_v2_freeze_report.html").write_text(html_doc, encoding="utf-8")
    (out_dir / "strategy_v2_freeze_user_analysis.html").write_text(html_doc, encoding="utf-8")
    print(out_dir.relative_to(ROOT))


if __name__ == "__main__":
    main()

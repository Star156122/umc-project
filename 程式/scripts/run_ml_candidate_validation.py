"""Run one formal 2025 H1 Controlled Validation for an existing V3 Candidate."""
from __future__ import annotations

import json
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from ml.candidate_artifact import file_sha256, load_candidate, predict_candidate
from ml.models import evaluate_multiclass
from ml.trading_backtest import run_signal_backtest
from ml.trading_diagnostics import stability_summary
from ml.trading_pipeline import load_trading_plan, prepare_trading_data
from ml.validation_access import finish_access, start_access

PLAN = ROOT / "configs/ml_trading_v3_20261004.json"
DATABASE = ROOT / "data/market_data.sqlite3"
CANDIDATE = ROOT / "exports/ml_v3_candidate_20261005"
LOG = ROOT / "research/registries/ml_validation_access_log.csv"
LABELS = np.asarray(["SELL", "HOLD", "BUY"])


def main() -> int:
    plan = load_trading_plan(PLAN)
    manifest_path = CANDIDATE / "candidate_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("plan_sha256") != file_sha256(PLAN):
        raise RuntimeError("V3 設定已變更，拒絕用不同 Features/Label/參數評估既有 Candidate。")
    access_id = start_access(LOG, manifest["candidate_name"], manifest_path, "正式 Candidate 評估")
    try:
        raw, markets = prepare_trading_data(DATABASE, plan, roles=("validation",), apply_scaling=False,
                                            workflow_stage="controlled_validation")
        candidate, model, mean, scale = load_candidate(CANDIDATE, plan)
        X = ((raw.X["validation"] - mean) / scale).astype(np.float32)
        probabilities = predict_candidate(candidate["selected_model"], model, X)
        classification = evaluate_multiclass(raw.y["validation"], probabilities)
        signals = LABELS[probabilities.argmax(axis=1)]
        rules = {**plan["trading"], "max_holding_sessions": plan["target"]["holding_sessions"]}
        trading = run_signal_backtest(markets["validation"], raw.metadata["validation"], signals, rules)
        stability = stability_summary(trading)
        result = {"candidate_name": candidate["candidate_name"], "role": "Controlled Validation",
                  "classification": classification, "trading_by_stock": trading, "stability": stability,
                  "model_modified": False}
        output = CANDIDATE / "controlled_validation_results.json"
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = {key: classification.get(key) for key in ("accuracy", "macro_f1", "macro_roc_auc_ovr")}
        summary.update({key: stability.get(key) for key in ("mean_return_pct", "median_return_pct", "return_std_pct", "mean_profit_factor", "mean_sharpe_ratio", "worst_max_drawdown_pct", "profitable_stocks", "stocks", "total_round_trips")})
        finish_access(LOG, access_id, candidate["candidate_name"], "COMPLETED", str(output), summary)
    except Exception as exc:
        finish_access(LOG, access_id, manifest["candidate_name"], "FAILED", notes=str(exc))
        raise
    print(f"Controlled Validation 完成：{output}")
    return 0


if __name__ == "__main__": raise SystemExit(main())

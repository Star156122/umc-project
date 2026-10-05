"""Evaluate a fixed Candidate on already-seen 2026 H1 Development; never selects/tunes."""
from __future__ import annotations

import json
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from ml.candidate_artifact import load_candidate, predict_candidate
from ml.models import evaluate_multiclass
from ml.trading_pipeline import load_trading_plan, prepare_trading_data

PLAN = ROOT / "configs/ml_trading_v3_20261004.json"
DATABASE = ROOT / "data/market_data.sqlite3"
CANDIDATE = ROOT / "exports/ml_v3_candidate_20261005"


def main() -> int:
    plan = load_trading_plan(PLAN)
    raw, _ = prepare_trading_data(DATABASE, plan, roles=("development",), apply_scaling=False,
                                  workflow_stage="development_diagnosis_only")
    manifest, model, mean, scale = load_candidate(CANDIDATE, plan)
    X = ((raw.X["development"] - mean) / scale).astype(np.float32)
    result = {"candidate_name": manifest["candidate_name"], "role": "Development diagnosis only",
              "automatic_parameter_search": False, "candidate_selection": False,
              "classification": evaluate_multiclass(raw.y["development"], predict_candidate(manifest["selected_model"], model, X))}
    output = CANDIDATE / "development_diagnostics.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Development 診斷完成：{output}")
    return 0


if __name__ == "__main__": raise SystemExit(main())

"""Training-only V3 walk-forward, Candidate selection and full-Training refit."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from ml.candidate_artifact import file_sha256, save_candidate
from ml.models import fit_multiclass_model, run_multiclass_model
from ml.trading_pipeline import load_trading_plan, prepare_trading_data
from ml.walk_forward import (aggregate_fold_metrics, build_full_training_data,
    build_walk_forward_fold, load_walk_forward_protocol, select_candidate_model)

PLAN = ROOT / "configs/ml_trading_v3_20261004.json"
PROTOCOL = ROOT / "configs/ml_training_protocol_v3.json"
DATABASE = ROOT / "data/market_data.sqlite3"
OUT = ROOT / "exports/ml_v3_candidate_20261005"


def main() -> int:
    plan = load_trading_plan(PLAN)
    protocol = load_walk_forward_protocol(PROTOCOL)
    raw, _ = prepare_trading_data(DATABASE, plan, roles=("train",), apply_scaling=False,
                                  workflow_stage="walk_forward_source")
    by_model: dict[str, list[dict]] = {name: [] for name in plan["models"]}
    gru_epochs: list[int] = []
    fold_audits = []
    for fold_spec in protocol["walk_forward"]["folds"]:
        fold = build_walk_forward_fold(raw, fold_spec)
        fold_audits.append(fold.audit)
        for name, params in plan["models"].items():
            _, metrics, _ = run_multiclass_model(name, fold, params, evaluation_splits=("validation",))
            item = {"fold": fold_spec["fold"], **metrics["splits"]["validation"]}
            by_model[name].append(item)
            if name == "gru": gru_epochs.append(int(metrics["epochs_completed"]))
    summaries = {name: aggregate_fold_metrics(items) for name, items in by_model.items()}
    selected, decision = select_candidate_model(summaries, protocol)
    full = build_full_training_data(raw)
    fixed_epochs = int(np.median(gru_epochs)) if selected == "gru" else None
    model, seconds, _, extra = fit_multiclass_model(selected, full, plan["models"][selected], fixed_epochs)
    manifest = {
        "candidate_name": protocol["candidate_name"], "candidate_status": "candidate_not_frozen",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "selected_model": selected, "decision": decision, "walk_forward": summaries,
        "refit": {"data": "Training 2023-01-01..2024-12-31", "training_seconds": seconds,
                  "fixed_epochs": fixed_epochs, **extra},
        "plan_sha256": file_sha256(PLAN), "protocol_sha256": file_sha256(PROTOCOL),
        "features": plan["features"], "target": plan["target"], "model_params": plan["models"][selected],
        "trading_rules": plan["trading"], "fold_audits": fold_audits,
        "controlled_validation_used": False, "development_used": False,
        "forbidden_roles_used": False,
    }
    save_candidate(OUT, selected, model, full.scaler_mean, full.scaler_scale, manifest)
    (OUT / "walk_forward_results.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"V3 Candidate 已建立：{OUT}")
    return 0


if __name__ == "__main__": raise SystemExit(main())

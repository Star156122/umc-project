import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_freeze_manifest_locks_candidate_and_scope():
    manifest_path = ROOT / "research/strategy_v2_freeze_manifest.json"
    assert manifest_path.exists(), "Run the freeze snapshot builder before this test"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate = manifest["candidate"]
    assert manifest["strategy_v2_status"] == "FROZEN_DEVELOPMENT_CANDIDATE"
    assert candidate["technical"] == "BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER"
    assert candidate["ml_model"] == "ML V1 Random Forest OOF"
    assert candidate["ml_gate"] == {"BUY": "ALLOW_LONG_ENTRY", "HOLD": "BLOCK", "SELL": "BLOCK", "short": False}
    assert candidate["risk"] == {"version": "Risk V1", "risk_per_trade": 0.01, "stop_loss_pct": 0.025, "max_position_fraction": 0.4}
    assert candidate["short"] is False and candidate["portfolio"] is False
    frozen = manifest["freeze_invariants"]
    assert all(frozen[name] for name in (
        "technical_parameters_frozen", "ml_model_frozen", "ml_gate_frozen", "risk_v1_frozen",
        "execution_frozen", "transaction_cost_frozen", "stock_universe_frozen", "no_short",
        "no_portfolio",
    ))
    assert manifest["freeze_invariants"]["candidate_ml_v2_included"] is False
    assert manifest["freeze_invariants"]["sell_veto_included"] is False
    assert manifest["freeze_invariants"]["risk_v2_atr_included"] is False
    assert manifest["freeze_invariants"]["final_oos_used"] is False
    assert manifest["research_data"]["role"] == "EXPOSED_DEVELOPMENT"
    assert manifest["research_data"]["period"] == {"start": "2023-01-01", "end": "2024-12-31"}
    assert manifest["research_data"]["stock_universe"] == ["2303", "2330", "2379", "2454", "3034"]


def test_hashes_and_exposure_statuses_are_preserved():
    manifest = json.loads((ROOT / "research/strategy_v2_freeze_manifest.json").read_text(encoding="utf-8"))
    identities = manifest["source_identity"]
    for key in ("technical_config", "technical_source", "ml_oof", "risk_config"):
        item = identities[key]
        digest = hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest()
        assert item["sha256"] == digest
    exposure = manifest["data_exposure"]
    expected = {
        "2023-2024": "EXPOSED",
        "2025-H1": "EXPOSED",
        "2025-H2": "UNKNOWN",
        "2026-H1": "EXPOSED",
        "2026-07-onward": "PARTIALLY_EXPOSED",
    }
    assert {period: exposure[period]["classification"] for period in expected} == expected
    assert manifest["final_oos"]["available_now"] is False
    assert manifest["validation_data_status"] == {
        "2025_H1": "EXPOSED", "2025_H2": "UNKNOWN",
        "2026_H1": "EXPOSED", "2026_H2_later": "PARTIALLY_EXPOSED",
    }


def test_rejected_candidates_and_freeze_outputs_are_explicit():
    manifest = json.loads((ROOT / "research/strategy_v2_freeze_manifest.json").read_text(encoding="utf-8"))
    rejected = {item["experiment"]: item["decision"] for item in manifest["rejected_or_non_adopted"]}
    assert rejected == {
        "Technical A Volume Confirmation": "NOT_ADOPTED",
        "Technical C EMA20 Exit": "NOT_ADOPTED",
        "Hybrid SELL-Veto": "NOT_ADOPTED",
        "Candidate-specific ML V2": "NOT_ADOPTED",
        "Risk V2 ATR Adaptive": "NOT_ADOPTED",
    }
    outputs = sorted((ROOT / "exports").glob("strategy_v2_development_freeze_*"))
    assert outputs
    latest = outputs[-1]
    required = {
        "strategy_v2_freeze_audit.json", "strategy_v2_freeze_manifest.json",
        "strategy_v2_development_summary.csv", "strategy_v2_per_stock_summary.csv",
        "strategy_v2_per_fold_summary.csv", "strategy_v2_freeze_report.html",
        "strategy_v2_freeze_user_analysis.html",
    }
    assert required <= {path.name for path in latest.iterdir()}


# Hybrid V2 Development Candidate Freeze

This freeze identifies the current **development candidate** for preregistered experiments on exposed 2023–2024 data. It is not a final model selection or an out-of-sample performance claim.

```text
HYBRID_V2_STATUS = FROZEN_DEVELOPMENT_CANDIDATE
TECHNICAL = BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER
ML = ML V1 Random Forest OOF
GATE = BUY_ONLY
SELL_IS_SHORT = FALSE
PORTFOLIO = FALSE
FINAL_OOS = FALSE
```

Risk V1 is the current control. Experiment `strategy-v2-risk-exp001-atr-adaptive` changes only stop distance and position size under its fixed preregistered ATR(14) rule. Technical candidate, B regime filter, RF OOF predictions, BUY-only gate, entry timing, other exits, transaction costs, and the 2023–2024 WF1/WF2/WF3 evaluation periods remain fixed. The experiment does not retrain ML or use 2025, 2026, or Final OOS data.

## Development candidate provenance

- Technical qualification: `exports/strategy_v2_technical_candidate_20261019T103707108020/technical_v2_candidate_audit.json`
- Frozen Technical configuration: `configs/technical_v2_candidate.json`
- Risk V1 control source: `hybrid/config/risk_v1.json`
- ML V1 OOF source: `exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv`
- Experiment preregistration: `research/strategy_v2_risk_exp001_preregistration.md`


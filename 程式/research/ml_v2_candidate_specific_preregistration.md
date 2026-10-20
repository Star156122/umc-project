# Strategy V2 Candidate-Specific ML V2 — Preregistration

**Experiment ID:** `strategy-v2-candidate-specific-ml-v2`  
**Prerecorded:** 2026-10-19 (Asia/Taipei)  
**Status:** `FROZEN_BEFORE_NEW_CANDIDATE_ERROR_AUDIT`  
**Purpose:** Test whether ML trained only on historical occurrences of the frozen Technical V2 candidate provides a useful BUY/HOLD/SELL filter for that candidate's actual use case. This is a research comparison, not parameter tuning or a claim of future profitability.

## Frozen design

- Technical candidate: `BR_SENS_RANGE55_BB20_2 + TECH_V2_B_MARKET_REGIME_FILTER`, status `FROZEN_CANDIDATE`. Technical parameters, market-regime filter, candidate timing, entry/exit, stop loss, execution, and costs remain fixed.
- Stocks: `2303`, `2330`, `2379`, `2454`, `3034`. No stock may be removed based on results.
- Models: Random Forest, XGBoost, and GRU. All three are retained; no model is added or dropped.
- Classes: `BUY`, `HOLD`, `SELL` (three-class classification only).
- Label: frozen ML V1 Fixed ±1% net-return label. For prediction session T, reference entry is T+1 open and exit is T+3 close after the already-defined one-round-trip costs: BUY at net return ≥ +1%; SELL at ≤ −1%; otherwise HOLD. Do not change horizon, thresholds, or cost treatment.
- Timing: for Technical Candidate Date D, use the previous **actual** trading session P as the ML Feature Session. Use only the frozen ML V1 feature pipeline and features available by P 13:30. Candidate-date or later information, future bars, trade PnL, exit outcome, and Technical result fields are not model features.
- Conditional sample definition: the Candidate-Specific training population contains only historical dates on which the frozen Technical V2 candidate actually produced a Candidate. Candidate occurrence selects the applicable ML-gate population; it is not itself an ML feature.
- Feature set: RF and XGBoost use the complete frozen ML V1 tabular Feature Set A. GRU uses the complete frozen ML V1 sequence Feature Set A plus the existing `volume_trend_5d` and `volume_trend_20d` auxiliary inputs. No feature selection, additions, removals, or lookback changes.
- Model parameters: use the frozen ML V1 parameters, unchanged: RF `{n_estimators: 200, max_depth: 10, min_samples_leaf: 5, class_weight: balanced, random_state: 42}`; XGBoost `{n_estimators: 150, max_depth: 4, learning_rate: 0.05, subsample: 0.8, colsample_bytree: 0.8, random_state: 42}`; GRU `{hidden_size: 16, num_layers: 1, dropout: 0.0, batch_size: 128, max_epochs: 10, patience: 2, learning_rate: 0.001, random_state: 42}`. If the repository's frozen implementation cannot honor these settings, stop and report; do not substitute parameters.
- Data scope: Training only, 2023-01-01 through 2024-12-31, five-stock Technology universe. No 2025, 2026, Holdout, Development, or Final OOS data.
- Gate: BUY allows the next-session Technical long entry; HOLD and SELL block it. No short positions. Do not use the Experiment 002 SELL-veto rule.
- Risk and execution remain the existing Risk V1 / Strategy V2 contracts: risk per trade 1%, maximum position fraction 40%, stop loss 2.5%, and the existing costs, slippage, entry/exit, cooldown, min/max hold, and completed-bar → next tradable 5-minute bar open execution.

## Phase A — frozen ML V1 candidate error audit

Before Phase A output is generated, this design is frozen. Phase A reads only the already-validated 138-row Common Coverage artifacts and frozen ML V1 OOF predictions from Strategy V2 Experiment 001 / 002, limited to 2023–2024, five stocks, and Technical folds WF1–WF3. It does not retrain or modify any model or gate.

The audit will check input identity, uniqueness, class/date alignment, previous-actual-session feature timing, OOF provenance, fold assignment, and allowed data roles before calculating metrics. It will report predicted and actual class counts, candidate-only confusion matrices, accuracy, macro precision/recall/F1, per-class precision/recall/F1, and per-model BUY precision/recall. AUC/ROC, probability distribution, and calibration are computed only if the frozen artifact already contains valid probabilities; probabilities will not be reconstructed. Strategy outcomes grouped by predicted class will be described separately from classification metrics. False positives and blocked profitable opportunities will be listed by stock and fold as diagnostics only. Results cannot change this preregistration.

**Phase A stop rule:** if any future leakage, candidate timing error, label alignment error, data corruption, invalid OOF provenance, or unresolved fold/coverage mismatch is found, mark the audit invalid and do not run Phase C.

## Phase C — Candidate-Specific ML V2 protocol

- Rebuild the candidate event dates from the frozen Technical V2 source and its existing WF1–WF3 schedule; validate parity with the already-frozen candidate ledger before fitting.
- Feature row for candidate date D is the frozen ML V1 feature row from previous actual session P. Label is the frozen ML V1 label aligned to P, with its existing target entry/exit references. No Candidate-day/post-Candidate fields enter X.
- Training rows are candidate events from the same five-stock Technology universe strictly earlier than the evaluated event. A training row is eligible only if its label's T+3 target end is no later than the training cutoff; rows whose label horizon crosses the validation boundary are excluded. Fit preprocessing/scaler/class weights on that fold's training rows only. No random shuffle or random split.
- Evaluation uses the frozen Technical/Hybrid chronological WF1, WF2, WF3 windows. For each evaluation candidate, emit a prediction only from a model trained on eligible earlier candidate events. No in-sample predictions. Before training, emit train/evaluation candidate counts, all three class counts/distributions, and per-stock counts per model/fold. If any model/fold lacks a legal three-class training set or sufficient data for the frozen implementation, mark `INSUFFICIENT_CANDIDATE_SAMPLE` and stop that model/fold without synthetic data or parameter substitution.
- If existing ML V1 four-fold OOF boundaries differ from Technical/Hybrid WF1–WF3, document both definitions and use an explicit candidate-date-to-feature-date-to-label-date mapping. Never infer fold equivalence from a fold name alone.
- Compare Technical V2 Alone against ML V1 BUY-only and Candidate-Specific ML V2 BUY-only for each model. Gate semantics remain BUY=allow, HOLD/SELL=block. Use model-specific fair intersection: Technical Candidate exists and both ML V1 and Candidate-Specific ML V2 have legal OOF. Also report the intersection common to all three models. Never compare unequal coverage as if it were paired.
- Report classification measures and trading/risk measures separately, overall, by stock, by fold, and by stock-fold. Classification metrics alone do not determine strategy support.
- Per-model decision rule: `CANDIDATE_ML_SUPPORTED` only if, on fair coverage, average return and Profit Factor are at least ML V1, worst MDD is no worse, return improves in at least 3/5 stocks and 2/3 folds, and Candidate-Specific trade count is at least 50% of ML V1 trade count on the same fair coverage. `CANDIDATE_ML_NOT_SUPPORTED` only for clear deterioration in the preregistered risk/return and stability evidence; otherwise `CANDIDATE_ML_MIXED`. Any leakage, fold, coverage, or implementation error is `INVALID`.

## Prohibited actions and reporting

No Grid/Random/Optuna/Bayesian/manual parameter search; no model or feature selection; no threshold, label, horizon, stock-universe, Technical, Hybrid, Risk, cost, or execution changes; no use of 2025, 2026, Holdout, Development, or Final OOS; no champion chosen by highest return; no subsequent rescue experiment in this run.

Phase A and, only if Phase A passes without leakage, Phase C outputs must be written to new timestamped export directories. Existing frozen inputs and prior reports must not be overwritten. Report input hashes, commands, tests, coverage, exclusions, classifications, and explicit data-role use flags.

## Frozen source references

- ML V1 research and label: `configs/ml_v1_research.json`, `configs/ml_v1_label_freeze.json`
- ML V1 walk-forward protocol: `configs/ml_training_protocol_v3.json`
- Technical V2 candidate and folds: `configs/technical_v2_candidate.json`
- Baseline identity: `research/strategy_v2_baseline_manifest.json`
- Technical candidates and common coverage: `exports/strategy_v2_technical_batch_20261019T094810074002/` and `exports/strategy_v2_ml_exp001_20261019T123507275634/`
- Frozen OOF and prior validated run references: `exports/hybrid_poc_v1_20261013T041540804059/`, `exports/strategy_v2_ml_exp001_20261019T123507275634/`, and `exports/strategy_v2_hybrid_exp002_20261019T132413877488/`

**Research status:** preregistration is frozen before the new Candidate Error Audit. No new audit metrics have been generated at the time this document is written.

# Strategy V2 Single-stock Development Freeze

**Status:** `FROZEN_DEVELOPMENT_CANDIDATE`

This freeze records the fixed single-stock, long-only development candidate. The evidence comes from the already completed 2023–2024 development runs, which are exposed development data. It is not an independent validation or Final OOS result.

## Frozen candidate

- **Technical:** `BR_SENS_RANGE55_BB20_2` + `TECH_V2_B_MARKET_REGIME_FILTER`.
- **ML:** ML V1 Random Forest OOF predictions. No model is retrained by this freeze.
- **Gate:** OOF `BUY` allows a long entry. `HOLD` and `SELL` block entry. `SELL` does not mean short.
- **Risk:** Risk V1; 1% risk budget per trade, 2.5% stop, 40% maximum position fraction.
- **Execution:** completed 5-minute signal, filled at the next tradable 5-minute bar open.
- **Scope:** five stocks (`2303`, `2330`, `2379`, `2454`, `3034`), single-stock evaluation, long-only, no portfolio.
- **Data:** 2023-01-01 through 2024-12-31, exposed development data.

The candidate is fixed because the prior Technical V2-B comparison supported its structural market-regime component, ML V1 Random Forest had supported incremental value in the pre-registered comparison, and Risk V1 remains the control after Risk V2 ATR adaptation was classified mixed. This is a development candidate, not a claim of best future performance.

## Not adopted

Technical A volume confirmation and Technical C EMA20 exit were mixed and are not included. Hybrid SELL-veto was `BALANCED_GATE_MIXED` for RF and GRU and `BALANCED_GATE_NOT_SUPPORTED` for XGBoost, so no model supported changing the BUY-only gate. Candidate-specific ML V2 was `NOT_SUPPORTED` for RF and XGBoost and `MIXED` for GRU, so ML V1 Random Forest remains in use. Risk V2 ATR adaptive was `RISK_V2_MIXED`: worst drawdown improved, but average return and Sharpe fell versus Risk V1. These experiments remain historical evidence and must not be rescued or retuned under this freeze.

## Reproduction evidence

No new backtest was run while creating this freeze. Reproduction is verified from existing audit records: the Technical V2 Candidate Qualification audit records Control and Technical V2-B reproduction as PASS; the Risk V2 Exp001 audit records the Risk V1 control reproduction as PASS and confirms the recorded return, drawdown, win rate, Profit Factor, Sharpe, trade count, PnL, cost, gross PnL, and final equity matched. The freeze snapshot copies only the existing `RISK_V1` summary, per-stock, and per-fold rows from that audited output.

The machine-readable file `research/strategy_v2_freeze_manifest.json` records source/config/data identities. Database hashes are carried forward from the existing parity audit; the raw databases were not opened during freeze generation. The working tree was already dirty at the beginning of this work, so the manifest identifies the exact frozen config/source files by SHA-256 and records the commit separately.

## Data exposure and validation plan

| Period | Status | Permitted interpretation |
|---|---|---|
| 2023–2024 | `EXPOSED` | Development and reproduction only; never Final OOS. |
| 2025 H1 | `EXPOSED` | Previously viewed; historical robustness only, clearly marked not Final OOS. |
| 2025 H2 | `UNKNOWN` | A locked registry entry does not prove no unrecorded access; do not call it Holdout or unseen. |
| 2026 H1 | `EXPOSED` | Previously viewed development data; diagnostics only. |
| 2026 H2 / later | `PARTIALLY_EXPOSED` | Per-stock records show some 2026-07 through 2026-09 exposure; later/aggregate coverage is not proven clean. |

There is no existing period the repository can prove is clean Final OOS now. Final OOS must be collected prospectively after this freeze, then evaluated under a separately registered protocol without tuning against it. The plan and exposure rationale are in `research/strategy_v2_validation_plan.md`.

## Freeze invariants

Technical parameters, ML model, ML gate, Risk V1 parameters, execution, costs, and stock universe are frozen. No short selling, portfolio simulation, Candidate ML V2, SELL-veto gate, or ATR Risk V2 is part of the candidate. No parameter search or new experiment was run during freeze creation.

The offline summary is under `exports/strategy_v2_development_freeze_<timestamp>/`. It is a view of existing development evidence, not a new performance run.

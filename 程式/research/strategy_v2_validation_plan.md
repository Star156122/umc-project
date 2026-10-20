# Strategy V2 Validation Plan

## A. Development reproduction

Use only the fixed candidate and the exposed 2023-01-01 through 2024-12-31 development data. The repository already contains a Technical V2-B Control reproduction and a Risk V1 control reproduction, both recorded as `PASS`. The Risk V2 Exp001 audit records the Risk V1 metric parity checks as passing. The freeze snapshot packages those existing outputs; freeze generation itself does not rerun a backtest. If a later independent reproduction is requested, run the frozen configuration unchanged and compare the complete per-stock/per-fold result matrix and execution/gate identities against the frozen manifest. Any changed hash or mismatch invalidates exact reproduction and must be documented before interpreting metrics.

This step is for reproducibility only. Since 2023–2024 has been repeatedly used for development, it is not fresh evidence and cannot be called a test set or Final OOS.

## B. Stress and historical robustness checks

Previously exposed periods may only be reported as historical checks or stress tests, with `NOT FINAL OOS` stated prominently. Current classifications are:

- 2025 H1: `EXPOSED`.
- 2025 H2: `UNKNOWN`; keep access locked because registry metadata alone cannot prove no unrecorded access.
- 2026 H1: `EXPOSED`.
- 2026 H2 / later: `PARTIALLY_EXPOSED`; project records show stock-level exposure from 2026-07-01 through 2026-09-24, and the entire period is not proven unused.

Do not relabel exposed, partially exposed, or unknown data as Holdout, unseen test, or clean validation. This plan does not authorize reading those periods.

## C. Final OOS

No existing data period can currently be proven clean Final OOS from repository records. A valid Final OOS must consist of genuinely new future observations first collected after this freeze. Register the exact start/end and access policy before evaluating; preserve the frozen candidate unchanged; log every data access; evaluate once using the preregistered metrics; do not use the outcome to tune this candidate. If the candidate is changed after inspecting future outcomes, those outcomes become exposed development evidence and a later prospective period is needed for a new Final OOS.

`FINAL_OOS_AVAILABLE_NOW = FALSE`.

# Strategy V2 Development Evidence Summary

This summarizes existing, exposed 2023–2024 development evidence used to define the frozen development candidate. It does not create a new model selection or Final OOS claim.

| Component | Existing evidence | Development decision |
|---|---|---|
| Technical V2-B market-regime filter | Technical V2 batch classified B as `SUPPORTED`; candidate qualification reproduced its result matrix and passed gate parity. | Retain as the technical component. |
| ML V1 Random Forest BUY-only gate | ML / Hybrid Exp001 classified RF incremental value as `INCREMENTAL_VALUE_SUPPORTED`; trade count fell from 138 to 29, and worst MDD improved from 5.49% to 1.83% in that comparison. | Retain RF OOF; only BUY allows entry. This is not an independent future validation. |
| Candidate-specific ML V2 | Existing closure describes results as mixed or unsupported. | Do not adopt; no rescue or retuning under this freeze. |
| Risk V1 | Reproduced as the control in the Risk V2 Exp001 audit. | Retain fixed 1% risk budget, 2.5% stop, 40% position cap. |
| Risk V2 ATR adaptive | Existing experiment classification is `RISK_V2_MIXED`; drawdown improved, but overall return and Sharpe were lower than V1. | Do not adopt; Risk V1 stays frozen. |

The recorded Risk V1 development snapshot is: mean return 0.72%, worst drawdown 1.83%, win rate 30.56%, Profit Factor 2.089, Sharpe 0.366, 29 trades, Net PnL NT$10,811, transaction costs NT$6,474. These figures summarize the existing five-stock / three-fold Risk V1 control output; they are not portfolio results and do not establish future profitability.

# Technical Control + RF BUY-only Gate + Risk V1 歷史成果 API

## Endpoint

`GET /api/research/v2/hybrid/:stockCode`

沿用 Express JWT Bearer 驗證，唯讀回傳指定股票在 Common Coverage Control、Random Forest V1 OOF BUY-only Gate 與 Risk V1 研究中的候選訊號、Gate 決策、Risk sizing、交易 Ledger、逐股票摘要及逐 Fold 結果。未知股票回傳 `404 RESEARCH_RESULT_NOT_FOUND`；固定來源檔案、雜湊或關聯檢查失敗時回傳 `503 HYBRID_RISK_SOURCE_INVALID`。

## 真實執行來源

- Technical source：`程式/exports/technical_v1_20261013T024919303843/`，run ID `20261013T024919303843`，experiment ID `technical-v1-20261010-v1`。Common Coverage candidate CSV 的 `technical_version` 為 `technical-v1-draft-20261010`。
- Common Coverage：`程式/exports/hybrid_common_coverage_20261018T121416162413/`，experiment ID `hybrid-common-coverage-20261018T121416162413`。
- Risk V1：`程式/exports/hybrid_risk_v1_20261018T122032419364/`，experiment ID `hybrid-risk-v1-20261018T122032419364`；Risk ID 為 `risk_v1`。
- ML OOF：`程式/exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv`。
- Risk 設定：`程式/hybrid/config/risk_v1.json`。

API 依 Common Coverage audit 的 Technical run 與檔案雜湊核對 Technical audit、結果及訊號檔，並驗證 Common Coverage、Risk audit、OOF、Gate、Risk sizing 與 Ledger 的來源關聯。API 不讀取 `research/strategy_v2_freeze_manifest.json`；後續 V2-B Freeze Manifest 不作為這組 Control 執行版本的證明。

## 策略身份與範圍

- `research_variant`: `CONTROL`
- `strategy_id`: `BR_SENS_RANGE55_BB20_2+CONTROL+ML_V1_RF_BUY_ONLY+RISK_V1`
- Technical 候選：`BR_SENS_RANGE55_BB20_2`，來源版本 `technical-v1-draft-20261010`。
- Gate：Random Forest OOF 預測 BUY 放行進場，HOLD/SELL 阻擋。
- Risk：Risk V1。
- `TECH_V2_B_MARKET_REGIME_FILTER` 未套用。
- 研究期間為 2023–2024，`data_role` 為 `historical_development`。這是已暴露的歷史開發資料，不是即時推論、交易建議或 Final OOS。

## 2330 核對摘要

- Common Coverage 在五檔股票保留 192 筆共同候選；原 Technical 候選 248 筆，56 筆因缺少完整共同 OOF 覆蓋而排除。2330 回傳 47 筆候選。
- 2330 的 RF Gate：14 筆 ENTRY、16 筆 BLOCK_HOLD、17 筆 BLOCK_SELL；Risk sizing 14 筆；Ledger 28 列（14 Buy、14 Sell）。
- Risk V1 的 2330 三個獨立 Fold 交易數為 0、9、5，Fold 平均報酬約 1.761104925%。這是獨立 Fold 算術平均，不是連續投資組合累積報酬；`cumulative_return_pct` 為 `null`。
- Technical V2-B 是另一組不同實驗：其 API 使用 2026/10/19 Technical V2 Batch，2330 為 8 筆交易、Fold 平均報酬約 1.427471%。兩組不可混合或當成同一策略身份。

## 回傳來源欄位

頂層 `technical_source` 和 `source.source_identity` 提供 Technical experiment/run、Common Coverage experiment、Risk ID/experiment。`technical_source.market_regime_filter.applied` 明確為 `false`。回應不再提供誤導性的 `freeze_id`；來源檔案清單與 SHA-256 也不包含 Freeze Manifest。

## 輕量測試

在 `前端/server` 執行：

```powershell
node --test tests/hybridRiskV1Research.test.cjs
```

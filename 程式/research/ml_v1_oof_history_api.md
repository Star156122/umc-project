# ML V1 歷史 OOF 預測 API

## Endpoint

`GET /api/research/v1/ml/oof/:stockCode/:model`

此唯讀 endpoint 使用 Express 現有 JWT `authenticate` middleware。模型只接受 `random_forest`、`xgboost`、`gru`；股票代號只用於資料篩選，不會拼成檔案路徑。沒有指定或不支援的模型會回覆 400，沒有該股票紀錄則回覆 404。

## 資料來源與範圍

- OOF CSV：`exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv`
- 來源稽核：`exports/hybrid_poc_v1_20261013T041540804059/hybrid_poc_audit.json`
- CSV SHA-256：`d70c57acd81445c5e33474ec7a28ff18d52e2a3e162c0737132f3be7ca6f7e4b`
- 期間：2023-01-01 至 2024-12-31；來源列 `data_role=training`，且全部為 walk-forward OOF。
- CSV 有 4,629 列；RF、XGBoost、GRU 各 1,543 列。2330 每個模型各 312 列。

API 會核對固定來源雜湊、audit 狀態、必需欄位、角色、Label/Feature 版本、模型與類別機率，並檢查 `(model, stock_code, signal_date)` 唯一性，以及三模型相同股票日期的 Fold、Entry/Target 日期與 `actual_label` 對齊。任一來源檢查失敗時不回退到舊資料。

OOF 原始資料的 Feature 版本依模型不同：RF 與 XGBoost 為 `feature_set_a_v3_24`；GRU 為 `feature_set_a_v3_24+volume_context`。API 原樣保留各模型版本，不將其宣稱為相同 Feature Set。

## 回傳語意

回應內 `data_role=historical_development` 表示這是已暴露的 2023–2024 歷史開發資料；`prediction_date_range` 另列該股票、該模型實際有 OOF 預測的日期範圍（例如 2330 為 2023-09-01 至 2024-12-26）。每筆預測包含原始 OOF 的預測類別、SELL/HOLD/BUY 機率、Fold、決策日期、`actual_label`、`target_date`、版本與參數指紋。`actual_label` 和 `target_date` 僅供歷史評估，不是決策當下可知資訊。這些資料曾用於既有研究與 Hybrid POC，因此不是乾淨的 Final OOS，也不是即時推論或交易建議。

API 每次只回傳一個模型的預測，避免合併時錯配。模型間的來源對齊只作完整性檢查，不重算或改寫任何預測。

## 範例

`GET /api/research/v1/ml/oof/2330/random_forest`

回應包含 `stock_code`、`model`、`data_period`、`folds`、`predictions`、來源檔名與 SHA-256。預測清單中的每筆 `probabilities` 使用 `sell`、`hold`、`buy` 小寫鍵，數值原樣取自 CSV。

## 測試

```powershell
cd 前端/server
npm test -- --test-name-pattern="ML OOF"
```

完整測試檔：`前端/server/tests/mlV1OofResearch.test.cjs`。

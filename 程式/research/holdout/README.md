# Holdout 鎖定區

- 期間：2025/07/01～2025/12/31
- 狀態：`locked_unseen`
- 權限：`forbidden`

正式設定：`configs/holdout_policy.json`  
程式防線：`trading_system/research_guard.py`  
測試：`tests/test_research_guard.py`

這個資料夾不得放 Holdout 績效、圖表或回測結果。只有使用者明確輸入「開始最終 Holdout Test」或同義的最終驗證指令，才能另建可稽核流程。一般的「全部跑一次」不構成授權。

## ML 額外時間鎖

ML 另將 2026/07/01 起至未來最新可取得資料列為 `Final Out-of-Sample Test`，目前同樣是 `forbidden`。正式設定在 `configs/ml_data_policy.json`，程式防線在 `ml/data_roles.py`。這不會抹除 2026Q3 曾被技術指標研究查看的既有紀錄。

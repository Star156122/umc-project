# Holdout 鎖定區

- 期間：2025/07/01～2025/12/31
- 狀態：`locked_unseen`
- 權限：`forbidden`

正式設定：`configs/holdout_policy.json`  
程式防線：`trading_system/research_guard.py`  
測試：`tests/test_research_guard.py`

這個資料夾不得放 Holdout 績效、圖表或回測結果。只有使用者明確輸入「開始最終 Holdout Test」或同義的最終驗證指令，才能另建可稽核流程。一般的「全部跑一次」不構成授權。

# 股票回測專題長期規則

所有後續新增策略、參數修改、回測、報告、版本比較，必須先閱讀並遵守 `程式/docs/長期研究規範.md`。

- 2025/07/01～2025/12/31 Additional Holdout 與 2026/07/01 起 Final Out-of-Sample 永久預設鎖定；一般「繼續測試、全部跑、產生HTML」絕非解鎖授權。
- 禁止在開發期間下載、讀取、分析或間接查看該期行情與績效。不要繞過 `trading_system/research_guard.py`。
- 只有使用者明確要求最終 Holdout Test 才能建立單獨、可稽核的正式驗證流程；一般開發入口仍維持鎖定。
- 首次正式使用前凍結程式、參數、候選、股票及評估標準，記錄授權；首次存取即標示 consumed，失敗也不能重置為 unseen。保存全部結果，不利用結果回頭調參再測同期間。
- 六策略分別診斷，不只研究MACD與多數決；同一邏輯跨股票，不依股票挑最佳參數，不從單一股票推論產業適配。
- 少量、有交易理由的修改，保留失敗結果，遵守規範中的績效紀錄欄位。報告使用台灣常用詞彙。
- 新資料讀取、模擬、最佳化或報告入口須接入共用日期鎖，測試交集、邊界及繞過入口的拒絕行為。
- 統一資料角色為 `training`（2023～2024）、`validation`（2025 上半年，開發可用但不得稱完全未看）、`development_seen`（2026 上半年）、`additional_holdout`（2025 下半年，禁止讀取）與 `final_out_of_sample`（2026/07/01 起，禁止讀取）。三個可用期間分開報告，不得合併平均。
- 每個實驗先登記假設與單一主要修改；同一問題最多約 1～2 輪有理由的開發。禁止先大量跑版本再補理由。
- 已標記 `FROZEN_RESEARCH_CANDIDATE` 的版本不得再依原 Development 結果修改；只能以完全不變的規則進入 Clean Validation。
- Clean Validation 第一次查看後即轉為 `validation_seen`，不得拿同一批資料反覆修改及驗證。
- 每次工作先更新 `程式/research/registries/dataset_registry.*` 與 `experiment_registry.*`；不得混報 Development 與 Clean Validation 平均值。
- 整理檔案時保留研究結果與失敗實驗。Cache、重複生成物或測試報告先列入建議刪除清單，未經使用者確認不得刪除。

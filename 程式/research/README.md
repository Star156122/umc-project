# 研究資料分類入口

這個資料夾用來管理「資料曾經怎麼用」與「策略目前是什麼狀態」。既有 `reports/`、`exports/` 與 `docs/` 暫時保留原路徑，避免網頁、程式與文件連結壞掉；本目錄用索引與 Registry 完成分類。

目前正式研究主線請從 [`current/hybrid_research_plan.md`](current/hybrid_research_plan.md) 開始閱讀。

|資料夾|用途|
|---|---|
|`current/`|目前保留、凍結或淘汰的策略狀態。|
|`development/`|原六檔、2026上半年開發資料與結果索引。|
|`validation_clean/`|尚未執行、尚未看結果的新股票驗證計畫。|
|`validation_seen/`|已跑過、已看過或曾影響研究的資料。|
|`archive/`|舊版本、失敗實驗與歷史研究索引；不代表可刪。|
|`holdout/`|最終保留資料鎖定說明，不放績效結果。|
|`registries/`|Dataset Registry 與 Experiment Registry。|

2026-10-09 已在使用者授權下清除可重建快取、臨時測試報表及部分已結束的一次性 ML 診斷輸出；完整判定與保留項目見 `../cleanup_manifest.md`。本次沒有重新回測，也沒有使用 Holdout。

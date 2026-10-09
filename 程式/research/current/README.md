# 目前策略狀態

唯一正式狀態檔是 `strategy_status.json`。

自 2026-10-09 起，研究主線改為 `Technical Strategy → ML Filter → Hybrid Strategy & Backtest`。正式角色、Freeze 條件、時間對齊與比較方式見 [`hybrid_research_plan.md`](hybrid_research_plan.md)。本輪暫不延伸 LLM 報告或新的研究支線。

ML 資料角色的正式設定是 `../../configs/ml_data_policy.json`，說明文件是 `../../docs/ML資料角色與命名_20261005.md`。ML Development 只允許讀取 Training、Validation 與 Development；兩個保留區維持禁止存取。

- MACD 前一修改版：保留為研究候選與下一階段對照，已禁止在原六檔繼續調整。
- MACD 早期價格延續版：淘汰，但結果保留。
- 三指標多數決 + 早期價格延續：凍結研究候選，只能以相同規則進 Clean Validation。

上述項目保留為歷史候選／對照，不會自動等同新主線的 Technical V1。

目前尚未完成 Technical V1 與 ML V1 Freeze，因此還不能進入正式 Hybrid Backtest。

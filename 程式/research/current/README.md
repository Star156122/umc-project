# 目前策略狀態

唯一正式狀態檔是 `strategy_status.json`。

ML 資料角色的正式設定是 `../../configs/ml_data_policy.json`，說明文件是 `../../docs/ML資料角色與命名_20261005.md`。ML Development 只允許讀取 Training、Validation 與 Development；兩個保留區維持禁止存取。

- MACD 前一修改版：保留為研究候選與下一階段對照，已禁止在原六檔繼續調整。
- MACD 早期價格延續版：淘汰，但結果保留。
- 三指標多數決 + 早期價格延續：凍結研究候選，只能以相同規則進 Clean Validation。

目前沒有任何「正式可用策略」。

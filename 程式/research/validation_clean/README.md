# Clean Validation 執行紀錄

8檔股票已於 2026/09/26 完成一次 Clean Validation。依規範，結果第一次查看後已全部轉為 `validation_seen`，本資料夾只保留它們原本確實以 clean 身分進場的歷史紀錄。

本次執行遵守：

1. 使用者先確認股票與 2026/01/01～2026/06/30 期間。
2. 執行前確認股票沒有既有報告資料夾。
3. 凍結並核對候選策略的程式與設定雜湊。
4. 事先固定 PASS／FAIL／INSUFFICIENT 判定方式。
5. 跑完一次後已將整批資料改列 `validation_seen`。

結果：MACD `FAIL`；多數決早期延續 `FAIL`。報告位於 `exports/clean_validation_latest.html`。禁止修改後重跑同一批並稱獨立驗證。

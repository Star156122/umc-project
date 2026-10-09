# Registry 使用說明

- `dataset_registry.csv`：每檔股票與期間曾被怎麼使用。`clean_validation_eligible=pending` 不代表已乾淨，只代表等待使用者確認與執行前檢查。
- `experiment_registry.csv`：實驗、策略版本、資料角色、結果與決策。

之後每次讀資料或開始實驗前先查這兩份表；實驗完成後立即更新。統一角色名稱為 `training`、`validation`、`development_seen`、`additional_holdout_locked` 與 `final_out_of_sample_locked`。歷史曾存取 final 區間的紀錄不得刪除，但本輪仍須標記 locked 並禁止再次讀取。

目前採 CSV 作為人工可讀的唯一資料來源，避免 CSV 與 JSON 兩份內容不同步。前端或程式若需要 JSON，應由 CSV 即時產生，不要手動維護第二份主檔。

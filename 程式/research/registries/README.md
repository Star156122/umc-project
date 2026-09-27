# Registry 使用說明

- `dataset_registry.csv`：每檔股票與期間曾被怎麼使用。`clean_validation_eligible=pending` 不代表已乾淨，只代表等待使用者確認與執行前檢查。
- `experiment_registry.csv`：實驗、策略版本、資料角色、結果與決策。

之後每次讀資料或開始實驗前先查這兩份表；實驗完成後立即更新。若資料是否曾看過無法確定，一律記成 `validation_seen`。

目前採 CSV 作為人工可讀的唯一資料來源，避免 CSV 與 JSON 兩份內容不同步。前端或程式若需要 JSON，應由 CSV 即時產生，不要手動維護第二份主檔。

# 本地歷史資料庫

執行 `main02.py` 或 `update_data.py` 後，程式會在此建立 `market_data.sqlite3`。

SQLite 內含：

- `market_ticks`：股票代號、交易日期、順序、時間戳、成交價、數量與 tick 類型。
- `market_data_days`：逐日下載狀態、筆數與更新時間，用來判斷哪些日期需要補抓。

實際 `.sqlite3`、`-wal` 與 `-shm` 檔已由 `.gitignore` 排除，避免把大量市場資料或執行中的暫存檔打包進程式碼。

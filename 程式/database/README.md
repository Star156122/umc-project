# 資料庫資料

本資料夾集中保存資料庫結構、升級腳本、ERD 與可交付隊友的匯出檔。

## 結構與說明

- `ai_stock_system.sql`：完整資料庫結構，包含回測結果與本地行情相關資料表。
- `回測輸出欄位升級.sql`：只用於升級朋友的舊版 `backtest_results`；新版結構不必重複執行。
- `ai_stock_system_erd.png`：資料庫關聯圖。
- `保留欄位說明.txt`：欄位保留與用途說明。
- `研究工作台資料表.sql`：只新增研究批次、策略績效、市場階段、每日風險及交易紀錄五張表，不刪除既有資料。

## 同步研究工作台成果

本機 XAMPP 的 MariaDB 啟動後，在「程式」資料夾執行：

```powershell
uv run python sync_research_mysql.py
```

這個指令讀取 `data/research.sqlite3` 的最新成果，不重新回測。重複同步同一批資料會更新原有批次，不會建立重複副本。只測試連線可加上 `--check`。

## 團隊匯出

- `exports/ai_stock_system_team_20260824.sql`：可交付隊友匯入的資料庫結構與正式半年回測資料。
- 匯出內容不包含 `.env`、Shioaji API Key 或 MySQL 密碼。
- `small_test` 與 `one_week_test` 未寫入資料庫，因此也不在團隊匯出檔中。

隊友匯入時，建議先建立空的 `ai_stock_system` 資料庫，使用 `utf8mb4_unicode_ci`，再由 phpMyAdmin 匯入團隊 SQL。

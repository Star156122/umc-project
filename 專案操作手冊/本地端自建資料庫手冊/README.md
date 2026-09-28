# 本地端自建資料庫手冊

這份手冊提供自己或組員在 Windows 使用 XAMPP 建立專題資料庫。即使沒有網路或雲端資料庫，也能在自己的電腦執行回測、保存成果及開啟策略研究工作台。

## 建議閱讀順序

1. [01_XAMPP與資料庫建立.md](01_XAMPP與資料庫建立.md)：第一次安裝及建立 `ai_stock_system`。
2. [02_專案連線設定.md](02_專案連線設定.md)：讓 Python 連到 MariaDB。
3. [03_匯入回測與研究成果.md](03_匯入回測與研究成果.md)：把現有成果放進資料庫。
4. [04_資料表用途.md](04_資料表用途.md)：了解每張表存什麼。
5. [05_備份還原與常見問題.md](05_備份還原與常見問題.md)：備份、換電腦及排除錯誤。

## 最短使用方式

已經安裝 XAMPP 的人，只要：

1. 開啟 XAMPP，啟動 Apache 和 MySQL。
2. 用 phpMyAdmin 建立 `ai_stock_system`，編碼選 `utf8mb4_unicode_ci`。
3. 匯入 `程式/database/ai_stock_system.sql`。
4. 將 `程式/.env.example` 複製成 `.env`，填入自己的資料庫帳號。
5. 在「程式」資料夾執行研究成果同步。

```powershell
uv run python sync_research_mysql.py
```

這些都是本機模擬與研究資料，不會送出真實股票委託。

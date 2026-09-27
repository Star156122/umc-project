# 01　安裝 XAMPP 與建立資料庫

## 一、啟動服務

開啟 XAMPP Control Panel，按下：

- Apache 的 `Start`
- MySQL 的 `Start`

兩列變成綠色，代表服務已啟動。MySQL 預設使用 `3306`，Apache 預設使用 `80`。

## 二、開啟 phpMyAdmin

按 MySQL 右側的 `Admin`，或用瀏覽器開啟：

```text
http://localhost/phpmyadmin/
```

phpMyAdmin 是管理畫面，真正保存資料的是 MariaDB。

## 三、建立專題資料庫

1. 點左側「新增」。
2. 資料庫名稱輸入 `ai_stock_system`。
3. 編碼排序選擇 `utf8mb4_unicode_ci`。
4. 按「建立」。

## 四、建立原本的專題資料表

1. 點左側 `ai_stock_system`。
2. 點上方「匯入」。
3. 選擇 `程式/database/ai_stock_system.sql`。
4. 按最下方「匯入」或「執行」。

這份 SQL 會建立使用者、股票、回測結果及報告等原始資料表。若資料庫已經有正式資料，不要重複匯入完整 SQL，因為完整版包含 `DROP TABLE`。研究用資料表請使用下一份安全的新增腳本。

## 五、建立研究工作台資料表

1. 仍然選擇 `ai_stock_system`。
2. 點「匯入」。
3. 選擇 `程式/database/研究工作台資料表.sql`。
4. 按「執行」。

這份檔案只使用 `CREATE TABLE IF NOT EXISTS`，不會刪除原本資料。通常直接執行 Python 同步程式也會自動建立，不必兩邊都做。

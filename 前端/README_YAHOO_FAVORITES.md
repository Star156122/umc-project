# Yahoo Finance 行情＋MySQL 自選股整合版

## 已完成

- 首頁與自選股頁使用同一套深色行情介面。
- 首頁每檔股票都有星號按鈕。
- 加入自選股後，會寫入 MySQL `favorite_stocks`。
- 自選股頁會從 `favorite_stocks` 讀取登入者自己的清單。
- 股票名稱、價格、漲跌、成交量與當日 5 分走勢由後端向 Yahoo Finance 取得。
- 搜尋支援台股代號、Yahoo 代號與公司名稱，例如：`2330`、`2330.TW`、`台積電`。
- Yahoo 暫時無法回傳資料時，畫面會顯示「行情暫時無法取得」，不會補上虛構價格。

> Yahoo Finance 沒有提供保證穩定的正式公開開發者 API。本專案透過伺服器端讀取 Yahoo 公開行情端點，適合課程與畢業專題展示；行情可能延遲，Yahoo 端點也可能調整。

## 主要 API

```text
GET    /api/market/hot
GET    /api/market/search?q=2330
GET    /api/favorites/codes
GET    /api/favorites
POST   /api/favorites
DELETE /api/favorites/:code
```

自選股 API 需要登入 JWT。前端 `src/config/api.js` 會自動從 AsyncStorage 讀取 `authToken` 並加入：

```text
Authorization: Bearer <token>
```

## 資料庫

完整新建資料庫可匯入：

```text
server/database/畢業專題_完整資料_20260824.sql
```

已有 `users` 與 `stocks` 資料表，只缺自選股表時，執行：

```text
server/database/favorites_migration.sql
```

實際使用的資料表名稱是：

```text
favorite_stocks
```

加入自選股時，後端會先把 Yahoo 驗證過的股票資料寫入或更新 `stocks`，再新增 `favorite_stocks`，因此不會違反外鍵限制。

## 啟動

### 1. MySQL

啟動 XAMPP MySQL，確認資料庫名稱為：

```text
畢業專題
```

### 2. 後端

```bat
cd server
npm install
npm run dev
```

測試：

```text
http://localhost:3000/api/health
```

應看到：

```json
{
  "ok": true,
  "marketSource": "Yahoo Finance",
  "favoriteTable": "favorite_stocks"
}
```

### 3. 前端

網頁版根目錄 `.env`：

```env
EXPO_PUBLIC_API_URL=http://localhost:3000
```

啟動：

```bat
npm install
npx expo start -c
```

實體手機需把 `localhost` 改成電腦區網 IP，例如：

```env
EXPO_PUBLIC_API_URL=http://192.168.1.100:3000
```

手機與電腦需在同一個 Wi-Fi，並允許 Windows 防火牆放行 Node.js。

## 驗證資料是否寫入

```sql
SELECT
  fs.favorite_id,
  fs.user_id,
  fs.stock_id,
  s.stock_name,
  s.market,
  fs.added_at
FROM favorite_stocks fs
JOIN stocks s ON s.stock_id = fs.stock_id
ORDER BY fs.favorite_id DESC;
```

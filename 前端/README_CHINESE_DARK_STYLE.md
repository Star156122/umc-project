# 中文股票名稱與深色介面整合版

## 本版調整

- 首頁與自選股中的股票名稱優先使用 Yahoo Finance 中文搜尋結果。
- Yahoo 未回傳中文名稱時，常用台股使用中文名稱備援；未知標的顯示「台股＋代號」，不再顯示英文公司名稱。
- 價格、漲跌、成交量與當日走勢仍由 Yahoo Finance 即時取得，不使用手動價格。
- 新增自選股時，中文名稱會寫入 `stocks.stock_name`。
- 讀取舊自選股時，會把原先存入的英文名稱同步更新成中文名稱。
- 登入、註冊、分析與設定頁面改成與首頁、自選股一致的深色行情風格。

## 啟動

後端：

```bat
cd server
npm install
npm run check
npm run dev
```

前端：

```bat
npm install
npx expo start -c
```

網頁版根目錄 `.env`：

```env
EXPO_PUBLIC_API_URL=http://localhost:3000
```

實體手機請將 `localhost` 改成電腦的區域網路 IP。

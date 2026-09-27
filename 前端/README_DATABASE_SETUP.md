# AI 股票預測分析系統：資料庫連線設定

此版本已把註冊與登入改成：

Expo 前端 → Express API → MariaDB / MySQL

帳號不再只存在瀏覽器 AsyncStorage；註冊資料會寫入 `users` 資料表，密碼會先使用 bcrypt 雜湊後再儲存。

## 1. 匯入資料庫

1. 開啟 XAMPP，啟動 MySQL。
2. 進入 phpMyAdmin。
3. 匯入：`server/database/畢業專題_完整資料_20260824.sql`。
4. 確認資料庫名稱為 `畢業專題`，並且存在 `users` 資料表。

## 2. 啟動後端 API

在 VS Code 終端機執行：

```powershell
cd server
copy .env.example .env
npm install
npm run dev
```

XAMPP 預設 root 沒有密碼時，`.env` 保持：

```env
DB_USER=root
DB_PASSWORD=
DB_NAME=畢業專題
```

瀏覽器開啟：

```text
http://localhost:3000/api/health
```

看到 `API 與資料庫連線正常` 代表成功。

## 3. 設定 Expo 的 API 位址

回到專案根目錄：

```powershell
copy .env.example .env
```

Web 測試使用：

```env
EXPO_PUBLIC_API_URL=http://localhost:3000
```

Android 模擬器使用：

```env
EXPO_PUBLIC_API_URL=http://10.0.2.2:3000
```

實體手機不能填 localhost，請填電腦的區網 IPv4，例如：

```env
EXPO_PUBLIC_API_URL=http://192.168.1.100:3000
```

手機與電腦必須連到同一個 Wi-Fi，Windows 防火牆也要允許 Node.js 通過私人網路。

## 4. 啟動 Expo

另外開一個終端機，在專案根目錄執行：

```powershell
npm install
npx expo start -c
```

## 5. 測試流程

1. 進入登入頁。
2. 按「註冊帳號」。
3. 輸入姓名、電子郵件、密碼與確認密碼。
4. 按「建立帳號」。
5. 註冊成功後會自動回登入頁。
6. 使用剛註冊的信箱與密碼登入。
7. 設定頁會顯示使用者姓名與信箱。
8. 登出後會刪除本機登入權杖並回到登入頁。

## 常見錯誤

### 顯示「無法連接後端」

確認 `server` 終端機仍在執行，以及 `.env` 的 API 位址正確。

### 顯示「API 已啟動，但無法連接資料庫」

確認 XAMPP MySQL 已啟動，並檢查 `server/.env` 的資料庫帳密。

### 手機可以看到 App，但不能註冊

不要使用 `localhost`。改成電腦區網 IP，並確認手機與電腦在同一個 Wi-Fi。

require("dotenv").config();

const express = require("express");
const cors = require("cors");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const jwt = require("jsonwebtoken");
const pool = require("./db");
const { getHotMarket, searchStocks } = require("./marketService");
const {
  listFavoriteCodes,
  getFavorites,
  addFavorite,
  removeFavorite,
} = require("./favoriteService");
const {
  listDatabaseReports,
  listReportHistory,
  loadLatestDatabaseReport,
  loadDatabaseReportByAnalysisId,
  loadDatabaseReportByLegacyId,
} = require("./databaseReportService");
const {
  PYTHON_BACKTEST_ROOT,
  RUNNABLE_STRATEGIES,
  executePythonBacktest,
} = require("./pythonBacktestService");

const { loadReportHtml } = require("./reportHtmlService");

const app = express();
const port = Number(process.env.PORT || 3000);
const jwtSecret = process.env.JWT_SECRET || "development-secret";
const runningBacktests = new Map();

const STRATEGY_CATALOG = [
  { key: "ma", label: "MA 均線交叉", runnable: true },
  { key: "rsi", label: "RSI", runnable: true },
  { key: "macd", label: "MACD", runnable: true },
  { key: "bollinger", label: "布林通道", runnable: true },
  { key: "breakout", label: "區間突破", runnable: true },
  { key: "vote", label: "三指標多數決", runnable: true },
];

function hashPassword(password) {
  const salt = crypto.randomBytes(16).toString("hex");
  const hash = crypto.scryptSync(password, salt, 64).toString("hex");
  return `${salt}:${hash}`;
}

function verifyPassword(password, storedValue) {
  const [salt, savedHash] = String(storedValue || "").split(":");
  if (!salt || !savedHash) return false;

  const calculatedHash = crypto.scryptSync(password, salt, 64);
  const savedHashBuffer = Buffer.from(savedHash, "hex");
  if (calculatedHash.length !== savedHashBuffer.length) return false;
  return crypto.timingSafeEqual(calculatedHash, savedHashBuffer);
}

function authenticate(req, res, next) {
  const header = String(req.headers.authorization || "");
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";

  if (!token) {
    return res.status(401).json({ message: "登入狀態已失效，請重新登入" });
  }

  try {
    const payload = jwt.verify(token, jwtSecret);
    req.user = {
      userId: Number(payload.userId),
      email: payload.email,
    };
    return next();
  } catch (_error) {
    return res.status(401).json({ message: "登入憑證已過期，請重新登入" });
  }
}

app.use(cors());
app.use(express.json({ limit: "5mb" }));
app.use((req, _res, next) => {
  console.log(`[API] ${req.method} ${req.originalUrl}`);
  next();
});

app.get("/api/health", async (_req, res) => {
  try {
    await pool.execute("SELECT 1");
    const pythonReady = fs.existsSync(path.join(PYTHON_BACKTEST_ROOT, "app_backtest.py"));
    return res.json({
      ok: true,
      message: "API 與畢業專題資料庫連線正常",
      database: process.env.DB_NAME || "畢業專題",
      marketSource: "Yahoo Finance",
      favoriteTable: "favorite_stocks",
      pythonBackend: pythonReady ? "ready" : "missing",
      pythonBackendRoot: PYTHON_BACKTEST_ROOT,
      reportStorage: "mysql",
    });
  } catch (error) {
    console.error("資料庫健康檢查失敗：", error);
    return res.status(500).json({
      ok: false,
      message: "API 已啟動，但無法連接畢業專題資料庫",
      database: process.env.DB_NAME || "畢業專題",
      detail: error.message,
    });
  }
});

app.post("/api/auth/register", async (req, res) => {
  try {
    const username = String(req.body?.username || "").trim();
    const email = String(req.body?.email || "").trim().toLowerCase();
    const password = String(req.body?.password || "");

    if (!username || !email || !password) {
      return res.status(400).json({ message: "請完整輸入姓名、信箱與密碼" });
    }
    if (username.length > 50) {
      return res.status(400).json({ message: "姓名不可超過 50 個字元" });
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return res.status(400).json({ message: "電子郵件格式不正確" });
    }
    if (password.length < 6) {
      return res.status(400).json({ message: "密碼至少需要 6 個字元" });
    }

    const [existingRows] = await pool.execute(
      "SELECT user_id, username, email FROM users WHERE email = ? OR username = ? LIMIT 1",
      [email, username]
    );

    if (existingRows.length > 0) {
      const existing = existingRows[0];
      if (existing.email === email) {
        return res.status(409).json({ message: "此電子郵件已經註冊" });
      }
      return res.status(409).json({ message: "此姓名已被使用，請更換姓名" });
    }

    const passwordHash = hashPassword(password);
    const [result] = await pool.execute(
      "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
      [username, email, passwordHash]
    );

    return res.status(201).json({
      message: "註冊成功",
      user: {
        userId: result.insertId,
        username,
        email,
      },
    });
  } catch (error) {
    console.error("註冊失敗：", error);
    if (error?.code === "ER_DUP_ENTRY") {
      return res.status(409).json({ message: "帳號或電子郵件已經存在" });
    }
    return res.status(500).json({ message: "建立帳號失敗，請稍後再試" });
  }
});

app.post("/api/auth/login", async (req, res) => {
  try {
    const email = String(req.body?.email || "").trim().toLowerCase();
    const password = String(req.body?.password || "");

    if (!email || !password) {
      return res.status(400).json({ message: "請輸入電子郵件與密碼" });
    }

    const [rows] = await pool.execute(
      `SELECT user_id, username, email, password_hash, created_at, last_login
       FROM users
       WHERE email = ?
       LIMIT 1`,
      [email]
    );

    if (rows.length === 0) {
      return res.status(404).json({ message: "沒有此帳號，請先註冊帳號" });
    }

    const user = rows[0];
    if (!verifyPassword(password, user.password_hash)) {
      return res.status(401).json({ message: "密碼錯誤" });
    }

    await pool.execute(
      "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE user_id = ?",
      [user.user_id]
    );

    const token = jwt.sign(
      { userId: user.user_id, email: user.email },
      jwtSecret,
      { expiresIn: "7d" }
    );

    return res.json({
      message: "登入成功",
      token,
      user: {
        userId: user.user_id,
        name: user.username,
        username: user.username,
        email: user.email,
        createdAt: user.created_at,
      },
    });
  } catch (error) {
    console.error("登入失敗：", error);
    return res.status(500).json({ message: "登入失敗，請稍後再試" });
  }
});

app.get("/api/market/hot", async (_req, res) => {
  try {
    const data = await getHotMarket();
    return res.json(data);
  } catch (error) {
    console.error("讀取 Yahoo 熱門股票失敗：", error);
    return res.status(502).json({
      message: "目前無法從 Yahoo Finance 取得市場行情",
      details: error.message,
    });
  }
});

app.get("/api/market/search", async (req, res) => {
  try {
    const query = String(req.query.q || "").trim();
    const data = await searchStocks(query);
    return res.json(data);
  } catch (error) {
    console.error("Yahoo 股票搜尋失敗：", error);
    return res.status(502).json({
      message: "Yahoo Finance 股票搜尋失敗，請稍後再試",
      details: error.message,
    });
  }
});

app.get("/api/favorites/codes", authenticate, async (req, res) => {
  try {
    const codes = await listFavoriteCodes(req.user.userId);
    return res.json({ codes });
  } catch (error) {
    console.error("讀取自選股代號失敗：", error);
    return res.status(500).json({ message: "讀取自選股失敗" });
  }
});

app.get("/api/favorites", authenticate, async (req, res) => {
  try {
    const data = await getFavorites(req.user.userId);
    return res.json({
      ...data,
      message: "自選股已從畢業專題資料庫讀取，行情來自 Yahoo Finance",
    });
  } catch (error) {
    console.error("讀取自選股失敗：", error);
    return res.status(500).json({
      message: "讀取自選股失敗",
      details: error.message,
    });
  }
});

app.post("/api/favorites", authenticate, async (req, res) => {
  try {
    const favorite = await addFavorite(req.user.userId, {
      code: req.body?.code,
      yahooSymbol: req.body?.yahooSymbol,
    });
    return res.status(201).json({
      message: `${favorite.code} 已加入自選股`,
      favorite,
    });
  } catch (error) {
    console.error("加入自選股失敗：", error);
    return res.status(error.status || 400).json({
      message: error.message || "加入自選股失敗",
    });
  }
});

app.delete("/api/favorites/:code", authenticate, async (req, res) => {
  try {
    const result = await removeFavorite(req.user.userId, req.params.code);
    return res.json({
      message: result.removed
        ? `${result.code} 已移除自選股`
        : "此股票不在自選股中",
      ...result,
    });
  } catch (error) {
    console.error("移除自選股失敗：", error);
    return res.status(error.status || 400).json({
      message: error.message || "移除自選股失敗",
    });
  }
});

app.get("/api/backtests/strategies", authenticate, (_req, res) => {
  return res.json({
    strategies: STRATEGY_CATALOG,
    runnableStrategies: RUNNABLE_STRATEGIES,
  });
});

app.post("/api/backtests/run", authenticate, async (req, res) => {
  const code = String(req.body?.code || "").trim();
  const strategy = String(req.body?.strategy || "ma").trim().toLowerCase();
  const startDate = req.body?.startDate
    ? String(req.body.startDate).trim()
    : undefined;
  const endDate = req.body?.endDate
    ? String(req.body.endDate).trim()
    : undefined;
  const jobKey = `${req.user.userId}:${code}:${strategy}`;

  if (runningBacktests.has(jobKey)) {
    return res.status(409).json({
      message: `股票 ${code} 的 ${strategy.toUpperCase()} 策略正在回測，請稍候`,
    });
  }

  runningBacktests.set(jobKey, true);
  try {
    const result = await executePythonBacktest({
      userId: req.user.userId,
      code,
      strategy,
      startDate,
      endDate,
    });

    return res.status(201).json({
      message: `股票 ${code} 的 ${strategy.toUpperCase()} 回測完成，結果已存入畢業專題資料庫`,
      ...result,
    });
  } catch (error) {
    console.error("執行 Python 回測失敗：", error);
    return res.status(error.status || 500).json({
      message: error.message || "執行回測失敗",
      details: error.details || undefined,
    });
  } finally {
    runningBacktests.delete(jobKey);
  }
});

app.get("/api/reports", authenticate, async (req, res) => {
  try {
    const code = req.query.code ? String(req.query.code).trim() : null;
    const strategy = req.query.strategy
      ? String(req.query.strategy).trim().toLowerCase()
      : null;
    const reports = await listDatabaseReports(req.user.userId, code, strategy);
    return res.json({ reports });
  } catch (error) {
    console.error("讀取資料庫報告清單失敗：", error);
    return res.status(error.status || 500).json({
      message: error.message || "讀取資料庫報告清單失敗",
    });
  }
});

app.get("/api/reports/latest/:code", authenticate, async (req, res) => {
  try {
    const strategy = req.query.strategy
      ? String(req.query.strategy).trim().toLowerCase()
      : null;
    const report = await loadLatestDatabaseReport(
      req.user.userId,
      req.params.code,
      strategy
    );
    return res.json({ report });
  } catch (error) {
    console.error("讀取資料庫最新報告失敗：", error);
    return res.status(error.status || 500).json({
      message: error.message || "讀取資料庫最新報告失敗",
    });
  }
});

app.get("/api/reports/analysis/:analysisId", authenticate, async (req, res) => {
  try {
    const report = await loadDatabaseReportByAnalysisId(
      req.user.userId,
      req.params.analysisId
    );
    return res.json({ report });
  } catch (error) {
    return res.status(error.status || 500).json({
      message: error.message || "讀取資料庫指定報告失敗",
    });
  }
});

app.get("/api/reports/history/:code", authenticate, async (req, res) => {
  try {
    const reports = await listReportHistory(req.user.userId, req.params.code, req.query.strategy);
    return res.json({ reports });
  } catch (error) {
    return res.status(error.status || 500).json({ message: error.message || "讀取歷史報告失敗" });
  }
});

app.get("/api/reports/analysis/:analysisId/html", authenticate, async (req, res) => {
  try {
    const result = await loadReportHtml(req.user.userId, req.params.analysisId);
    res.set("Cache-Control", "no-store");
    return res.json(result);
  } catch (error) {
    return res.status(error.status || 500).json({ message: error.message || "讀取 HTML 報告失敗" });
  }
});

app.get("/api/reports/:code/:reportId", authenticate, async (req, res) => {
  try {
    const report = await loadDatabaseReportByLegacyId(
      req.user.userId,
      req.params.code,
      req.params.reportId
    );
    return res.json({ report });
  } catch (error) {
    return res.status(error.status || 500).json({
      message: error.message || "讀取資料庫指定報告失敗",
    });
  }
});

app.use((error, _req, res, _next) => {
  console.error("未處理的伺服器錯誤：", error);
  return res.status(500).json({ message: "伺服器發生錯誤" });
});

app.listen(port, "0.0.0.0", () => {
  console.log(`AI 股票系統 API 已啟動：http://localhost:${port}`);
  console.log(`資料庫：${process.env.DB_NAME || "畢業專題"}`);
  console.log("市場行情來源：Yahoo Finance");
  console.log(`Python 回測後端：${PYTHON_BACKTEST_ROOT}`);
  console.log("回測結果儲存：MySQL/MariaDB 的畢業專題資料庫");
});

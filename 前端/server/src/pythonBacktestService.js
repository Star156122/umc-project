const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const pool = require("./db");
const { loadReportFromRoot } = require("./reportService");

const RUNNABLE_STRATEGIES = ["ma", "rsi", "macd", "bollinger", "breakout", "vote"];

function createHttpError(message, status = 500, details = undefined) {
  const error = new Error(message);
  error.status = status;
  if (details) error.details = details;
  return error;
}

function resolveBackendRoot() {
  const configured = String(process.env.PYTHON_BACKTEST_ROOT || "").trim();
  if (!configured) return path.resolve(__dirname, "..", "..", "..", "程式");
  return path.isAbsolute(configured)
    ? configured
    : path.resolve(__dirname, "..", configured);
}

const PYTHON_BACKTEST_ROOT = resolveBackendRoot();
const BACKTEST_TIMEOUT_MS = Math.max(
  60_000,
  Number(process.env.BACKTEST_TIMEOUT_MS || 17_200_000)
);

function validateCode(code) {
  const cleanCode = String(code || "").trim();
  if (!/^\d{4,6}$/.test(cleanCode)) {
    throw createHttpError("股票代號須為 4～6 位數字", 400);
  }
  return cleanCode;
}

function validateStrategy(strategy) {
  const cleanStrategy = String(strategy || "ma").trim().toLowerCase();
  if (!RUNNABLE_STRATEGIES.includes(cleanStrategy)) {
    throw createHttpError(
      "支援 MA、RSI、MACD、布林通道、區間突破與多數決六種策略",
      400
    );
  }
  return cleanStrategy;
}

function validateDate(value, label) {
  const text = String(value || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    throw createHttpError(`${label}格式必須是 YYYY-MM-DD`, 400);
  }
  const date = new Date(`${text}T00:00:00`);
  if (Number.isNaN(date.getTime()) || date.getFullYear() !== Number(text.slice(0, 4)) || date.getMonth() + 1 !== Number(text.slice(5, 7)) || date.getDate() !== Number(text.slice(8, 10))) {
    throw createHttpError(`${label}不是有效日期`, 400);
  }
  return text;
}

function listReportDirectories(reportsRoot, code) {
  const codeDir = path.join(reportsRoot, code);
  if (!fs.existsSync(codeDir)) return [];
  return fs
    .readdirSync(codeDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => {
      const fullPath = path.join(codeDir, entry.name);
      return { reportId: entry.name, mtimeMs: fs.statSync(fullPath).mtimeMs };
    })
    .sort((a, b) => b.mtimeMs - a.mtimeMs);
}

function resolvePythonRunner() {
  const configured = String(process.env.PYTHON_EXECUTABLE || "").trim();
  if (configured) return { command: configured, prefixArgs: [] };

  const windowsVenv = path.join(
    PYTHON_BACKTEST_ROOT,
    ".venv",
    "Scripts",
    "python.exe"
  );
  const unixVenv = path.join(PYTHON_BACKTEST_ROOT, ".venv", "bin", "python");

  if (fs.existsSync(windowsVenv)) return { command: windowsVenv, prefixArgs: [] };
  if (fs.existsSync(unixVenv)) return { command: unixVenv, prefixArgs: [] };

  return {
    command: process.platform === "win32" ? "python" : "python3",
    prefixArgs: [],
  };
}



function tailText(text, maxLength = 6000) {
  const value = String(text || "");
  return value.length <= maxLength ? value : value.slice(-maxLength);
}

function runPythonProcess(args, temporaryReportsRoot) {
  return new Promise((resolve, reject) => {
    const runner = resolvePythonRunner();
    const child = spawn(runner.command, [...runner.prefixArgs, ...args], {
      cwd: PYTHON_BACKTEST_ROOT,
      env: {
        ...process.env,
        ALLOW_REAL_TRADING: "false",
        IS_BACKTEST: "true",
        IS_SIMULATION: "true",
        ONLY_BACKTEST: "true",
        REPORT_DIR: temporaryReportsRoot,
        LLM_PROVIDER: "Gemini",
        PYTHONIOENCODING: "utf-8",
      },
      windowsHide: true,
    });

    let stdout = "";
    let stderr = "";
    let finished = false;

    const timer = setTimeout(() => {
      if (finished) return;
      child.kill("SIGTERM");
      reject(
        createHttpError(
          `回測執行超過 ${Math.round(BACKTEST_TIMEOUT_MS / 60000)} 分鐘，已停止`,
          504
        )
      );
    }, BACKTEST_TIMEOUT_MS);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
      if (stdout.length > 120_000) stdout = stdout.slice(-120_000);
      process.stdout.write(chunk);
    });

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
      if (stderr.length > 120_000) stderr = stderr.slice(-120_000);
      process.stderr.write(chunk);
    });

    child.on("error", (error) => {
      clearTimeout(timer);
      finished = true;
      if (error.code === "ENOENT") {
        reject(
          createHttpError(
            "找不到 Python 執行環境。請先到 程式 執行 uv sync，或在 server/.env 設定 PYTHON_EXECUTABLE",
            500
          )
        );
        return;
      }
      reject(createHttpError(`無法啟動 Python 回測：${error.message}`, 500));
    });

    child.on("close", (code) => {
      clearTimeout(timer);
      finished = true;
      if (code !== 0) {
        reject(
          createHttpError(
            "Python 回測執行失敗。請檢查 程式/.env 的 Shioaji／TSST 憑證、日期與資料來源設定",
            500,
            tailText(stderr || stdout)
          )
        );
        return;
      }
      resolve({ stdout: tailText(stdout), stderr: tailText(stderr) });
    });
  });
}

function deriveSignal(report) {
  const latest = report.latestSignal;
  if (!latest) return "Hold";
  const action = String(latest.action || "").toLowerCase();
  if (action === "buy") return "Buy";
  if (action === "sell") return "Sell";
  return "Hold";
}

function derivePrediction(report) {
  const total = Number(report?.summary?.totalNetPnl || 0);
  if (total > 0) return "策略獲利";
  if (total < 0) return "策略虧損";
  return "策略持平";
}

async function persistBacktest({ userId, code, strategy, report }) {
  const connection = await pool.getConnection();
  try {
    await connection.beginTransaction();

    await connection.execute(
      `INSERT INTO stocks (stock_id, stock_name, market, industry)
       VALUES (?, ?, 'TWSE', NULL)
       ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP`,
      [code, code]
    );

    const [analysisResult] = await connection.execute(
      `INSERT INTO analysis_records
       (user_id, stock_id, prediction, \`signal\`, strategy_type, model_version, analysis_date)
       VALUES (?, ?, ?, ?, ?, 'umc-integrated-v1', NOW())`,
      [userId, code, derivePrediction(report), deriveSignal(report), strategy]
    );

    const analysisId = analysisResult.insertId;
    const summary = report.summary || {};
    const initialCapital = Number(summary.initialCapital ?? process.env.BACKTEST_INITIAL_CAPITAL ?? 100000);
    const totalNetPnl = Number(summary.totalNetPnl || 0);
    const totalGrossPnl = Number(summary.totalGrossPnl || 0);
    const totalFee = Number(summary.totalFee || 0);
    const totalTax = Number(summary.totalTax || 0);
    const finalAssets = Number(summary.finalAssets ?? initialCapital + totalNetPnl);

    await connection.execute(
      `INSERT INTO backtest_results
       (analysis_id, strategy_name, backtest_start, backtest_end,
        initial_capital, final_assets, gross_pnl, fee_amount, tax_amount,
        transaction_cost, net_pnl, completed_trades, return_rate, win_rate,
        sharpe_ratio, mdd, equity_curve)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        analysisId,
        strategy,
        report.startDate || null,
        report.endDate || null,
        initialCapital,
        finalAssets,
        totalGrossPnl,
        totalFee,
        totalTax,
        totalFee + totalTax,
        totalNetPnl,
        Number(summary.totalTrades || 0),
        Number(summary.totalReturnPct ?? summary.compoundedReturnPct ?? 0) / 100,
        Number(summary.winRate || 0) / 100,
        summary.sharpeRatio ?? null,
        summary.maxDrawdownPct == null
          ? null
          : Number(summary.maxDrawdownPct) / 100,
        JSON.stringify(
          (summary.equityCurve || []).map((point) => ({
            time: point.datetime,
            assets:
              point.equity ??
              initialCapital + Number(point.cumulativeNetPnl || 0),
          }))
        ),
      ]
    );

    const latest = report.latestSignal;
    if (latest?.datetime) {
      await connection.execute(
        `INSERT INTO technical_indicators
         (stock_id, trade_date, ma5, ma20, rsi, macd)
         VALUES (?, ?, ?, ?, ?, ?)
         ON DUPLICATE KEY UPDATE
           ma5 = VALUES(ma5), ma20 = VALUES(ma20),
           rsi = VALUES(rsi), macd = VALUES(macd)`,
        [
          code,
          latest.datetime,
          latest.maFast || null,
          latest.maSlow || null,
          latest.rsi || null,
          latest.macd || null,
        ]
      );
    }

    const databaseReport = {
      ...report,
      schemaVersion: 2,
      analysisId,
      storage: "database",
      reportUrl: null,
    };

    const serialized = JSON.stringify(databaseReport);
    const [[packetSettings]] = await connection.query("SELECT @@max_allowed_packet AS maxPacket");
    const reportBytes = Buffer.byteLength(serialized, "utf8");
    if (reportBytes + 65536 > Number(packetSettings.maxPacket)) {
      throw createHttpError(
        `報告大小約 ${(reportBytes / 1048576).toFixed(2)} MB，超過目前 MySQL 連線的傳輸上限。請調整 max_allowed_packet 後重新啟動 API。`,
        500
      );
    }
    // Send the complete JSON in one parameterized write. Repeated CONCAT updates
    // can lose the beginning of large LONGTEXT values on some MariaDB setups.
    const [reportInsertResult] = await connection.execute(
      "INSERT INTO reports (analysis_id, report_content) VALUES (?, ?)",
      [analysisId, serialized]
    );
    databaseReport.reportDbId = Number(reportInsertResult.insertId);
    delete databaseReport.reportHtml;

    await connection.commit();
    return { analysisId, report: databaseReport };
  } catch (error) {
    try { await connection.rollback(); }
    catch (rollbackError) { console.error("回復交易時連線已中斷：", rollbackError.code || rollbackError.message); }
    throw error;
  } finally {
    connection.release();
  }
}

async function executePythonBacktest({
  userId,
  code,
  strategy = "ma",
  startDate,
  endDate,
}) {
  const cleanCode = validateCode(code);
  const cleanStrategy = validateStrategy(strategy);

  if (!fs.existsSync(path.join(PYTHON_BACKTEST_ROOT, "app_backtest.py"))) {
    throw createHttpError(
      `找不到 Python 後端：${path.join(PYTHON_BACKTEST_ROOT, "app_backtest.py")}`,
      500
    );
  }

  const config = JSON.parse(fs.readFileSync(path.join(PYTHON_BACKTEST_ROOT, "backtest_config.json"), "utf8"));
  const first = validateDate(startDate || config.backtest_start, "開始日期");
  const last = validateDate(endDate || config.backtest_end, "結束日期");
  if (first > last) throw createHttpError("開始日期不能晚於結束日期", 400);
  if (first <= "2025-12-31" && last >= "2025-07-01") {
    throw createHttpError("2025/07/01～2025/12/31 為鎖定保留資料，無法從前端執行。", 400);
  }
  const temporaryReportsRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), "graduation-stock-backtest-")
  );

  let reportSaved = false;
  try {
    const args = [
      path.join(PYTHON_BACKTEST_ROOT, "app_backtest.py"),
      "--strategy",
      cleanStrategy,
      "--tick-source",
      String(process.env.BACKTEST_TICK_SOURCE || "sinopac"),
      "--run-name",
      `app_${cleanCode}_${cleanStrategy}`,
      "--code",
      cleanCode,
      "--profile",
      `2303_${cleanStrategy}`,
      "--backtest", "--simulation", "--only-backtest",
      "--db-disabled", "--llm-enabled", "--llm-use-cache",
    ];

    args.push("--backtest-start", first, "--backtest-end", last);

    const processResult = await runPythonProcess(args, temporaryReportsRoot);
    const created = listReportDirectories(temporaryReportsRoot, cleanCode)[0];

    if (!created) {
      throw createHttpError(
        `Python 回測已結束，但暫存區找不到 ${cleanCode} 的回測結果`,
        500,
        processResult.stdout
      );
    }

    const parsedReport = loadReportFromRoot(
      temporaryReportsRoot,
      cleanCode,
      created.reportId
    );

    const persisted = await persistBacktest({
      userId,
      code: cleanCode,
      strategy: cleanStrategy,
      report: parsedReport,
    });

    reportSaved = true;
    return {
      report: persisted.report,
      analysisId: persisted.analysisId,
      strategy: cleanStrategy,
      backend: "umc-integrated",
      storage: "mysql",
    };
  } catch (error) {
    error.details = [error.details, `回測輸出已保留於：${temporaryReportsRoot}`].filter(Boolean).join("\n");
    throw error;
  } finally {
    if (reportSaved) {
      try { fs.rmSync(temporaryReportsRoot, { recursive: true, force: true }); }
      catch (cleanupError) { console.warn("報告已存入資料庫，暫存檔清理失敗：", cleanupError.message); }
    }
  }
}

module.exports = {
  PYTHON_BACKTEST_ROOT,
  RUNNABLE_STRATEGIES,
  executePythonBacktest,
};

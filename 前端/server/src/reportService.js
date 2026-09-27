const fs = require("fs");
const path = require("path");
const { stockName } = require("./stockNames");

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];

    if (char === '"') {
      if (inQuotes && next === '"') {
        field += '"';
        index += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (char === "," && !inQuotes) {
      row.push(field);
      field = "";
      continue;
    }

    if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(field);
      field = "";
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
      continue;
    }

    field += char;
  }

  if (field !== "" || row.length > 0) {
    row.push(field);
    if (row.some((value) => value !== "")) rows.push(row);
  }

  if (rows.length === 0) return [];
  const headers = rows[0].map((header) => header.replace(/^\uFEFF/, "").trim());

  return rows.slice(1).map((values) => {
    const record = {};
    headers.forEach((header, index) => {
      record[header] = values[index] ?? "";
    });
    return record;
  });
}

function readCsv(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return parseCsv(fs.readFileSync(filePath, "utf8"));
}

function toNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function round(value, digits = 2) {
  if (!Number.isFinite(value)) return null;
  const factor = 10 ** digits;
  return Math.round((value + Number.EPSILON) * factor) / factor;
}

function safeSegment(value, label) {
  const text = String(value || "");
  if (!/^[A-Za-z0-9_.-]+$/.test(text)) {
    const error = new Error(`${label}格式不正確`);
    error.status = 400;
    throw error;
  }
  return text;
}

function getReportDirectory(reportsRoot, code, reportId) {
  const safeCode = safeSegment(code, "股票代號");
  const safeReportId = safeSegment(reportId, "報告編號");
  const root = path.resolve(reportsRoot);
  const directory = path.resolve(root, safeCode, safeReportId);
  const expectedPrefix = `${root}${path.sep}`;

  if (!directory.startsWith(expectedPrefix)) {
    const error = new Error("報告路徑不正確");
    error.status = 400;
    throw error;
  }

  if (!fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) {
    const error = new Error("找不到指定的暫存回測報告");
    error.status = 404;
    throw error;
  }

  return directory;
}

function parseReportMetadata(code, reportId) {
  const dates = reportId.match(/(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})$/);
  const generated = reportId.match(/^(\d{8})_(\d{6})_/);
  const strategyMatch = reportId.match(/^\d{8}_\d{6}_[^_]+_([^_]+)/);

  let generatedAt = null;
  if (generated) {
    const date = generated[1];
    const time = generated[2];
    generatedAt = `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}T${time.slice(0, 2)}:${time.slice(2, 4)}:${time.slice(4, 6)}`;
  }

  return {
    code,
    reportId,
    strategy: strategyMatch ? strategyMatch[1].toUpperCase() : "UNKNOWN",
    startDate: dates ? dates[1] : null,
    endDate: dates ? dates[2] : null,
    generatedAt,
  };
}

function normalizePnlRow(row) {
  return {
    code: row.code,
    buyDatetime: row.buy_datetime,
    sellDatetime: row.sell_datetime,
    quantity: toNumber(row.quantity),
    buyPrice: toNumber(row.buy_price),
    sellPrice: toNumber(row.sell_price),
    buyAmount: toNumber(row.buy_amount),
    sellAmount: toNumber(row.sell_amount),
    grossPnl: toNumber(row.gross_pnl),
    fee: toNumber(row.fee),
    tax: toNumber(row.tax),
    netPnl: toNumber(row.net_pnl),
    returnPct: toNumber(row.return_pct) * 100,
  };
}

function normalizeSignalRow(row) {
  return {
    datetime: row.datetime,
    code: row.code,
    action: row.action,
    signalType: row.signal_type,
    price: toNumber(row.price),
    reason: row.reason,
    maFast: toNumber(row.ma_fast),
    maMid: toNumber(row.ma_mid),
    maSlow: toNumber(row.ma_slow),
    rsi: toNumber(row.rsi),
    macd: toNumber(row.macd),
    macdSignal: toNumber(row.macd_signal),
    macdHist: toNumber(row.macd_hist),
  };
}

function calculateSummary(pnlRows) {
  const totalTrades = pnlRows.length;
  const winningRows = pnlRows.filter((row) => row.netPnl > 0);
  const losingRows = pnlRows.filter((row) => row.netPnl < 0);
  const breakEvenRows = pnlRows.filter((row) => row.netPnl === 0);
  const totalNetPnl = pnlRows.reduce((sum, row) => sum + row.netPnl, 0);
  const totalGrossPnl = pnlRows.reduce((sum, row) => sum + row.grossPnl, 0);
  const totalFee = pnlRows.reduce((sum, row) => sum + row.fee, 0);
  const totalTax = pnlRows.reduce((sum, row) => sum + row.tax, 0);
  const grossProfit = winningRows.reduce((sum, row) => sum + row.netPnl, 0);
  const grossLoss = Math.abs(losingRows.reduce((sum, row) => sum + row.netPnl, 0));
  const initialCapital = Math.max(1, Number(process.env.INITIAL_CAPITAL || process.env.BACKTEST_INITIAL_CAPITAL || 100000));
  const compoundedReturn = pnlRows.reduce((capital, row) => capital * (1 + row.returnPct / 100), 1);

  let cumulativePnl = 0;
  let peakEquity = initialCapital;
  let maxDrawdownAmount = 0;
  let maxDrawdownPct = 0;
  const equityCurve = pnlRows.map((row) => {
    cumulativePnl += row.netPnl;
    const equity = initialCapital + cumulativePnl;
    peakEquity = Math.max(peakEquity, equity);
    const drawdownAmount = peakEquity - equity;
    const drawdownPct = peakEquity > 0 ? (drawdownAmount / peakEquity) * 100 : 0;
    maxDrawdownAmount = Math.max(maxDrawdownAmount, drawdownAmount);
    maxDrawdownPct = Math.max(maxDrawdownPct, drawdownPct);
    return {
      datetime: row.sellDatetime,
      cumulativeNetPnl: round(cumulativePnl, 2),
      equity: round(equity, 2),
    };
  });

  const sampleSize = 120;
  const sampleStep = Math.max(1, Math.ceil(equityCurve.length / sampleSize));
  const sampledEquityCurve = equityCurve.filter((_point, index) => index % sampleStep === 0 || index === equityCurve.length - 1);
  const returnValues = pnlRows.map((row) => row.returnPct);
  const meanReturn = totalTrades ? returnValues.reduce((sum, value) => sum + value, 0) / totalTrades : 0;
  const variance = totalTrades > 1
    ? returnValues.reduce((sum, value) => sum + (value - meanReturn) ** 2, 0) / (totalTrades - 1)
    : 0;
  const standardDeviation = Math.sqrt(variance);
  const sharpeRatio = standardDeviation > 0 ? (meanReturn / standardDeviation) * Math.sqrt(totalTrades) : 0;

  return {
    totalTrades,
    winningTrades: winningRows.length,
    losingTrades: losingRows.length,
    breakEvenTrades: breakEvenRows.length,
    winRate: totalTrades > 0 ? round((winningRows.length / totalTrades) * 100) : 0,
    totalNetPnl: round(totalNetPnl, 2),
    totalGrossPnl: round(totalGrossPnl, 2),
    totalFee: round(totalFee, 2),
    totalTax: round(totalTax, 2),
    transactionCost: round(totalFee + totalTax, 2),
    initialCapital: round(initialCapital, 2),
    finalAssets: round(initialCapital + totalNetPnl, 2),
    totalReturnPct: round((totalNetPnl / initialCapital) * 100, 2),
    averageNetPnl: totalTrades > 0 ? round(totalNetPnl / totalTrades, 2) : 0,
    averageReturnPct: totalTrades > 0 ? round(meanReturn, 3) : 0,
    compoundedReturnPct: round((compoundedReturn - 1) * 100, 2),
    bestTradePct: totalTrades > 0 ? round(Math.max(...returnValues), 3) : 0,
    worstTradePct: totalTrades > 0 ? round(Math.min(...returnValues), 3) : 0,
    profitFactor: grossLoss > 0 ? round(grossProfit / grossLoss, 3) : null,
    sharpeRatio: round(sharpeRatio, 3),
    maxDrawdownAmount: round(maxDrawdownAmount, 2),
    maxDrawdownPct: round(maxDrawdownPct, 2),
    equityCurve: sampledEquityCurve,
  };
}

function loadReportFromRoot(reportsRoot, code, reportId) {
  const directory = getReportDirectory(reportsRoot, code, reportId);
  const pnlRows = readCsv(path.join(directory, "pnl.csv")).map(normalizePnlRow);
  const signals = readCsv(path.join(directory, "signals.csv")).map(normalizeSignalRow);
  const transactions = readCsv(path.join(directory, "trades.csv"));
  const metadata = parseReportMetadata(code, reportId);
  const latestSignal = signals.length > 0 ? signals[signals.length - 1] : null;
  const summaryPath = path.join(directory, "summary.json");
  if (!fs.existsSync(summaryPath)) throw new Error("新版回測缺少 summary.json，無法確認績效");
  const raw = JSON.parse(fs.readFileSync(summaryPath, "utf8"));
  const summary = calculateSummary(pnlRows);
  const fields = {
    totalTrades: "completed_trades", initialCapital: "initial_capital",
    finalAssets: "final_assets", totalNetPnl: "net_pnl", totalGrossPnl: "gross_pnl",
    totalFee: "fee", totalTax: "tax", transactionCost: "transaction_cost",
    profitFactor: "profit_factor", sharpeRatio: "sharpe_ratio",
  };
  for (const [target, source] of Object.entries(fields)) {
    if (raw[source] !== undefined) summary[target] = raw[source];
  }
  for (const [target, source] of Object.entries({
    totalReturnPct: "total_return", winRate: "win_rate", maxDrawdownPct: "max_drawdown",
  })) {
    summary[target] = raw[source] == null ? null : Number(raw[source]) * 100;
  }
  // Curve shows realized trade PnL; official return/drawdown/Sharpe come from the engine.
  summary.equityCurve = summary.equityCurve.map(point => ({
    ...point, equity: Number(summary.initialCapital) + point.cumulativeNetPnl,
  }));
  summary.equityCurveBasis = "realized_trades";

  return {
    ...metadata,
    storage: "database",
    stockName: stockName(code, raw.stock_name),
    reportHtml: fs.existsSync(path.join(directory, "report.html"))
      ? fs.readFileSync(path.join(directory, "report.html"), "utf8") : null,
    hasHtml: fs.existsSync(path.join(directory, "report.html")),
    strategyLabel: raw.strategy_label,
    startDate: raw.backtest_start,
    endDate: raw.backtest_end,
    researchStatus: raw.research_status,
    engineSummary: raw,
    summary,
    latestSignal,
    signalCount: signals.length,
    transactionCount: transactions.length,
    recentTrades: pnlRows.slice(-10).reverse(),
    recentSignals: signals.slice(-10).reverse(),
    // 全部結果會寫進 MySQL 的 reports.report_content，不留下永久檔案。
    pnlRows,
    signals,
    transactions,
    reportUrl: null,
  };
}

module.exports = {
  loadReportFromRoot,
};

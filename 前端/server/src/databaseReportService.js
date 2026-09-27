const pool = require("./db");
const { stockName } = require("./stockNames");

const IMPORT_USER_EMAIL = "local_import@example.com";

function createHttpError(message, status = 500) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function toNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function round(value, digits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  const factor = 10 ** digits;
  return Math.round((number + Number.EPSILON) * factor) / factor;
}

function parseJson(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "object") return value;
  try {
    return JSON.parse(String(value));
  } catch (_error) {
    return fallback;
  }
}

function normalizeStrategy(value) {
  return String(value || "").trim().toLowerCase();
}

function normalizeLegacySignal(row) {
  if (!row || typeof row !== "object") return null;
  return {
    datetime: row.datetime || null,
    code: row.code || null,
    action: row.action || "",
    signalType: row.signalType || row.signal_type || "",
    price: toNumber(row.price, 0),
    reason: row.reason || "",
    maFast: toNumber(row.maFast ?? row.ma_fast, 0),
    maMid: toNumber(row.maMid ?? row.ma_mid, 0),
    maSlow: toNumber(row.maSlow ?? row.ma_slow, 0),
    rsi: toNumber(row.rsi, 0),
    macd: toNumber(row.macd, 0),
    macdSignal: toNumber(row.macdSignal ?? row.macd_signal, 0),
    macdHist: toNumber(row.macdHist ?? row.macd_hist, 0),
    bbMiddle: row.bb_middle == null ? null : toNumber(row.bb_middle, 0),
    bbUpper: row.bb_upper == null ? null : toNumber(row.bb_upper, 0),
    bbLower: row.bb_lower == null ? null : toNumber(row.bb_lower, 0),
    breakoutHigh: row.breakout_high == null ? null : toNumber(row.breakout_high, 0),
    breakoutLow: row.breakout_low == null ? null : toNumber(row.breakout_low, 0),
  };
}

function normalizeLegacyPnl(row) {
  if (!row || typeof row !== "object") return null;
  const returnFraction = toNumber(row.returnPct ?? row.return_pct, 0);
  return {
    code: row.code || null,
    buyDatetime: row.buyDatetime || row.buy_datetime || null,
    sellDatetime: row.sellDatetime || row.sell_datetime || null,
    quantity: toNumber(row.quantity, 0),
    buyPrice: toNumber(row.buyPrice ?? row.buy_price, 0),
    sellPrice: toNumber(row.sellPrice ?? row.sell_price, 0),
    buyAmount: toNumber(row.buyAmount ?? row.buy_amount, 0),
    sellAmount: toNumber(row.sellAmount ?? row.sell_amount, 0),
    grossPnl: toNumber(row.grossPnl ?? row.gross_pnl, 0),
    fee: toNumber(row.fee, 0),
    tax: toNumber(row.tax, 0),
    netPnl: toNumber(row.netPnl ?? row.net_pnl, 0),
    returnPct:
      row.returnPct !== undefined
        ? toNumber(row.returnPct, 0)
        : returnFraction * 100,
  };
}

function calculateProfitFactor(pnlRows) {
  const grossProfit = pnlRows
    .filter((row) => row.netPnl > 0)
    .reduce((sum, row) => sum + row.netPnl, 0);
  const grossLoss = Math.abs(
    pnlRows
      .filter((row) => row.netPnl < 0)
      .reduce((sum, row) => sum + row.netPnl, 0)
  );
  return grossLoss > 0 ? round(grossProfit / grossLoss, 3) : null;
}

function normalizeEquityCurve(value, initialCapital) {
  const raw = parseJson(value, []);
  if (!Array.isArray(raw)) return [];
  return raw.map((point) => {
    const equity = toNumber(point.equity ?? point.assets, initialCapital);
    return {
      datetime: point.datetime || point.time || null,
      equity: round(equity, 2),
      cumulativeNetPnl: round(
        point.cumulativeNetPnl ?? point.cumulative_net_pnl ?? equity - initialCapital,
        2
      ),
    };
  });
}

function calculateMaxDrawdownAmount(equityCurve, initialCapital) {
  let peak = initialCapital;
  let maxAmount = 0;
  for (const point of equityCurve) {
    const equity = toNumber(point.equity, initialCapital);
    peak = Math.max(peak, equity);
    maxAmount = Math.max(maxAmount, peak - equity);
  }
  return maxAmount;
}

function normalizeImportedReport(report, row) {
  const legacySummary = report.summary || {};
  const pnlRows = (report.pnl_rows || report.pnlRows || [])
    .map(normalizeLegacyPnl)
    .filter(Boolean);
  const signals = (report.signals || [])
    .map(normalizeLegacySignal)
    .filter(Boolean);
  const transactions = Array.isArray(report.transactions) ? report.transactions : [];

  const winningTrades = pnlRows.filter((item) => item.netPnl > 0).length;
  const losingTrades = pnlRows.filter((item) => item.netPnl < 0).length;
  const breakEvenTrades = pnlRows.filter((item) => item.netPnl === 0).length;
  const totalTrades = Number(
    legacySummary.completed_trades ?? row.completed_trades ?? pnlRows.length
  );
  const initialCapital = toNumber(
    legacySummary.initial_capital ?? row.initial_capital,
    100000
  );
  const finalAssets = toNumber(
    legacySummary.final_assets ?? row.final_assets,
    initialCapital + toNumber(legacySummary.net_pnl ?? row.net_pnl, 0)
  );
  const totalNetPnl = toNumber(legacySummary.net_pnl ?? row.net_pnl, 0);
  const totalGrossPnl = toNumber(legacySummary.gross_pnl ?? row.gross_pnl, 0);
  const totalFee = toNumber(legacySummary.fee ?? row.fee_amount, 0);
  const totalTax = toNumber(legacySummary.tax ?? row.tax_amount, 0);
  const totalReturnFraction = toNumber(
    legacySummary.total_return ?? row.return_rate,
    initialCapital ? (finalAssets - initialCapital) / initialCapital : 0
  );
  const winRateFraction = toNumber(
    legacySummary.win_rate ?? row.win_rate,
    totalTrades > 0 ? winningTrades / totalTrades : 0
  );
  const maxDrawdownFraction = toNumber(
    legacySummary.max_drawdown ?? row.mdd,
    0
  );
  const returnValues = pnlRows.map((item) => item.returnPct);
  const averageReturnPct =
    returnValues.length > 0
      ? returnValues.reduce((sum, value) => sum + value, 0) / returnValues.length
      : 0;

  const equityCurve = normalizeEquityCurve(row.equity_curve, initialCapital);
  const maxDrawdownAmount = equityCurve.length
    ? calculateMaxDrawdownAmount(equityCurve, initialCapital)
    : initialCapital * maxDrawdownFraction;

  return {
    schemaVersion: Number(report.schema_version || 1),
    storage: "database",
    imported: true,
    code: legacySummary.stock_code || row.stock_id,
    strategy: String(
      legacySummary.strategy || row.strategy_type || row.strategy_name || ""
    ).toUpperCase(),
    strategyKey: normalizeStrategy(
      legacySummary.strategy || row.strategy_type || row.strategy_name
    ),
    strategyLabel: legacySummary.strategy_label || null,
    strategyCategory: legacySummary.strategy_category || null,
    suitableMarket: legacySummary.suitable_market || null,
    startDate: legacySummary.backtest_start || row.backtest_start || null,
    endDate: legacySummary.backtest_end || row.backtest_end || null,
    generatedAt: legacySummary.generated_at || row.report_created_at || row.created_at,
    summary: {
      totalTrades,
      winningTrades,
      losingTrades,
      breakEvenTrades,
      winRate: round(winRateFraction * 100, 2),
      totalNetPnl: round(totalNetPnl, 2),
      totalGrossPnl: round(totalGrossPnl, 2),
      totalFee: round(totalFee, 2),
      totalTax: round(totalTax, 2),
      averageNetPnl: totalTrades > 0 ? round(totalNetPnl / totalTrades, 2) : 0,
      averageReturnPct: round(averageReturnPct, 3),
      totalReturnPct: round(totalReturnFraction * 100, 2),
      compoundedReturnPct: round(totalReturnFraction * 100, 2),
      bestTradePct: returnValues.length ? round(Math.max(...returnValues), 3) : 0,
      worstTradePct: returnValues.length ? round(Math.min(...returnValues), 3) : 0,
      profitFactor: calculateProfitFactor(pnlRows),
      sharpeRatio: round(
        toNumber(legacySummary.sharpe_ratio ?? row.sharpe_ratio, 0),
        3
      ),
      maxDrawdownAmount: round(maxDrawdownAmount, 2),
      maxDrawdownPct: round(maxDrawdownFraction * 100, 2),
      initialCapital: round(initialCapital, 2),
      finalAssets: round(finalAssets, 2),
      transactionCost: round(
        toNumber(
          legacySummary.transaction_cost ?? row.transaction_cost,
          totalFee + totalTax
        ),
        2
      ),
      equityCurve,
    },
    latestSignal: signals.length ? signals[signals.length - 1] : null,
    signalCount: Number(legacySummary.signal_records ?? signals.length),
    transactionCount: Number(legacySummary.trade_records ?? transactions.length),
    recentTrades: pnlRows.slice(-10).reverse(),
    recentSignals: signals.slice(-10).reverse(),
    pnlRows,
    signals,
    transactions,
    reportUrl: null,
  };
}

function normalizeCurrentReport(report, row) {
  const normalized = {
    ...report,
    storage: "database",
    reportUrl: null,
  };
  if (normalized.strategy && !normalized.strategyKey) {
    normalized.strategyKey = normalizeStrategy(normalized.strategy);
  }
  return normalized;
}

function mapRow(row) {
  const report = parseJson(row.report_content, {});
  const isImportedFormat =
    report?.summary?.net_pnl !== undefined || report?.schema_version === 1;
  const normalized = isImportedFormat
    ? normalizeImportedReport(report, row)
    : normalizeCurrentReport(report, row);

  // HTML is fetched separately so history and summary responses stay small.
  delete normalized.reportHtml;
  return {
    ...normalized,
    stockName: stockName(normalized.code || row.stock_id, row.stock_name,
      report.stockName, report.summary?.stock_name, report.engineSummary?.stock_name),
    hasHtml: Boolean(report.reportHtml || (report.sourceArchiveSha256 && report.report_file)),
    isTestReport: Boolean(report.isTestReport),
    analysisId: Number(row.analysis_id),
    reportDbId: Number(row.report_db_id),
    code: normalized.code || row.stock_id,
    strategy:
      normalized.strategy || String(row.strategy_type || row.strategy_name || "").toUpperCase(),
    strategyKey:
      normalized.strategyKey || normalizeStrategy(row.strategy_type || row.strategy_name),
    generatedAt: normalized.generatedAt || row.report_created_at || row.created_at,
    storage: "database",
    reportUrl: null,
    sourceOwner: row.owner_email === IMPORT_USER_EMAIL ? "imported" : "user",
  };
}

const BASE_SELECT = `SELECT
  r.report_id AS report_db_id,
  r.analysis_id,
  r.report_content,
  r.created_at AS report_created_at,
  ar.stock_id,
  st.stock_name,
  ar.strategy_type,
  ar.user_id,
  u.email AS owner_email,
  br.strategy_name,
  br.backtest_start,
  br.backtest_end,
  br.initial_capital,
  br.final_assets,
  br.gross_pnl,
  br.fee_amount,
  br.tax_amount,
  br.transaction_cost,
  br.net_pnl,
  br.completed_trades,
  br.return_rate,
  br.win_rate,
  br.sharpe_ratio,
  br.mdd,
  br.equity_curve
FROM reports r
INNER JOIN analysis_records ar ON ar.analysis_id = r.analysis_id
INNER JOIN users u ON u.user_id = ar.user_id
LEFT JOIN backtest_results br ON br.analysis_id = ar.analysis_id
LEFT JOIN stocks st ON st.stock_id = ar.stock_id`;

function buildAccessWhere({ userId, code, strategy, analysisId }) {
  const clauses = ["(ar.user_id = ? OR u.email = ?)"];
  const values = [userId, IMPORT_USER_EMAIL];

  if (code) {
    clauses.push("ar.stock_id = ?");
    values.push(String(code).trim());
  }
  if (strategy) {
    clauses.push("LOWER(ar.strategy_type) = ?");
    values.push(normalizeStrategy(strategy));
  }
  if (analysisId) {
    clauses.push("ar.analysis_id = ?");
    values.push(Number(analysisId));
  }

  return { where: clauses.join(" AND "), values };
}

async function listDatabaseReports(userId, code = null, strategy = null) {
  const { where, values } = buildAccessWhere({ userId, code, strategy });
  const [rows] = await pool.execute(
    `${BASE_SELECT}\nWHERE ${where}\nORDER BY r.created_at DESC, r.report_id DESC`,
    values
  );
  return rows.map(mapRow);
}

async function loadLatestDatabaseReport(userId, code, strategy = null) {
  const { where, values } = buildAccessWhere({ userId, code, strategy });
  const [rows] = await pool.execute(
    `${BASE_SELECT}\nWHERE ${where}\nORDER BY
       CASE WHEN ar.user_id = ? THEN 0 ELSE 1 END,
       r.created_at DESC, r.report_id DESC\nLIMIT 1`,
    [...values, userId]
  );

  if (rows.length === 0) {
    const strategyText = strategy ? `、策略 ${String(strategy).toUpperCase()}` : "";
    throw createHttpError(`資料庫中找不到股票 ${code}${strategyText} 的回測報告`, 404);
  }
  return mapRow(rows[0]);
}

async function loadDatabaseReportByAnalysisId(userId, analysisId) {
  const cleanId = Number(analysisId);
  if (!Number.isInteger(cleanId) || cleanId <= 0) {
    throw createHttpError("分析編號格式不正確", 400);
  }

  const { where, values } = buildAccessWhere({ userId, analysisId: cleanId });
  const [rows] = await pool.execute(`${BASE_SELECT}\nWHERE ${where}\nLIMIT 1`, values);
  if (rows.length === 0) {
    throw createHttpError("找不到指定的資料庫回測報告", 404);
  }
  return mapRow(rows[0]);
}

async function loadDatabaseReportByLegacyId(userId, code, reportId) {
  const reports = await listDatabaseReports(userId, code);
  const report = reports.find(
    (item) => String(item.reportId || item.reportDbId) === String(reportId)
  );
  if (!report) {
    throw createHttpError("找不到指定的資料庫回測報告", 404);
  }
  return report;
}

async function listReportHistory(userId, code, strategy) {
  const reports = await listDatabaseReports(userId, code, strategy);
  return reports.map(({ analysisId, code, stockName, strategyLabel, strategy,
    generatedAt, startDate, endDate, sourceOwner, hasHtml, isTestReport }) => ({
    analysisId, code, stockName, strategyLabel, strategy, generatedAt,
    startDate, endDate, sourceOwner, hasHtml, isTestReport,
  }));
}

module.exports = {
  listReportHistory,
  listDatabaseReports,
  loadLatestDatabaseReport,
  loadDatabaseReportByAnalysisId,
  loadDatabaseReportByLegacyId,
};

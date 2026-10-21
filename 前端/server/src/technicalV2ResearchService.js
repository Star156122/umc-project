const fs = require("fs");
const path = require("path");
const { PYTHON_BACKTEST_ROOT } = require("./pythonBacktestService");

const BATCH_ID = "strategy-v2-technical-batch-20261019T094810074002";
const FREEZE_ID = "strategy-v2-single-stock-development-freeze-20261019T181513514684";
const BATCH_DIRECTORY = path.join("exports", "strategy_v2_technical_batch_20261019T094810074002");
const FREEZE_DIRECTORY = path.join("exports", "strategy_v2_development_freeze_20261019T181513514684");
const FOLD_ORDER = ["WF1", "WF2", "WF3"];
const TECHNICAL_CANDIDATE = "BR_SENS_RANGE55_BB20_2";
const MODEL = "random_forest";
const VERSION = "TECH_V2_B";

class TechnicalV2ResearchError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = "TechnicalV2ResearchError";
    this.status = status;
    this.code = code;
  }
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[i + 1] === "\n") i += 1;
      row.push(field);
      field = "";
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
    } else {
      field += char;
    }
  }
  if (field !== "" || row.length) {
    row.push(field);
    if (row.some((value) => value !== "")) rows.push(row);
  }
  if (!rows.length) return [];
  const headers = rows[0].map((header) => header.replace(/^\uFEFF/, "").trim());
  return rows.slice(1).map((values) => Object.fromEntries(
    headers.map((header, index) => [header, values[index] ?? ""])
  ));
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new TechnicalV2ResearchError("Technical V2 研究索引格式無效。", 500, "RESEARCH_DATA_INVALID");
    }
    throw new TechnicalV2ResearchError("Technical V2 研究輸出目前無法讀取。", 503, "RESEARCH_DATA_UNAVAILABLE");
  }
}

function readCsv(file) {
  try {
    const text = fs.readFileSync(file, "utf8");
    const rows = parseCsv(text);
    if (!rows.length) throw new Error("empty csv");
    return rows;
  } catch (_error) {
    throw new TechnicalV2ResearchError("Technical V2 逐股或 Fold 研究輸出缺少或格式無效。", 503, "RESEARCH_DATA_UNAVAILABLE");
  }
}

function finiteNumber(value, field) {
  const number = Number(value);
  if (value === "" || value == null || !Number.isFinite(number)) {
    throw new TechnicalV2ResearchError(`Technical V2 研究欄位 ${field} 無有效數值。`, 500, "RESEARCH_DATA_INVALID");
  }
  return number;
}

function optionalNumber(value) {
  if (value === "" || value == null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function validateSource(root) {
  const auditPath = path.join(root, BATCH_DIRECTORY, "technical_v2_batch_audit.json");
  const freezePath = path.join(root, FREEZE_DIRECTORY, "strategy_v2_freeze_manifest.json");
  const audit = readJson(auditPath);
  const freeze = readJson(freezePath);
  const valid = audit.status === "COMPLETED"
    && audit.experiment_id === BATCH_ID
    && audit.classifications?.[VERSION] === "SUPPORTED"
    && audit.period?.start === "2023-01-01"
    && audit.period?.end === "2024-12-31"
    && audit.technical_candidate === TECHNICAL_CANDIDATE
    && JSON.stringify(audit.folds) === JSON.stringify(FOLD_ORDER)
    && audit.models?.includes(MODEL)
    && freeze.freeze_id === FREEZE_ID
    && freeze.strategy_v2_status === "FROZEN_DEVELOPMENT_CANDIDATE"
    && freeze.candidate?.technical_candidate_id === TECHNICAL_CANDIDATE
    && String(freeze.candidate?.technical || "").includes("TECH_V2_B_MARKET_REGIME_FILTER")
    && freeze.candidate?.ml_model === "ML V1 Random Forest OOF"
    && freeze.candidate?.ml_gate?.BUY === "ALLOW_LONG_ENTRY"
    && freeze.candidate?.ml_gate?.HOLD === "BLOCK"
    && freeze.candidate?.ml_gate?.SELL === "BLOCK"
    && freeze.candidate?.risk?.version === "Risk V1"
    && freeze.research_data?.period?.start === "2023-01-01"
    && freeze.research_data?.period?.end === "2024-12-31";
  if (!valid) {
    throw new TechnicalV2ResearchError("Technical V2-B 批次與凍結版本索引不一致，拒絕回傳研究結果。", 503, "RESEARCH_SOURCE_MISMATCH");
  }
  return { audit, freeze };
}

function assertClose(actual, expected, label, tolerance = 1e-8) {
  if (Math.abs(actual - expected) > tolerance) {
    throw new TechnicalV2ResearchError(`Technical V2 摘要與 Fold 明細的 ${label} 不一致。`, 500, "RESEARCH_DATA_INVALID");
  }
}

function calculateTradeWeightedWinRate(folds) {
  let totalTrades = 0;
  let totalWins = 0;
  const integerTolerance = 0.01;
  for (const fold of folds) {
    const trades = fold.metrics.trade_count;
    const rate = fold.metrics.win_rate_pct / 100;
    if (!Number.isInteger(trades) || trades < 0 || !Number.isFinite(rate) || rate < 0 || rate > 1) {
      return { value: null, reason: "Fold 交易次數或勝率超出有效範圍。" };
    }
    if (trades === 0) continue;
    const estimatedWins = trades * rate;
    const roundedWins = Math.round(estimatedWins);
    if (Math.abs(estimatedWins - roundedWins) > integerTolerance) {
      return { value: null, reason: `Fold ${fold.fold_id} 的勝率與交易數無法推得可信的整數獲利筆數。` };
    }
    totalTrades += trades;
    totalWins += roundedWins;
  }
  if (totalTrades === 0) {
    return { value: null, reason: "所有 Fold 均無已完成交易，交易加權勝率不適用。" };
  }
  return { value: (totalWins / totalTrades) * 100, reason: null };
}

function buildTechnicalV2StockResearch(stockCode, root = PYTHON_BACKTEST_ROOT) {
  const code = String(stockCode || "").trim();
  if (!/^\d{4,6}$/.test(code)) {
    throw new TechnicalV2ResearchError("股票代號格式錯誤，請輸入 4～6 位數字。", 400, "INVALID_STOCK_CODE");
  }

  const { audit, freeze } = validateSource(root);
  const batchDir = path.join(root, BATCH_DIRECTORY);
  const detailRows = readCsv(path.join(batchDir, "technical_v2_expB.csv"))
    .filter((row) => row.version === VERSION && row.model === MODEL && row.stock_code === code);
  const summaryRows = readCsv(path.join(batchDir, "technical_v2_per_stock.csv"))
    .filter((row) => row.version === VERSION && row.model === MODEL && row.stock_code === code);

  if (!detailRows.length || !summaryRows.length) {
    throw new TechnicalV2ResearchError(`找不到股票 ${code} 的 Technical V2-B 歷史研究成果。`, 404, "RESEARCH_RESULT_NOT_FOUND");
  }
  if (summaryRows.length !== 1 || detailRows.length !== FOLD_ORDER.length) {
    throw new TechnicalV2ResearchError(`股票 ${code} 的 Technical V2-B 輸出筆數不完整或重複。`, 500, "RESEARCH_DATA_INVALID");
  }
  if (detailRows.some((row) => row.candidate_id !== TECHNICAL_CANDIDATE)) {
    throw new TechnicalV2ResearchError(`股票 ${code} 的逐 Fold Candidate 與凍結版本不一致。`, 500, "RESEARCH_DATA_INVALID");
  }

  const summary = summaryRows[0];
  const byFold = new Map(detailRows.map((row) => [row.fold_id, row]));
  if (byFold.size !== FOLD_ORDER.length || FOLD_ORDER.some((fold) => !byFold.has(fold))) {
    throw new TechnicalV2ResearchError(`股票 ${code} 的 Walk-forward Fold 不完整。`, 500, "RESEARCH_DATA_INVALID");
  }
  const folds = FOLD_ORDER.map((foldId) => {
    const row = byFold.get(foldId);
    const foldTradeCount = finiteNumber(row.trade_count, "trade_count");
    const foldWinRatePct = finiteNumber(row.win_rate, "win_rate") * 100;
    const foldProfitFactor = optionalNumber(row.profit_factor);
    const foldSharpe = finiteNumber(row.sharpe_ratio, "sharpe_ratio");
    const hasTrades = foldTradeCount > 0;
    return {
      fold_id: foldId,
      evaluation_period: { start: row.evaluate_start, end: row.evaluate_end },
      metrics: {
        return_pct: finiteNumber(row.total_return, "total_return") * 100,
        win_rate_pct: foldWinRatePct,
        max_drawdown_pct: finiteNumber(row.max_drawdown, "max_drawdown") * 100,
        sharpe: foldSharpe,
        profit_factor: foldProfitFactor,
        trade_count: foldTradeCount,
        net_pnl: finiteNumber(row.net_pnl, "net_pnl"),
        transaction_cost: finiteNumber(row.transaction_cost, "transaction_cost"),
        initial_capital: finiteNumber(row.initial_capital, "initial_capital"),
        final_equity: finiteNumber(row.final_equity, "final_equity"),
      },
      metric_applicability: {
        win_rate: hasTrades ? "applicable" : "not_applicable_no_trades",
        profit_factor: hasTrades ? "applicable" : "not_applicable_no_trades",
        sharpe: !hasTrades
          ? "engine_zero_for_flat_no_trade_equity; not evidence of risk-adjusted performance"
          : "computed_from_mark_to_market_daily_returns",
      },
    };
  });

  const evaluationCount = finiteNumber(summary.evaluation_count, "evaluation_count");
  const tradeCount = finiteNumber(summary.trade_count, "trade_count");
  const netPnl = finiteNumber(summary.net_pnl, "net_pnl");
  const averageReturn = finiteNumber(summary.average_return, "average_return");
  const meanFoldWinRate = finiteNumber(summary.win_rate, "win_rate");
  if (evaluationCount !== folds.length) {
    throw new TechnicalV2ResearchError("Technical V2 摘要的 Fold 數與明細不一致。", 500, "RESEARCH_DATA_INVALID");
  }
  assertClose(tradeCount, folds.reduce((sum, fold) => sum + fold.metrics.trade_count, 0), "交易次數", 0);
  assertClose(netPnl, folds.reduce((sum, fold) => sum + fold.metrics.net_pnl, 0), "Net PnL", 0.011);
  assertClose(averageReturn, folds.reduce((sum, fold) => sum + fold.metrics.return_pct / 100, 0) / folds.length, "平均報酬率");
  assertClose(meanFoldWinRate, folds.reduce((sum, fold) => sum + fold.metrics.win_rate_pct / 100, 0) / folds.length, "平均 Fold 勝率");
  const weightedWinRate = calculateTradeWeightedWinRate(folds);

  const sourceFile = path.join(BATCH_DIRECTORY, "technical_v2_expB.csv").replace(/\\/g, "/");
  const summaryFile = path.join(BATCH_DIRECTORY, "technical_v2_per_stock.csv").replace(/\\/g, "/");
  return {
    stock_code: code,
    strategy_id: `${TECHNICAL_CANDIDATE}+TECH_V2_B_MARKET_REGIME_FILTER`,
    strategy_version: "Technical V2-B",
    data_period: { start: "2023-01-01", end: "2024-12-31" },
    data_role: "historical_development",
    folds,
    metrics: {
      evaluation_count: evaluationCount,
      trade_count: tradeCount,
      average_return_pct: averageReturn * 100,
      cumulative_return_pct: null,
      cumulative_return_status: "unavailable_independent_folds_not_a_continuous_portfolio",
      median_return_pct: finiteNumber(summary.median_return, "median_return") * 100,
      return_std_pct: finiteNumber(summary.return_std, "return_std") * 100,
      win_rate_pct: meanFoldWinRate * 100,
      mean_fold_win_rate_pct: meanFoldWinRate * 100,
      trade_weighted_win_rate_pct: weightedWinRate.value,
      trade_weighted_win_rate_status: weightedWinRate.value == null ? "unavailable" : "available",
      trade_weighted_win_rate_reason: weightedWinRate.reason,
      worst_max_drawdown_pct: finiteNumber(summary.worst_mdd, "worst_mdd") * 100,
      profit_factor: optionalNumber(summary.profit_factor),
      profit_factor_applicability: "arithmetic_mean_of_existing_fold_values; zero-trade Fold source value is retained but not applicable",
      sharpe: finiteNumber(summary.sharpe, "sharpe"),
      net_pnl: netPnl,
      gross_pnl: finiteNumber(summary.gross_pnl, "gross_pnl"),
      transaction_cost: finiteNumber(summary.transaction_cost, "transaction_cost"),
      mean_final_equity: finiteNumber(summary.final_equity_mean, "final_equity_mean"),
      aggregation: {
        return_and_win_rate: "arithmetic_mean_across_independent_folds",
        average_return_pct: "arithmetic_mean_of_independent_fold_returns",
        cumulative_return_pct: "not_computed; independent Fold simulations are not a continuous portfolio",
        win_rate_pct: "legacy alias of mean_fold_win_rate_pct; source Fold values include zero-trade Fold values",
        trade_weighted_win_rate_pct: "sum of validated integer estimated wins divided by total completed trades; null if invalid or no trades",
        return_standard_deviation: "sample_standard_deviation_across_fold_returns",
        worst_max_drawdown: "maximum_of_fold_max_drawdowns",
        profit_factor_and_sharpe: "arithmetic_mean_of_existing_fold_metrics; not recomputed from merged trades",
        net_pnl_and_cost: "sum_across_independent_fold_simulations",
        fold_returns_compounded: false,
      },
    },
    evaluation_context: {
      ml_gate: "ML V1 Random Forest OOF; BUY allows long entry, HOLD/SELL block entry",
      risk: "Risk V1 fixed sizing",
      execution_contract: audit.execution_contract,
      long_only: true,
      portfolio: false,
    },
    source: {
      experiment_id: "strategy-v2-technical-exp-b",
      batch_id: audit.experiment_id,
      freeze_id: freeze.freeze_id,
      batch_classification: audit.classifications[VERSION],
      generated_at: audit.generated_at,
      result_file: sourceFile,
      per_stock_summary_file: summaryFile,
      audit_file: path.join(BATCH_DIRECTORY, "technical_v2_batch_audit.json").replace(/\\/g, "/"),
    },
    limitations: [
      "這是 2023–2024 已暴露的歷史開發研究結果，不是未來或即時預測。",
      "報酬與勝率等摘要依各 Fold 獨立模擬彙整；不同 Fold 報酬沒有複利或串成連續投資組合。",
      "此批次結果沿用 ML V1 Random Forest OOF gate 與 Risk V1 固定風控，並非脫離 Hybrid/Risk 的純 Technical-only 回測。",
      "Technical V2-B 使用核心股票的等權重市場代理，不代表外部市場指數。",
      "研究成果不構成今日交易訊號或投資建議。",
    ],
  };
}

function createTechnicalV2ResearchRouter({ express, authenticate, root = PYTHON_BACKTEST_ROOT }) {
  const router = express.Router();
  router.get("/v2/technical/:stockCode", authenticate, (req, res) => {
    try {
      return res.json(buildTechnicalV2StockResearch(req.params.stockCode, root));
    } catch (error) {
      if (error instanceof TechnicalV2ResearchError) {
        return res.status(error.status).json({
          error: { code: error.code, message: error.message },
        });
      }
      console.error("讀取 Technical V2 研究成果失敗：", error);
      return res.status(500).json({
        error: { code: "RESEARCH_READ_FAILED", message: "讀取 Technical V2 研究成果失敗。" },
      });
    }
  });
  return router;
}

module.exports = {
  TechnicalV2ResearchError,
  buildTechnicalV2StockResearch,
  calculateTradeWeightedWinRate,
  createTechnicalV2ResearchRouter,
};

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { PYTHON_BACKTEST_ROOT } = require("./pythonBacktestService");

const COMMON_DIR = "exports/hybrid_common_coverage_20261018T121416162413";
const RISK_DIR = "exports/hybrid_risk_v1_20261018T122032419364";
const RISK_VARIANT = "hybrid_random_forest_risk_v1";
const BASE_CANDIDATE = "BR_SENS_RANGE55_BB20_2";
const TECHNICAL_SOURCE_VERSION = "technical-v1-draft-20261010";
const TECHNICAL_SOURCE_EXPERIMENT_ID = "technical-v1-20261010-v1";
const CANDIDATE_FINGERPRINT = "24dbe3030fe335b8e62684e908fbe139315933add7a904c38fae07e0e24707bf";
const OOF_SHA256 = "d70c57acd81445c5e33474ec7a28ff18d52e2a3e162c0737132f3be7ca6f7e4b";
const FILE_HASHES = {
  [`${COMMON_DIR}/common_coverage_audit.json`]: "3794b3c4b31b251de6b59f853873e802292143cbd2377faabeca8afdd5b171e8",
  [`${COMMON_DIR}/common_coverage_candidates.csv`]: "e66c808eb73c9aef37cfeba411f0e8bfd3ce8b98cf49fe7d4f82ca1f7badbaca",
  [`${COMMON_DIR}/common_coverage_decisions.csv`]: "1f41b109f1123d383286e58b96c6e755cda5dabf47f66a6587b831ab441e1eea",
  [`${RISK_DIR}/risk_v1_audit.json`]: "3c83b1b2fde2b7c5c34d0c9e6f1a7fba35f8af3cfc25898a2f6253eff9d6437f",
  [`${RISK_DIR}/risk_v1_decisions.csv`]: "435533f1c0c9aab3734108da2c623146ecdb78db9088a148c23fd720bd589eaf",
  [`${RISK_DIR}/risk_v1_trades.csv`]: "5d67d36d37712fbcb4ff8046052f80274807990f1817a24d783478c927d7845c",
  [`${RISK_DIR}/risk_v1_results.csv`]: "f627d657f233cedc03b40d373c809556344e9360766dacdaa7a9c5cb1e3cd50f",
  [`${RISK_DIR}/risk_v1_per_stock.csv`]: "f22bec6966e0bb0f31de48939509f900c7722e739db50c5bb09e0fe14661cbcd",
  "hybrid/config/risk_v1.json": "9035e97a0c9dff8f481af47d69147566712422e46222f9b1db5bbe66135d59ec",
  "exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv": OOF_SHA256,
};
const MODELS = ["random_forest", "xgboost", "gru"];
const FOLDS = ["WF1", "WF2", "WF3"];
const LABELS = new Set(["BUY", "HOLD", "SELL"]);

class HybridRiskV1ResearchError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = "HybridRiskV1ResearchError";
    this.status = status;
    this.code = code;
  }
}

function unavailable(message = "Technical Control + RF BUY-only Gate + Risk V1 研究來源缺少或未通過完整性檢查。") {
  return new HybridRiskV1ResearchError(message, 503, "HYBRID_RISK_SOURCE_INVALID");
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { field += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") { row.push(field); field = ""; }
    else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[i + 1] === "\n") i += 1;
      row.push(field); field = "";
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
    } else field += char;
  }
  if (field !== "" || row.length) {
    row.push(field);
    if (row.some((value) => value !== "")) rows.push(row);
  }
  if (!rows.length) return { headers: [], records: [] };
  const headers = rows[0].map((item) => item.replace(/^\uFEFF/, "").trim());
  return { headers, records: rows.slice(1).map((values) => Object.fromEntries(headers.map((h, i) => [h, values[i] ?? ""]))) };
}

function hashFile(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function readText(root, relative) {
  try { return fs.readFileSync(path.join(root, relative), "utf8"); }
  catch (_error) { throw new HybridRiskV1ResearchError(`Control + RF BUY-only Gate + Risk V1 來源檔案無法讀取：${relative}`, 503, "HYBRID_RISK_SOURCE_UNAVAILABLE"); }
}

function readJson(root, relative) {
  try { return JSON.parse(readText(root, relative)); }
  catch (error) {
    if (error instanceof HybridRiskV1ResearchError) throw error;
    throw unavailable(`Control + RF BUY-only Gate + Risk V1 來源 JSON 無效：${relative}`);
  }
}

function readCsv(root, relative, requiredColumns) {
  const text = readText(root, relative);
  const parsed = parseCsv(text);
  if (requiredColumns.some((column) => !parsed.headers.includes(column)) || !parsed.records.length) {
    throw unavailable(`Control + RF BUY-only Gate + Risk V1 CSV 欄位不完整：${relative}`);
  }
  return parsed.records;
}

function num(value, field) {
  const result = Number(value);
  if (value === "" || value == null || !Number.isFinite(result)) throw unavailable(`研究輸出欄位 ${field} 不是有效數值。`);
  return result;
}

function timestampEpochSeconds(value) {
  const normalized = String(value).replace(/(\.\d{3})\d+(?=[+-]\d{2}:?\d{2}$)/, "$1");
  const milliseconds = Date.parse(normalized);
  return Number.isFinite(milliseconds) ? milliseconds / 1000 : NaN;
}

function assertHash(root, relative, expected) {
  let actual;
  try { actual = hashFile(path.join(root, relative)); }
  catch (_error) { throw new HybridRiskV1ResearchError(`研究來源檔案不存在：${relative}`, 503, "HYBRID_RISK_SOURCE_UNAVAILABLE"); }
  if (actual !== expected) throw unavailable(`研究來源雜湊不符：${relative}`);
  return actual;
}

function loadAndValidateSources(root) {
  const hashes = {};
  for (const [relative, expected] of Object.entries(FILE_HASHES)) hashes[relative] = assertHash(root, relative, expected);

  const commonAudit = readJson(root, `${COMMON_DIR}/common_coverage_audit.json`);
  const riskAudit = readJson(root, `${RISK_DIR}/risk_v1_audit.json`);
  const riskConfig = readJson(root, "hybrid/config/risk_v1.json");
  const validCommon = commonAudit.status === "PASS"
    && commonAudit.experiment_id === "hybrid-common-coverage-20261018T121416162413"
    && commonAudit.scope?.data_role === "training"
    && JSON.stringify(commonAudit.scope?.period) === JSON.stringify(["2023-01-01", "2024-12-31"])
    && JSON.stringify(commonAudit.scope?.stock_universe) === JSON.stringify(["2303", "2330", "2379", "2454", "3034"])
    && commonAudit.technical_candidate_count === 248
    && commonAudit.common_candidate_count === 192
    && commonAudit.excluded_candidate_count === 56
    && commonAudit.missing === 0 && commonAudit.invalid === 0 && commonAudit.non_oof === 0
    && commonAudit.training_only === true
    && commonAudit.inputs?.oof_sha256 === OOF_SHA256
    && commonAudit.inputs?.technical_source_run?.replaceAll("\\", "/") === "exports/technical_v1_20261013T024919303843"
    && commonAudit.forbidden_roles_used === false;
  const technicalSourceRun = commonAudit.inputs?.technical_source_run?.replaceAll("\\", "/");
  const technicalSourceFiles = {
    technical_v1_audit: `${technicalSourceRun}/technical_v1_audit.json`,
    technical_v1_results: `${technicalSourceRun}/technical_v1_results.json`,
    technical_v1_signals: `${technicalSourceRun}/technical_v1_signals.csv`,
  };
  const technicalSourceHashes = {};
  for (const [key, relative] of Object.entries(technicalSourceFiles)) {
    const expected = commonAudit.inputs?.technical_source_hashes?.[relative.split("/").at(-1)];
    if (!expected) throw unavailable(`Common Coverage 缺少 Technical source hash：${relative}`);
    technicalSourceHashes[relative] = assertHash(root, relative, expected);
  }
  const technicalAudit = readJson(root, technicalSourceFiles.technical_v1_audit);
  const validTechnicalSource = technicalAudit.run_id === "20261013T024919303843"
    && technicalAudit.experiment_id === TECHNICAL_SOURCE_EXPERIMENT_ID
    && technicalAudit.requested_role === "training"
    && technicalAudit.market_data_preflight?.query_period?.start === "2023-01-01"
    && technicalAudit.market_data_preflight?.query_period?.end === "2024-12-31";
  const validRisk = riskAudit.status === "COMPLETED"
    && riskAudit.experiment_id === "hybrid-risk-v1-20261018T122032419364"
    && riskAudit.data_role === "training"
    && JSON.stringify(riskAudit.period) === JSON.stringify(["2023-01-01", "2024-12-31"])
    && JSON.stringify(riskAudit.stock_universe) === JSON.stringify(["2303", "2330", "2379", "2454", "3034"])
    && riskAudit.common_candidate_count === 192
    && riskAudit.baseline_reproduction === "PASS"
    && riskAudit.gate_parity_by_model?.random_forest === true
    && riskAudit.trade_timing_parity?.baseline_pairs_without_risk_trade === 0
    && riskAudit.risk_per_trade === 0.01
    && riskAudit.max_position_fraction === 0.4
    && riskAudit.existing_stop_loss_pct === 0.025
    && riskAudit.final_oos_used === false && riskAudit.controlled_validation_used === false
    && riskAudit.development_used === false && riskAudit.additional_holdout_used === false
    && riskAudit.forbidden_roles_used === false
    && riskAudit.optimization_performed === false && riskAudit.parameter_search_performed === false
    && riskAudit.common_coverage_source?.replaceAll("\\", "/") === COMMON_DIR;
  const validConfig = riskConfig.version === "risk_v1"
    && riskConfig.data_role === "training"
    && riskConfig.risk_per_trade === 0.01
    && riskConfig.max_position_fraction === 0.4
    && riskConfig.stop_loss_pct === 0.025
    && riskConfig.technical_candidate_id === BASE_CANDIDATE
    && riskConfig.common_coverage_source === COMMON_DIR;
  if (!validCommon || !validTechnicalSource || !validRisk || !validConfig) {
    const invalidSources = [!validCommon && "common_coverage_audit", !validTechnicalSource && "technical_source_audit", !validRisk && "risk_v1_audit", !validConfig && "risk_v1_config"].filter(Boolean);
    throw unavailable(`Control + RF BUY-only Gate + Risk V1 來源 metadata 驗證失敗：${invalidSources.join(", ")}`);
  }

  const candidates = readCsv(root, `${COMMON_DIR}/common_coverage_candidates.csv`, [
    "technical_version", "candidate_id", "parameter_fingerprint", "stock_code", "data_role", "fold_id", "signal_time",
    "signal_bar_end_time", "earliest_entry_time", "technical_signal", "entry_rule_id", "exit_rule_id",
    "technical_candidate_key", "expected_previous_trading_day", "common_coverage", "close_at_signal",
  ]);
  const commonDecisions = readCsv(root, `${COMMON_DIR}/common_coverage_decisions.csv`, [
    "model", "stock_code", "technical_fold_id", "technical_signal", "technical_signal_time",
    "technical_earliest_entry_time", "ml_fold_id", "ml_signal_date", "ml_entry_date", "ml_prediction",
    "prob_sell", "prob_hold", "prob_buy", "is_oof", "time_order_valid", "alignment_status",
    "hybrid_action", "hybrid_decision", "candidate_id", "technical_candidate_key", "expected_previous_trading_day",
  ]);
  const riskDecisions = readCsv(root, `${RISK_DIR}/risk_v1_decisions.csv`, [
    "variant", "fold_id", "stock_code", "timestamp", "model", "entry_price", "risk_per_trade",
    "stop_loss_pct", "original_quantity", "final_quantity", "decision",
  ]);
  const ledger = readCsv(root, `${RISK_DIR}/risk_v1_trades.csv`, [
    "variant", "fold_id", "stock_code", "datetime", "timestamp", "trade_id", "action", "price",
    "quantity", "gross_amount", "fee", "tax", "net_cash_flow", "cash_before", "cash_after",
  ]);
  const resultRows = readCsv(root, `${RISK_DIR}/risk_v1_results.csv`, [
    "variant", "stock_code", "fold_id", "evaluate_start", "evaluate_end", "candidate_id",
    "parameter_fingerprint", "total_return", "initial_capital", "final_equity", "net_pnl", "gross_pnl",
    "win_rate", "profit_factor", "max_drawdown", "sharpe_ratio", "trade_count", "transaction_cost",
  ]);
  const stockRows = readCsv(root, `${RISK_DIR}/risk_v1_per_stock.csv`, [
    "variant", "stock_code", "evaluation_count", "trade_count", "average_return", "median_return",
    "return_std", "win_rate", "profit_factor", "worst_mdd", "sharpe", "transaction_cost", "net_pnl",
  ]);

  validateDataset({ root, candidates, commonDecisions, riskDecisions, ledger, resultRows, stockRows, riskAudit });
  if (new Set(candidates.map((row) => row.technical_version)).size !== 1
      || candidates[0]?.technical_version !== TECHNICAL_SOURCE_VERSION) {
    throw unavailable("Common Coverage Technical source version 不一致或非預期。");
  }
  return { commonAudit, riskAudit, riskConfig, technicalAudit, technicalSourceFiles, technicalSourceHashes, candidates, commonDecisions, riskDecisions, ledger, resultRows, stockRows, hashes };
}

function validateDataset({ root, candidates, commonDecisions, riskDecisions, ledger, resultRows, stockRows, riskAudit }) {
  if (candidates.length !== 192 || commonDecisions.length !== 576) throw unavailable("Common Coverage 候選或逐模型決策筆數不完整。");
  const candidateByKey = new Map();
  for (const row of candidates) {
    if (row.candidate_id !== BASE_CANDIDATE || row.parameter_fingerprint !== CANDIDATE_FINGERPRINT
        || row.data_role !== "training" || row.technical_signal !== "BUY_CANDIDATE"
        || row.common_coverage.toLowerCase() !== "true" || !FOLDS.includes(row.fold_id)) {
      throw unavailable("Common Coverage Control 候選、期間角色或 Fold 不一致。");
    }
    if (candidateByKey.has(row.technical_candidate_key)) throw unavailable("Common Coverage 候選鍵重複。");
    candidateByKey.set(row.technical_candidate_key, row);
  }
  const decisionsByKey = new Map();
  const oofPath = "exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv";
  const oof = readCsv(root, oofPath, ["model", "stock_code", "signal_date", "entry_date", "fold_id", "prediction", "prob_sell", "prob_hold", "prob_buy", "is_oof", "data_role"]);
  const oofLookup = new Map();
  for (const row of oof) {
    const key = `${row.model}|${row.stock_code}|${row.entry_date}`;
    if (oofLookup.has(key)) throw unavailable("ML OOF 存在重複的模型、股票、Entry 日期鍵。");
    oofLookup.set(key, row);
  }
  for (const row of commonDecisions) {
    const candidate = candidateByKey.get(row.technical_candidate_key);
    if (!candidate || row.candidate_id !== candidate.candidate_id
        || row.stock_code !== candidate.stock_code || row.technical_fold_id !== candidate.fold_id
        || row.technical_signal_time !== candidate.signal_time
        || row.technical_earliest_entry_time !== candidate.earliest_entry_time
        || row.expected_previous_trading_day !== candidate.expected_previous_trading_day
        || !MODELS.includes(row.model) || row.alignment_status !== "MATCHED"
        || row.time_order_valid.toLowerCase() !== "true" || row.is_oof.toLowerCase() !== "true"
        || row.technical_signal !== "BUY_CANDIDATE" || !LABELS.has(row.ml_prediction)) {
      throw unavailable("Common Coverage 決策與 Technical 候選鍵或 OOF 時序資格未能對齊。");
    }
    const mlDate = row.ml_signal_date.slice(0, 10);
    if (mlDate !== candidate.expected_previous_trading_day || row.ml_entry_date !== candidate.signal_time.slice(0, 10)) {
      throw unavailable("ML OOF 並非候選前一交易日訊號，拒絕合併。");
    }
    const prediction = oofLookup.get(`${row.model}|${row.stock_code}|${row.ml_entry_date}`);
    if (!prediction || prediction.signal_date !== mlDate || prediction.fold_id !== row.ml_fold_id
        || prediction.prediction !== row.ml_prediction || prediction.is_oof.toLowerCase() !== "true"
        || prediction.data_role !== "training") {
      throw unavailable("Common Coverage 模型輸出與原始 ML OOF 無法精確對齊。");
    }
    for (const field of ["prob_sell", "prob_hold", "prob_buy"]) {
      if (Math.abs(num(row[field], field) - num(prediction[field], field)) > 1e-10) {
        throw unavailable("Common Coverage 機率與原始 ML OOF 不一致。");
      }
    }
    const expectedAction = row.ml_prediction === "BUY" ? "ENTRY" : `BLOCK_${row.ml_prediction}`;
    if (row.hybrid_action !== expectedAction || row.hybrid_decision !== expectedAction) {
      throw unavailable("Hybrid Gate 決策與 BUY-only Gate 規則不一致。");
    }
    const key = `${row.model}|${row.technical_candidate_key}`;
    if (decisionsByKey.has(key)) throw unavailable("Common Coverage 模型候選決策重複。");
    decisionsByKey.set(key, row);
  }
  for (const key of candidateByKey.keys()) {
    for (const model of MODELS) if (!decisionsByKey.has(`${model}|${key}`)) throw unavailable("共同候選缺少任一模型 Gate 決策。");
  }

  const rfEntryDecisions = new Map();
  for (const row of commonDecisions.filter((item) => item.model === "random_forest" && item.hybrid_action === "ENTRY")) {
    rfEntryDecisions.set(`${row.stock_code}|${row.technical_fold_id}|${row.technical_earliest_entry_time}`, row);
  }
  const riskByEntry = new Map();
  const relevantRisk = riskDecisions.filter((row) => row.variant === RISK_VARIANT);
  for (const row of relevantRisk) {
    const key = `${row.stock_code}|${row.fold_id}|${row.timestamp}`;
    const candidate = rfEntryDecisions.get(key);
    if (!candidate || row.model !== "random_forest"
        || num(row.risk_per_trade, "risk_per_trade") !== 0.01
        || num(row.stop_loss_pct, "stop_loss_pct") !== 0.025
        || Math.abs(num(row.entry_price, "entry_price") - num(candidate.close_at_signal, "close_at_signal")) > 1e-8
        || num(row.final_quantity, "final_quantity") <= 0
        || num(row.final_quantity, "final_quantity") > num(row.original_quantity, "original_quantity")) {
      throw unavailable("Risk V1 sizing 無法精確連結 RF BUY Gate，或部位輸出違反凍結規則。");
    }
    if (riskByEntry.has(key)) throw unavailable("Risk V1 進場 sizing 決策重複。");
    riskByEntry.set(key, row);
  }
  if (riskByEntry.size !== rfEntryDecisions.size || relevantRisk.length !== 43 || riskAudit.risk_reduced_entries !== 120) {
    throw unavailable("RF BUY Gate 與 Risk V1 進場 sizing 筆數不一致。");
  }

  const riskTrades = ledger.filter((row) => row.variant === RISK_VARIANT);
  const tradeGroups = new Map();
  for (const row of riskTrades) {
    const key = `${row.stock_code}|${row.fold_id}`;
    if (!tradeGroups.has(key)) tradeGroups.set(key, []);
    tradeGroups.get(key).push(row);
  }
  const matchedBuys = new Set();
  let pairCount = 0;
  for (const rows of tradeGroups.values()) {
    rows.sort((a, b) => num(a.timestamp, "timestamp") - num(b.timestamp, "timestamp"));
    if (rows.length % 2 !== 0) throw unavailable("Risk V1 交易 ledger 存在無法配對的交易列。");
    for (let i = 0; i < rows.length; i += 2) {
      const buy = rows[i]; const sell = rows[i + 1];
      if (buy.action !== "Buy" || sell.action !== "Sell"
          || num(buy.quantity, "quantity") !== num(sell.quantity, "quantity")
          || num(sell.timestamp, "timestamp") <= num(buy.timestamp, "timestamp")) {
        throw unavailable("Risk V1 交易列買賣次序或數量不匹配。");
      }
      const buyEpoch = num(buy.timestamp, "timestamp");
      const key = [...riskByEntry.keys()].find((entryKey) => {
        const decision = riskByEntry.get(entryKey);
        return entryKey.startsWith(`${buy.stock_code}|${buy.fold_id}|`)
          && Math.abs(timestampEpochSeconds(decision.timestamp) - buyEpoch) < 1;
      });
      const riskDecision = key ? riskByEntry.get(key) : null;
      if (!riskDecision || num(riskDecision.final_quantity, "final_quantity") !== num(buy.quantity, "quantity")
          ) {
        throw unavailable("Risk V1 Buy 交易列與 sizing 決策無法依時間及股數連結。");
      }
      matchedBuys.add(key);
      pairCount += 1;
    }
  }
  if (pairCount !== relevantRisk.length || matchedBuys.size !== riskByEntry.size) throw unavailable("Risk V1 實際交易列與 sizing 決策筆數未對齊。");

  const rfResults = resultRows.filter((row) => row.variant === RISK_VARIANT);
  if (rfResults.length !== 15 || stockRows.filter((row) => row.variant === RISK_VARIANT).length !== 5) {
    throw unavailable("Risk V1 RF 逐股票或逐 Fold 績效列不完整。");
  }
  for (const row of rfResults) {
    if (row.candidate_id !== BASE_CANDIDATE || row.parameter_fingerprint !== CANDIDATE_FINGERPRINT || !FOLDS.includes(row.fold_id)) {
      throw unavailable("Risk V1 績效列與 Control Candidate 或 Fold 不一致。");
    }
  }
}

function serializeCandidate(candidate, gate, riskDecision) {
  return {
    technical: {
      candidate_id: candidate.candidate_id,
      parameter_fingerprint: candidate.parameter_fingerprint,
      strategy_family: candidate.strategy_family,
      stock_code: candidate.stock_code,
      data_role: candidate.data_role,
      fold_id: candidate.fold_id,
      signal_time: candidate.signal_time,
      signal_bar_end_time: candidate.signal_bar_end_time,
      earliest_entry_time: candidate.earliest_entry_time,
      signal: candidate.technical_signal,
      close_at_signal: num(candidate.close_at_signal, "close_at_signal"),
      entry_rule_id: candidate.entry_rule_id,
      exit_rule_id: candidate.exit_rule_id,
      reason: candidate.reason,
      candidate_key: candidate.technical_candidate_key,
      expected_previous_trading_day: candidate.expected_previous_trading_day,
    },
    ml_gate: {
      model: gate.model,
      fold_id: gate.ml_fold_id,
      signal_date: gate.ml_signal_date,
      prediction_date: gate.ml_prediction_date,
      decision_time: gate.ml_decision_time,
      prediction: gate.ml_prediction,
      probabilities: { sell: num(gate.prob_sell, "prob_sell"), hold: num(gate.prob_hold, "prob_hold"), buy: num(gate.prob_buy, "prob_buy") },
      is_oof: gate.is_oof.toLowerCase() === "true",
      time_order_valid: gate.time_order_valid.toLowerCase() === "true",
      alignment_status: gate.alignment_status,
      action: gate.hybrid_action,
      decision_reason: gate.hybrid_decision,
    },
    risk_sizing: riskDecision ? {
      timestamp: riskDecision.timestamp,
      decision: riskDecision.decision,
      current_equity: num(riskDecision.current_equity, "current_equity"),
      sizing_reference_price: num(riskDecision.entry_price, "entry_price"),
      stop_loss_pct: num(riskDecision.stop_loss_pct, "stop_loss_pct"),
      stop_distance_per_share: num(riskDecision.stop_distance_per_share, "stop_distance_per_share"),
      risk_per_trade: num(riskDecision.risk_per_trade, "risk_per_trade"),
      risk_budget: num(riskDecision.risk_budget, "risk_budget"),
      quantity_by_risk: num(riskDecision.quantity_by_risk, "quantity_by_risk"),
      quantity_by_exposure: num(riskDecision.quantity_by_exposure, "quantity_by_exposure"),
      original_quantity: num(riskDecision.original_quantity, "original_quantity"),
      final_quantity: num(riskDecision.final_quantity, "final_quantity"),
      estimated_stop_loss_amount: num(riskDecision.estimated_stop_loss_amount, "estimated_stop_loss_amount"),
      estimated_risk_fraction: num(riskDecision.estimated_risk_fraction, "estimated_risk_fraction"),
      position_fraction: num(riskDecision.position_fraction, "position_fraction"),
    } : null,
  };
}

function foldMetrics(row) {
  const tradeCount = num(row.trade_count, "trade_count");
  return {
    fold_id: row.fold_id,
    evaluation_period: { start: row.evaluate_start, end: row.evaluate_end },
    metrics: {
      return_pct: num(row.total_return, "total_return") * 100,
      initial_capital: num(row.initial_capital, "initial_capital"),
      final_equity: num(row.final_equity, "final_equity"),
      net_pnl: num(row.net_pnl, "net_pnl"),
      gross_pnl: num(row.gross_pnl, "gross_pnl"),
      transaction_cost: num(row.transaction_cost, "transaction_cost"),
      trade_count: tradeCount,
      win_rate_pct: num(row.win_rate, "win_rate") * 100,
      profit_factor: num(row.profit_factor, "profit_factor"),
      max_drawdown_pct: num(row.max_drawdown, "max_drawdown") * 100,
      sharpe: num(row.sharpe_ratio, "sharpe_ratio"),
      technical_entry_count: num(row.technical_entry_count, "technical_entry_count"),
    },
    metric_applicability: {
      win_rate: tradeCount > 0 ? "applicable" : "not_applicable_no_trades_source_zero_retained",
      profit_factor: tradeCount > 0 ? "applicable" : "not_applicable_no_trades_source_zero_retained",
      sharpe: tradeCount > 0 ? "computed_from_source_backtest" : "source_zero_for_no_trade_fold_not_risk_adjusted_evidence",
    },
  };
}

function buildHybridRiskV1StockResearch(stockCode, root = PYTHON_BACKTEST_ROOT) {
  const code = String(stockCode || "").trim();
  if (!/^\d{4,6}$/.test(code)) throw new HybridRiskV1ResearchError("股票代號格式錯誤，請輸入 4～6 位數字。", 400, "INVALID_STOCK_CODE");
  const src = loadAndValidateSources(root);
  const candidateRows = src.candidates.filter((row) => row.stock_code === code);
  if (!candidateRows.length) throw new HybridRiskV1ResearchError(`找不到股票 ${code} 的 Technical Control + RF BUY-only Gate + Risk V1 歷史成果。`, 404, "RESEARCH_RESULT_NOT_FOUND");

  const decisionsByKey = new Map(src.commonDecisions
    .filter((row) => row.model === "random_forest" && row.stock_code === code)
    .map((row) => [row.technical_candidate_key, row]));
  const riskByKey = new Map(src.riskDecisions
    .filter((row) => row.variant === RISK_VARIANT && row.stock_code === code)
    .map((row) => [`${row.stock_code}|${row.fold_id}|${row.timestamp}`, row]));
  const candidates = candidateRows.sort((a, b) => a.signal_time.localeCompare(b.signal_time)).map((candidate) => {
    const gate = decisionsByKey.get(candidate.technical_candidate_key);
    if (!gate) throw unavailable(`股票 ${code} 有 Common Coverage 候選缺少 RF Gate 決策。`);
    const risk = gate.hybrid_action === "ENTRY"
      ? riskByKey.get(`${code}|${candidate.fold_id}|${candidate.earliest_entry_time}`)
      : null;
    if (gate.hybrid_action === "ENTRY" && !risk) throw unavailable(`股票 ${code} 的 RF BUY 候選缺少 Risk V1 sizing 決策。`);
    return serializeCandidate(candidate, gate, risk || null);
  });
  const gateSummary = { candidate_count: candidates.length, entry: 0, block_hold: 0, block_sell: 0 };
  for (const candidate of candidates) {
    if (candidate.ml_gate.action === "ENTRY") gateSummary.entry += 1;
    else if (candidate.ml_gate.action === "BLOCK_HOLD") gateSummary.block_hold += 1;
    else if (candidate.ml_gate.action === "BLOCK_SELL") gateSummary.block_sell += 1;
  }

  const stockRiskDecisions = [...riskByKey.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  const transactionRows = src.ledger.filter((row) => row.variant === RISK_VARIANT && row.stock_code === code)
    .sort((a, b) => num(a.timestamp, "timestamp") - num(b.timestamp, "timestamp"));
  const transactions = transactionRows.map((row) => {
    const sizing = row.action === "Buy"
      ? [...riskByKey.values()].find((decision) => decision.fold_id === row.fold_id
          && Math.abs(timestampEpochSeconds(decision.timestamp) - num(row.timestamp, "timestamp")) < 1)
      : null;
    return {
      variant: row.variant,
      fold_id: row.fold_id,
      stock_code: row.stock_code,
      datetime: row.datetime,
      timestamp: num(row.timestamp, "timestamp"),
      transaction_id: row.trade_id,
      action: row.action,
      price: num(row.price, "price"),
      quantity: num(row.quantity, "quantity"),
      gross_amount: num(row.gross_amount, "gross_amount"),
      fee: num(row.fee, "fee"),
      tax: num(row.tax, "tax"),
      net_cash_flow: num(row.net_cash_flow, "net_cash_flow"),
      cash_before: num(row.cash_before, "cash_before"),
      cash_after: num(row.cash_after, "cash_after"),
      risk_decision_id: sizing ? `${row.stock_code}|${row.fold_id}|${sizing.timestamp}` : null,
    };
  });

  const perStock = src.stockRows.find((row) => row.variant === RISK_VARIANT && row.stock_code === code);
  const perFold = src.resultRows.filter((row) => row.variant === RISK_VARIANT && row.stock_code === code)
    .sort((a, b) => FOLDS.indexOf(a.fold_id) - FOLDS.indexOf(b.fold_id)).map(foldMetrics);
  if (!perStock || perFold.length !== FOLDS.length) throw unavailable(`股票 ${code} 的 Risk V1 績效列不完整。`);
  const meanFoldWinRate = num(perStock.win_rate, "win_rate") * 100;
  const performance = {
    stock_summary: {
      evaluation_count: num(perStock.evaluation_count, "evaluation_count"),
      trade_count: num(perStock.trade_count, "trade_count"),
      average_fold_return_pct: num(perStock.average_return, "average_return") * 100,
      median_fold_return_pct: num(perStock.median_return, "median_return") * 100,
      fold_return_std_pct: num(perStock.return_std, "return_std") * 100,
      mean_fold_win_rate_pct: meanFoldWinRate,
      profit_factor: num(perStock.profit_factor, "profit_factor"),
      worst_fold_max_drawdown_pct: num(perStock.worst_mdd, "worst_mdd") * 100,
      mean_fold_sharpe: num(perStock.sharpe, "sharpe"),
      transaction_cost: num(perStock.transaction_cost, "transaction_cost"),
      gross_pnl: num(perStock.gross_pnl, "gross_pnl"),
      net_pnl: num(perStock.net_pnl, "net_pnl"),
      mean_final_equity: num(perStock.final_equity_mean, "final_equity_mean"),
      average_position_fraction: num(perStock.average_position_fraction, "average_position_fraction"),
      maximum_position_fraction: num(perStock.maximum_position_fraction, "maximum_position_fraction"),
      average_estimated_risk_per_trade: num(perStock.average_estimated_risk_per_trade, "average_estimated_risk_per_trade"),
      maximum_estimated_risk_per_trade: num(perStock.maximum_estimated_risk_per_trade, "maximum_estimated_risk_per_trade"),
      risk_reduced_trades: num(perStock.risk_reduced_trades, "risk_reduced_trades"),
      zero_quantity_blocked_trades: num(perStock.zero_quantity_blocked_trades, "zero_quantity_blocked_trades"),
      cumulative_return_pct: null,
      aggregation: {
        average_fold_return_pct: "arithmetic_mean_of_three_independent_fold_returns; not compounded",
        mean_fold_win_rate_pct: "arithmetic_mean_of_fold_win_rates_including_no_trade_fold source zero",
        profit_factor: "arithmetic_mean_of_existing_fold_values; no-trade Fold source zero retained",
        worst_fold_max_drawdown_pct: "maximum_drawdown_among_independent_folds",
        mean_fold_sharpe: "arithmetic_mean_of_existing_fold_sharpe; no-trade Fold source zero retained",
        net_pnl_and_transaction_cost: "sum_of_independent_fold_simulations",
        cumulative_return_pct: "not computed; independent Fold simulations are not a continuous portfolio",
      },
    },
    per_fold: perFold,
  };

  const commonAudit = src.commonAudit;
  return {
    stock_code: code,
    research_variant: "CONTROL",
    strategy_id: `${BASE_CANDIDATE}+CONTROL+ML_V1_RF_BUY_ONLY+RISK_V1`,
    strategy_version: "Technical Control + ML V1 RF BUY-only Gate + Risk V1",
    model: "random_forest",
    model_version: "ML V1 OOF",
    experiment_id: src.riskAudit.experiment_id,
    common_coverage_experiment_id: commonAudit.experiment_id,
    technical_experiment_id: src.technicalAudit.experiment_id,
    risk_experiment_id: src.riskAudit.experiment_id,
    risk_id: src.riskConfig.version,
    technical_version: src.candidates[0].technical_version,
    technical_source: {
      candidate_id: BASE_CANDIDATE,
      version: src.candidates[0].technical_version,
      experiment_id: src.technicalAudit.experiment_id,
      run_id: src.technicalAudit.run_id,
      common_coverage_applied: true,
      market_regime_filter: { id: "TECH_V2_B_MARKET_REGIME_FILTER", applied: false },
    },
    data_period: { start: "2023-01-01", end: "2024-12-31" },
    data_role: "historical_development",
    source_data_role: "training",
    single_stock: true,
    long_only: true,
    portfolio_enabled: false,
    technical_candidate: { id: BASE_CANDIDATE, version: src.candidates[0].technical_version, parameter_fingerprint: CANDIDATE_FINGERPRINT },
    gate: {
      rule: { BUY: "ALLOW_LONG_ENTRY", HOLD: "BLOCK_HOLD", SELL: "BLOCK_SELL" },
      model: "random_forest",
      common_coverage: {
        technical_candidates_before_common_filter: commonAudit.technical_candidate_count,
        common_candidates: commonAudit.common_candidate_count,
        excluded_candidates: commonAudit.excluded_candidate_count,
        excluded_reasons: commonAudit.excluded_reasons,
        stock_candidates_returned: candidates.length,
        definition: "Only candidates with valid prior-session Training OOF from all three models are included; these are not all raw Technical candidates.",
      },
      decisions: gateSummary,
    },
    risk: {
      version: "Risk V1",
      risk_per_trade: num(src.riskConfig.risk_per_trade, "risk_per_trade"),
      max_position_fraction: num(src.riskConfig.max_position_fraction, "max_position_fraction"),
      stop_loss_pct: num(src.riskConfig.stop_loss_pct, "stop_loss_pct"),
      risk_decision_count: stockRiskDecisions.length,
      decisions: stockRiskDecisions.map((row) => ({
        fold_id: row.fold_id,
        stock_code: row.stock_code,
        timestamp: row.timestamp,
        decision: row.decision,
        current_equity: num(row.current_equity, "current_equity"),
        sizing_reference_price: num(row.entry_price, "entry_price"),
        stop_loss_pct: num(row.stop_loss_pct, "stop_loss_pct"),
        risk_per_trade: num(row.risk_per_trade, "risk_per_trade"),
        max_position_fraction: num(src.riskConfig.max_position_fraction, "max_position_fraction"),
        risk_budget: num(row.risk_budget, "risk_budget"),
        quantity_by_risk: num(row.quantity_by_risk, "quantity_by_risk"),
        quantity_by_exposure: num(row.quantity_by_exposure, "quantity_by_exposure"),
        original_quantity: num(row.original_quantity, "original_quantity"),
        final_quantity: num(row.final_quantity, "final_quantity"),
        estimated_stop_loss_amount: num(row.estimated_stop_loss_amount, "estimated_stop_loss_amount"),
        estimated_risk_fraction: num(row.estimated_risk_fraction, "estimated_risk_fraction"),
        position_fraction: num(row.position_fraction, "position_fraction"),
      })),
    },
    candidate_signals: candidates,
    transactions,
    performance,
    source: {
      files: {
        common_coverage_audit: `${COMMON_DIR}/common_coverage_audit.json`,
        technical_source_run: src.commonAudit.inputs.technical_source_run.replaceAll("\\", "/"),
        technical_source_audit: src.technicalSourceFiles.technical_v1_audit,
        technical_source_results: src.technicalSourceFiles.technical_v1_results,
        technical_source_signals: src.technicalSourceFiles.technical_v1_signals,
        common_candidates: `${COMMON_DIR}/common_coverage_candidates.csv`,
        common_decisions: `${COMMON_DIR}/common_coverage_decisions.csv`,
        risk_config: "hybrid/config/risk_v1.json",
        risk_audit: `${RISK_DIR}/risk_v1_audit.json`,
        risk_decisions: `${RISK_DIR}/risk_v1_decisions.csv`,
        risk_transactions: `${RISK_DIR}/risk_v1_trades.csv`,
        risk_per_stock_fold_results: `${RISK_DIR}/risk_v1_results.csv`,
        risk_per_stock_summary: `${RISK_DIR}/risk_v1_per_stock.csv`,
        ml_oof: "exports/hybrid_poc_v1_20261013T041540804059/ml_v1_baseline_oof_predictions.csv",
      },
      sha256: src.hashes,
      technical_source_sha256: src.technicalSourceHashes,
      source_identity: {
        technical_experiment_id: src.technicalAudit.experiment_id,
        common_coverage_experiment_id: src.commonAudit.experiment_id,
        risk_id: src.riskConfig.version,
        risk_experiment_id: src.riskAudit.experiment_id,
      },
      risk_classification: src.riskAudit.result_classification,
      risk_interpretation: src.riskAudit.interpretation,
      baseline_reproduction: src.riskAudit.baseline_reproduction,
    },
    limitations: [
      "這是 2023–2024 已暴露的 Training 歷史開發研究，不是即時推論、交易建議或 Final OOS。",
      "只回傳 Common Coverage 中的候選；56 個原始 Technical 候選因缺少完整共同 OOF 覆蓋而排除，不能把本清單當成全部 Technical 訊號。",
      "Random Forest OOF 是前一實際交易日的歷史預測；機率與真實歷史資料不可視為當下已知的新訊號。",
      "Risk V1 的摘要報酬是獨立 Fold 算術平均，Net PnL/成本為獨立 Fold 模擬加總；沒有計算連續投資組合累積報酬。",
      "Risk V1 為固定 1% 單筆風險、40% 部位上限，沿用 2.5% 停損；研究分類為 MIXED。",
      "本結果是 Technical Control 基準，使用 Common Coverage + RF OOF BUY-only Gate + Risk V1；沒有套用 TECH_V2_B_MARKET_REGIME_FILTER。",
      "Technical source 是 technical-v1-draft-20261010（Technical V1 run 20261013T024919303843）；後續 V2-B Freeze Manifest 不作為本 Control 執行版本的來源證明。",
    ],
  };
}

function createHybridRiskV1ResearchRouter({ express, authenticate, root = PYTHON_BACKTEST_ROOT }) {
  const router = express.Router();
  router.get("/v2/hybrid/:stockCode", authenticate, (req, res) => {
    try { return res.json(buildHybridRiskV1StockResearch(req.params.stockCode, root)); }
    catch (error) {
      if (error instanceof HybridRiskV1ResearchError) return res.status(error.status).json({ error: { code: error.code, message: error.message } });
      console.error("讀取 Hybrid V2 + Risk V1 歷史成果失敗：", error);
      return res.status(500).json({ error: { code: "HYBRID_RISK_READ_FAILED", message: "讀取 Hybrid V2 + Risk V1 歷史成果失敗。" } });
    }
  });
  return router;
}

module.exports = { HybridRiskV1ResearchError, buildHybridRiskV1StockResearch, createHybridRiskV1ResearchRouter };

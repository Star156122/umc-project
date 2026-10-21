const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { PYTHON_BACKTEST_ROOT } = require("./pythonBacktestService");

const SOURCE_DIRECTORY = path.join("exports", "hybrid_poc_v1_20261013T041540804059");
const OOF_FILE = "ml_v1_baseline_oof_predictions.csv";
const AUDIT_FILE = "hybrid_poc_audit.json";
const EXPECTED_SHA256 = "d70c57acd81445c5e33474ec7a28ff18d52e2a3e162c0737132f3be7ca6f7e4b";
const MODELS = new Set(["random_forest", "xgboost", "gru"]);
const FEATURE_VERSION_BY_MODEL = {
  random_forest: "feature_set_a_v3_24",
  xgboost: "feature_set_a_v3_24",
  gru: "feature_set_a_v3_24+volume_context",
};
const STOCKS = new Set(["2303", "2330", "2379", "2454", "3034"]);
const FOLDS = ["ML_WF1", "ML_WF2", "ML_WF3", "ML_WF4"];
const LABELS = new Set(["SELL", "HOLD", "BUY"]);
const REQUIRED_COLUMNS = [
  "model", "stock_code", "signal_date", "entry_date", "target_date", "fold_id",
  "fold_train_start", "fold_train_end", "fold_validation_start", "fold_validation_end",
  "prediction", "actual_label", "prob_sell", "prob_hold", "prob_buy", "is_oof",
  "data_role", "label_version", "prediction_source", "model_parameter_fingerprint",
  "feature_version", "decision_time",
];

class MlV1OofResearchError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = "MlV1OofResearchError";
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
  const headers = rows[0].map((header) => header.replace(/^\uFEFF/, "").trim());
  return {
    headers,
    records: rows.slice(1).map((values) => Object.fromEntries(
      headers.map((header, index) => [header, values[index] ?? ""])
    )),
  };
}

function invalidSource(message = "ML V1 OOF 研究來源不完整或與已稽核版本不一致。") {
  return new MlV1OofResearchError(message, 503, "ML_OOF_SOURCE_INVALID");
}

function readSource(root) {
  const base = path.join(root, SOURCE_DIRECTORY);
  const csvPath = path.join(base, OOF_FILE);
  const auditPath = path.join(base, AUDIT_FILE);
  let csvText;
  let audit;
  try {
    csvText = fs.readFileSync(csvPath, "utf8");
    audit = JSON.parse(fs.readFileSync(auditPath, "utf8"));
  } catch (_error) {
    throw new MlV1OofResearchError("ML V1 歷史 OOF 研究輸出目前無法讀取。", 503, "ML_OOF_SOURCE_UNAVAILABLE");
  }
  const sha256 = crypto.createHash("sha256").update(csvText, "utf8").digest("hex");
  if (sha256 !== EXPECTED_SHA256 || audit.status !== "COMPLETED_POC_ONLY"
      || audit.oof_artifact_sha256 !== EXPECTED_SHA256
      || audit.oof_artifact?.replace(/\\/g, "/").split("/").pop() !== OOF_FILE
      || audit.oof_validation?.status !== "PASS"
      || audit.oof_validation?.rows !== 4629
      || audit.oof_validation?.in_sample_predictions_used !== false
      || audit.oof_validation?.forbidden_roles_used !== false
      || audit.source_roles?.ml !== "training_walk_forward_oof"
      || audit.controlled_validation_used !== false
      || audit.development_used !== false
      || audit.additional_holdout_used !== false
      || audit.final_oos_used !== false) {
    throw invalidSource();
  }
  const parsed = parseCsv(csvText);
  if (REQUIRED_COLUMNS.some((column) => !parsed.headers.includes(column)) || parsed.records.length !== 4629) {
    throw invalidSource("ML V1 OOF CSV 欄位或資料列數與已稽核來源不符。");
  }
  validateRecords(parsed.records);
  return { records: parsed.records, sha256, audit };
}

function numericProbability(value, label) {
  const number = Number(value);
  if (value === "" || !Number.isFinite(number) || number < 0 || number > 1) {
    throw invalidSource(`ML V1 OOF 的 ${label} 機率值無效，拒絕回傳資料。`);
  }
  return number;
}

function validateRecords(records) {
  const keys = new Set();
  const aligned = new Map();
  for (const row of records) {
    if (!MODELS.has(row.model) || !STOCKS.has(row.stock_code)
        || !/^202[34]-\d{2}-\d{2}$/.test(row.signal_date)
        || !FOLDS.includes(row.fold_id) || row.is_oof.toLowerCase() !== "true"
        || row.data_role !== "training" || !LABELS.has(row.prediction)
        || !LABELS.has(row.actual_label) || row.label_version !== "ml_v1_fixed_1pct"
        || row.prediction_source !== "ml_v1_baseline_fixed_1pct_training_walk_forward_oof_rebuilt"
        || row.feature_version !== FEATURE_VERSION_BY_MODEL[row.model]
        || !row.model_parameter_fingerprint
        || !/^\d{4}-\d{2}-\d{2}T13:30:00\+08:00$/.test(row.decision_time)) {
      throw invalidSource("ML V1 OOF 出現超出既定期間、角色、版本或欄位規格的資料。");
    }
    for (const field of ["signal_date", "entry_date", "target_date", "fold_train_start", "fold_train_end", "fold_validation_start", "fold_validation_end"]) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(row[field])) throw invalidSource(`ML V1 OOF 的 ${field} 日期格式無效。`);
    }
    if (row.decision_time.slice(0, 10) !== row.signal_date
        || row.fold_train_start > row.fold_train_end
        || row.fold_train_end >= row.fold_validation_start
        || row.fold_validation_start > row.signal_date
        || row.signal_date > row.fold_validation_end
        || row.entry_date <= row.signal_date
        || row.target_date <= row.signal_date) {
      throw invalidSource("ML V1 OOF 的決策時間、Fold 時間邊界或未來目標日期不合法。");
    }
    const probabilities = [
      numericProbability(row.prob_sell, "SELL"),
      numericProbability(row.prob_hold, "HOLD"),
      numericProbability(row.prob_buy, "BUY"),
    ];
    if (Math.abs(probabilities.reduce((sum, value) => sum + value, 0) - 1) > 1e-5) {
      throw invalidSource("ML V1 OOF 的類別機率總和不等於 1。");
    }
    const key = `${row.model}|${row.stock_code}|${row.signal_date}`;
    if (keys.has(key)) throw invalidSource("ML V1 OOF 存在重複的模型、股票與日期鍵。");
    keys.add(key);
    const groupKey = `${row.stock_code}|${row.signal_date}`;
    const previous = aligned.get(groupKey) || [];
    previous.push(row);
    aligned.set(groupKey, previous);
  }
  for (const group of aligned.values()) {
    if (group.length !== 3 || new Set(group.map((row) => row.model)).size !== 3
        || new Set(group.map((row) => row.fold_id)).size !== 1
        || new Set(group.map((row) => row.target_date)).size !== 1
        || new Set(group.map((row) => row.entry_date)).size !== 1
        || new Set(group.map((row) => row.actual_label)).size !== 1) {
      throw invalidSource("三模型 OOF 的股票、日期、Fold、目標日期或真實 Label 未能一對一對齊。");
    }
  }
  for (const model of MODELS) {
    if (records.filter((row) => row.model === model).length !== 1543) {
      throw invalidSource(`ML V1 OOF 的 ${model} 資料列數與 audit 不一致。`);
    }
  }
}

function buildMlV1OofStockResearch(stockCode, model, root = PYTHON_BACKTEST_ROOT) {
  const code = String(stockCode || "").trim();
  const selectedModel = String(model || "").trim().toLowerCase();
  if (!/^\d{4,6}$/.test(code)) {
    throw new MlV1OofResearchError("股票代號格式錯誤，請輸入 4～6 位數字。", 400, "INVALID_STOCK_CODE");
  }
  if (!MODELS.has(selectedModel)) {
    throw new MlV1OofResearchError("請指定 random_forest、xgboost 或 gru 模型。", 400, "INVALID_MODEL");
  }
  const { records, sha256, audit } = readSource(root);
  const rows = records.filter((row) => row.stock_code === code && row.model === selectedModel)
    .sort((a, b) => a.signal_date.localeCompare(b.signal_date));
  if (!rows.length) {
    throw new MlV1OofResearchError(`找不到股票 ${code} 的 ${selectedModel} ML V1 歷史 OOF 預測。`, 404, "ML_OOF_RESULT_NOT_FOUND");
  }
  const sourceFile = path.join(SOURCE_DIRECTORY, OOF_FILE).replace(/\\/g, "/");
  return {
    stock_code: code,
    model: selectedModel,
    model_version: "ML V1",
    data_period: { start: "2023-01-01", end: "2024-12-31" },
    prediction_date_range: { start: rows[0].signal_date, end: rows[rows.length - 1].signal_date },
    data_role: "historical_development",
    source_data_role: "training_walk_forward_oof",
    prediction_count: rows.length,
    folds: [...new Set(rows.map((row) => row.fold_id))].map((foldId) => {
      const foldRows = rows.filter((row) => row.fold_id === foldId);
      const first = foldRows[0];
      return {
        fold_id: foldId,
        training_period: { start: first.fold_train_start, end: first.fold_train_end },
        validation_period: { start: first.fold_validation_start, end: first.fold_validation_end },
        prediction_count: foldRows.length,
      };
    }),
    predictions: rows.map((row) => ({
      signal_date: row.signal_date,
      decision_time: row.decision_time,
      entry_date: row.entry_date,
      target_date: row.target_date,
      fold_id: row.fold_id,
      prediction: row.prediction,
      probabilities: {
        sell: Number(row.prob_sell),
        hold: Number(row.prob_hold),
        buy: Number(row.prob_buy),
      },
      actual_label: row.actual_label,
      is_oof: true,
      source_data_role: row.data_role,
      label_version: row.label_version,
      feature_version: row.feature_version,
      prediction_source: row.prediction_source,
      model_parameter_fingerprint: row.model_parameter_fingerprint,
    })),
    source: {
      experiment_id: "hybrid-poc-v1-20261013T041540804059",
      artifact: sourceFile,
      artifact_sha256: sha256,
      source_audit: path.join(SOURCE_DIRECTORY, AUDIT_FILE).replace(/\\/g, "/"),
      source_audit_status: audit.status,
      prediction_source: rows[0].prediction_source,
      label_version: rows[0].label_version,
      feature_version: rows[0].feature_version,
    },
    limitations: [
      "這是 2023–2024 Training 期間的 Walk-forward OOF 歷史開發預測，不是即時推論或今日交易訊號。",
      "此資料已用於既有 Hybrid POC 與研究流程，不能視為乾淨、未接觸過的 Final OOS。",
      "actual_label 與 target_date 僅供歷史研究評估；不得解讀為決策當下已知資訊。",
      "此端點逐模型回傳資料；三模型間僅以相同 stock_code、signal_date、fold_id、target_date 進行來源完整性檢查，不合併預測結果。",
    ],
  };
}

function createMlV1OofResearchRouter({ express, authenticate, root = PYTHON_BACKTEST_ROOT }) {
  const router = express.Router();
  router.get("/v1/ml/oof/:stockCode/:model", authenticate, (req, res) => {
    try {
      return res.json(buildMlV1OofStockResearch(req.params.stockCode, req.params.model, root));
    } catch (error) {
      if (error instanceof MlV1OofResearchError) {
        return res.status(error.status).json({ error: { code: error.code, message: error.message } });
      }
      console.error("讀取 ML V1 OOF 歷史成果失敗：", error);
      return res.status(500).json({ error: { code: "ML_OOF_READ_FAILED", message: "讀取 ML V1 OOF 歷史成果失敗。" } });
    }
  });
  return router;
}

module.exports = {
  MlV1OofResearchError,
  buildMlV1OofStockResearch,
  createMlV1OofResearchRouter,
};

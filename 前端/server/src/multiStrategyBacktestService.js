const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const crypto = require("crypto");
const { PYTHON_BACKTEST_ROOT } = require("./pythonBacktestService");

const ALLOWED_STRATEGIES = new Set(["technical", "rf", "xgboost", "gru"]);
const JOB_TTL_MS = 6 * 60 * 60 * 1000;
const MAX_LOG_CHARS = 12_000;

function apiError(status, code, message) {
  return Object.assign(new Error(message), { status, code });
}

function readDevelopmentConfig(root) {
  const configPath = path.join(root, "hybrid", "config", "multi_strategy_development_v1.json");
  let config;
  try { config = JSON.parse(fs.readFileSync(configPath, "utf8")); }
  catch (_error) { throw apiError(503, "DEVELOPMENT_CONFIG_UNAVAILABLE", "歷史開發回測設定目前無法讀取"); }
  if (config.data_role !== "historical_development" || config.data_protection?.final_oos_used !== false) {
    throw apiError(503, "DEVELOPMENT_POLICY_BLOCKED", "目前設定不符合歷史開發資料政策");
  }
  return config;
}

function validateMultiRunRequest(body, config) {
  const code = String(body?.stock_code || "").trim();
  if (!/^\d{4,6}$/.test(code)) throw apiError(400, "INVALID_STOCK_CODE", "stock_code 須為 4～6 位數字");
  const stockCode = code.padStart(4, "0");
  if (!config.stock_universe.map((item) => String(item).padStart(4, "0")).includes(stockCode)) {
    throw apiError(422, "BACKTEST_BLOCKED", "此股票不在目前核准的歷史開發資料範圍");
  }
  if (["start_date", "end_date", "backtest_period", "data_role", "initial_capital"].some((key) => body && Object.hasOwn(body, key))) {
    throw apiError(400, "DEVELOPMENT_DEFAULTS_ONLY", "目前僅允許使用核准的歷史開發日期與資金設定");
  }
  const strategies = body?.strategies;
  if (!Array.isArray(strategies) || strategies.length < 1 || strategies.some((value) => typeof value !== "string") ||
      new Set(strategies).size !== strategies.length || strategies.some((value) => !ALLOWED_STRATEGIES.has(value))) {
    throw apiError(400, "INVALID_STRATEGIES", "strategies 須為 technical、rf、xgboost、gru 的不重複非空陣列");
  }
  return { stock_code: stockCode, strategies: [...strategies] };
}

function parseRunnerJson(stdout) {
  const lines = String(stdout || "").split(/\r?\n/).filter(Boolean);
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    try {
      const value = JSON.parse(lines[index]);
      if (value && typeof value.status === "string") return value;
    } catch (_error) { /* Training libraries may write non-JSON text to stdout. */ }
  }
  return null;
}

function normalizeRunPayload(runId, stockCode, source) {
  const run = JSON.parse(fs.readFileSync(path.join(source, "run.json"), "utf8"));
  const audit = run.audit || {};
  if (audit.data_role !== "historical_development" || String(audit.stock_code).padStart(4, "0") !== stockCode) {
    throw apiError(500, "RESULT_IDENTITY_MISMATCH", "回測結果的股票或資料角色與工作要求不一致");
  }
  if (!Array.isArray(run.results) || !run.buy_and_hold || !run.comparison) {
    throw apiError(500, "RESULT_SCHEMA_INVALID", "Python 回測輸出缺少必要資料");
  }
  const modelKey = { rf: "random_forest", xgboost: "xgboost", gru: "gru" };
  const strategies = run.results.map((item) => ({
    ...item,
    details: {
      ...item.details,
      trades: item.trades || [],
      model_training: modelKey[item.strategy_id] ? audit.model_training?.models?.[modelKey[item.strategy_id]] || null : null,
      strategy_source: item.audit || null,
    },
  }));
  const comparisonRows = [...strategies, run.buy_and_hold].map((item) => ({
    strategy_id: item.strategy_id,
    strategy_version: item.strategy_version,
    summary: item.summary,
  }));
  return {
    api_version: "multi_strategy_backtest_v1",
    run_id: runId,
    stock_code: stockCode,
    data_role: audit.data_role,
    backtest_period: audit.periods?.backtest || run.results[0]?.period || null,
    status: "completed",
    strategies,
    comparison: { ...run.comparison, rows: comparisonRows },
    buy_and_hold: run.buy_and_hold,
    audit: { ...audit, output_directory: source },
  };
}

function createMultiStrategyBacktestService({
  root = PYTHON_BACKTEST_ROOT,
  spawnImpl = spawn,
  pythonCommand,
  pythonArgs = [],
  schedule = setImmediate,
  reportStore = null,
} = {}) {
  const jobs = new Map();
  let activeJobId = null;
  const exportsRoot = path.resolve(root, "exports");

  function pruneJobs() {
    const cutoff = Date.now() - JOB_TTL_MS;
    for (const [id, job] of jobs) {
      if (id !== activeJobId && job.updated_at_ms < cutoff) jobs.delete(id);
    }
  }

  function getJob(id) { return jobs.get(id) || null; }

  async function resolveJob(id,userId){
    const existing=getJob(id);
    if(existing)return existing.ownerUserId===userId?existing:null;
    if(!reportStore)return null;
    const saved=await reportStore.load(id,userId);
    if(saved)jobs.set(id,saved);
    return saved;
  }
  async function persistJob(job){
    if(!reportStore){job.persistence={status:'unavailable'};return;}
    if(job.savePending)return job.savePending;
    job.persistence={status:'saving'};
    job.savePending=(async()=>{try{await reportStore.saveResult(job);job.persistence={status:'saved'};}catch{job.persistence={status:'failed',message:'回測已完成，但歷史報告儲存失敗。請確認 MySQL 後按「重試儲存」，不要關閉伺服器。'};}finally{job.savePending=null;}})();
    await job.savePending;
  }
  async function persistAnalysis(job){
    if(!reportStore){job.analysisPersistence={status:'unavailable'};return;}
    await persistJob(job);
    try{if(job.persistence.status!=='saved')throw Error('result not saved');await reportStore.saveAnalysis(job);job.analysisPersistence={status:'saved'};}catch{job.analysisPersistence={status:'failed',message:'分析已產生，但存入資料庫失敗。可重試保存，不會重新呼叫 Gemini。'};}
  }
  async function listHistory(userId,page){if(!reportStore)return {reports:[],hasMore:false,page};return reportStore.list(userId,page);}
  function submit(body) {
    pruneJobs();
    const config = readDevelopmentConfig(root);
    const request = validateMultiRunRequest(body, config);
    if (activeJobId) throw apiError(409, "BACKTEST_QUEUE_BUSY", "目前已有多策略回測執行中，請稍後再試");
    const id = crypto.randomUUID();
    const job = {
      run_id: id, stock_code: request.stock_code, strategies: request.strategies,
      status: "pending", created_at: new Date().toISOString(), updated_at_ms: Date.now(),
      result: null, error: null,
    };
    jobs.set(id, job);
    activeJobId = id;
    schedule(() => execute(job));
    return job;
  }

  function execute(job) {
    job.status = "running";
    job.updated_at_ms = Date.now();
    const configuredPython = pythonCommand || String(process.env.PYTHON_EXECUTABLE || "").trim();
    const venvPython = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
    const command = configuredPython || (fs.existsSync(venvPython) ? venvPython : process.platform === "win32" ? "python" : "python3");
    const args = [
      ...pythonArgs,
      "hybrid/scripts/run_multi_strategy_backtest_v1.py",
      "--stock-code", job.stock_code,
      "--strategies", ...job.strategies,
    ];
    let child;
    try {
      child = spawnImpl(command, args, {
        cwd: root, windowsHide: true,
        env: { ...process.env, PYTHONIOENCODING: "utf-8", ALLOW_REAL_TRADING: "false", IS_BACKTEST: "true", IS_SIMULATION: "true" },
        shell: false,
      });
    } catch (_error) {
      finishJob(job, "failed", null, { code: "PYTHON_START_FAILED", message: "無法啟動 Python 回測程序" });
      return;
    }
    let stdout = "";
    let stderr = "";
    const append = (old, chunk) => (old + chunk.toString("utf8")).slice(-MAX_LOG_CHARS);
    const timeoutMs = Math.max(60_000, Number(process.env.MULTI_BACKTEST_TIMEOUT_MS || 17_200_000));
    const timer = setTimeout(() => {
      if (job.status !== "running") return;
      try { child.kill("SIGTERM"); } catch (_error) { /* best effort termination */ }
      finishJob(job, "failed", null, { code: "BACKTEST_TIMEOUT", message: "回測執行超過允許時間，已停止；請檢查工作狀態與後端記錄" });
    }, timeoutMs);
    child.stdout?.on("data", (chunk) => { stdout = append(stdout, chunk); });
    child.stderr?.on("data", (chunk) => { stderr = append(stderr, chunk); });
    child.on("error", (_error) => {
      clearTimeout(timer);
      finishJob(job, "failed", null, {
        code: "PYTHON_START_FAILED", message: "無法啟動 Python 回測程序，請確認後端環境已安裝相依套件",
      });
    });
    child.on("close", (exitCode) => {
      clearTimeout(timer);
      if (job.status !== "running") return;
      const runnerResult = parseRunnerJson(stdout);
      if (runnerResult?.status === "BLOCKED") {
        finishJob(job, "blocked", null, { code: "BACKTEST_BLOCKED", message: String(runnerResult.reason || "回測被資料政策阻擋") });
        return;
      }
      if (exitCode !== 0 || runnerResult?.status !== "COMPLETED_DEVELOPMENT_RESEARCH") {
        finishJob(job, "failed", null, {
          code: "BACKTEST_EXECUTION_FAILED",
          message: "Python 回測未成功完成，請檢查後端執行環境與資料來源",
          diagnostic: String(stderr || stdout).slice(-2000),
        });
        return;
      }
      const outputDirectory = path.resolve(String(runnerResult.output_directory || ""));
      const relative = path.relative(exportsRoot, outputDirectory);
      if (!relative || relative.startsWith("..") || path.isAbsolute(relative) || !fs.existsSync(path.join(outputDirectory, "run.json"))) {
        finishJob(job, "failed", null, { code: "INVALID_OUTPUT_PATH", message: "Python 回測輸出位置不符合允許的 exports 範圍" });
        return;
      }
      try {
        const result = normalizeRunPayload(job.run_id, job.stock_code, outputDirectory);
        if (result.strategies.map((item) => item.strategy_id).join(",") !== job.strategies.join(",")) {
          throw apiError(500, "RESULT_STRATEGY_MISMATCH", "回測策略結果與提交的選項不一致");
        }
        finishJob(job, "completed", result, null);
      } catch (error) {
        finishJob(job, "failed", null, { code: error.code || "RESULT_READ_FAILED", message: error.message || "無法讀取回測結果" });
      }
    });
  }

  async function finishJob(job, status, result, error) {
    if(job.status!=="running"&&job.status!=="pending")return;
    job.status = status === "completed" && reportStore ? "running" : status;
    job.result = result;
    job.error = error;
    if(status==="completed"&&reportStore)await persistJob(job);
    job.status=status;
    job.updated_at = new Date().toISOString();
    job.updated_at_ms = Date.now();
    if (activeJobId === job.run_id) activeJobId = null;
  }

  return { submit, getJob, resolveJob, persistJob, persistAnalysis, listHistory };
}

function createMultiStrategyBacktestRouter({ express, authenticate, service }) {
  const router = express.Router();
  const safe=fn=>async(req,res)=>{try{await fn(req,res);}catch{res.status(503).json({message:"無法讀取歷史報告，請確認 MySQL 連線"});}};
  const resolve=(req)=>service.resolveJob?service.resolveJob(req.params.runId,req.user.userId):service.getJob(req.params.runId);
  router.get("/history",authenticate,safe(async(req,res)=>{
    const page=Number(req.query.page||0);if(!Number.isInteger(page)||page<0||page>100000)return res.status(400).json({message:"頁碼無效"});
    res.json(await service.listHistory(req.user.userId,page));
  }));
  router.post("/:runId/save",authenticate,safe(async(req,res)=>{const job=await resolve(req);if(!job||job.ownerUserId!==req.user.userId)return res.status(404).json({message:"找不到此報告"});if(job.status!=="completed")return res.status(409).json({message:"回測尚未完成"});await service.persistJob(job);res.json({persistence:job.persistence});}));
  router.post("/run", authenticate, (req, res) => {
    try {
      const job = service.submit(req.body || {});
      job.ownerUserId=req.user.userId;
      return res.status(202).json({
        run_id: job.run_id, status: job.status,
        status_url: `/api/backtests/multi/${job.run_id}/status`,
        result_url: `/api/backtests/multi/${job.run_id}/result`,
        stock_code: job.stock_code, strategies: job.strategies,
      });
    } catch (error) {
      return res.status(error.status || 500).json({ error: { code: error.code || "BACKTEST_REQUEST_FAILED", message: error.message } });
    }
  });
  router.get("/:runId/status", authenticate, safe(async (req, res) => {
    const job = await resolve(req);
    if (!job || job.ownerUserId!==req.user.userId) return res.status(404).json({ error: { code: "BACKTEST_RUN_NOT_FOUND", message: "找不到此回測工作" } });
    return res.json({ run_id: job.run_id, status: job.status, stock_code: job.stock_code,
      strategies: job.strategies, created_at: job.created_at, updated_at: job.updated_at || job.created_at,
      error: job.error ? { code: job.error.code, message: job.error.message } : null });
  }));
  router.get("/:runId/result", authenticate, safe(async (req, res) => {
    const job = await resolve(req);
    if (!job || job.ownerUserId!==req.user.userId) return res.status(404).json({ error: { code: "BACKTEST_RUN_NOT_FOUND", message: "找不到此回測工作" } });
    if (job.status === "completed") return res.json({...job.result,persistence:job.persistence});
    if (job.status === "blocked" || job.status === "failed") {
      return res.status(409).json({ error: { code: job.error?.code || "BACKTEST_NOT_COMPLETED", message: job.error?.message || "回測尚未完成" }, run_id: job.run_id, status: job.status });
    }
    return res.status(202).json({ run_id: job.run_id, status: job.status, message: "回測仍在執行中，請稍後查詢" });
  }));
  return router;
}

module.exports = {
  ALLOWED_STRATEGIES, createMultiStrategyBacktestRouter, createMultiStrategyBacktestService,
  normalizeRunPayload, parseRunnerJson, validateMultiRunRequest,
};

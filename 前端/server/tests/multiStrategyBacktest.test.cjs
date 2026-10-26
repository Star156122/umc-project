const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { PassThrough } = require("node:stream");
const test = require("node:test");
const express = require("express");
const {
  createMultiStrategyBacktestRouter,
  createMultiStrategyBacktestService,
  validateMultiRunRequest,
} = require("../src/multiStrategyBacktestService");

function fixtureRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "multi-backtest-api-"));
  fs.mkdirSync(path.join(root, "hybrid", "config"), { recursive: true });
  fs.mkdirSync(path.join(root, "exports"), { recursive: true });
  fs.writeFileSync(path.join(root, "hybrid", "config", "multi_strategy_development_v1.json"), JSON.stringify({
    data_role: "historical_development", stock_universe: ["2303", "2330"],
    data_protection: { final_oos_used: false },
  }));
  return root;
}

function invokeRouter(router, method, url, body = {}, authorization = "Bearer test-session") {
  const req = Object.assign(Object.create(express.request), {
    method, url, originalUrl: url, baseUrl: "", body, user:{userId:1},
    headers: authorization ? { authorization } : {},
  });
  const res = Object.assign(Object.create(express.response), {
    statusCode: 200, locals: {},
    status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; },
  });
  const auth = (request, response, next) => request.headers.authorization === "Bearer test-session"
    ? next()
    : response.status(401).json({ error: { code: "UNAUTHORIZED" } });
  return new Promise((resolve, reject) => {
    router.handle(req, res, (error) => error ? reject(error) : resolve(res));
    setImmediate(() => resolve(res));
  });
}

function makeFakeSpawn(root) {
  return (_command, args, options) => {
    assert.equal(options.shell, false);
    assert.equal(options.cwd, root);
    assert.ok(args.includes("--stock-code"));
    const stockCode = args[args.indexOf("--stock-code") + 1];
    const strategies = args.slice(args.indexOf("--strategies") + 1);
    const output = path.join(root, "exports", "fixture_run");
    fs.mkdirSync(output, { recursive: true });
    fs.writeFileSync(path.join(output, "run.json"), JSON.stringify({
      audit: { data_role: "historical_development", stock_code: stockCode,
        periods: { backtest: { start: "2024-07-01", end: "2024-12-30" } } },
      comparison: { time_axis_identical: true },
      buy_and_hold: { strategy_id: "buy_and_hold", summary: {}, details: {}, equity_curve: [] },
      results: strategies.map((strategy_id) => ({ strategy_id, summary: {}, details: {}, equity_curve: [] })),
    }));
    const child = new (require("node:events").EventEmitter)();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    setImmediate(() => {
      child.stdout.write(JSON.stringify({ status: "COMPLETED_DEVELOPMENT_RESEARCH", output_directory: output }) + "\n");
      child.emit("close", 0);
    });
    child.kill = () => true;
    return child;
  };
}

test("validates stock and strategy allowlists and rejects development overrides", () => {
  const config = { stock_universe: ["2330"], data_role: "historical_development", data_protection: { final_oos_used: false } };
  assert.deepEqual(validateMultiRunRequest({ stock_code: "2330", strategies: ["technical", "rf"] }, config), {
    stock_code: "2330", strategies: ["technical", "rf"],
  });
  assert.throws(() => validateMultiRunRequest({ stock_code: "9999", strategies: ["rf"] }, config), (error) => error.status === 422);
  assert.throws(() => validateMultiRunRequest({ stock_code: "2330", strategies: ["buy_and_hold"] }, config), (error) => error.status === 400);
  for (const strategy of ["technical", "rf", "xgboost", "gru"]) {
    assert.equal(validateMultiRunRequest({ stock_code: "2330", strategies: [strategy] }, config).strategies[0], strategy);
  }
  assert.throws(() => validateMultiRunRequest({ stock_code: "2330", strategies: ["rf"], end_date: "2026-07-01" }, config), /核准的歷史開發日期/);
});

test("JWT-protected async router submits, reports status, and returns normalized result", async () => {
  const root = fixtureRoot();
  const service = createMultiStrategyBacktestService({ root, spawnImpl: makeFakeSpawn(root) });
  const router = createMultiStrategyBacktestRouter({ express, service, authenticate: (req, res, next) => {
    if (req.headers.authorization !== "Bearer test-session") return res.status(401).json({});
    return next();
  } });
  const unauth = await invokeRouter(router, "POST", "/run", { stock_code: "2330", strategies: ["rf"] }, "");
  assert.equal(unauth.statusCode, 401);
  const submitted = await invokeRouter(router, "POST", "/run", { stock_code: "2330", strategies: ["technical", "rf"] });
  assert.equal(submitted.statusCode, 202);
  assert.equal(submitted.body.status, "pending");
  await new Promise((resolve) => setTimeout(resolve, 30));
  const state = await invokeRouter(router, "GET", `/${submitted.body.run_id}/status`);
  assert.equal(state.body.status, "completed");
  const result = await invokeRouter(router, "GET", `/${submitted.body.run_id}/result`);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.data_role, "historical_development");
  assert.deepEqual(result.body.strategies.map((row) => row.strategy_id), ["technical", "rf"]);
  assert.equal(result.body.buy_and_hold.strategy_id, "buy_and_hold");
  fs.rmSync(root, { recursive: true, force: true });
});

test("unknown run id returns 404 and unknown stocks are blocked before process launch", async () => {
  const root = fixtureRoot();
  let spawnCalls = 0;
  const service = createMultiStrategyBacktestService({ root, spawnImpl: (...args) => { spawnCalls += 1; return makeFakeSpawn(root)(...args); } });
  const router = createMultiStrategyBacktestRouter({ express, service, authenticate: (_req, _res, next) => next() });
  const missing = await invokeRouter(router, "GET", "/not-a-run/status");
  assert.equal(missing.statusCode, 404);
  const rejected = await invokeRouter(router, "POST", "/run", { stock_code: "9999", strategies: ["technical"] });
  assert.equal(rejected.statusCode, 422);
  assert.equal(spawnCalls, 0);
  fs.rmSync(root, { recursive: true, force: true });
});

test("Python policy rejection becomes blocked and result endpoint does not claim success", async () => {
  const root = fixtureRoot();
  const spawnImpl = () => {
    const child = new (require("node:events").EventEmitter)();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => true;
    setImmediate(() => {
      child.stdout.write(JSON.stringify({ status: "BLOCKED", reason: "BLOCKED_PERIOD_OUTSIDE_AUTHORIZED_DEVELOPMENT" }) + "\n");
      child.emit("close", 2);
    });
    return child;
  };
  const service = createMultiStrategyBacktestService({ root, spawnImpl });
  const router = createMultiStrategyBacktestRouter({ express, service, authenticate: (_req, _res, next) => next() });
  const submitted = await invokeRouter(router, "POST", "/run", { stock_code: "2330", strategies: ["technical"] });
  await new Promise((resolve) => setTimeout(resolve, 30));
  const state = await invokeRouter(router, "GET", `/${submitted.body.run_id}/status`);
  assert.equal(state.body.status, "blocked");
  assert.equal(state.body.error.code, "BACKTEST_BLOCKED");
  const result = await invokeRouter(router, "GET", `/${submitted.body.run_id}/result`);
  assert.equal(result.statusCode, 409);
  assert.equal(result.body.status, "blocked");
  fs.rmSync(root, { recursive: true, force: true });
});

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const express = require("express");
const {
  buildTechnicalV2StockResearch,
  calculateTradeWeightedWinRate,
  createTechnicalV2ResearchRouter,
} = require("../src/technicalV2ResearchService");

const researchRoot = path.resolve(__dirname, "../../../程式");

async function invokeRouter(url, authorization) {
  const testAuthenticate = (req, res, next) => {
    if (req.headers.authorization !== "Bearer test-session") {
      res.status(401).json({ message: "登入狀態已失效" });
      return;
    }
    return next();
  };
  const router = createTechnicalV2ResearchRouter({
    express,
    authenticate: testAuthenticate,
    root: researchRoot,
  });
  const req = Object.assign(Object.create(express.request), {
    method: "GET",
    url,
    originalUrl: url,
    baseUrl: "",
    headers: authorization ? { authorization } : {},
  });
  const res = Object.assign(Object.create(express.response), {
    statusCode: 200,
    locals: {},
    status(code) { this.statusCode = code; return this; },
    json(body) { this.body = body; return this; },
  });
  await new Promise((resolve, reject) => {
    router.handle(req, res, (error) => error ? reject(error) : resolve());
    setImmediate(resolve);
  });
  return res;
}

test("2330 returns the frozen Technical V2-B historical result and its three real folds", () => {
  const result = buildTechnicalV2StockResearch("2330", researchRoot);
  assert.equal(result.stock_code, "2330");
  assert.equal(result.strategy_version, "Technical V2-B");
  assert.equal(result.data_role, "historical_development");
  assert.deepEqual(result.data_period, { start: "2023-01-01", end: "2024-12-31" });
  assert.equal(result.source.batch_id, "strategy-v2-technical-batch-20261019T094810074002");
  assert.equal(result.source.batch_classification, "SUPPORTED");
  assert.deepEqual(result.folds.map((fold) => fold.fold_id), ["WF1", "WF2", "WF3"]);
  assert.deepEqual(result.folds.map((fold) => fold.metrics.trade_count), [0, 4, 4]);
  assert.equal(result.metrics.win_rate_pct, 41.66666666666667);
  assert.equal(result.metrics.mean_fold_win_rate_pct, 41.66666666666667);
  assert.equal(result.metrics.trade_weighted_win_rate_pct, 62.5);
  assert.equal(result.metrics.trade_weighted_win_rate_status, "available");
  assert.ok(Math.abs(result.metrics.average_return_pct - 1.4274710666666668) < 1e-9);
  assert.equal(result.metrics.trade_count, 8);
  assert.equal(result.metrics.cumulative_return_pct, null);
  assert.match(result.metrics.cumulative_return_status, /independent_folds/);
  assert.equal(result.folds[0].metrics.win_rate_pct, 0);
  assert.equal(result.folds[0].metric_applicability.win_rate, "not_applicable_no_trades");
  assert.equal(result.folds[0].metric_applicability.profit_factor, "not_applicable_no_trades");
  assert.match(result.folds[0].metric_applicability.sharpe, /not evidence of risk-adjusted performance/);
  assert.match(result.metrics.profit_factor_applicability, /zero-trade Fold source value is retained/);
  assert.equal(result.metrics.aggregation.fold_returns_compounded, false);
  assert.match(result.limitations.join(" "), /不是未來或即時預測/);
});

test("trade-weighted win rate uses completed-trade weighting only", () => {
  const folds = [
    { fold_id: "WF1", metrics: { trade_count: 0, win_rate_pct: 0 } },
    { fold_id: "WF2", metrics: { trade_count: 4, win_rate_pct: 75 } },
    { fold_id: "WF3", metrics: { trade_count: 4, win_rate_pct: 50 } },
  ];
  assert.deepEqual(calculateTradeWeightedWinRate(folds), { value: 62.5, reason: null });
});

test("unreliable or absent trade data yields null weighted win rate with reason", () => {
  assert.equal(calculateTradeWeightedWinRate([
    { fold_id: "WF1", metrics: { trade_count: 3, win_rate_pct: 67 } },
  ]).value, null);
  assert.match(calculateTradeWeightedWinRate([
    { fold_id: "WF1", metrics: { trade_count: 3, win_rate_pct: 67 } },
  ]).reason, /整數獲利筆數/);
  const noTrades = calculateTradeWeightedWinRate([
    { fold_id: "WF1", metrics: { trade_count: 0, win_rate_pct: 0 } },
  ]);
  assert.equal(noTrades.value, null);
  assert.match(noTrades.reason, /均無已完成交易/);
});

test("unknown stock returns an explicit not-found response without fallback", () => {
  assert.throws(
    () => buildTechnicalV2StockResearch("9999", researchRoot),
    (error) => error.status === 404 && error.code === "RESEARCH_RESULT_NOT_FOUND"
  );
});

test("rejects invalid stock codes and never uses them as filesystem paths", () => {
  assert.throws(
    () => buildTechnicalV2StockResearch("../2330", researchRoot),
    (error) => error.status === 400 && error.code === "INVALID_STOCK_CODE"
  );
});

test("Express route requires authentication and serves the verified 2330 record", async () => {
  const unauthorized = await invokeRouter("/v2/technical/2330");
  assert.equal(unauthorized.statusCode, 401);

  const response = await invokeRouter("/v2/technical/2330", "Bearer test-session");
  assert.equal(response.statusCode, 200);
  assert.equal(response.body.stock_code, "2330");
  assert.equal(response.body.strategy_version, "Technical V2-B");
  assert.equal(response.body.data_role, "historical_development");
});

test("Express route returns 404 for a stock with no V2-B output", async () => {
  const response = await invokeRouter("/v2/technical/9999", "Bearer test-session");
  assert.equal(response.statusCode, 404);
  assert.equal(response.body.error.code, "RESEARCH_RESULT_NOT_FOUND");
});

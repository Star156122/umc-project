const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const express = require("express");
const {
  buildHybridRiskV1StockResearch,
  createHybridRiskV1ResearchRouter,
} = require("../src/hybridRiskV1ResearchService");
const { buildTechnicalV2StockResearch } = require("../src/technicalV2ResearchService");

const researchRoot = path.resolve(__dirname, "../../../程式");

async function invokeRouter(url, authorization) {
  const testAuthenticate = (req, res, next) => {
    if (req.headers.authorization !== "Bearer test-session") {
      res.status(401).json({ message: "登入狀態已失效" });
      return;
    }
    return next();
  };
  const router = createHybridRiskV1ResearchRouter({ express, authenticate: testAuthenticate, root: researchRoot });
  const req = Object.assign(Object.create(express.request), {
    method: "GET", url, originalUrl: url, baseUrl: "", headers: authorization ? { authorization } : {},
  });
  const res = Object.assign(Object.create(express.response), {
    statusCode: 200, locals: {},
    status(code) { this.statusCode = code; return this; },
    json(body) { this.body = body; return this; },
  });
  await new Promise((resolve, reject) => {
    router.handle(req, res, (error) => error ? reject(error) : resolve());
    setImmediate(resolve);
  });
  return res;
}

test("2330 returns audited Technical Control + RF BUY-only Gate + Risk V1 historical result", () => {
  const result = buildHybridRiskV1StockResearch("2330", researchRoot);
  assert.equal(result.stock_code, "2330");
  assert.equal(result.research_variant, "CONTROL");
  assert.equal(result.strategy_version, "Technical Control + ML V1 RF BUY-only Gate + Risk V1");
  assert.equal(result.strategy_id, "BR_SENS_RANGE55_BB20_2+CONTROL+ML_V1_RF_BUY_ONLY+RISK_V1");
  assert.equal(result.model_version, "ML V1 OOF");
  assert.equal(result.data_role, "historical_development");
  assert.equal("freeze_id" in result, false);
  assert.equal(result.experiment_id, "hybrid-risk-v1-20261018T122032419364");
  assert.equal(result.common_coverage_experiment_id, "hybrid-common-coverage-20261018T121416162413");
  assert.equal(result.technical_experiment_id, "technical-v1-20261010-v1");
  assert.equal(result.risk_experiment_id, "hybrid-risk-v1-20261018T122032419364");
  assert.equal(result.risk_id, "risk_v1");
  assert.equal(result.technical_version, "technical-v1-draft-20261010");
  assert.deepEqual(result.technical_source, {
    candidate_id: "BR_SENS_RANGE55_BB20_2",
    version: "technical-v1-draft-20261010",
    experiment_id: "technical-v1-20261010-v1",
    run_id: "20261013T024919303843",
    common_coverage_applied: true,
    market_regime_filter: { id: "TECH_V2_B_MARKET_REGIME_FILTER", applied: false },
  });
  assert.equal(result.technical_candidate.version, "technical-v1-draft-20261010");
  assert.equal(result.source.source_identity.risk_id, "risk_v1");
  assert.equal("freeze_manifest" in result.source.files, false);
  assert.equal("freeze_manifest" in result.source.sha256, false);
  assert.deepEqual(result.data_period, { start: "2023-01-01", end: "2024-12-31" });
  assert.deepEqual(result.gate.decisions, { candidate_count: 47, entry: 14, block_hold: 16, block_sell: 17 });
  assert.equal(result.risk.risk_decision_count, 14);
  assert.equal(result.transactions.length, 28);
  assert.equal(result.transactions.filter((row) => row.action === "Buy").length, 14);
  assert.equal(result.transactions.filter((row) => row.action === "Sell").length, 14);
  assert.equal(result.performance.stock_summary.trade_count, 14);
  assert.ok(Math.abs(result.performance.stock_summary.average_fold_return_pct - 1.761104925) < 1e-9);
  assert.equal(result.performance.stock_summary.cumulative_return_pct, null);
  assert.deepEqual(result.performance.per_fold.map((row) => row.metrics.trade_count), [0, 9, 5]);
  assert.match(result.limitations.join(" "), /沒有套用 TECH_V2_B_MARKET_REGIME_FILTER/);
  assert.match(result.limitations.join(" "), /Freeze Manifest 不作為本 Control 執行版本的來源證明/);
  assert.equal(result.performance.per_fold[0].metric_applicability.win_rate, "not_applicable_no_trades_source_zero_retained");
  assert.match(result.limitations.join(" "), /不是即時推論/);
  assert.match(result.limitations.join(" "), /56 個原始 Technical 候選/);
  assert.ok(result.candidate_signals.filter((row) => row.ml_gate.action === "ENTRY")
    .every((row) => row.risk_sizing && row.risk_sizing.final_quantity > 0));
});

test("Technical V2-B API remains a distinct 8-trade experiment", () => {
  const result = buildTechnicalV2StockResearch("2330", researchRoot);
  assert.equal(result.strategy_version, "Technical V2-B");
  assert.equal(result.strategy_id, "BR_SENS_RANGE55_BB20_2+TECH_V2_B_MARKET_REGIME_FILTER");
  assert.equal(result.metrics.trade_count, 8);
  assert.ok(Math.abs(result.metrics.average_return_pct - 1.4274710667) < 1e-8);
});

test("unknown stock and malformed stock code fail without fallback or path access", () => {
  assert.throws(() => buildHybridRiskV1StockResearch("9999", researchRoot),
    (error) => error.status === 404 && error.code === "RESEARCH_RESULT_NOT_FOUND");
  assert.throws(() => buildHybridRiskV1StockResearch("../2330", researchRoot),
    (error) => error.status === 400 && error.code === "INVALID_STOCK_CODE");
});

test("Hybrid V2 + Risk V1 route requires authentication and serves audited 2330", async () => {
  const unauthorized = await invokeRouter("/v2/hybrid/2330");
  assert.equal(unauthorized.statusCode, 401);
  const response = await invokeRouter("/v2/hybrid/2330", "Bearer test-session");
  assert.equal(response.statusCode, 200);
  assert.equal(response.body.stock_code, "2330");
  assert.equal(response.body.gate.decisions.entry, 14);
  assert.equal(response.body.risk.risk_decision_count, 14);
  assert.equal(response.body.transactions.length, 28);
});

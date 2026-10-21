const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const express = require("express");
const {
  buildMlV1OofStockResearch,
  createMlV1OofResearchRouter,
} = require("../src/mlV1OofResearchService");

const researchRoot = path.resolve(__dirname, "../../../程式");

async function invokeRouter(url, authorization) {
  const testAuthenticate = (req, res, next) => {
    if (req.headers.authorization !== "Bearer test-session") {
      res.status(401).json({ message: "登入狀態已失效" });
      return;
    }
    return next();
  };
  const router = createMlV1OofResearchRouter({ express, authenticate: testAuthenticate, root: researchRoot });
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

test("2330 RF OOF returns the audited historical predictions with exact source identity", () => {
  const result = buildMlV1OofStockResearch("2330", "random_forest", researchRoot);
  assert.equal(result.stock_code, "2330");
  assert.equal(result.model, "random_forest");
  assert.equal(result.model_version, "ML V1");
  assert.equal(result.data_role, "historical_development");
  assert.equal(result.source_data_role, "training_walk_forward_oof");
  assert.deepEqual(result.prediction_date_range, { start: "2023-09-01", end: "2024-12-26" });
  assert.equal(result.prediction_count, 312);
  assert.deepEqual(result.folds.map((fold) => fold.fold_id), ["ML_WF1", "ML_WF2", "ML_WF3", "ML_WF4"]);
  assert.equal(result.predictions[0].signal_date, "2023-09-01");
  assert.equal(result.predictions[0].decision_time, "2023-09-01T13:30:00+08:00");
  assert.equal(result.predictions[0].prediction, "HOLD");
  assert.equal(result.predictions[0].actual_label, "HOLD");
  assert.ok(Math.abs(result.predictions[0].probabilities.sell - 0.3346458704) < 1e-10);
  assert.equal(result.predictions[0].is_oof, true);
  assert.equal(result.source.artifact_sha256, "d70c57acd81445c5e33474ec7a28ff18d52e2a3e162c0737132f3be7ca6f7e4b");
  assert.match(result.limitations.join(" "), /不是即時推論/);
  assert.match(result.limitations.join(" "), /不能視為乾淨、未接觸過的 Final OOS/);
});

test("all three models have 312 distinct 2330 records and shared date-fold-label keys", () => {
  const results = ["random_forest", "xgboost", "gru"].map((model) =>
    buildMlV1OofStockResearch("2330", model, researchRoot));
  for (const result of results) assert.equal(result.prediction_count, 312);
  assert.equal(results[0].source.feature_version, "feature_set_a_v3_24");
  assert.equal(results[1].source.feature_version, "feature_set_a_v3_24");
  assert.equal(results[2].source.feature_version, "feature_set_a_v3_24+volume_context");
  const keys = results.map((result) => new Set(result.predictions.map((row) =>
    `${row.signal_date}|${row.fold_id}|${row.target_date}|${row.actual_label}`)));
  assert.equal(keys[0].size, 312);
  for (const key of keys[0]) {
    assert.ok(keys[1].has(key));
    assert.ok(keys[2].has(key));
  }
});

test("missing stock result and unsupported model fail explicitly without fallback", () => {
  assert.throws(() => buildMlV1OofStockResearch("9999", "random_forest", researchRoot),
    (error) => error.status === 404 && error.code === "ML_OOF_RESULT_NOT_FOUND");
  assert.throws(() => buildMlV1OofStockResearch("2330", "candidate", researchRoot),
    (error) => error.status === 400 && error.code === "INVALID_MODEL");
});

test("ML OOF route requires authentication and serves one model only", async () => {
  const unauthorized = await invokeRouter("/v1/ml/oof/2330/random_forest");
  assert.equal(unauthorized.statusCode, 401);
  const response = await invokeRouter("/v1/ml/oof/2330/random_forest", "Bearer test-session");
  assert.equal(response.statusCode, 200);
  assert.equal(response.body.model, "random_forest");
  assert.equal(response.body.stock_code, "2330");
  assert.equal(response.body.prediction_count, 312);
  assert.ok(response.body.predictions.every((row) => row.is_oof));
});

test("invalid stock code is rejected before any path resolution", () => {
  assert.throws(() => buildMlV1OofStockResearch("../2330", "random_forest", researchRoot),
    (error) => error.status === 400 && error.code === "INVALID_STOCK_CODE");
});

// 共用 Gemini 呼叫：主要模型忙碌（500/503/504）時同模型重試；
// 逾時、額度用完（429）或仍失敗則依序改用備用模型。
// 可在 程式/.env 以 GEMINI_FALLBACK_MODELS（逗號分隔）覆寫備用模型；設為空字串即停用。
const ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions';
const RETRYABLE = new Set([500, 503, 504]);
const RETRY_DELAYS_MS = [3000, 8000];
const DEFAULT_FALLBACK_MODELS = 'gemini-3.5-flash-lite';
const wait = ms => new Promise(r => setTimeout(r, ms));
const validModel = m => /^gemini-[\w.-]+$/.test(m || '');

function geminiModels(env) {
  const primary = env.GEMINI_MODEL || env.OPENAI_MODEL;
  if (!validModel(primary)) return null;
  const fallbacks = (env.GEMINI_FALLBACK_MODELS ?? DEFAULT_FALLBACK_MODELS)
    .split(',').map(s => s.trim()).filter(m => validModel(m) && m !== primary);
  return [primary, ...new Set(fallbacks)];
}

function geminiStatusMessage(status) {
  if (status === 'timeout') return 'Gemini 連線逾時或網路無法連線（已嘗試備用模型），請稍後重試';
  if (status === 429) return 'Gemini 額度已用完（免費方案每日次數有限，主要與備用模型皆已達上限），請明天再試或改用付費方案';
  if (status === 503) return 'Gemini 目前使用人數過多（Google 端暫時無法服務），已自動重試仍失敗，請過幾分鐘再試';
  if (status === 500 || status === 504) return `Gemini 服務暫時異常（${status}），已自動重試仍失敗，請稍後再試`;
  return `Gemini 暫時無法產生（${status}）`;
}

// 成功回傳 {response, model}；全部失敗回傳 {response:null, error:狀態碼或 'timeout'}。
async function geminiChat({ key, models, messages, maxTokens = 4096, fetchImpl = fetch, sleep = wait,
  retryDelays = RETRY_DELAYS_MS, primaryTimeoutMs = 90000, fallbackTimeoutMs = 120000, totalMs = 130000 }) {
  const deadline = Date.now() + totalMs;
  const request = (model, timeoutMs) => fetchImpl(ENDPOINT, {
    method: 'POST', signal: AbortSignal.timeout(timeoutMs),
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
    body: JSON.stringify({ model, max_completion_tokens: maxTokens, messages }),
  });
  let response = null, error = null;
  for (const [i, candidate] of models.entries()) {
    const timeoutMs = i === 0 ? primaryTimeoutMs : fallbackTimeoutMs;
    for (let attempt = 0; ; attempt++) {
      const remaining = deadline - Date.now();
      if (remaining <= 0) { response = null; error = 'timeout'; break; }
      try { response = await request(candidate, Math.min(timeoutMs, remaining)); }
      catch { response = null; error = 'timeout'; break; }
      if (response.ok || !RETRYABLE.has(response.status) || attempt >= retryDelays.length
        || deadline - Date.now() <= retryDelays[attempt]) break;
      await sleep(retryDelays[attempt]);
    }
    if (response?.ok) return { response, model: candidate };
    if (response) {
      error = response.status;
      // 429 代表這個模型的額度用完（免費方案每個模型各自計算），不重試同模型、直接換備用模型。
      if (!RETRYABLE.has(response.status) && response.status !== 429) break;
    }
  }
  return { response: null, error };
}

module.exports = { geminiChat, geminiModels, geminiStatusMessage, validModel, RETRYABLE };

const MARKET_CACHE_MS = Number(
  process.env.YAHOO_MARKET_CACHE_MS || process.env.MARKET_CACHE_MS || 20_000
);
const SYMBOL_CACHE_MS = Number(process.env.YAHOO_SYMBOL_CACHE_MS || 15_000);
const HTTP_TIMEOUT_MS = Number(process.env.YAHOO_HTTP_TIMEOUT_MS || 12_000);
const HOT_LIMIT = Math.max(4, Math.min(12, Number(process.env.YAHOO_HOT_LIMIT || 8)));

const YAHOO_QUERY_HOSTS = [
  "https://query1.finance.yahoo.com",
  "https://query2.finance.yahoo.com",
];

// 這份清單只在 Yahoo 的熱門／篩選端點暫時不可用時，用來決定要向 Yahoo 查哪些台股。
// 價格、漲跌、成交量與走勢仍全部由 Yahoo Finance 回傳，不含任何手動價格。
const CURATED_TAIWAN_SYMBOLS = [
  "2330.TW",
  "2317.TW",
  "2454.TW",
  "2303.TW",
  "2382.TW",
  "3231.TW",
  "2881.TW",
  "2882.TW",
  "0050.TW",
  "0056.TW",
  "00878.TW",
  "00919.TW",
  "2603.TW",
  "2618.TW",
  "3037.TW",
  "2002.TW",
  "1101.TW",
  "5347.TWO",
  "6488.TWO",
];

const INDEX_SYMBOLS = [
  { symbol: "^TWII", code: "TWII", name: "加權指數", market: "index" },
  { symbol: "^TWOII", code: "TWOII", name: "櫃買指數", market: "index" },
];

// 常用台股中文名稱。行情價格、漲跌、成交量與走勢仍全部取自 Yahoo Finance；
// 有中文名稱時顯示中文，找不到中文時保留 Yahoo 回傳的英文名稱。
const CHINESE_NAME_BY_SYMBOL = Object.freeze({
  "0050.TW": "元大台灣50",
  "0056.TW": "元大高股息",
  "00878.TW": "國泰永續高股息",
  "00919.TW": "群益台灣精選高息",
  "1101.TW": "台泥",
  "2002.TW": "中鋼",
  "2303.TW": "聯電",
  "2317.TW": "鴻海",
  "2330.TW": "台積電",
  "2382.TW": "廣達",
  "2454.TW": "聯發科",
  "2603.TW": "長榮",
  "2618.TW": "長榮航",
  "2881.TW": "富邦金",
  "2882.TW": "國泰金",
  "3037.TW": "欣興",
  "3231.TW": "緯創",
  "5347.TWO": "世界",
  "6488.TWO": "環球晶",
});


const cache = {
  hot: { data: null, expiresAt: 0 },
  symbols: new Map(),
  names: new Map(),
};

function cleanText(value) {
  return String(value ?? "").trim();
}

function containsChinese(value) {
  return /[\u3400-\u9fff]/u.test(cleanText(value));
}

function isSymbolLikeName(symbol, value) {
  const normalizedSymbol = normalizeYahooSymbol(symbol);
  const normalizedValue = normalizeYahooSymbol(value);
  return (
    !normalizedValue ||
    normalizedValue === normalizedSymbol ||
    normalizedValue === symbolToCode(normalizedSymbol)
  );
}

function chooseDisplayName(symbol, candidates = []) {
  const normalized = normalizeYahooSymbol(symbol);
  const cleanedCandidates = candidates.map(cleanText).filter(Boolean);

  // 先使用 Yahoo 回傳的中文名稱。
  const yahooChineseName = cleanedCandidates.find(containsChinese);
  if (yahooChineseName) return yahooChineseName;

  // Yahoo 沒有中文名稱時，再使用已知的台股中文名稱對照。
  const knownChineseName = CHINESE_NAME_BY_SYMBOL[normalized];
  if (knownChineseName) return knownChineseName;

  // 確定沒有中文名稱時，保留 Yahoo 的英文名稱，不自行翻譯或編造。
  const englishName = cleanedCandidates.find(
    (candidate) => !containsChinese(candidate) && !isSymbolLikeName(normalized, candidate)
  );
  return englishName || normalized;
}

function toFiniteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function round(value, digits = 2) {
  if (!Number.isFinite(value)) return null;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function isTaiwanYahooSymbol(symbol) {
  return /\.(TW|TWO)$/i.test(cleanText(symbol));
}

function normalizeYahooSymbol(symbol) {
  return cleanText(symbol).toUpperCase();
}

function symbolToCode(symbol) {
  return normalizeYahooSymbol(symbol).replace(/\.(TW|TWO)$/i, "");
}

function symbolToMarket(symbol) {
  const normalized = normalizeYahooSymbol(symbol);
  if (normalized.endsWith(".TWO")) return "otc";
  if (normalized.endsWith(".TW")) return "tse";
  return "index";
}

function encodeQuery(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  return search.toString();
}

async function fetchJson(url, { timeoutMs = HTTP_TIMEOUT_MS } = {}) {
  if (typeof fetch !== "function") {
    throw new Error("目前 Node.js 版本不支援 fetch，請使用 Node.js 18 以上版本");
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        Accept: "application/json,text/plain,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36",
        Referer: "https://finance.yahoo.com/",
      },
    });

    if (!response.ok) {
      const error = new Error(`Yahoo Finance 回應 ${response.status}`);
      error.status = response.status;
      throw error;
    }

    return await response.json();
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("連接 Yahoo Finance 逾時");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function fetchYahoo(pathname, params = {}, hosts = YAHOO_QUERY_HOSTS) {
  const query = encodeQuery({
    lang: "zh-TW",
    region: "TW",
    ...params,
  });
  let lastError = null;

  for (const host of hosts) {
    const url = `${host}${pathname}${query ? `?${query}` : ""}`;
    try {
      return await fetchJson(url);
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError || new Error("無法連接 Yahoo Finance");
}

function extractChartResult(payload, symbol) {
  const error = payload?.chart?.error;
  if (error) {
    throw new Error(error.description || `Yahoo 查無 ${symbol} 行情`);
  }

  const result = payload?.chart?.result?.[0];
  if (!result) {
    throw new Error(`Yahoo 查無 ${symbol} 行情`);
  }

  return result;
}

function lastFinite(values) {
  if (!Array.isArray(values)) return null;
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const number = toFiniteNumber(values[index]);
    if (number !== null) return number;
  }
  return null;
}

function sumFinite(values) {
  if (!Array.isArray(values)) return null;
  let total = 0;
  let found = false;
  for (const value of values) {
    const number = toFiniteNumber(value);
    if (number !== null) {
      total += number;
      found = true;
    }
  }
  return found ? total : null;
}

function minFinite(values) {
  const numbers = (Array.isArray(values) ? values : [])
    .map(toFiniteNumber)
    .filter((value) => value !== null);
  return numbers.length ? Math.min(...numbers) : null;
}

function maxFinite(values) {
  const numbers = (Array.isArray(values) ? values : [])
    .map(toFiniteNumber)
    .filter((value) => value !== null);
  return numbers.length ? Math.max(...numbers) : null;
}

function normalizeChartStock(result, requestedSymbol, overrides = {}) {
  const meta = result?.meta || {};
  const quote = result?.indicators?.quote?.[0] || {};
  const timestamps = Array.isArray(result?.timestamp) ? result.timestamp : [];
  const closes = Array.isArray(quote?.close) ? quote.close : [];
  const volumes = Array.isArray(quote?.volume) ? quote.volume : [];

  const symbol = normalizeYahooSymbol(meta.symbol || requestedSymbol);
  const lastClose = lastFinite(closes);
  const price =
    toFiniteNumber(meta.regularMarketPrice) ??
    toFiniteNumber(meta.postMarketPrice) ??
    lastClose;
  const prevClose =
    toFiniteNumber(meta.chartPreviousClose) ?? toFiniteNumber(meta.previousClose);
  const change =
    price !== null && prevClose !== null ? round(price - prevClose) : null;
  const changePercent =
    change !== null && prevClose
      ? round((change / prevClose) * 100)
      : null;

  const history = [];
  const count = Math.min(timestamps.length, closes.length);
  for (let index = 0; index < count; index += 1) {
    const value = toFiniteNumber(closes[index]);
    const timestamp = toFiniteNumber(timestamps[index]);
    if (value === null || timestamp === null) continue;
    history.push({
      time: new Date(timestamp * 1000).toISOString(),
      value,
    });
  }

  // 非交易時段偶爾只回傳 meta，使用 Yahoo 的昨收與最新價作為兩個真實資料點。
  if (history.length < 2) {
    const actualPoints = [prevClose, price].filter((value) => value !== null);
    for (let index = 0; index < actualPoints.length; index += 1) {
      history.push({ time: String(index), value: actualPoints[index] });
    }
  }

  const regularMarketTime =
    toFiniteNumber(meta.regularMarketTime) ?? lastFinite(timestamps);

  return {
    code: overrides.code || symbolToCode(symbol),
    yahooSymbol: symbol,
    name:
      overrides.name ||
      cleanText(meta.shortName) ||
      cleanText(meta.longName) ||
      symbol,
    market: overrides.market || symbolToMarket(symbol),
    price,
    prevClose,
    change,
    changePercent,
    open: toFiniteNumber(meta.regularMarketOpen) ?? lastFinite(quote.open),
    high: toFiniteNumber(meta.regularMarketDayHigh) ?? maxFinite(quote.high),
    low: toFiniteNumber(meta.regularMarketDayLow) ?? minFinite(quote.low),
    volume: toFiniteNumber(meta.regularMarketVolume) ?? sumFinite(volumes),
    currency: cleanText(meta.currency) || "TWD",
    exchangeName: cleanText(meta.exchangeName),
    marketState: cleanText(meta.marketState),
    updatedAt: regularMarketTime
      ? new Date(regularMarketTime * 1000).toISOString()
      : null,
    history,
    source: "Yahoo Finance",
    available: price !== null,
  };
}

async function fetchYahooSearchRows(query, limit = 12) {
  const payload = await fetchYahoo(
    "/v1/finance/search",
    {
      q: cleanText(query),
      quotesCount: Math.max(limit, 12),
      newsCount: 0,
      enableFuzzyQuery: "true",
      quotesQueryId: "tss_match_phrase_query",
    },
    [...YAHOO_QUERY_HOSTS].reverse()
  );

  return Array.isArray(payload?.quotes) ? payload.quotes : [];
}

async function resolveDisplayStockName(symbol, currentName) {
  const normalized = normalizeYahooSymbol(symbol);

  // 已經有中文名稱或內建中文對照時，不必再送一次 Yahoo 搜尋。
  const immediateChinese = cleanText(currentName);
  if (containsChinese(immediateChinese)) return immediateChinese;
  if (CHINESE_NAME_BY_SYMBOL[normalized]) return CHINESE_NAME_BY_SYMBOL[normalized];

  const cached = cache.names.get(normalized);
  if (cached && cached.expiresAt > Date.now()) return cached.name;

  try {
    const rows = await fetchYahooSearchRows(symbolToCode(normalized), 16);
    const exact = rows.find(
      (item) => normalizeYahooSymbol(item?.symbol) === normalized
    );
    const name = chooseDisplayName(normalized, [
      exact?.shortname,
      exact?.longname,
      exact?.displayName,
      exact?.name,
      currentName,
    ]);
    cache.names.set(normalized, {
      name,
      expiresAt: Date.now() + 24 * 60 * 60 * 1000,
    });
    return name;
  } catch (error) {
    console.warn(`Yahoo ${normalized} 名稱查詢失敗：`, error.message);
  }

  const fallback = chooseDisplayName(normalized, [currentName]);
  cache.names.set(normalized, {
    name: fallback,
    expiresAt: Date.now() + 60 * 60 * 1000,
  });
  return fallback;
}

async function localizeStockNames(stocks) {
  return Promise.all(
    (stocks || []).map(async (stock) => ({
      ...stock,
      originalName: stock?.originalName || stock?.name || "",
      name: await resolveDisplayStockName(stock?.yahooSymbol, stock?.name),
    }))
  );
}

async function getYahooStockBySymbol(symbol, overrides = {}) {
  const normalized = normalizeYahooSymbol(symbol);
  if (!normalized) throw new Error("缺少 Yahoo 股票代號");

  const cached = cache.symbols.get(normalized);
  if (cached && cached.expiresAt > Date.now()) {
    return { ...cached.data, ...overrides, yahooSymbol: normalized };
  }

  const payload = await fetchYahoo(
    `/v8/finance/chart/${encodeURIComponent(normalized)}`,
    {
      range: "1d",
      interval: "5m",
      includePrePost: "false",
      events: "div,splits",
    }
  );
  const result = extractChartResult(payload, normalized);
  const stock = normalizeChartStock(result, normalized, overrides);

  cache.symbols.set(normalized, {
    data: stock,
    expiresAt: Date.now() + SYMBOL_CACHE_MS,
  });

  return stock;
}

async function getYahooStocksBySymbols(symbols, options = {}) {
  const unique = [...new Set((symbols || []).map(normalizeYahooSymbol).filter(Boolean))];
  const includeUnavailable = Boolean(options.includeUnavailable);
  const overridesBySymbol = options.overridesBySymbol || new Map();

  const results = await Promise.all(
    unique.map(async (symbol) => {
      const overrides = overridesBySymbol.get(symbol) || {};
      try {
        return await getYahooStockBySymbol(symbol, overrides);
      } catch (error) {
        console.warn(`Yahoo ${symbol} 行情失敗：`, error.message);
        if (!includeUnavailable) return null;
        return {
          code: overrides.code || symbolToCode(symbol),
          yahooSymbol: symbol,
          name: overrides.name || symbol,
          market: overrides.market || symbolToMarket(symbol),
          price: null,
          prevClose: null,
          change: null,
          changePercent: null,
          open: null,
          high: null,
          low: null,
          volume: null,
          currency: "TWD",
          updatedAt: null,
          history: [],
          source: "Yahoo Finance",
          available: false,
          sourceError: error.message,
        };
      }
    })
  );

  return results.filter(Boolean);
}

function extractSymbolsFromScreener(payload) {
  const financeResults = Array.isArray(payload?.finance?.result)
    ? payload.finance.result
    : [];
  const symbols = [];

  for (const result of financeResults) {
    const rows = Array.isArray(result?.quotes)
      ? result.quotes
      : Array.isArray(result?.documents)
      ? result.documents
      : [];
    for (const row of rows) {
      const symbol = normalizeYahooSymbol(row?.symbol);
      if (isTaiwanYahooSymbol(symbol)) symbols.push(symbol);
    }
  }

  return symbols;
}

async function discoverYahooHotSymbols() {
  const discovered = [];

  try {
    const screener = await fetchYahoo(
      "/v1/finance/screener/predefined/saved",
      {
        scrIds: "most_actives",
        count: 50,
        size: 50,
        start: 0,
        formatted: "false",
      },
      [...YAHOO_QUERY_HOSTS].reverse()
    );
    discovered.push(...extractSymbolsFromScreener(screener));
  } catch (error) {
    console.warn("Yahoo most_actives 暫時不可用：", error.message);
  }

  try {
    const trending = await fetchYahoo("/v1/finance/trending/TW", { count: 30 });
    const rows = trending?.finance?.result?.[0]?.quotes || [];
    for (const row of rows) {
      const symbol = normalizeYahooSymbol(row?.symbol);
      if (isTaiwanYahooSymbol(symbol)) discovered.push(symbol);
    }
  } catch (error) {
    console.warn("Yahoo trending 暫時不可用：", error.message);
  }

  return [...new Set([...discovered, ...CURATED_TAIWAN_SYMBOLS])];
}

async function getMarketIndices() {
  const overridesBySymbol = new Map(
    INDEX_SYMBOLS.map((item) => [item.symbol, item])
  );
  return getYahooStocksBySymbols(
    INDEX_SYMBOLS.map((item) => item.symbol),
    { includeUnavailable: false, overridesBySymbol }
  );
}

async function getHotMarket() {
  if (cache.hot.data && cache.hot.expiresAt > Date.now()) {
    return cache.hot.data;
  }

  const symbols = await discoverYahooHotSymbols();
  const candidates = await getYahooStocksBySymbols(symbols.slice(0, 24));
  const selectedStocks = candidates
    .filter((stock) => isTaiwanYahooSymbol(stock.yahooSymbol))
    .sort((a, b) => (Number(b.volume) || 0) - (Number(a.volume) || 0))
    .slice(0, HOT_LIMIT);
  const stocks = await localizeStockNames(selectedStocks);

  if (stocks.length === 0) {
    throw new Error("Yahoo Finance 目前未回傳可用的台股行情");
  }

  const indices = await getMarketIndices();
  const updatedTimes = [...stocks, ...indices]
    .map((item) => item.updatedAt)
    .filter(Boolean)
    .sort();

  const result = {
    title: "Yahoo 熱門成交",
    stocks,
    indices,
    updatedAt: updatedTimes.at(-1) || new Date().toISOString(),
    source: "Yahoo Finance",
    sourceNote: "Yahoo Finance 行情可能為延遲資料",
  };

  cache.hot = {
    data: result,
    expiresAt: Date.now() + MARKET_CACHE_MS,
  };

  return result;
}

async function searchYahoo(query, limit = 8) {
  const rows = await fetchYahooSearchRows(query, Math.max(limit * 2, 12));

  return rows
    .filter((item) => isTaiwanYahooSymbol(item?.symbol))
    .filter((item) => ["EQUITY", "ETF"].includes(cleanText(item?.quoteType).toUpperCase()))
    .map((item) => {
      const symbol = normalizeYahooSymbol(item.symbol);
      return {
        symbol,
        name: chooseDisplayName(symbol, [
          item.shortname,
          item.longname,
          item.displayName,
          item.name,
        ]),
      };
    });
}

async function resolveNumericCode(code) {
  const symbols = [`${code}.TW`, `${code}.TWO`];
  const results = [];
  for (const symbol of symbols) {
    try {
      results.push(await getYahooStockBySymbol(symbol));
    } catch (_error) {
      // 代號可能只存在上市或上櫃其中一個市場，繼續嘗試另一個。
    }
  }
  return results;
}

async function searchStocks(query, limit = 8) {
  const keyword = cleanText(query);
  if (!keyword) return getHotMarket();

  let stocks = [];
  if (/^\d{4,6}$/.test(keyword)) {
    stocks = await resolveNumericCode(keyword);
  } else if (isTaiwanYahooSymbol(keyword)) {
    try {
      stocks = [await getYahooStockBySymbol(keyword)];
    } catch (_error) {
      stocks = [];
    }
  } else {
    const matches = await searchYahoo(keyword, limit);
    const overridesBySymbol = new Map(
      matches.map((item) => [item.symbol, { name: item.name || undefined }])
    );
    stocks = await getYahooStocksBySymbols(
      matches.slice(0, limit).map((item) => item.symbol),
      { overridesBySymbol }
    );
  }

  const unique = new Map();
  for (const stock of stocks) {
    if (!unique.has(stock.yahooSymbol)) unique.set(stock.yahooSymbol, stock);
  }

  const localizedStocks = await localizeStockNames(
    [...unique.values()].slice(0, limit)
  );

  return {
    title: `Yahoo 搜尋「${keyword}」`,
    stocks: localizedStocks,
    indices: [],
    updatedAt: new Date().toISOString(),
    source: "Yahoo Finance",
    sourceNote: "Yahoo Finance 行情可能為延遲資料",
  };
}

async function resolveStockForFavorite({ code, yahooSymbol }) {
  const suppliedSymbol = normalizeYahooSymbol(yahooSymbol);
  if (isTaiwanYahooSymbol(suppliedSymbol)) {
    const [stock] = await localizeStockNames([
      await getYahooStockBySymbol(suppliedSymbol),
    ]);
    return stock;
  }

  const cleanCode = cleanText(code);
  if (!/^\d{4,6}$/.test(cleanCode)) {
    throw new Error("股票代號格式不正確");
  }

  const matches = await resolveNumericCode(cleanCode);
  if (matches.length === 0) {
    throw new Error(`Yahoo Finance 查無股票 ${cleanCode}`);
  }

  const [stock] = await localizeStockNames([matches[0]]);
  return stock;
}

function databaseRowToYahooSymbol(row) {
  const code = cleanText(row?.stock_id || row?.code);
  const market = cleanText(row?.market).toLowerCase();
  return `${code}.${market === "otc" || market === "two" ? "TWO" : "TW"}`;
}

module.exports = {
  getHotMarket,
  searchStocks,
  getYahooStockBySymbol,
  getYahooStocksBySymbols,
  localizeStockNames,
  resolveStockForFavorite,
  databaseRowToYahooSymbol,
};

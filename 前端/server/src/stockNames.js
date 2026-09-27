const NAMES = Object.freeze({
  "0050": "元大台灣50", "0056": "元大高股息", "00878": "國泰永續高股息",
  "00919": "群益台灣精選高息", "1101": "台泥", "1301": "台塑", "2002": "中鋼",
  "2303": "聯電", "2317": "鴻海", "2330": "台積電", "2382": "廣達", "2412": "中華電",
  "2454": "聯發科", "2603": "長榮", "2618": "長榮航", "2881": "富邦金", "2882": "國泰金",
  "2891": "中信金", "3037": "欣興", "3045": "台灣大", "3231": "緯創", "4904": "遠傳",
  "5347": "世界", "6488": "環球晶",
});
function stockName(code, ...candidates) {
  const key = String(code || "").trim();
  return NAMES[key] || candidates.map(value => String(value || "").trim())
    .find(value => value !== key && /[\u3400-\u9fff]/u.test(value)) || "";
}
module.exports = { stockName };

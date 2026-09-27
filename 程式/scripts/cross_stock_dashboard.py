"""彙整最新的多股票六策略報告，保存基準並輸出跨股票總覽。"""
from __future__ import annotations
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads

import csv
import hashlib
import json
import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading_system.research import aggregate_days, classify_days, equity_days

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
STOCKS_FILE = ROOT / "configs/stocks.json"
BASELINE = ROOT / "data/baselines/cross_stock_fixed_v1.json"
EXPORT_JSON = ROOT / "exports/cross_stock_dashboard_latest.json"
EXPORT_HTML = ROOT / "exports/cross_stock_dashboard_latest.html"
TAIPEI = timezone(timedelta(hours=8))
MARKET_CACHE = ROOT / "data/market_data.sqlite3"
ROUTING_FILE = ROOT / "configs/market_strategy_routing.json"
V2_STOCKS_FILE = ROOT / "configs/stock_universe_v2.json"
V2_EXPERIMENT_FILE = ROOT / "configs/multi_stock_experiment_v2.json"


def latest_comparison(stock_code: str) -> Path:
    candidates = sorted((REPORTS / stock_code).glob("strategy_comparison_*/strategy_comparison.csv"))
    if not candidates:
        raise ValueError(f"{stock_code} 找不到六策略比較 CSV")
    return candidates[-1]


def latest_market_state(labels: dict[str, str]) -> str:
    """取最新可判斷的行情；標籤只使用前一天以前資料，不偷看當日價格。"""
    for state in reversed(list(labels.values())):
        if state != "資料不足":
            return state
    return "資料不足"


def decision(row: dict) -> tuple[str, str]:
    if row["total_return"] <= 0 or row["profit_factor"] <= 1 or row["sharpe_ratio"] <= 0:
        return "暫不適用", "淨報酬、Profit Factor 或 Sharpe 未通過"
    if row["completed_trades"] < 5:
        return "候選觀察", "有獲利但交易樣本少於5筆"
    if row["excess_return"] <= 0:
        return "候選觀察", "風險指標通過，但仍低於同期買進持有"
    return "正式推薦", "報酬、風險、樣本及超額報酬均通過"


def route_strategy(state: str, rows: list[dict], routing: dict) -> dict:
    if state not in routing["market_rules"]:
        return {"action": routing["fallback"], "strategy_key": None, "strategy_label": None,
                "regime_contribution": None, "regime_fills": 0,
                "reason": "目前資料不足，尚無法判斷行情"}
    rule = routing["market_rules"][state]
    ordered_keys = rule["primary"] + rule["secondary"]
    candidates = [row for key in ordered_keys for row in rows if row["strategy_key"] == key]
    gate = routing["candidate_gate"]
    regime_gate = routing["regime_gate"]
    evaluated = []
    for row in candidates:
        regime = next(item for item in row["regimes"] if item["regime"] == state)
        evaluated.append((row, regime))
    passed = [(row, regime) for row, regime in evaluated if
              row["total_return"] > gate["minimum_return"] and
              row["profit_factor"] > gate["minimum_profit_factor"] and
              row["sharpe_ratio"] > gate["minimum_sharpe"] and
              row["completed_trades"] >= gate["minimum_completed_trades"] and
              row["max_drawdown"] <= gate["maximum_drawdown"] and
              regime["contribution"] > regime_gate["minimum_contribution"] and
              regime["fills"] >= regime_gate["minimum_fills"]]
    if not passed:
        return {"action": routing["fallback"], "strategy_key": None, "strategy_label": None,
                "regime_contribution": None, "regime_fills": 0,
                "reason": f"{state}行情的候選策略沒有同時通過分段獲利、交易樣本與整體風險門檻"}
    best, regime = max(passed, key=lambda item: item[1]["contribution"])
    formal = best["excess_return"] > 0
    return {"action": "正式推薦" if formal else "候選觀察",
            "strategy_key": best["strategy_key"], "strategy_label": best["strategy_label"],
            "regime_contribution": regime["contribution"], "regime_fills": regime["fills"],
            "reason": ("通過門檻且超越買進持有" if formal else
                       "相同行情階段有獲利且風險通過，但尚未超越同期買進持有")}


def read_v2_validation() -> list[dict]:
    """讀取已完成的第二輪報告；尚未執行的股票不硬塞空資料。"""
    catalog = {str(row["code"]): row for row in
               guarded_json_loads(V2_STOCKS_FILE.read_text(encoding="utf-8"))["stocks"]}
    experiment = guarded_json_loads(V2_EXPERIMENT_FILE.read_text(encoding="utf-8"))
    results = []
    for code in experiment["stock_codes"]:
        matches = []
        for path in (REPORTS / code).glob("strategy_comparison_*/strategy_comparison.csv"):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            if rows and rows[0]["backtest_start"] == experiment["backtest_start"] and rows[0]["backtest_end"] == experiment["backtest_end"]:
                matches.append((path, rows))
        if not matches:
            continue
        path, rows = sorted(matches, key=lambda item: item[0])[-1]
        strategies = []
        for row in rows:
            item = {"strategy_key": row["strategy"],
                    "strategy_label": row["strategy_label"],
                    "total_return": float(row["total_return"]),
                    "buy_and_hold_return": float(row["buy_and_hold_return"]),
                    "gross_pnl": float(row["gross_pnl"]),
                    "transaction_cost": float(row["transaction_cost"]),
                    "completed_trades": int(row["completed_trades"]),
                    "profit_factor": float(row["profit_factor"]),
                    "max_drawdown": float(row["max_drawdown"]),
                    "sharpe_ratio": float(row["sharpe_ratio"])}
            item["excess_return"] = item["total_return"] - item["buy_and_hold_return"]
            if item["completed_trades"] == 0:
                item["validation_status"] = "沒有訊號"
                item["validation_reason"] = "本期沒有進場，不能算策略獲利"
            elif item["completed_trades"] < 5:
                item["validation_status"] = "樣本不足"
                item["validation_reason"] = "交易少於5筆，只能先觀察"
            elif item["total_return"] > 0 and item["profit_factor"] > 1 and item["sharpe_ratio"] > 0:
                item["validation_status"] = "候選策略" if item["excess_return"] <= 0 else "通過驗證"
                item["validation_reason"] = ("風險與獲利通過，但尚未超越買進持有" if item["excess_return"] <= 0
                                             else "報酬、風險、樣本及超額報酬均通過")
            else:
                item["validation_status"] = "不適合"
                item["validation_reason"] = "報酬、Profit Factor或Sharpe未通過"
            strategies.append(item)
        results.append({"stock_code": code, "stock_name": catalog[code]["name"],
                        "source": str(path.relative_to(ROOT)),
                        "period_start": experiment["backtest_start"], "period_end": experiment["backtest_end"],
                        "strategies": strategies})
    return results


def aggregate_v2(results: list[dict]) -> list[dict]:
    strengths = {
        "ma": "持續趨勢候選；本期在長榮有正報酬",
        "bollinger": "交易成本與回撤最低；可觀察低波動盤整股",
        "breakout": "平均報酬最佳；較適合有明顯動能的股票",
    }
    rows = [row for stock in results for row in stock["strategies"]]
    aggregates = []
    for key in sorted({row["strategy_key"] for row in rows}):
        selected = [row for row in rows if row["strategy_key"] == key]
        aggregates.append({
            "strategy_key": key,
            "strategy_label": selected[0]["strategy_label"],
            "average_return": statistics.fmean(row["total_return"] for row in selected),
            "median_return": statistics.median(row["total_return"] for row in selected),
            "profitable_stocks": sum(row["total_return"] > 0 for row in selected),
            "beat_buy_hold": sum(row["excess_return"] > 0 for row in selected),
            "average_drawdown": statistics.fmean(row["max_drawdown"] for row in selected),
            "average_cost": statistics.fmean(row["transaction_cost"] for row in selected),
            "strength": strengths.get(key, "持續蒐集樣本"),
        })
    return sorted(aggregates, key=lambda item: item["average_return"], reverse=True)


def build_payload() -> dict:
    catalog = guarded_json_loads(STOCKS_FILE.read_text(encoding="utf-8"))["stocks"]
    routing = guarded_json_loads(ROUTING_FILE.read_text(encoding="utf-8"))
    stocks = []
    sources = []
    all_rows = []
    with closing(sqlite3.connect(MARKET_CACHE.resolve().as_uri() + "?mode=ro", uri=True)) as cache:
      cache.row_factory = sqlite3.Row
      for stock in catalog:
        code = str(stock["code"])
        source = latest_comparison(code)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        sources.append({"stock_code": code, "file": str(source.relative_to(ROOT)), "sha256": digest})
        bars = [dict(item) for item in cache.execute(
            "SELECT * FROM market_kbars WHERE stock_code=? AND freq_minutes=5 AND range_start=? "
            "AND range_end=? ORDER BY kbar_timestamp", (code, "2026-01-01", "2026-06-30"))]
        daily_closes = {}
        for bar in bars:
            daily_closes[datetime.fromtimestamp(bar["kbar_timestamp"], TAIPEI).date().isoformat()] = float(bar["close"])
        labels = classify_days(list(daily_closes.items()), 20, 0.05)
        summaries = {}
        for summary_path in (REPORTS / code).glob("*/summary.json"):
            summary = guarded_json_loads(summary_path.read_text(encoding="utf-8"))
            if summary.get("backtest_start") != "2026-01-01" or summary.get("backtest_end") != "2026-06-30":
                continue
            key = summary.get("strategy")
            if key and (key not in summaries or summary.get("generated_at", "") > summaries[key][1].get("generated_at", "")):
                summaries[key] = (summary_path, summary)
        rows = []
        with source.open(encoding="utf-8-sig", newline="") as stream:
          for raw in csv.DictReader(stream):
                assert_payload(raw)
                row = {
                    "stock_code": code,
                    "stock_name": stock["name"],
                    "industry": stock["industry"],
                    "strategy_key": raw["strategy"],
                    "strategy_label": raw["strategy_label"],
                    "total_return": float(raw["total_return"]),
                    "buy_and_hold_return": float(raw["buy_and_hold_return"]),
                    "gross_pnl": float(raw["gross_pnl"]),
                    "transaction_cost": float(raw["transaction_cost"]),
                    "completed_trades": int(raw["completed_trades"]),
                    "win_rate": float(raw["win_rate"]),
                    "profit_factor": float(raw["profit_factor"]),
                    "max_drawdown": float(raw["max_drawdown"]),
                    "sharpe_ratio": float(raw["sharpe_ratio"]),
                }
                row["excess_return"] = row["total_return"] - row["buy_and_hold_return"]
                row["cost_to_gross"] = (row["transaction_cost"] / abs(row["gross_pnl"])
                                        if row["gross_pnl"] else None)
                row["decision"], row["decision_reason"] = decision(row)
                summary_path, _ = summaries[row["strategy_key"]]
                with (summary_path.parent / "trades.csv").open(encoding="utf-8-sig", newline="") as trade_stream:
                    trades = list(csv.DictReader(trade_stream))
                row["regimes"] = aggregate_days(equity_days(bars, trades, 100000, labels), 100000)
                rows.append(row)
                all_rows.append(row)
        best = max(rows, key=lambda item: item["total_return"])
        state = latest_market_state(labels)
        stocks.append({**stock, "market_state": state,
                       "buy_and_hold_return": best["buy_and_hold_return"], "best": best,
                       "routing": route_strategy(state, rows, routing), "strategies": rows})
    aggregates = []
    for key in sorted({row["strategy_key"] for row in all_rows}):
        selected = [row for row in all_rows if row["strategy_key"] == key]
        aggregates.append({
            "strategy_key": key, "strategy_label": selected[0]["strategy_label"],
            "average_return": statistics.fmean(row["total_return"] for row in selected),
            "median_return": statistics.median(row["total_return"] for row in selected),
            "profitable_stocks": sum(row["total_return"] > 0 for row in selected),
            "beat_buy_hold": sum(row["excess_return"] > 0 for row in selected),
            "average_drawdown": statistics.fmean(row["max_drawdown"] for row in selected),
            "average_cost": statistics.fmean(row["transaction_cost"] for row in selected),
        })
    aggregates.sort(key=lambda item: item["median_return"], reverse=True)
    regime_aggregates = []
    for key in sorted({row["strategy_key"] for row in all_rows}):
        selected = [row for row in all_rows if row["strategy_key"] == key]
        for regime in ("上漲", "盤整", "下跌"):
            values = [next(item for item in row["regimes"] if item["regime"] == regime)["contribution"]
                      for row in selected]
            regime_aggregates.append({"strategy_key": key, "strategy_label": selected[0]["strategy_label"],
                                      "regime": regime, "average_contribution": statistics.fmean(values),
                                      "profitable_stocks": sum(value > 0 for value in values)})
    v2_validation = read_v2_validation()
    return {"created_at": datetime.now(TAIPEI).isoformat(),
            "parameter_version": "2303-frozen-20260925-v1",
            "period_start": "2026-01-01", "period_end": "2026-06-30",
            "recommendation_rule": "正報酬、PF>1、Sharpe>0、至少5筆交易，且超越同期買進持有",
            "routing_version": routing["version"],
            "sources": sources, "stocks": stocks, "strategy_aggregates": aggregates,
            "regime_aggregates": regime_aggregates, "v2_validation": v2_validation,
            "v2_aggregates": aggregate_v2(v2_validation)}


def save_baseline(payload: dict) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    if BASELINE.exists():
        existing = guarded_json_loads(BASELINE.read_text(encoding="utf-8"))
        if existing.get("sources") != payload["sources"]:
            raise ValueError("基準版已存在且來源不同；為避免覆蓋研究證據，請建立新的版本名稱。")
        return
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_html(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>跨股票策略總覽</title><style>
body{{font-family:"Microsoft JhengHei",sans-serif;margin:0;background:#f5f7fb;color:#182235}}main{{max-width:1500px;margin:auto;padding:28px}}h1{{margin-bottom:6px}}.note{{background:#fff3cd;border:1px solid #ffe69c;padding:12px;border-radius:8px;margin:16px 0}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:16px 0}}.card{{background:white;border-radius:10px;padding:16px;box-shadow:0 2px 10px #dce2ed}}.big{{font-size:1.45rem;font-weight:700}}table{{width:100%;border-collapse:collapse;background:white;margin:12px 0 24px}}th,td{{border:1px solid #dbe1ea;padding:9px;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}th{{background:#e9eef7;cursor:pointer}}.pos{{color:#08783e;font-weight:700}}.neg{{color:#bd2430;font-weight:700}}.推薦{{background:#d1e7dd}}.觀察{{background:#fff3cd}}.不適用{{background:#f8d7da}}.scroll{{overflow:auto}}small{{color:#5f6b7b}}select{{padding:8px;margin-left:8px}}</style></head><body><main>
<h1>跨股票策略總覽</h1><p><a href="early_followthrough_latest.html">最新研究：早期價格延續確認拆項（24組）</a></p><p><a href="trade_failure_diagnosis_latest.html">MACD與多數決逐筆失敗分析（185筆）</a></p><p><a href="six_strategy_logic_latest.html">六策略各自問題導向改善（84組）</a></p><p><a href="entry_exit_improvement_latest.html">六策略進出場改善與相同暖機期補測（252組）</a></p><p><a href="risk_ablation_latest.html">風控拆項與少量參數比較（2026/09/26）</a></p><p><a href="risk_candidate_latest.html">新風控完整規則對照</a>｜<a href="four_group_latest.html">四組開發實驗</a>｜<a href="routing_simulation_latest.html">行情切換實際模擬</a>｜<a href="regime_validation_latest.html">行情適用性與不同期間檢查</a></p><div id="meta"></div><div class="note">下方候選為歷史回測篩選，不是即時交易訊號。行情日期為各回測期末；最新分段驗證尚無通過組合，維持暫不交易。單期達標不代表通過獨立驗證。</div>
<div class="cards" id="cards"></div><h2>股票 × 策略報酬率</h2><div class="scroll"><table id="matrix"></table></div>
<h2>行情導向策略建議</h2><p>最新行情使用前一天以前20個交易日判斷；策略還必須在相同行情階段有正貢獻及至少2次成交，並通過整體風險門檻。</p><div class="scroll"><table id="routing"></table></div>
<h2>第二輪不同期間驗證</h2><p>候選v2使用2026/07/01～09/24，共完成6檔股票、3種策略。零交易會標成「沒有訊號」，不會當成獲利。</p><div class="scroll"><table id="v2"></table></div>
<h2>第二輪策略優勢</h2><div class="scroll"><table id="v2aggregate"></table></div>
<h2>策略穩定性</h2><div class="scroll"><table id="aggregate"></table></div>
<h2>不同市場階段</h2><p>使用前一天以前20個交易日的漲跌幅分類，超過5%為上漲、低於-5%為下跌，其餘為盤整。</p><div class="scroll"><table id="regimes"></table></div>
<h2>逐筆診斷</h2><label>股票<select id="stock"></select></label><div class="scroll"><table id="detail"></table></div>
<small>行情標籤只使用當天以前已完成的資料：以前20個交易日漲幅超過5%為上漲、低於-5%為下跌，其餘為盤整，避免使用未來價格。</small>
</main><script>const D={data};const pct=v=>(v*100).toFixed(2)+'%';const money=v=>Math.round(v).toLocaleString('zh-TW');const cls=v=>v>=0?'pos':'neg';
document.querySelector('#meta').textContent=`期間：${{D.period_start}} ～ ${{D.period_end}}｜固定參數：${{D.parameter_version}}｜建立：${{D.created_at}}`;
const formal=D.stocks.flatMap(s=>s.strategies).filter(r=>r.decision==='正式推薦').length;document.querySelector('#cards').innerHTML=`<div class="card"><div>股票數</div><div class="big">${{D.stocks.length}}</div></div><div class="card"><div>策略組合</div><div class="big">${{D.stocks.length*6}}</div></div><div class="card"><div>正式推薦</div><div class="big">${{formal}}</div></div><div class="card"><div>判定規則</div><div>${{D.recommendation_rule}}</div></div>`;
const keys=D.stocks[0].strategies.map(r=>[r.strategy_key,r.strategy_label]);document.querySelector('#matrix').innerHTML='<tr><th>股票／行情</th><th>買進持有</th>'+keys.map(k=>`<th>${{k[1]}}</th>`).join('')+'</tr>'+D.stocks.map(s=>`<tr><td>${{s.code}} ${{s.name}}／${{s.market_state}}</td><td class="${{cls(s.buy_and_hold_return)}}">${{pct(s.buy_and_hold_return)}}</td>${{keys.map(k=>{{const r=s.strategies.find(x=>x.strategy_key===k[0]);return `<td class="${{cls(r.total_return)}}" title="${{r.decision}}：${{r.decision_reason}}">${{pct(r.total_return)}}</td>`}}).join('')}}</tr>`).join('');
document.querySelector('#routing').innerHTML='<tr><th>股票</th><th>最新行情</th><th>系統動作</th><th>候選策略</th><th>相同行情報酬貢獻</th><th>成交次數</th><th>原因</th></tr>'+D.stocks.map(s=>`<tr class="${{s.routing.action==='正式推薦'?'推薦':s.routing.action==='候選觀察'?'觀察':'不適用'}}"><td>${{s.code}} ${{s.name}}</td><td>${{s.market_state}}</td><td>${{s.routing.action}}</td><td>${{s.routing.strategy_label||'—'}}</td><td>${{s.routing.regime_contribution===null?'—':pct(s.routing.regime_contribution)}}</td><td>${{s.routing.regime_fills}}</td><td>${{s.routing.reason}}</td></tr>`).join('');
const v2rows=D.v2_validation.flatMap(s=>s.strategies.map(r=>`<tr><td>${{s.stock_code}} ${{s.stock_name}}</td><td>${{r.strategy_label}}</td><td class="${{cls(r.total_return)}}">${{pct(r.total_return)}}</td><td>${{pct(r.buy_and_hold_return)}}</td><td class="${{cls(r.excess_return)}}">${{pct(r.excess_return)}}</td><td>${{r.completed_trades}}</td><td>${{money(r.transaction_cost)}}</td><td>${{r.profit_factor.toFixed(2)}}</td><td>${{r.sharpe_ratio.toFixed(2)}}</td><td>${{pct(r.max_drawdown)}}</td><td title="${{r.validation_reason}}">${{r.validation_status}}</td></tr>`));document.querySelector('#v2').innerHTML='<tr><th>股票</th><th>策略</th><th>報酬</th><th>買進持有</th><th>超額報酬</th><th>交易</th><th>成本</th><th>PF</th><th>Sharpe</th><th>回撤</th><th>判定</th></tr>'+v2rows.join('');
document.querySelector('#v2aggregate').innerHTML='<tr><th>策略</th><th>平均報酬</th><th>中位數</th><th>獲利股票</th><th>勝過買進持有</th><th>平均成本</th><th>平均回撤</th><th>目前優勢</th></tr>'+D.v2_aggregates.map(r=>`<tr><td>${{r.strategy_label}}</td><td class="${{cls(r.average_return)}}">${{pct(r.average_return)}}</td><td class="${{cls(r.median_return)}}">${{pct(r.median_return)}}</td><td>${{r.profitable_stocks}}／${{D.v2_validation.length}}</td><td>${{r.beat_buy_hold}}／${{D.v2_validation.length}}</td><td>${{money(r.average_cost)}}</td><td>${{pct(r.average_drawdown)}}</td><td>${{r.strength}}</td></tr>`).join('');
document.querySelector('#aggregate').innerHTML='<tr><th>策略</th><th>平均報酬</th><th>中位數報酬</th><th>獲利股票</th><th>勝過買進持有</th><th>平均回撤</th><th>平均成本</th></tr>'+D.strategy_aggregates.map(r=>`<tr><td>${{r.strategy_label}}</td><td class="${{cls(r.average_return)}}">${{pct(r.average_return)}}</td><td class="${{cls(r.median_return)}}">${{pct(r.median_return)}}</td><td>${{r.profitable_stocks}}／${{D.stocks.length}}</td><td>${{r.beat_buy_hold}}／${{D.stocks.length}}</td><td>${{pct(r.average_drawdown)}}</td><td>${{money(r.average_cost)}}</td></tr>`).join('');
document.querySelector('#regimes').innerHTML='<tr><th>策略</th><th>市場階段</th><th>平均報酬貢獻</th><th>獲利股票數</th></tr>'+D.regime_aggregates.map(r=>`<tr><td>${{r.strategy_label}}</td><td>${{r.regime}}</td><td class="${{cls(r.average_contribution)}}">${{pct(r.average_contribution)}}</td><td>${{r.profitable_stocks}}／${{D.stocks.length}}</td></tr>`).join('');
const select=document.querySelector('#stock');D.stocks.forEach((s,i)=>select.add(new Option(`${{s.code}} ${{s.name}}`,i)));function render(){{const s=D.stocks[select.value];document.querySelector('#detail').innerHTML='<tr><th>策略</th><th>報酬</th><th>超額報酬</th><th>交易</th><th>成本</th><th>成本／毛損益</th><th>PF</th><th>回撤</th><th>判定</th><th>原因</th></tr>'+s.strategies.map(r=>`<tr class="${{r.decision==='正式推薦'?'推薦':r.decision==='候選觀察'?'觀察':'不適用'}}"><td>${{r.strategy_label}}</td><td>${{pct(r.total_return)}}</td><td>${{pct(r.excess_return)}}</td><td>${{r.completed_trades}}</td><td>${{money(r.transaction_cost)}}</td><td>${{r.cost_to_gross===null?'無法計算':pct(r.cost_to_gross)}}</td><td>${{r.profit_factor.toFixed(2)}}</td><td>${{pct(r.max_drawdown)}}</td><td>${{r.decision}}</td><td>${{r.decision_reason}}</td></tr>`).join('')}}select.onchange=render;render();</script></body></html>'''


def main() -> None:
    payload = build_payload()
    save_baseline(payload)
    EXPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    EXPORT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"已保存固定參數基準：{BASELINE}")
    print(f"已輸出跨股票總覽：{EXPORT_HTML}")


if __name__ == "__main__":
    main()


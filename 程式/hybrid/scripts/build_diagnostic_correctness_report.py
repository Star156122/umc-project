"""Build combined JSON, Markdown, and standalone HTML reports for preregistered runs."""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STOCKS = ("2330", "2303", "2454")
SCENARIOS = (0.0, 5.0)
STRATEGIES = ("technical", "rf", "xgboost", "gru", "buy_and_hold", "exposure_matched_buy_and_hold")


def _path_map(values: list[str], key_count: int) -> dict[tuple, Path]:
    result = {}
    for value in values:
        *keys, raw_path = value.split("=", key_count)
        if len(keys) != key_count:
            raise ValueError(f"Expected {key_count} key(s)=PATH, got: {value}")
        result[tuple(keys)] = Path(raw_path)
    return result


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


def _load(path: Path) -> dict[str, Any]:
    return json.loads((path / "run.json").read_text(encoding="utf-8"))


def build_report(baseline_paths: dict, run_paths: dict, output_dir: Path) -> dict:
    records = []
    input_runs = {}
    preflight_by_stock = {}
    shared_model_audit = None
    shared_gate_diagnostics = None
    for stock in STOCKS:
        baseline = _load(baseline_paths[(stock,)])
        input_runs[f"{stock}:baseline"] = str(baseline_paths[(stock,)].resolve())
        for scenario in SCENARIOS:
            source = _load(run_paths[(stock, str(int(scenario)))])
            preflight_by_stock.setdefault(stock, source.get("audit", {}).get("preflight", {}))
            shared_model_audit = shared_model_audit or source.get("audit", {}).get("model_training")
            shared_gate_diagnostics = shared_gate_diagnostics or source.get("diagnostics", {}).get("ml_gates")
            input_runs[f"{stock}:{int(scenario)}bps"] = str(run_paths[(stock, str(int(scenario)))].resolve())
            current = {str(row["strategy_id"]): row for row in source.get("results", [])}
            current["buy_and_hold"] = source["buy_and_hold"]
            current["exposure_matched_buy_and_hold"] = source["exposure_matched_buy_and_hold"]
            old = {str(row["strategy_id"]): row for row in baseline.get("results", [])}
            old["buy_and_hold"] = baseline["buy_and_hold"]
            old["exposure_matched_buy_and_hold"] = baseline["exposure_matched_buy_and_hold"]
            diagnostics = source.get("diagnostics", {})
            for strategy in STRATEGIES:
                item = current.get(strategy)
                if not item:
                    continue
                summary = item.get("summary", {})
                details = item.get("details", {})
                diagnostic = diagnostics.get("strategies", {}).get(strategy, {})
                decomposed = diagnostic.get("pnl")
                if decomposed is None:
                    costs = float(details.get("total_transaction_cost", 0.0) or 0.0)
                    decomposed = {
                        "gross_pnl": float(summary.get("net_pnl", 0.0) or 0.0) + costs,
                        "commission": details.get("commission_total", 0.0),
                        "tax": details.get("transaction_tax_total", 0.0),
                        "slippage": details.get("slippage_total", 0.0),
                        "total_cost": costs,
                        "net_pnl": summary.get("net_pnl"),
                    }
                old_item = old.get(strategy, {})
                old_summary = old_item.get("summary", {})
                records.append({
                    "stock_code": stock, "scenario_slippage_bps": int(scenario),
                    "strategy_id": strategy, "modified": _finite(summary),
                    "before": _finite(old_summary) if scenario == 0 else None,
                    "details": _finite(details),
                    "pnl_breakdown": _finite(decomposed),
                    "signal_funnel": _finite(diagnostic.get("signal_funnel")),
                    "trade_quality": _finite(diagnostic.get("trade_quality")),
                    "average_holding_bars": _finite(diagnostic.get("average_holding_bars")),
                    "average_holding_minutes": _finite(diagnostic.get("average_holding_minutes")),
                    "average_holding_trading_days": _finite(diagnostic.get("average_holding_trading_days")),
                    "exit_reasons": _finite(diagnostic.get("exit_reasons", {})),
                    "run_directory": str(run_paths[(stock, str(int(scenario)))].resolve()),
                    "baseline_directory": str(baseline_paths[(stock,)].resolve()) if scenario == 0 else None,
                })
    zero_lookup = {
        (row["stock_code"], row["strategy_id"]): row["modified"]
        for row in records if row["scenario_slippage_bps"] == 0
    }
    for row in records:
        if row["scenario_slippage_bps"] == 5:
            zero = zero_lookup.get((row["stock_code"], row["strategy_id"]), {})
            row["zero_bps_modified"] = zero
            zero_return = zero.get("total_return_pct")
            stress_return = row["modified"].get("total_return_pct")
            row["delta_vs_zero_bps_return_pct"] = (
                None if zero_return is None or stress_return is None else stress_return - zero_return
            )
    payload = {
        "schema_version": "diagnostic_correctness_report_v1",
        "experiment_id": "diagnostic-correctness-20261027",
        "data_role": "historical_development",
        "periods": {
            "training": {"start": "2023-01-01", "end": "2023-12-31"},
            "validation_diagnostic_only": {"start": "2024-01-01", "end": "2024-06-30"},
            "historical_development": {"start": "2024-07-01", "end": "2024-12-30"},
        },
        "stocks": list(STOCKS), "slippage_scenarios_bps": list(SCENARIOS),
        "final_oos_used": False, "additional_holdout_used": False,
        "benchmark_runs": input_runs,
        "preflight_by_stock": _finite(preflight_by_stock),
        "model_training_audit": _finite(shared_model_audit or {}),
        "ml_gate_diagnostics": _finite(shared_gate_diagnostics or {}),
        "records": records,
        "method_notes": [
            "This is historically exposed development data, not independent final OOS.",
            "0 bps is the baseline cost scenario; 5 bps is a stress scenario, not an optimized setting.",
            "Stops and MFE/MAE use completed five-minute OHLC; intrabar path ordering is unknown.",
            "At development-period end the engine force-closes any open position at the final close.",
            "The 0 bps before/after comparison also includes use of one shared prepared model-prediction artifact in the revised runs; old runs trained models separately.",
            "Small trade counts are descriptive only and do not establish model superiority.",
        ],
    }
    payload = _finite(payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output_dir / "report.md").write_text(_markdown(payload), encoding="utf-8")
    html_report = _html(payload)
    (output_dir / "index.html").write_text(html_report, encoding="utf-8")
    (output_dir / "backtest_report.html").write_text(html_report, encoding="utf-8")
    return payload


def _pct(value):
    return "—" if value is None else f"{float(value):.2f}%"


def _money(value):
    return "—" if value is None else f"NT$ {float(value):,.0f}"


def _markdown(data):
    lines = ["# 多策略回測正確性診斷", "", "**研究角色：歷史開發期**｜訓練 2023｜診斷驗證 2024 H1｜回測 2024/07/01–12/30｜Final OOS 未使用。", "",
             "0 bps 為基準成本，5 bps 為每邊滑價壓力測試。基準前後數字可能受 XGBoost 多執行緒重訓差異影響；本次所有新策略與情境共用同一份模型預測 artifact。", ""]
    for scenario in (0, 5):
        lines += [f"## 修改後結果：{scenario} bps", "", "| 標的 | 策略 | 淨報酬 | 淨損益 | 毛損益 | 完成交易 | 勝率 | PF | 最大回撤 | Sharpe | Sortino | 費稅滑價總成本 |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for row in data["records"]:
            if row["scenario_slippage_bps"] != scenario:
                continue
            s = row["modified"]
            details = row["details"] or {}
            cost = details.get("total_transaction_cost")
            if cost is None:
                cost = (details.get("commission_total", 0) or 0) + (details.get("transaction_tax_total", 0) or 0) + (details.get("slippage_total", 0) or 0)
            gross = (row.get("pnl_breakdown") or {}).get("gross_pnl")
            lines.append(f"| {row['stock_code']} | {row['strategy_id']} | {_pct(s.get('total_return_pct'))} | {_money(s.get('net_pnl'))} | {_money(gross)} | {s.get('trade_count') if s.get('trade_count') is not None else '—'} | {_pct(s.get('win_rate_pct'))} | {s.get('profit_factor') if s.get('profit_factor') is not None else '—'} | {_pct(s.get('max_drawdown_pct'))} | {s.get('sharpe_ratio') if s.get('sharpe_ratio') is not None else '—'} | {details.get('sortino_ratio') if details.get('sortino_ratio') is not None else '—'} | {_money(cost)} |")
        lines.append("")
    lines += ["## ML Gate 訓練與機率診斷", "", "| 模型 | 驗證準確率 | Macro-F1 | ROC-AUC | 開發候選 BUY/HOLD/SELL | BUY 機率平均 | 門檻 |", "|---|---:|---:|---:|---|---:|---|"]
    model_names = {"random_forest": "RF", "xgboost": "XGBoost", "gru": "GRU"}
    for key, label in model_names.items():
        audit = (data.get("model_training_audit") or {}).get("models", {}).get(key, {})
        gate_key = {"random_forest": "rf", "xgboost": "xgboost", "gru": "gru"}[key]
        gate = (data.get("ml_gate_diagnostics") or {}).get(gate_key, {})
        metrics = audit.get("validation_metrics_diagnostic_only", {})
        counts = gate.get("candidate_predicted_class_counts", {})
        prob = gate.get("buy_probability", {})
        lines.append(f"| {label} | {metrics.get('accuracy') if metrics else '—'} | {metrics.get('macro_f1') if metrics else '—'} | {metrics.get('macro_roc_auc_ovr') if metrics else '—'} | {counts.get('BUY', 0)}/{counts.get('HOLD', 0)}/{counts.get('SELL', 0)} | {prob.get('mean') if prob else '—'} | {gate.get('gate_threshold')} |")
    lines += ["", "## 診斷重點", ""]
    for row in data["records"]:
        if row["scenario_slippage_bps"] != 0 or row["strategy_id"] not in ("rf", "xgboost", "gru", "technical"):
            continue
        funnel = row.get("signal_funnel") or {}
        quality = row.get("trade_quality") or {}
        lines.append(f"- {row['stock_code']} {row['strategy_id']}: 技術候選 {funnel.get('technical_candidates')}；BUY/HOLD/SELL {funnel.get('model_output_buy')}/{funnel.get('model_output_hold')}/{funnel.get('model_output_sell')}；成交 {funnel.get('successful_fills')}；完成 {funnel.get('completed_trades')}；MFE 均值 {((quality.get('mfe_pct') or {}).get('mean'))}; MAE 均值 {((quality.get('mae_pct') or {}).get('mean'))}。阻擋原因：`{json.dumps(funnel.get('block_reasons', {}), ensure_ascii=False)}`")
    lines += ["", "## 解讀限制", "", *[f"- {note}" for note in data["method_notes"]], "", "各原始回測資料夾保留 `run.json`、`diagnostics.json`、CSV 成交/候選/訊號/風險明細；本報告為彙總索引。", ""]
    return "\n".join(lines)


def _html(data):
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    page = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>多策略回測診斷</title><style>
*{box-sizing:border-box}body{margin:0;background:#0b1020;color:#e8edf7;font:15px/1.55 system-ui,'Segoe UI',sans-serif}main{max-width:1400px;margin:auto;padding:28px}h1{margin:0 0 8px;font-size:30px}.sub{color:#9eacc7;margin-bottom:20px}.banner{background:#18233b;border:1px solid #304264;padding:14px 18px;border-radius:12px;margin:16px 0 22px}.controls{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}select{background:#121a2c;color:#e8edf7;padding:10px 14px;border:1px solid #394865;border-radius:8px}.cards{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin:16px 0}.card{background:#121a2c;border:1px solid #263650;border-radius:12px;padding:14px}.card span{display:block;color:#9eacc7;font-size:12px}.card b{font-size:19px}section{margin:25px 0}h2{font-size:20px}table{width:100%;border-collapse:collapse;background:#121a2c;border-radius:12px;overflow:hidden}th,td{text-align:right;padding:10px 9px;border-bottom:1px solid #263650;white-space:nowrap}th{color:#9eacc7;font-weight:600;font-size:12px}th:first-child,td:first-child{text-align:left}.positive{color:#64d7aa}.negative{color:#ff8a8a}.muted{color:#9eacc7}.scroll{overflow:auto}.pill{background:#233251;color:#cfe0ff;padding:3px 8px;border-radius:20px}.note{color:#aeb9cd;font-size:13px}footer{border-top:1px solid #263650;margin-top:32px;padding:20px 0;color:#9eacc7;font-size:13px}@media(max-width:800px){main{padding:16px}.cards{grid-template-columns:repeat(2,1fr)}}
</style><main><h1>多策略回測｜診斷與結果</h1><div class="sub">2330 台積電 · 2303 聯電 · 2454 聯發科　|　Technical V2-B × RF / XGBoost / GRU</div><div class="banner"><b>研究資料角色：歷史開發期</b><br>2023 訓練｜2024 H1 診斷驗證｜2024 H2 歷史開發回測。Final OOS 與 Additional Holdout 未使用。0 bps 為基準；5 bps 為壓力情境。</div><div class="controls"><label>標的　<select id="stock"></select></label><label>滑價情境　<select id="scenario"><option value="0">0 bps（基準）</option><option value="5">5 bps（壓力測試）</option></select></label></div><div class="cards" id="cards"></div><section><h2>策略比較</h2><div class="scroll"><table><thead><tr><th>策略</th><th>淨報酬</th><th>淨損益</th><th>交易次數</th><th>勝率</th><th>Profit Factor</th><th>最大回撤</th><th>Sharpe</th><th>Sortino</th><th>曝險</th><th>總成本</th></tr></thead><tbody id="table"></tbody></table></div></section><section><h2>訊號、Gate 與交易品質</h2><div class="scroll"><table><thead><tr><th>策略</th><th>Technical候選</th><th>BUY</th><th>HOLD</th><th>SELL</th><th>Gate通過</th><th>Risk阻擋</th><th>成交</th><th>完成</th><th>MFE均值</th><th>MAE均值</th><th>出場原因</th></tr></thead><tbody id="funnel"></tbody></table></div></section><section><h2>修改前後與滑價差異</h2><div class="scroll"><table><thead><tr><th>策略</th><th>修改前報酬</th><th>修改後報酬</th><th>差異</th><th>修改前交易</th><th>修改後交易</th><th>成本</th></tr></thead><tbody id="compare"></tbody></table></div><p class="note" id="scenarioNote"></p></section><section><h2>模型與研究解讀</h2><div id="notes" class="note"></div></section><footer>報表由本機歷史行情回測產生。OHLC停損與 MFE/MAE 是 5 分鐘 K 棒近似；同一棒內先後順序無法還原。期末持倉按最後收盤強制平倉。各策略交易少時，指標只作描述，不代表模型優越。</footer></main><script id="payload" type="application/json">""" + serialized + """</script><script>
const D=JSON.parse(document.getElementById('payload').textContent),$=id=>document.getElementById(id);const stocks=[...new Set(D.records.map(x=>x.stock_code))];stocks.forEach(s=>{let o=document.createElement('option');o.value=s;o.textContent=s;$('stock').append(o)});const names={technical:'Technical V2-B',rf:'V2-B + Random Forest',xgboost:'V2-B + XGBoost',gru:'V2-B + GRU',buy_and_hold:'Buy & Hold',exposure_matched_buy_and_hold:'曝險匹配 Buy & Hold'};function n(v,d=2){return v==null?'—':Number(v).toFixed(d)}function pct(v){return v==null?'—':n(v)+'%'}function money(v){return v==null?'—':'NT$ '+Math.round(v).toLocaleString('zh-TW')}function signed(v){return v==null?'—':(v>0?'+':'')+pct(v)}function cls(v){return v==null?'':v>=0?'positive':'negative'}function render(){const stock=$('stock').value,sc=+$('scenario').value,rows=D.records.filter(x=>x.stock_code===stock&&x.scenario_slippage_bps===sc),base=D.records.filter(x=>x.stock_code===stock&&x.scenario_slippage_bps===0);let act=rows.find(x=>x.strategy_id==='technical')||{};$('cards').innerHTML=[['標的',stock],['研究期間','2024 H2'],['滑價情境',sc+' bps'],['主動策略數','4']].map(x=>`<div class="card"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');$('table').innerHTML=rows.map(x=>{let s=x.modified,d=x.details||{},f=d.exposure||{},cost=d.total_transaction_cost??((d.commission_total||0)+(d.transaction_tax_total||0)+(d.slippage_total||0));return `<tr><td>${names[x.strategy_id]||x.strategy_id}</td><td class="${cls(s.total_return_pct)}">${pct(s.total_return_pct)}</td><td>${money(s.net_pnl)}</td><td>${s.trade_count??'—'}</td><td>${pct(s.win_rate_pct)}</td><td>${n(s.profit_factor)}</td><td>${pct(s.max_drawdown_pct)}</td><td>${n(d.sharpe_ratio)}</td><td>${n(d.sortino_ratio)}</td><td>${pct(f.time_in_market==null?null:f.time_in_market*100)}</td><td>${money(cost)}</td></tr>`}).join('');$('funnel').innerHTML=rows.filter(x=>['technical','rf','xgboost','gru'].includes(x.strategy_id)).map(x=>{let f=x.signal_funnel||{},q=x.trade_quality||{},m=q.mfe_pct||{},a=q.mae_pct||{};return `<tr><td>${names[x.strategy_id]}</td><td>${f.technical_candidates??'—'}</td><td>${f.model_output_buy??'—'}</td><td>${f.model_output_hold??'—'}</td><td>${f.model_output_sell??'—'}</td><td>${f.gate_passed??'—'}</td><td>${f.risk_blocked??'—'}</td><td>${f.successful_fills??'—'}</td><td>${f.completed_trades??'—'}</td><td>${m.mean==null?'—':n(m.mean*100)+'%'}</td><td>${a.mean==null?'—':n(a.mean*100)+'%'}</td><td>${Object.entries(x.exit_reasons||{}).map(([k,v])=>k+':'+v.count).join(', ')||'—'}</td></tr>`}).join('');$('compare').innerHTML=rows.map(x=>{let b=base.find(y=>y.strategy_id===x.strategy_id),before=b?.before,now=x.modified,delta=before?.total_return_pct==null||now.total_return_pct==null?null:now.total_return_pct-before.total_return_pct,d=x.details||{},cost=d.total_transaction_cost??((d.commission_total||0)+(d.transaction_tax_total||0)+(d.slippage_total||0));return `<tr><td>${names[x.strategy_id]}</td><td>${before?pct(before.total_return_pct):'—'}</td><td>${pct(now.total_return_pct)}</td><td class="${cls(delta)}">${signed(delta)}</td><td>${before?.trade_count??'—'}</td><td>${now.trade_count??'—'}</td><td>${money(cost)}</td></tr>`}).join('');$('scenarioNote').textContent=sc===0?'修改前為同一標的先前的 0 bps 執行；修改前後模型分別重訓，尤其 XGBoost 存在重跑結果差異，應將績效差視為含訓練隨機性，避免解讀成單一程式修正效果。':'本表和 0 bps 共用同一模型 artifact；差異主要反映每邊 5 bps 滑價成本與其造成的股數變化。';$('notes').innerHTML=D.method_notes.map(x=>`<p>• ${x}</p>`).join('')} $('stock').addEventListener('change',render);$('scenario').addEventListener('change',render);render();
</script></html>"""
    page = page.replace(
        "<section><h2>模型與研究解讀</h2>",
        "<section><h2>ML Gate 機率與驗證診斷</h2><div id=\"mlSummary\" class=\"scroll\"></div></section><section><h2>模型與研究解讀</h2>",
    )
    page = page.replace("<th>淨損益</th>", "<th>淨損益</th><th>毛損益</th>")
    page = page.replace("<td>${money(s.net_pnl)}</td><td>${s.trade_count", "<td>${money(s.net_pnl)}</td><td>${money(x.pnl_breakdown?.gross_pnl)}</td><td>${s.trade_count")
    model_js = """const ma=D.model_training_audit?.models||{},mg=D.ml_gate_diagnostics||{};const ms=[['random_forest','RF','rf'],['xgboost','XGBoost','xgboost'],['gru','GRU','gru']];$('mlSummary').innerHTML='<table><thead><tr><th>模型</th><th>驗證 Accuracy</th><th>Macro-F1</th><th>ROC-AUC</th><th>預測 BUY/HOLD/SELL</th><th>BUY 機率 mean / p10 / p90</th><th>Gate規則</th></tr></thead><tbody>'+ms.map(([k,n,g])=>{const a=ma[k]||{},v=a.validation_metrics_diagnostic_only||{},d=mg[g]||{},c=d.candidate_predicted_class_counts||{},p=d.buy_probability||{};return '<tr><td>'+n+'</td><td>'+Number(v.accuracy||0).toFixed(3)+'</td><td>'+Number(v.macro_f1||0).toFixed(3)+'</td><td>'+Number(v.macro_roc_auc_ovr||0).toFixed(3)+'</td><td>'+(c.BUY||0)+' / '+(c.HOLD||0)+' / '+(c.SELL||0)+'</td><td>'+(p.mean==null?'—':Number(p.mean).toFixed(3))+' / '+(p.p10==null?'—':Number(p.p10).toFixed(3))+' / '+(p.p90==null?'—':Number(p.p90).toFixed(3))+'</td><td>'+String(d.gate_rule||'ARGMAX')+'</td></tr>'}).join('')+'</tbody></table>';"""
    return page.replace("render();\n</script></html>", model_js + "render();\n</script></html>")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", nargs=3, required=True, metavar="STOCK=PATH")
    parser.add_argument("--run", nargs=6, required=True, metavar="STOCK=SCENARIO=PATH")
    parser.add_argument("--output", type=Path, default=ROOT / "exports/diagnostic_correctness_20261027")
    args = parser.parse_args()
    baseline = _path_map(args.baseline, 1)
    runs = _path_map(args.run, 2)
    build_report(baseline, runs, args.output)
    print(json.dumps({"status": "REPORT_CREATED", "output_directory": str(args.output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()

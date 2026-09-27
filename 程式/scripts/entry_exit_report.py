"""六策略進出場改善與損失來源的集中報告。"""
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads
import html
import json
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
NAMES={'ma':'MA均線','rsi':'RSI動能','macd':'MACD','bollinger':'布林通道','breakout':'區間突破','vote':'三指標多數決'}


def write_report(payload):
    assert_payload(payload)
    audit=guarded_json_loads((ROOT/'exports/entry_exit_improvement/warmup_audit.json').read_text(encoding='utf-8'))
    audits={(a['strategy'],a['variant']):a for a in audit['audits']}
    for s in payload['summary']:
        a=audits.get((s['strategy'],s['variant']))
        s['initially_retained']=s['retained']
        if a:
            s['warmup_audit']=a
            s['retained']=a['retained_after_audit']
    registry={'independent_validation_passed':False,'formal_strategy_changed':False,
              'development_period':['2026-01-01','2026-06-30'],
              'source_hashes':{name:hashlib.sha256((ROOT/'exports/entry_exit_improvement'/name).read_bytes()).hexdigest()
                               for name in ('results.json','warmup_audit.json','registered_plan.json','warmup_registered_plan.json')},
              'candidates':[s for s in payload['summary'] if s['retained']]}
    (ROOT/'exports/entry_exit_improvement/candidates.json').write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding='utf-8')
    summary=payload['summary']; kept=[s for s in summary if s['retained']]
    conclusion=('通過本輪開發保留門檻：'+'、'.join(NAMES[s['strategy']]+'／'+s['label'] for s in kept)) if kept else '本輪尚無同時通過原V2與風控對照門檻的版本；改善案例保留，不升級為正式策略。'
    diag=[]
    for key in payload['plan']['strategies']:
        selected=[r for r in payload['records'] if r['strategy']==key and r['variant']=='v2']
        trades=[t for r in selected for t in r['trade_diagnostics']]
        row={'strategy':key,'cost_flip_trades':sum(t['category']=='成本轉虧' for t in trades),
             'cost_flip_loss':-sum(t['net_pnl'] for t in trades if t['category']=='成本轉虧'),
             'price_loss_trades':sum(t['category']=='價差與成本皆虧' for t in trades),
             'price_loss':-sum(t['net_pnl'] for t in trades if t['category']=='價差與成本皆虧'),
             'total_cost':sum(r['metrics']['transaction_cost'] for r in selected),
             'gross_pnl':sum(r['metrics']['gross_pnl'] for r in selected),
             'net_pnl':sum(r['metrics']['net_pnl'] for r in selected)}
        diag.append(row)
    lines=['# 六策略進出場改善與損失診斷（2026/09/26）','',conclusion,'',
           '本輪固定5版本×6策略×6股票＝180組，使用2026/01/01～06/30已知開發資料。沒有下載或讀取2025下半年保留期間。所有結果保留。', '',
           '## 改了什麼', '',
           '1. 原V2為對照，使用者風控也獨立重跑，避免把風控差異算成新濾網效果。',
           '2. 日線進場確認：前一交易日收盤高於20日均線，而且該均線高於5個交易日前；前25個交易日因歷史不足暫不進場。只讀當時已知價格。',
           '3. 一般技術賣出訊號連續3根完成K棒成立才出場；停損、趨勢跌破、移動停利立即依原規則處理，不等待3根確認。',
           '4. 日線確認與出場確認各自測試，再測合併版；不調整門檻或按股票挑參數。', '',
           '## 原V2損失來源（六檔合計）', '',
           '成本轉虧與價差虧損是互斥的逐筆分類；總成本包含所有獲利及虧損交易的成本，不能再加到兩類淨虧損上，否則重複計算。出場原因及事後最高浮盈是診斷線索，不代表已證明虧損因果。', '',
           '|策略|成本轉虧筆數|成本轉虧淨損失|價差虧損筆數|價差虧損含費用損失|所有交易總成本|',
           '|---|---:|---:|---:|---:|---:|']
    for r in diag:
        lines.append(f"|{NAMES[r['strategy']]}|{r['cost_flip_trades']}|{r['cost_flip_loss']:.0f}|{r['price_loss_trades']}|{r['price_loss']:.0f}|{r['total_cost']:.0f}|")
    lines += ['', '## 版本比較', '', '|策略|版本|平均報酬|報酬中位數|獲利股票|對原V2改善股票|對原V2報酬差中位數／百分點|判定|', '|---|---|---:|---:|---:|---:|---:|---|']
    for s in summary:
        delta=s.get('against_v2',{})
        lines.append(f"|{NAMES[s['strategy']]}|{s['label']}|{s['mean_return']:.2%}|{s['median_return']:.2%}|{s['profitable_stocks']}/6|{delta.get('improved_stocks','—')}|{delta.get('median_return_delta',0)*100:+.2f}|{'對照' if s['variant']=='v2' else '開發改善候選' if s['retained'] else '未通過保留門檻'}|")
    audit_lines=['', '## 相同暖機期補測（另外72組）', '',
                 '對照也等待25個交易日再開始交易，排除單純跳過前段交易的影響。這是同一批開發資料的混淆因素檢查，不是獨立驗證。MA的兩個日線候選因此撤下；MACD、多數決與RSI保留相對改善候選。RSI仍以減少虧損為主。', '',
                 '|策略|版本|改善股票|報酬差中位數／百分點|補測後保留|', '|---|---|---:|---:|---|']
    for a in audit['audits']:
        audit_lines.append(f"|{NAMES[a['strategy']]}|{a['variant']}|{a['improved_stocks']}/6|{a['median_return_delta']*100:+.2f}|{'是' if a['retained_after_audit'] else '否'}|")
    lines += audit_lines
    lines += ['', '## 判定與限制', '',
              '- 相對原V2及同風控對照，皆至少4檔改善、報酬差中位數為正、成本及回撤差中位數不增加；拿掉任一檔後中位數仍為正、額外5bps成本後改善為正、至少4檔各5筆完整交易。沒有因結果修改門檻。',
              '- 通過代表開發資料上的相對改善，不代表淨報酬為正、勝過買進持有或已證明未來有效。',
              '- 5bps為每次成交額增加0.05%的固定路徑成本估計，不是重新模擬成交價及後續資金變化。',
              '- 最高浮盈、最低浮虧與回吐是在交易結束後統計，只作診斷，沒有傳回進場判斷。',
              '- 本輪採v2範本，與早期V1報告不能直接當成完全同一版本。',
              '- 暫不覆蓋正式策略或自動選擇個別股票的最高版本。候選留在此報告與結果JSON。', '',
              '## 下一步', '',
              '整理通過候選的股票分布、持倉與風險限制。等其他研究收斂，再固定少數版本與正式評估標準，才執行已預留的歷史驗證；本輪結果不作為已通過獨立驗證的宣稱。']
    (ROOT/'docs/六策略進出場改善_20260926.md').write_text('\n'.join(lines),encoding='utf-8')
    compact=dict(payload,loss_diagnosis=diag,strategy_names=NAMES)
    compact['records']=[{k:v for k,v in r.items() if k not in ('trades','signals')} for r in payload['records']]
    data=json.dumps(compact,ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>六策略進出場改善</title><style>body{font-family:"Microsoft JhengHei",sans-serif;background:#f4f6fa;color:#202c40;margin:0}main{max-width:1600px;margin:auto;padding:28px}p{line-height:1.8}.note{padding:18px;background:#fff0c9;border-radius:10px}.scroll{overflow:auto}table{border-collapse:collapse;background:white;width:100%;margin:15px 0}th,td{padding:10px;border:1px solid #d8dfe9;white-space:nowrap;text-align:right}th{background:#e5edf5}th:first-child,td:first-child{text-align:left}select{padding:8px;margin:8px;font-size:16px}.retained{background:#e2f1e7}summary{padding:12px;background:#e5edf5;cursor:pointer}</style></head><body><main>
<h1>六策略：進場、收手與交易成本改善</h1><p>2026/01/01～06/30｜5版本×6策略×6檔＝180組開發對照｜保留驗證資料未使用</p><p class="note">__CONCLUSION__<br>保留候選代表相對改善，仍須看絕對損益與獨立驗證；不等於正式交易推薦。</p>
<h2>原V2的損失分布</h2><p>兩類虧損互斥；總成本另列，不能再加到含成本的虧損金額。這是損益分類，非已證實的因果分析。</p><div class="scroll"><table id="loss"></table></div>
<h2>版本比較</h2><label>策略<select id="strategy"></select></label><p>風控版包含停損2%、最低持有4根、冷卻12／24根、3%啟動／回跌2%移動停利。日線確認只用前一天以前資料；連續3根僅限制一般技術出場。</p><div class="scroll"><table id="summary"></table></div>
<h2>逐股票比較</h2><label>股票<select id="stock"></select></label><p id="benchmark"></p><div class="scroll"><table id="stocks"></table></div>
<details><summary>展開逐筆交易診斷</summary><label>版本<select id="variant"></select></label><p>最高浮盈、最低浮虧為持有期間已完成收盤價估計，只作事後診斷；進場條件取成交前一根完成K棒，日線濾網只讀前一日以前。</p><div class="scroll"><table id="trades"></table></div></details>
<details><summary>事先固定的保留門檻與限制</summary><p>對原V2及同風控對照都要：至少4/6檔報酬改善、報酬差中位數正、回撤及成本差中位數不增加、拿掉任一檔後仍改善、額外成交成本後改善為正、至少4檔各5筆交易。沒有選擇個別股票最佳版本。</p><p>資料全部已看過；此為開發實驗。每次成交額額外5bps是成本敏感度估計，未重跑滑價路徑。全程現金0%不計息。平均回撤不等於投資組合回撤。</p></details></main>
<script>const D=__DATA__;const pct=x=>(x*100).toFixed(2)+'%';const money=x=>Math.round(x).toLocaleString('zh-TW');const strategy=document.querySelector('#strategy'),stock=document.querySelector('#stock'),variant=document.querySelector('#variant');D.plan.strategies.forEach(k=>strategy.add(new Option(D.strategy_names[k],k)));D.plan.stock_codes.forEach(c=>stock.add(new Option(c+' '+D.records.find(r=>r.code===c).name,c)));D.plan.variants.forEach(v=>variant.add(new Option(v.label,v.id)));
document.querySelector('#loss').innerHTML='<tr><th>策略</th><th>成本轉虧筆數</th><th>成本轉虧淨損失</th><th>價差虧損筆數</th><th>價差虧損含費用</th><th>全部交易總成本</th></tr>'+D.loss_diagnosis.map(r=>`<tr><td>${D.strategy_names[r.strategy]}</td><td>${r.cost_flip_trades}</td><td>${money(r.cost_flip_loss)}</td><td>${r.price_loss_trades}</td><td>${money(r.price_loss)}</td><td>${money(r.total_cost)}</td></tr>`).join('');
function render(){const summaries=D.summary.filter(s=>s.strategy===strategy.value);document.querySelector('#summary').innerHTML='<tr><th>版本</th><th>平均報酬</th><th>報酬中位數</th><th>獲利股票</th><th>平均成本</th><th>平均回撤</th><th>改善股票／對V2</th><th>改善股票／對同風控</th><th>判定</th></tr>'+summaries.map(s=>`<tr class="${s.retained?'retained':''}"><td>${s.label}</td><td>${pct(s.mean_return)}</td><td>${pct(s.median_return)}</td><td>${s.profitable_stocks}/6</td><td>${money(s.mean_cost)}</td><td>${pct(s.mean_drawdown)}</td><td>${s.against_v2?s.against_v2.improved_stocks+'/6':'—'}</td><td>${s.against_control?s.against_control.improved_stocks+'/6':'—'}</td><td title="${[...(s.against_v2?.reasons||[]),...(s.against_control?.reasons||[])].join('、')}">${s.variant==='v2'?'原版對照':s.retained?'開發改善候選':'未通過保留門檻'}</td></tr>`).join('');
document.querySelector('#benchmark').textContent='同期間買進持有：'+pct(D.benchmarks[stock.value].total_return)+'；全程現金：0%（不計息）。';const rows=D.records.filter(r=>r.code===stock.value&&r.strategy===strategy.value);const names=Object.fromEntries(D.plan.variants.map(v=>[v.id,v.label]));document.querySelector('#stocks').innerHTML='<tr><th>版本</th><th>報酬</th><th>毛損益</th><th>成本</th><th>回撤</th><th>完整交易</th><th>持倉比例</th><th>額外成本後報酬</th></tr>'+rows.map(r=>`<tr><td>${names[r.variant]}</td><td>${pct(r.metrics.total_return)}</td><td>${money(r.metrics.gross_pnl)}</td><td>${money(r.metrics.transaction_cost)}</td><td>${pct(r.metrics.max_drawdown)}</td><td>${r.metrics.completed_trades}</td><td>${pct(r.diagnostics.holding_bar_fraction)}</td><td>${pct(r.diagnostics.stressed_return)}</td></tr>`).join('');
const record=rows.find(r=>r.variant===variant.value);document.querySelector('#trades').innerHTML='<tr><th>買進</th><th>賣出</th><th>毛損益</th><th>淨損益</th><th>分類</th><th>出場原因</th><th>持有K棒</th><th>最高浮盈</th><th>最低浮虧</th><th>進場高於趨勢MA</th><th>量比</th><th>日線確認</th></tr>'+record.trade_diagnostics.map(t=>`<tr><td>${t.buy_datetime}</td><td>${t.sell_datetime}</td><td>${money(t.gross_pnl)}</td><td>${money(t.net_pnl)}</td><td>${t.category}</td><td>${t.exit_reason}</td><td>${t.holding_bars}</td><td>${pct(t.peak_close_return)}</td><td>${pct(t.worst_close_return)}</td><td>${t.entry_trend_distance===null?'—':pct(t.entry_trend_distance)}</td><td>${t.entry_volume_ratio===null?'—':t.entry_volume_ratio.toFixed(2)}</td><td>${t.entry_daily_trend_ok?'通過':'未通過／資料不足'}</td></tr>`).join('');}strategy.onchange=render;stock.onchange=render;variant.onchange=render;render();</script></body></html>'''
    audit_html='<h2>相同暖機期補測：72組</h2><p>對照也等25個交易日後才進場。MA日線候選撤下；MACD、多數決、RSI仍有相對改善，RSI多數股票仍虧損。</p><div class="scroll"><table><tr><th>策略／版本</th><th>改善股票</th><th>報酬差中位數（百分點）</th><th>補測後保留</th></tr>'
    for a in audit['audits']:
        audit_html+=f"<tr><td>{NAMES[a['strategy']]}／{a['variant']}</td><td>{a['improved_stocks']}/6</td><td>{a['median_return_delta']*100:+.2f}</td><td>{'是' if a['retained_after_audit'] else '否'}</td></tr>"
    audit_html+='</table></div>'
    page=page.replace('<h2>原V2的損失分布</h2>',audit_html+'<h2>原V2的損失分布</h2>')
    page=page.replace('__CONCLUSION__',html.escape(conclusion)).replace('__DATA__',data)
    (ROOT/'exports/entry_exit_improvement_latest.html').write_text(page,encoding='utf-8')


if __name__=='__main__':
    write_report(guarded_json_loads((ROOT/'exports/entry_exit_improvement/results.json').read_text(encoding='utf-8')))

"""風控拆項結果呈現；不重新回測或挑選新參數。"""
from trading_system.research_guard import assert_config, assert_payload, assert_development_period, guarded_json_loads
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def percent(value):
    return f'{value*100:+.2f}%'


def write_report(payload):
    assert_payload(payload)
    summary = payload['summary']
    by_id = {(r['variant'],r['filtered']):r for r in summary}
    stable = [r for r in summary if not r['filtered'] and r.get('status')=='保留研究候選'
              and by_id[(r['variant'],True)].get('status')=='保留研究候選']
    conclusion = ('兩種進場條件都通過保留門檻：'+ '、'.join(r['label'] for r in stable)
                  if stable else '沒有任何變動同時通過兩種進場條件的保留門檻。暫不宣稱找到最佳參數。')
    labels = {v['id']:v['label'] for v in payload['plan']['variants']}
    def trades_for(code, key, filtered):
        record = next(r for r in payload['records'] if r['code']==code and r['variant']==key and r['filtered']==filtered)
        return [(t['timestamp'],t['action'],t['price'],t['quantity']) for t in record['trades']]
    identical = {}
    for key in ('trend_only','cool_only'):
        identical[key] = sum(trades_for(code,key,f)==trades_for(code,'reference',f)
                             for f in (False,True) for code in payload['plan']['stock_codes'])
    trail = by_id[('trail_only',False)]
    filtered_trail = by_id[('trail_only',True)]
    short_trend = by_id[('trend_60',True)]
    long_cool = by_id[('cool_long',False)]
    findings = [
        f"提早MA120出場：{identical['trend_only']}/12組股票與進場條件的成交時間、方向、價格、股數完全不變。這輪只改了部分出場原因名稱，未證明提早出場帶來優勢。",
        f"停損冷卻12→24根：{identical['cool_only']}/12組成交完全不變。每天最多一次及訊號間隔可能讓這項限制沒有實際影響，不能据此選出最佳冷卻。",
        f"移動停利3%／2%：原進場改善{trail['improved_stocks']}/6檔；上漲進場改善{filtered_trail['improved_stocks']}/6檔，未達事先設定的4檔門檻。暫保留原設定作研究參考，沒有證據升級為通用參數。",
        f"提早MA60出場＋上漲篩選：改善{short_trend['improved_stocks']}/6檔，配對報酬差中位數{short_trend['median_return_delta']*100:+.2f}個百分點，但成本差中位數{short_trend['median_cost_delta']:+.0f}元。{short_trend['status']}；不能在看到結果後取消成本門檻。",
        f"一般／停損冷卻24／48根：原進場改善{long_cool['improved_stocks']}/6檔；成本差中位數{long_cool['median_cost_delta']:+.0f}元，但沒有普遍改善，暫不採用為預設。",
        '這輪沒有找出可宣稱最佳的數值。維持原版與既有候選，停止追加同批參數搜尋，等其他策略診斷完成再挑少數候選進入保留驗證。'
    ]
    lines = ['# 風控拆項與參數敏感度實驗（2026/09/26）', '', conclusion, '',
             '本輪：六檔區間突破，2026/01/01～06/30，17版本×兩種固定進場篩選＝204組。所有資料都是開發資料，2025/07/01～12/31保留期間未讀取、未下載。', '',
             '## 主要發現', '', *['- '+s for s in findings], '', '## 如何看結果', '',
             '- 共同基準統一停損2%、最少持有4根、一般與停損冷卻各12根、每天最多進場1次；跨日不重置冷卻。',
             '- 原V2單獨保留為錨點。共同基準已修改持有、停損及冷卻實作，不能將其對原V2的差異都算成某一風控的效果。',
             '- 提早趨勢出場只控制最低持有期間前的風控途徑；即使關閉，持滿後原本MA120技術出場仍存在。MA60/240只改額外出場均線，進場MA120不變。',
             '- 移動停利用持倉最高已完成收盤价；達啟動門檻後回跌比例觸發，下一根開盤成交。',
             '- 冷卻以完整可交易K棒計數，跨日保留。停止或減少交易不是自動改善。',
             '- 第一季／第二季貢獻、拿掉任一檔、成本壓力都只是開發診斷，不是獨立驗證。',
             '- 額外成本為每次成交額5bps的敏感度估計，固定成交路徑，不是完整滑價重跑。', '',
             '## 事先登記的保留門檻', '',
             '相對指定對照：至少4/6檔報酬改善、報酬差中位數為正、回撤與成本差中位數不增加；拿掉任一檔後中位數仍為正；額外成本後差異仍為正；至少4檔各有5筆完整交易。門檻只用於保留研究候選，不代表可交易。', '',
             '|進場條件|版本|對照|改善檔數|報酬差中位數（百分點）|成本差中位數（元）|結論|',
             '|---|---|---|---:|---:|---:|---|']
    for r in summary:
        if not r['compare_to']:
            continue
        lines.append(f"|{'上漲才進場' if r['filtered'] else '原進場'}|{r['label']}|{labels[r['compare_to']]}|{r['improved_stocks']}/6|{r['median_return_delta']*100:+.2f}|{r['median_cost_delta']:+.0f}|{r['status']}：{'、'.join(r['reasons']) or '尚待獨立驗證'}|")
    lines += ['', '## 後續', '',
              '保留完整失敗結果。先確認單項效果是否跨股票一致、參數附近是否穩定，再決定候選。尚未通過者不依本輪結果追加參數搜尋或放寬門檻。進入歷史保留驗證前，先鎖定候選與整體驗收標準。', '',
              '## 檔案', '',
              '- configs/risk_ablation_20260926.json：事先設定的17版本及比較規則。',
              '- exports/risk_ablation/registered_plan.json：執行前固定計畫。',
              '- exports/risk_ablation/execution_log.jsonl：每次開始時間及程式／設定SHA256。',
              '- exports/risk_ablation/results.json：204組指標、成交與出場原因，含來源行情SHA256。',
              '- exports/risk_ablation_latest.html：可直接開啟的比較畫面。',
              '- 舊reports保留、不新增數百份報告；本輪未修改MySQL資料表。']
    (ROOT/'docs/風控拆項實驗_20260926.md').write_text('\n'.join(lines),encoding='utf-8')
    compact = dict(payload)
    compact['records'] = [{k:v for k,v in r.items() if k not in ('trades','signals')} for r in payload['records']]
    data = json.dumps(compact,ensure_ascii=False).replace('<','\\u003c')
    page = '''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>風控拆項研究</title>
<style>body{font-family:"Microsoft JhengHei",sans-serif;background:#f4f6f9;color:#202d40;margin:0}main{max-width:1600px;margin:auto;padding:28px}p{line-height:1.8}table{border-collapse:collapse;background:white;width:100%;margin:15px 0}td,th{border:1px solid #dae0e8;padding:10px;text-align:right;white-space:nowrap}td:first-child,th:first-child{text-align:left}th{background:#e8eef5}.scroll{overflow:auto}.note{background:#fff0cc;padding:16px;border-radius:10px}select{padding:9px;font-size:16px;margin:8px}.positive{color:#127045}.negative{color:#b52238}summary{cursor:pointer;padding:12px;background:#e8eef5}small{color:#556}</style></head><body><main>
<h1>風控拆項與參數敏感度</h1><p>2026/01/01～06/30｜六檔區間突破｜17版本×2種進場篩選×6檔＝204組開發測試</p>
<p class="note">__CONCLUSION__<br>這是已使用資料的開發研究。2025下半年的保留資料未使用，不依最高報酬挑參數。</p>
<h2>這輪發現與參數處理</h2><ul>__FINDINGS__</ul>
<p>單項新增以「共同基準」為對照；完整規則拿掉一項以「完整規則」為對照；附近參數以原單項設定為對照。差異為零不代表有效，可能根本沒有改變成交。</p>
<label>固定進場條件<select id="filter"><option value="false">原進場</option><option value="true">上漲才進場</option></select></label>
<h2>拆項結果</h2><p>報酬差為各股票配對差異的中位數，單位是百分點；回撤差與成本差越低越好。</p><div class="scroll"><table id="main"></table></div>
<details><summary>展開少量附近參數測試</summary><div class="scroll"><table id="sensitivity"></table></div></details>
<h2>各股票結果與成本、持倉時間</h2><label>股票<select id="stock"></select></label><p id="benchmark"></p><div class="scroll"><table id="stocks"></table></div>
<details><summary>計算方式與保留標準</summary><p>共同基準：停損2%、一般持有至少4根、冷卻12／12根、每天最多一次。新增提早趨勢出場不會移除原本持滿後的MA120出場。最高價採最高完成收盤價，冷卻依交易K棒跨日累計。所有風控收盤判斷、下一棒開盤成交。</p>
<p>至少4檔改善、報酬差中位數正、回撤及成本差中位數不增加；拿掉任一檔仍改善，額外成交成本後仍改善，至少4檔各5筆完整交易。第一季與第二季是損益貢獻，不是獨立開倉回測。額外5bps成本為固定成交路徑估計，沒有重算價格改變後的交易。</p></details>
<small>平均回撤不是投資組合回撤。持倉比例為持有股票的K棒占比；股票間資金使用率不同。原V2與完整候選已核對重現上一輪成交損益。</small>
</main><script>const D=__DATA__; const pct=x=>(x*100).toFixed(2)+'%';const pp=x=>(x*100).toFixed(2);const money=x=>Math.round(x).toLocaleString('zh-TW');const label=Object.fromEntries(D.plan.variants.map(v=>[v.id,v.label]));
const stock=document.querySelector('#stock'); D.plan.stock_codes.forEach(code=>{const r=D.records.find(r=>r.code===code);stock.add(new Option(code+' '+r.name,code))});
function render(){const filtered=document.querySelector('#filter').value==='true';const summaries=D.summary.filter(r=>r.filtered===filtered);
const header='<tr><th>版本</th><th>對照</th><th>平均報酬</th><th>報酬中位數</th><th>改善股票</th><th>報酬差／百分點</th><th>回撤差／百分點</th><th>成本差／元</th><th>第一季貢獻差</th><th>第二季貢獻差</th><th>判定</th></tr>';
function row(r){return `<tr><td>${r.label}</td><td>${label[r.compare_to]||'—'}</td><td>${pct(r.mean_return)}</td><td>${pct(r.median_return)}</td><td>${r.improved_stocks===undefined?'—':r.improved_stocks+'/6'}</td><td>${r.compare_to?pp(r.median_return_delta):'—'}</td><td>${r.compare_to?pp(r.median_drawdown_delta):'—'}</td><td>${r.compare_to?money(r.median_cost_delta):'—'}</td><td>${r.compare_to?pp(r.median_first_delta):'—'}</td><td>${r.compare_to?pp(r.median_second_delta):'—'}</td><td title="${(r.reasons||[]).join('、')}">${r.status||'對照基準'}</td></tr>`}
document.querySelector('#main').innerHTML=header+summaries.filter(r=>r.kind!=='sensitivity').map(row).join('');document.querySelector('#sensitivity').innerHTML=header+summaries.filter(r=>r.kind==='sensitivity').map(row).join('');
document.querySelector('#benchmark').textContent='同期間買進持有：'+pct(D.benchmarks[stock.value].total_return)+'；現金：0%（不計息）。';
document.querySelector('#stocks').innerHTML='<tr><th>版本</th><th>報酬</th><th>毛損益</th><th>成本</th><th>回撤</th><th>完整交易</th><th>持倉K棒比例</th><th>平均資金使用率</th><th>額外成本後報酬</th><th>出場原因</th></tr>'+D.records.filter(r=>r.filtered===filtered&&r.code===stock.value).map(r=>`<tr><td>${label[r.variant]}</td><td>${pct(r.metrics.total_return)}</td><td>${money(r.metrics.gross_pnl)}</td><td>${money(r.metrics.transaction_cost)}</td><td>${pct(r.metrics.max_drawdown)}</td><td>${r.metrics.completed_trades}</td><td>${pct(r.diagnostics.holding_bar_fraction)}</td><td>${pct(r.diagnostics.average_invested_fraction)}</td><td>${pct(r.diagnostics.stressed_return)}</td><td>${Object.entries(r.exit_counts).map(([k,v])=>k+': '+v).join('、')}</td></tr>`).join('');}
document.querySelector('#filter').onchange=render;stock.onchange=render;render();</script></body></html>'''
    page = page.replace('__CONCLUSION__',html.escape(conclusion)).replace('__FINDINGS__',''.join('<li><p>'+html.escape(s)+'</p></li>' for s in findings)).replace('__DATA__',data)
    (ROOT/'exports/risk_ablation_latest.html').write_text(page,encoding='utf-8')


if __name__=='__main__':
    write_report(guarded_json_loads((ROOT/'exports/risk_ablation/results.json').read_text(encoding='utf-8')))

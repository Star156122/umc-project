"""Presentation from completed frozen results only; never reads new market data."""
import ast,csv,json,html
from pathlib import Path
H=Path(__file__).resolve().parents[1];O=H/'結果'/json.loads((H/'結果/latest.json').read_text(encoding='utf8'))['folder']
raw=(O/'data.js').read_text(encoding='utf8');D=json.loads(raw.removeprefix('window.FORMAL=').removesuffix(';'))
labels=D['labels'];names={'Q1':'三個月｜2025/01～03','H1':'六個月｜2025/01～06','YEAR':'一年｜2025/01～12','H2':'正式保留判定｜2025/07～12'}
tree=ast.parse((H.parent/'華碩2357_2025上半年/程式/build_trade_charts.py').read_text(encoding='utf8'))
template=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TEMPLATE' for t in n.targets))
for w in D['config']['windows']:
 sub=O/('買賣圖_'+w['id']);sub.mkdir(exist_ok=True)
 bars=[b for b in D['bars'] if w['start']<=__import__('datetime').datetime.fromtimestamp(b[0],__import__('datetime').timezone(__import__('datetime').timedelta(hours=8))).date().isoformat()<=w['end']]
 payload={a:{'name':labels[a],'bars':bars,'orders':D['results'][f"{w['id']}_{a}_5"]['orders'],'trades':D['results'][f"{w['id']}_{a}_5"]['trades'],'metrics':D['results'][f"{w['id']}_{a}_5"]['metrics']} for a in labels}
 for a in ['',*labels]:
  page=template.replace('__DATA__',json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')).replace('__ONLY__',json.dumps(a))
  page=page.replace('2025-01-01',w['start']).replace('2025-06-30',w['end']).replace('華碩','和碩').replace('2025 年 1–6 月',names[w['id']]).replace('完整六個月','完整區間').replace('新增到期','取消到期')
  page=page.replace('修改策略＋相同 ML','不加碼、不延期；共同完整日＋三模型合法預測覆蓋').replace('原始資料與績效保持不變','原始資料與績效保持不變；2025下半年已正式消耗，不依結果調參')
  (sub/((a or 'index')+'.html')).write_text(page,encoding='utf8')
style=(H/'程式/report.css').read_text(encoding='utf8')
page='''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>和碩4938｜三個月、六個月、全年回測</title><style>'''+style+'''</style><main><header><div class="eyebrow">和碩4938 · 每組各25萬元 · 固定三模型與規則</div><h1>三個月、六個月與全年，策略表現如何？</h1><p>不加碼、不延期；只允許共同完整日＋三模型合法預測覆蓋的新進場。現金40%、保留初始20%、無1%風險額度、2.5%停損。</p></header><div class="notice"><strong>2025下半年正式保留資料已使用。</strong>先前已開放的上半年不能稱未見資料，全年也是混合期間；正式判準只看單獨下半年。凍結後未調參，2026資料仍未解鎖。普通5分K近似零股數量成交，不能保證真實撮合。</div><div class="toolbar"><label>滑價 <select id="slip"><option value="5">每邊0.05%（主結果）</option><option value="0">零滑價（補充）</option></select></label></div><div id="content"></div><section class="panel"><h2>怎麼比較</h2><p>每個期間、每組都從25萬元空手開始，不承接前期持倉。年度報酬不能把季度或半年報酬相加；H2是獨立帳戶判定，與YEAR曲線下半段不同。</p><p>買進持有使用首個合法09:10開盤最多80%資金，仍受相同成交量限制，不保證投入80%；不受ML日期限制，持有至期間末收盤。主動策略全部都受同一個完整日與三模型合法覆蓋交集限制；各混合組仍只要求自己的BUY，純技術不看預測類別。非共同日仍處理既有部位的停損與賣出，不刪中間行情。</p><p>162根觀測5分K到期安排出場，不加碼、不延期。每筆買入含手續費不超過當時現金40%，且買後保留至少5萬元。手續費0.1425%、最低20元、賣出稅0.3%；整股與零股部分分計。前20根完成棒均量1%限量，買賣不早於09:10，不允許同日新增部位同日賣出；停損可能延後越價。</p><p>6/17現金股利每股4.50282529元，除息認列應收、7/9才成為現金；半年期末仍未領到者列應收，全年則在付款日變現。技術指標採因果現金調整，圖上使用原始價。模型僅使用原科技五股2023～2024成熟標籤，沒有和碩或2025資料訓練或調門檻。</p><p>事前判準：下半年各ML組須成本後報酬優於純技術、最大回撤不惡化，且至少5筆；少於5筆標證據不足。這是本次事前固定的單股比較門檻，不代表產業泛化、部署核准或最佳模型。結果不佳不回頭調整。</p><p><a href="formal_verdicts.json">正式判定明細</a> · <a href="metrics.csv">全部指標CSV</a> · <a href="../../稽核/freeze.json">事前凍結規格</a> · <a href="../../稽核/consumed.json">首次存取已使用紀錄</a> · <a href="../../稽核/preflight_tests.txt">事前測試</a></p></section><p class="muted">完整結果、失敗也保留。一般日期鎖未改寫，正式路徑僅授權和碩4938的2025年。</p></main><script src="data.js"></script><script>
const D=window.FORMAL,L=D.labels,n=(v,d=2)=>v==null?'—':Number(v).toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d}),pct=v=>v==null?'—':n(v*100)+'%',NAMES=__NAMES__;
function table(h,rows){return '<div class="scroll"><table><thead><tr>'+h.map(x=>'<th>'+x+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'}
function paint(){const slip=document.querySelector('#slip').value;document.querySelector('#content').innerHTML=D.config.windows.map(w=>{
const rows=Object.keys(L).map(a=>{const m=D.results[w.id+'_'+a+'_'+slip].metrics;return [L[a],pct(m.return),n(m.net_pnl),n(m.trade_count,0),a==='BH'?'基準不作統計':pct(m.win_rate),pct(m.max_drawdown),pct(m.avg_exposure),a==='BH'?'買進持有基準':m.trade_count<5?'證據不足':'僅單股研究','<a href="買賣圖_'+w.id+'/'+a+'.html">看買賣圖</a>']});
return '<section class="panel"><h2>'+NAMES[w.id]+'</h2>'+table(['組別','報酬','淨損益元','交易數','勝率','最大回撤','平均曝險','證據','圖表'],rows)+'<p><a href="買賣圖_'+w.id+'/index.html">五組互動5分K圖</a></p></section>'}).join('')+'<section class="panel"><h2>事前固定的H2正式判定（每邊0.05%）</h2>'+table(['模型','判定','較純技術報酬差','交易數'],D.verdicts.map(v=>[v.model,v.verdict==='PASS_FIXED_CRITERIA_ONLY'?'僅通過本次固定門檻':v.verdict==='FAIL'?'未通過':'證據不足',n(v.difference_vs_technical*100)+' 個百分點',v.trade_count]))+'</section>'}
document.querySelector('#slip').onchange=paint;paint();</script></html>'''
page=page.replace('__NAMES__',json.dumps(names,ensure_ascii=False))
(O/'index.html').write_text(page,encoding='utf8')
print('REPORTS_BUILT',O,flush=True)

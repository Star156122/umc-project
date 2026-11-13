/* Offline, fixed-stock research report. All prices are ordinary-market proxies. */
(()=>{'use strict';
const D=window.RESEARCH,C=D.config,S=C.stocks,s=document.body.dataset.stock,kind=document.body.dataset.kind;
const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const n=(x,d=2)=>x==null?'—':Number(x).toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d});
const pct=x=>x==null?'—':n(x*100,Math.abs(x)>0&&Math.abs(x)<.0001?4:2)+'%';
const table=(heads,rows)=>`<div class="scroll"><table><thead><tr>${heads.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(x=>`<td>${x}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
const link=(s,k,label)=>`<a href="${s}_${k}.html">${label}</a>`;
document.querySelector('main').innerHTML=`<header><div class="eyebrow">2024 年 10–12 月 · 五檔科技股 · 已曝光開發資料</div><h1>${kind==='summary'?'修改策略＋ML：五檔股票的結果':esc(S[s])+' '+s+'｜'+(kind==='funnel'?'哪些條件擋下買進？':'報酬與買賣位置')}</h1><p>每檔獨立 100 萬元，10/1 空手開始；相同規則、相同模型，不重訓、不調參。</p><p><a href="index.html">五檔總覽</a>　${Object.keys(S).map(x=>link(x,'analysis',esc(S[x]))).join('　')}</p></header><div class="warning"><strong>近似研究，證據有限。</strong>普通交易 K 線估計零股數量成交，並非真實零股撮合。每檔僅 1～4 筆標示「證據不足」；零筆不代表獲利或風險優良。本次只有修改版，不能判定比原版改善。</div><div class="controls"><label>成交滑價 <select id="slip"><option value="5">買賣各 0.05%（主結果）</option><option value="0">零滑價（追溯對照）</option></select></label></div><div id="content"></div><section class="panel"><h2>這份結果怎麼看</h2><p>報酬率是 100 萬元帳戶扣除費用後的資產變化；勝率只計已平倉交易；最大回撤是帳戶從高點下跌最多多少。平均曝險是平均投入股票的資金比例，五檔各自計算，不把五個帳戶當成同一個投資組合。</p><p>暖機資料從 2024/9/16 開始，只供指標計算，不帶入舊持倉。大盤採官方加權指數前一日收盤與 SMA20；首次進場、再進場及加碼皆須合法 ML BUY，HOLD、SELL、缺預測及時間不合法分開記錄。保留 1% 風險、2.5% 停損、12:30 截止與一次加碼等既有規則。</p><p>下一合法棒估價；前 20 棒平均成交量的 1% 為買入上限，不保證成交。沿用保守規則：買賣不早於 09:10，同日買入不當日賣出；停損可能延後且越價。買賣手續費 0.1425%、每筆最低 20 元、一般賣出證交稅 0.3%；整股與零股部分分計手續費。期末可合法賣出者以末棒收盤估計清算；不能合法賣出的部位保留市值及未實現損益。</p><p>台積電 12/12 除息每股 3.99963706 元，若持有則認列應收，2025/1/9 才變現；不為了領息讀取 2025 行情。其他四檔在暖機至期末無已識別除權息事件。指標採因果現金調整，K 線仍顯示原始價。個人股利稅與補充保費未計。來源：<a href="https://investor.tsmc.com/english/dividends/2q24">台積電公告</a>。</p><p>名單在回測前固定為現有資料與預測涵蓋的五檔，屬半導體便利樣本，並非隨機抽樣或所有科技股的代表。三個月結果不推論全年，也不因績效更換股票或條件。原策略、資料庫、聯電及聯發科舊結果前後雜湊一致。</p><p><a href="metrics.csv">全部績效 CSV</a> · <a href="freeze.json">事前凍結設定</a> · <a href="audit.json">檔案稽核</a> · <a href="data_audit.json">資料與 ML 覆蓋</a> · <a href="preflight.json">官方成交量單位核對</a> · <a href="test_results.txt">正確性檢查</a></p></section><p class="muted">${esc(D.generated_at)} · 2025 下半年與 2026/7 起鎖定資料均未使用。</p>`;
const content=document.getElementById('content'),slip=document.getElementById('slip');
const r=x=>D.results[x+'_'+slip.value];
function paint(){
 if(kind==='summary'){
  const ms=Object.keys(S).map(x=>r(x).metrics),rs=ms.map(m=>m.return).sort((a,b)=>a-b),total=ms.reduce((v,m)=>v+m.trade_count,0);
  content.innerHTML=`<section class="panel"><h2>先看五檔各自表現</h2>${table(['股票','成本後報酬率','淨損益（元）','完成交易','勝率','最大回撤','平均曝險','證據','詳細報告'],Object.keys(S).map(x=>{const m=r(x).metrics;return [esc(S[x])+' '+x,pct(m.return),n(m.net_pnl),n(m.trade_count,0),pct(m.win_rate),pct(m.max_drawdown),pct(m.avg_exposure),m.trade_count===0?'沒有交易':m.trade_count<5?'證據不足':'僅供開發研究',link(x,'funnel','看篩選過程')+'<br>'+link(x,'analysis','看報酬與買賣圖')]}))}<p>各檔報酬平均 ${pct(ms.reduce((v,m)=>v+m.return,0)/5)}，中位數 ${pct(rs[2])}；${ms.filter(m=>m.net_pnl>0).length}/5 檔獲利、共 ${total} 筆已平倉交易。這是五個獨立帳戶的描述統計，不是可交易投資組合績效。</p><p>未跑原版：改善股票數及不含聯電的「改善幅度」均不適用，不把獲利股票數當成改善股票數。</p></section>`;
 }else if(kind==='funnel'){
  const a=r(s),cover=D.data_audits[s].q4_prediction_status;
  content.innerHTML=`<section class="panel"><h2>依序通過的訊號棒數</h2><p>每階段只檢查前一階段留下的機會；這是 K 棒或訊號事件數，不是獨立交易數，也不是全市場選股檔數。</p>${table(['檢查條件','接受檢查','通過','擋下'],a.funnel.map(f=>[esc(f.stage),n(f.input,0),n(f.pass,0),n(f.blocked,0)]))}</section><section class="panel"><h2>ML 預測與拒絕原因</h2><p>下表為每個觀測交易日 09:00 的合法狀態，與訊號棒數分開看。</p>${table(['每日狀態','天數'],Object.entries(cover).map(([k,v])=>[esc(k),n(v,0)]))}<h3>訊號檢查遇到的 ML 狀態</h3>${table(['狀態','次數'],Object.entries(a.ml_counts).map(([k,v])=>[esc(k),n(v,0)]))}<h3>拒絕與阻擋記錄</h3>${table(['原因','次數'],Object.entries(a.blockers).map(([k,v])=>[esc(k),n(v,0)]))}<p><a href="${s}_${slip.value}_events.csv">候選事件 CSV</a> · <a href="${s}_${slip.value}_funnel.csv">漏斗 CSV</a></p></section>`;
 }else{
  const a=r(s),m=a.metrics;
  content.innerHTML=`<section class="panel"><h2>成本後績效</h2>${table(['指標','結果'],[['報酬率',pct(m.return)],['淨損益（元）',n(m.net_pnl)],['毛損益（元，已含滑價）',n(m.gross_pnl)],['已平倉交易數',n(m.trade_count,0)],['買進／加碼',n(m.buys,0)+'／'+n(m.adds,0)],['勝率',pct(m.win_rate)],['最大回撤',pct(m.max_drawdown)],['Profit Factor',n(m.profit_factor)],['Sharpe',n(m.sharpe,3)],['平均獲利／平均虧損',n(m.average_win)+'／'+n(m.average_loss)],['手續費／交易稅',n(m.fees)+'／'+n(m.tax)],['滑價影響（已含於成交價，不重扣）',n(m.slippage_cost)],['平均／最大曝險',pct(m.avg_exposure)+'／'+pct(m.max_exposure)],['未實現損益',n(m.unrealized_pnl)],['期末應收股利',n(m.receivables_at_end)],['證據',m.trade_count===0?'沒有交易':m.trade_count<5?'證據不足':'僅供開發研究']])}</section><section class="panel"><h2>K 線與模擬買賣位置</h2><p>紅 K 上漲、綠 K 下跌；藍色 B 為買進，紫色 A 為加碼，橘色 S 為賣出。沒有成交就沒有標記。顯示原始五分 K 價格，使用日期範圍縮小查看。</p><label>開始 <input type="date" id="from" value="2024-10-01" min="2024-10-01" max="2024-12-31"></label> <label>結束 <input type="date" id="to" value="2024-12-31" min="2024-10-01" max="2024-12-31"></label><canvas id="chart" style="display:block;width:100%;height:440px;margin-top:20px"></canvas><p id="chartnote"></p></section><section class="panel"><h2>逐筆交易</h2>${table(['買進時間','賣出時間','股數','首次買價','賣價','淨損益','出場原因'],a.trades.map(t=>[esc(t.entry_time),esc(t.exit_time),n(t.quantity,0),n(t.entry_price,3),n(t.exit_price,3),n(t.net_pnl),esc(t.exit_reason)]))}<p>${a.open_position?'期末仍持有：'+esc(JSON.stringify(a.open_position)):'期末沒有未平倉部位。'}</p><p><a href="${s}_${slip.value}_orders.csv">全部委託 CSV</a> · <a href="${s}_${slip.value}_trades.csv">逐筆交易 CSV</a> · <a href="${s}_${slip.value}_equity.csv">逐棒資產 CSV</a> · ${link(s,'funnel','看篩選過程')}</p></section>`;
  ['from','to'].forEach(id=>document.getElementById(id).addEventListener('change',chart));chart();
 }
}
function chart(){
 const from=document.getElementById('from').value,to=document.getElementById('to').value;
 const date=t=>new Date(t*1000+28800000).toISOString().slice(0,10);
 const bars=D.results[s+'_bars'].filter(b=>date(b[0])>=from&&date(b[0])<=to);
 const canvas=document.getElementById('chart'),w=Math.max(300,canvas.clientWidth),h=440,ratio=window.devicePixelRatio||1;
 canvas.width=w*ratio;canvas.height=h*ratio;const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.fillStyle='#101b29';ctx.fillRect(0,0,w,h);
 if(!bars.length){ctx.fillStyle='#fff';ctx.fillText('此日期範圍沒有可用 K 棒',25,30);return}
 const orders=r(s).orders.filter(o=>date(o.timestamp)>=from&&date(o.timestamp)<=to);
 const lo=Math.min(...bars.map(b=>b[3]),...orders.map(o=>o.price)),hi=Math.max(...bars.map(b=>b[2]),...orders.map(o=>o.price));const pad=(hi-lo)*.1||1;
 const x=i=>55+(w-85)*(i+.5)/bars.length,y=p=>h-35-(p-lo+pad)/(hi-lo+2*pad)*(h-65);
 ctx.font='12px sans-serif';
 for(let i=0;i<5;i++){const p=lo+(hi-lo)*i/4;ctx.strokeStyle='#304054';ctx.beginPath();ctx.moveTo(50,y(p));ctx.lineTo(w-15,y(p));ctx.stroke();ctx.fillStyle='#dbe6f4';ctx.fillText(p.toFixed(1),2,y(p)+4)}
 bars.forEach((b,i)=>{ctx.strokeStyle=ctx.fillStyle=b[4]>=b[1]?'#ff6b6b':'#53cba1';ctx.beginPath();ctx.moveTo(x(i),y(b[2]));ctx.lineTo(x(i),y(b[3]));ctx.stroke();ctx.fillRect(x(i)-Math.max(.5,(w-85)/bars.length*.3),Math.min(y(b[1]),y(b[4])),Math.max(1,(w-85)/bars.length*.6),Math.max(1,Math.abs(y(b[1])-y(b[4]))))});
 orders.forEach(o=>{let idx=bars.findIndex(b=>b[0]>=o.timestamp);if(idx<0)idx=bars.length-1;ctx.fillStyle=o.action==='SELL'?'#ffbe64':o.action==='ADD'?'#c79cff':'#69bfff';ctx.beginPath();ctx.arc(x(idx),y(o.price),5,0,Math.PI*2);ctx.fill();ctx.font='bold 14px sans-serif';ctx.fillText(o.action==='SELL'?'S':o.action==='ADD'?'A':'B',x(idx)+6,y(o.price)-7)});
 ctx.fillStyle='#dbe6f4';ctx.font='12px sans-serif';ctx.fillText(date(bars[0][0]),55,h-10);ctx.fillText(date(bars[bars.length-1][0]),w-100,h-10);
 document.getElementById('chartnote').textContent=`此範圍 ${bars.length} 根 K 棒、${orders.length} 筆買賣委託標記；可縮小日期查看細節。`;
}
slip.addEventListener('change',paint);window.addEventListener('resize',()=>{if(kind==='analysis')chart()});paint();
})();

/* Offline HTML reports. No CDN, fetch, remote scripts or telemetry. */
(() => {
  'use strict';
  const D = window.EXPERIMENT;
  const kind = document.body.dataset.kind, variant = document.body.dataset.variant;
  const labels = {A:'原策略（不加 ML）',B:'修改策略（不加 ML）',C:'原策略＋ML 篩選',D:'修改策略＋ML 篩選'};
  const riskStage=D.config.lot_size===1?'風險至少一股':'風險至少一張';
  const esc = x => String(x ?? '—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const n = (x,d=0) => x == null ? '—' : Number(x).toLocaleString('zh-TW',{maximumFractionDigits:d,minimumFractionDigits:d});
  const pct = x => x == null ? '—' : n(x*100,2)+'%';
  const pp = x => (x>0?'+':'')+n(x*100,2)+' 個百分點';
  const dt = ts => new Date(ts*1000).toLocaleString('sv-SE',{timeZone:'Asia/Taipei'}).slice(0,16);
  const main = document.querySelector('main');
  main.innerHTML = `<header><div class="eyebrow">聯發科 2454 · 2023–2024 · 獨立研究</div><h1>四種買進策略，哪一種表現較好？</h1><p class="muted">目前頁面：${kind==='summary'?'四種策略總比較':esc(labels[variant])+'／'+(kind==='funnel'?'篩選過程：哪些條件擋下買進':'回測結果：報酬與買賣位置')}</p><a class="overview-link" href="index.html">查看四種策略總比較 →</a><nav class="strategy-nav" aria-label="四種策略報告">${Object.keys(labels).map(v=>`<section class="strategy-choice ${variant===v?'current':''}"><h2>${esc(labels[v])}</h2><p class="strategy-description">${v==='A'?'保留原本規則':v==='B'?'套用這次討論的修改':v==='C'?'原本規則，再由 ML 決定能否買進':'修改後規則，再由 ML 決定能否買進'}</p><span class="return-caption">扣除成本後報酬率</span><strong id="nav-return-${v}" class="nav-return">—</strong><span id="nav-pnl-${v}" class="muted"></span><div class="strategy-actions"><a href="${v}_funnel.html" ${variant===v&&kind==='funnel'?'aria-current="page"':''}>看篩選過程</a><a href="${v}_analysis.html" ${variant===v&&kind==='analysis'?'aria-current="page"':''}>看報酬與買賣圖</a></div></section>`).join('')}</nav><p class="muted">「ML 篩選」使用相同的 Random Forest 模型；只有判斷為 BUY 才允許買進。各組都使用本次零股數量近似模型。</p></header>
    <div class="notice"><strong>零股數量近似回測 · 不是已核實的零股成交</strong><br>這次允許按股買進，資金仍為 100 萬、每段風險仍為 1%。依你的選擇，暫用普通交易 K 線估計成交價格及流動性；真實零股價格、撮合與成交量可能不同。結果僅供單股研究，不代表實盤可複製。</div>
    <div class="toolbar"><label>成交價偏差（滑價） <select id="slip"><option value="5">買賣各 0.05%（主要設定）</option><option value="0">不計滑價（補充比較）</option></select></label><label>比較範圍 <select id="scope"><option value="common">相同日期，公平比較（主要結果）</option><option value="full">各策略全部可用日期（補充結果）</option></select></label><span class="tag">近似研究／非實盤結果</span></div><p id="view-explanation" class="view-explanation"></p><div id="content"></div>
    <div class="panel"><h2>解讀與限制</h2><p>A／B 不使用 ML，C／D 使用相同 RF OOF、BUY-only。原規則與修訂規則共用此次「普通 K 線估計零股數量成交」模型；原策略檔案及舊歷史報告不改寫。</p><p>共同樣本按資料與合法預測覆蓋固定，不按 BUY 或獲利挑日期。全可用日期與共同日期為獨立回放；舊版指標沿用共同完整日序列，新版使用原始真實 K 棒並遮蔽缺口窗口。主對照使用共同日期；全日期另列，不混接漏斗。</p><p>成交量由張換算成股，但仍是普通市場前均量，不是零股流動性。買入依前均量 1% 限量、收盤決策後下一合法棒估價，買賣均不得早於 09:10；零股不適用當日沖銷。同日新買或尚未開市時觸發出場，延到後續合法時段估價，停損損失可能超出原預估；沒有委託簿，未模擬實際零股限價排隊與部分成交。滑價 0.05% 不是已驗證的零股價差。</p><p>已依公司官方歷史表納入 2023–2024 三次現金股利：除息日認列應收、發放日才變現；指標及保護線採當時已發生股利的一致價格換算，不用未來股利。圖上仍顯示原始價格。未計個人股利所得稅與補充保費。</p><p>無交易時指標用「—」。加碼不是獨立交易，只有少量加碼不宣稱有效；單股已曝光資料不作跨股票、Clean Validation 或 Final OOS 成功主張。</p><p>原策略及兩個行情庫前後雜湊一致：<strong>${D.audit.original_files_unchanged?'通過':'未通過'}</strong>。沒有重訓、沒有使用鎖定資料。</p><div class="links"><a href="freeze.json">事前凍結設定</a><a href="audit.json">檔案與日期稽核</a><a href="data_audit.json">資料資格稽核</a><a href="metrics.csv">全部指標 CSV</a><a href="fold_metrics.csv">Fold 比較</a><a href="test_results.txt">正確性測試</a></div></div>
    <div class="footer">產生時間 ${esc(D.generated_at)} · 純離線 HTML，資料及圖表腳本位於同一資料夾。請保留整個資料夾。</div>`;
  const content = document.getElementById('content');
  const key = v => `${v}_${document.getElementById('slip').value}_${document.getElementById('scope').value}`;
  const result = v => D.results[key(v)];
  function table(headers, rows) {
    return `<div class="scroll"><table><thead><tr>${headers.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(x=>`<td>${x}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  const card = (title,value,note='') => `<div class="card"><small>${esc(title)}</small><b>${value}</b><small>${esc(note)}</small></div>`;
  function metricsTable(results) {
    return table(['策略','扣除成本後報酬率','賺／虧金額（元）','完成交易次數','勝率','最大回撤'],results.map(r=>{
      const m=r.metrics;return [esc(labels[m.variant]),`<strong class="${m.return>=0?'positive':'negative'}">${pct(m.return)}</strong>`,n(m.net_pnl,2),n(m.trade_count),pct(m.win_rate),pct(m.max_drawdown)];
    }));
  }
  function paint() {
    Object.keys(labels).forEach(v=>{
      const m=result(v).metrics,el=document.getElementById('nav-return-'+v);
      el.textContent=pct(m.return);el.className='nav-return '+(m.return>=0?'positive':'negative');
      document.getElementById('nav-pnl-'+v).textContent=(m.net_pnl>=0?'賺 ':'虧 ')+n(Math.abs(m.net_pnl),2)+' 元 · '+n(m.trade_count)+' 次交易';
    });
    document.getElementById('view-explanation').textContent=(document.getElementById('scope').value==='common'?'目前使用四組都有資料及合法 ML 預測的相同日期，適合直接比較新舊策略。':'目前各策略使用自己的全部可用日期，涵蓋範圍不同；請和「相同日期」結果分開看。')+' 上方四張卡片與下方結果會一起切換。';
    if(kind==='summary') summary();
    else if(kind==='funnel') funnel();
    else analysis();
  }
  function summary() {
    const rs=Object.keys(labels).map(result),a=D.data_audit;
    content.innerHTML=`<section class="panel"><h2>先看結果，再看原因</h2><p>上方四張卡片是各策略的報酬率。下方先比較報酬與風險，再比較每個條件擋下多少機會。</p><p><strong>報酬率：</strong>100 萬元投入後，扣除回測費用與滑價的資產變化。<strong>勝率：</strong>已完成交易中賺錢的比例。<strong>最大回撤：</strong>帳戶從先前高點往下跌最多多少，通常越低越好。</p></section>
      <section class="panel"><h2>四組績效</h2>${metricsTable(rs)}<p class="muted">現金基準為 0%。注意平均曝險：投入更多或更久可能提高報酬與風險，不能只看報酬率。價格型買進持有參考：${pct(D.benchmark.buy_hold_price_only_return)}，不含股利，只供追溯，不是總報酬或相同風險比較。</p></section>
      <section class="panel"><h2>篩選與成交概況</h2>${table(['組別','突破事件','技術條件通過','市場後','ML 後','風險門檻輸入','風險通過','實際買進','深入查看'],rs.map(r=>{
        const f=Object.fromEntries(r.funnel.map(x=>[x.stage,x]));let m=r.metrics;return [esc(m.variant+' '+labels[m.variant]),n(m.candidate_events),n(f['收盤高於MA240'].pass),n(f['市場濾網'].pass),n(f['ML Gate'].pass),n(f[riskStage].input),n(f[riskStage].pass),n(m.buys),`<a href="${m.variant}_funnel.html">看篩選過程</a> · <a href="${m.variant}_analysis.html">K 線與結果</a>`];
      }))}<p>逐層數量依固定順序計算。較早被持倉、流動性或其他條件擋下者不會再進入風險層，不能把單層阻擋量當成全部潛在問題。股數下限已改為一股。</p></section>
      <section class="panel"><h2>修改規則、加入 ML，各自帶來什麼差別？</h2>${table(['比較問題','比較前報酬率','比較後報酬率','報酬增減','最大回撤增減','解讀'],[['不加 ML：原策略 → 修改策略','A','B'],['有 ML：原策略 → 修改策略','C','D'],['原策略：不加 ML → 加入 ML','A','C'],['修改策略：不加 ML → 加入 ML','B','D']].map(([title,x,y])=>[title,pct(result(x).metrics.return),pct(result(y).metrics.return),pp(result(y).metrics.return-result(x).metrics.return),pp(result(y).metrics.max_drawdown-result(x).metrics.max_drawdown),compare(x,y)]))}<p>例如從 2% 變成 3%，是增加 1 個百分點。報酬增減為正代表較高；回撤增減為負代表下跌幅度較小。交易少、單股及近似成交的限制仍需一起看。</p></section>
      <details class="panel"><summary>查看資料涵蓋天數</summary><div class="grid">${card('官方市場交易日',n(a.official_sessions),'2023–2024')}${card('舊版共同完整日',n(a.old_complete_common_sessions),'五股完整 54 棒')}${card('聯發科完整日',n(a.target_complete_sessions),'原始真實 K 棒')}${card('新版新增觀測日期',n(a.new_dates.length),'增加日期不等於增加成交')}</div></details>
      <section class="panel"><h2>曝險補充比較</h2>${table(['版本','平均曝險','報酬／平均曝險（描述值）'],rs.map(r=>[r.metrics.variant,pct(r.metrics.avg_exposure),r.metrics.avg_exposure?pct(r.metrics.return/r.metrics.avg_exposure):'—']))}<p>此比值只用來提醒資金投入差異，沒有重播相同曝險的交易策略，也不等於可交易報酬或相同風險績效。</p></section>
      <section class="panel"><h2>四組資產曲線</h2><canvas id="curve" class="curve"></canvas><p class="muted">A 青綠、B 藍、C 黃、D 紫。每組從 1,000,000 元開始，股利應收計入淨值但尚未發放前不能拿來下單。</p></section>
      <section class="panel"><h2>固定 Fold 配對（每邊 0.05%／共同覆蓋）</h2>${table(['Fold','版本','起日','迄日','報酬','交易','回撤','狀態'],D.folds.map(m=>[m.fold,esc(m.variant+' '+labels[m.variant]),m.start,m.end,pct(m.return),n(m.trade_count),pct(m.max_drawdown),m.status]))}</section>`;
    lineChart('curve',rs.map(r=>r.equity));
  }
  function compare(a,b) {
    const x=result(a).metrics,y=result(b).metrics;
    if(Math.min(x.trade_count,y.trade_count)<5)return '交易不足，僅供觀察';
    if(y.return>x.return&&y.max_drawdown<=x.max_drawdown)return '近似模型中報酬較高、回撤未增';
    if(y.return>x.return)return '報酬較高，但回撤增加';
    return '此近似模型未改善報酬';
  }
  function funnel() {
    const r=result(variant),m=r.metrics;
    content.innerHTML=`<div class="grid">${card('突破事件',n(m.candidate_events),'已含非交易時間及不合格候選')}${card('委託核准',n(r.funnel.at(-2).pass),'符合所有前置條件')}${card('實際首次買進',n(m.buys),'加碼另列')}${card('完整交易',n(m.trade_count),'空手到空手')}</div>
      <section class="panel"><h2>依序篩選過程</h2>${table(['階段','輸入','通過','阻擋','通過率','通過量'],r.funnel.map(f=>[esc(f.stage),n(f.input),n(f.pass),n(f.blocked),f.input?pct(f.pass/f.input):'—',`<div class="bar" style="width:${f.input?Math.max(0,100*f.pass/f.input):0}px"></div>`]))}<p>第一層以所有觀測 K 棒為分母，第二層再找首次突破；因此「非新突破」是等待狀態，不是已錯過的買進交易。各技術條件的順序只影響第一阻擋歸屬。</p></section>
      <div class="columns"><section class="panel"><h2>突破事件的第一阻擋原因</h2>${table(['原因','筆數'],Object.entries(r.blockers).filter(([k])=>k.startsWith('FIRST:')).sort((a,b)=>b[1]-a[1]).map(([k,v])=>[esc(k.slice(6)),n(v)]))}</section><section class="panel"><h2>突破事件的全部不符合條件</h2>${table(['條件','事件數'],Object.entries(r.blockers).filter(([k])=>k.startsWith('ALL:')).sort((a,b)=>b[1]-a[1]).map(([k,v])=>[esc(k.slice(4)),n(v)]))}<p class="muted">同一事件可不符多項，不可將此表相加當成獨立機會總數。</p></section></div>
      <section class="panel"><h2>ML 分類／覆蓋</h2>${table(['結果','突破事件數'],Object.entries(r.ml_counts).map(([k,v])=>[esc(k),n(v)]))}<p>A／B 不使用 ML；C／D 的 HOLD、SELL、MISSING、INVALID_OR_LATE 分列。共同樣本已按合法覆蓋限制，缺預測主要見全樣本。</p></section>
      <section class="panel"><h2>逐筆突破事件</h2><div class="toolbar"><input id="search" aria-label="搜尋事件" placeholder="搜尋日期、阻擋原因、ML 分類"><a href="${key(variant)}_events.csv">下載完整事件 CSV</a></div><div id="events"></div></section>`;
    function events() {
      const text=document.getElementById('search').value.toLowerCase();
      const rows=r.events.filter(e=>JSON.stringify(e).toLowerCase().includes(text));
      document.getElementById('events').innerHTML=table(['事件','訊號完成時間','收盤','ML','市場','第一阻擋','風險可買股','流動性可買股','核准股數','成交股數','新增日'],rows.slice(0,400).map(e=>[n(e.event_id),esc(e.time.slice(0,16)),n(e.close,2),esc(e.ml),e.market?'通過':'阻擋',esc(e.first_blocker),n(e.caps.risk),n(e.caps.liquidity),n(e.approved_quantity),n(e.filled_quantity),e.new_date?'是':'否']))+`<p class="muted">符合 ${n(rows.length)} 筆；畫面最多顯示 400 筆，完整資料見 CSV。</p>`;
    }
    document.getElementById('search').addEventListener('input',events);events();
  }
  function analysis() {
    const r=result(variant),m=r.metrics;
    content.innerHTML=`<div class="grid">${card('成本後報酬',pct(m.return),'淨損益 '+n(m.net_pnl,2)+' 元')}${card('勝率',pct(m.win_rate),'無交易則不計算')}${card('最大回撤',pct(m.max_drawdown),'5 分 K 收盤及成交後觀測淨值')}${card('完整交易／加碼',n(m.trade_count)+' / '+n(m.adds),'首次買進 '+n(m.buys))}</div>
      <section class="panel"><h2>5 分 K 線 · 買賣點</h2><p class="muted">原始未還原 K 線；滑鼠滾輪縮放、拖曳平移，或用下方日期與滑桿定位。買進 ▲、加碼 ◆、賣出 ▼；只標記實際模擬成交。</p><div class="chart-controls"><label>定位日期 <input type="date" id="date" min="2023-01-01" max="2024-12-31"></label><button id="latest">移至最新</button><label>顯示根數 <select id="count"><option>120</option><option selected>240</option><option>540</option><option>1080</option></select></label></div><canvas id="candles" aria-label="聯發科五分K線圖"></canvas><div class="chart-controls"><input type="range" id="position" min="0" aria-label="K線位置"><span id="range"></span></div><p id="hover" class="muted">移動滑鼠查看 OHLC。</p>${r.orders.length?'':`<div class="empty">本組沒有實際成交，沒有買賣點可標記。候選及拒單原因請查看 <a href="${variant}_funnel.html">篩選過程</a>。</div>`}</section>
      <section class="panel"><h2>資產曲線</h2><canvas id="curve" class="curve"></canvas><h3>回撤曲線</h3><canvas id="drawdown" class="curve"></canvas></section>
      <section class="panel"><h2>基本指標與成本</h2>${table(['指標','數值'],[['初始資金',n(m.initial_capital,2)],['期末淨值',n(m.final_equity,2)],['毛損益',n(m.gross_pnl,2)],['淨損益',n(m.net_pnl,2)],['已實現淨損益',n(m.closed_trade_net_pnl,2)],['未實現損益',n(m.unrealized_pnl,2)],['現金股利',n(m.cash_dividends,2)],['延後出場紀錄',n(m.deferred_exit_episodes)],['期末未平倉股數',n(r.open_position?.quantity||0)],['手續費',n(m.fees,2)],['交易稅',n(m.tax,2)],['滑價成本（已含於成交價）',n(m.slippage_cost,2)],['Profit Factor',n(m.profit_factor,2)],['Sharpe',n(m.sharpe,2)],['平均獲利',n(m.average_win,2)],['平均虧損',n(m.average_loss,2)],['平均持有5分K根數',n(m.average_holding_bars,2)],['平均曝險',pct(m.avg_exposure)],['最大曝險',pct(m.max_exposure)],['證據狀態',m.status]])}</section>
      <section class="panel"><h2>逐筆完整交易</h2>${r.trades.length?table(['編號','買進時間','賣出時間','股數','淨損益','出場原因'],r.trades.map(t=>[n(t.trade_id),esc(t.entry_time),esc(t.exit_time),n(t.quantity),n(t.net_pnl,2),esc(t.exit_reason)])):'<div class="empty">零筆交易；不能計算勝率或判定加碼／再進場效果。</div>'}<div class="links"><a href="${key(variant)}_trades.csv">交易 CSV</a><a href="${key(variant)}_orders.csv">成交 CSV</a><a href="${key(variant)}_equity.csv">逐棒淨值 CSV</a><a href="${variant}_funnel.html">查看未成交原因</a></div></section>
      <section class="panel"><h2>年度與新增日期</h2>${table(['年度','期末淨值','完整交易'],['2023','2024'].map(y=>{const curve=r.equity.filter(e=>e.date.startsWith(y));return [y,n(curve.at(-1)?.equity,2),n(r.trades.filter(t=>t.exit_day.startsWith(y)).length)];}))}<p>新增可用日期上的突破事件 ${n(m.new_date_events)} 筆；新增日期不等於新增交易。報酬與風險須由完整回放判讀。</p></section>`;
    candleChart(r);lineChart('curve',[r.equity]);lineChart('drawdown',[r.equity],true);
  }
  function canvasSetup(id) {
    const canvas=document.getElementById(id), rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
    canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);return [canvas,ctx,rect.width,rect.height];
  }
  function lineChart(id,series,dd=false) {
    const [,ctx,w,h]=canvasSetup(id),pad=65;
    ctx.clearRect(0,0,w,h);ctx.font='12px system-ui';
    const vals=series.map(s=>{let peak=D.config.initial_capital;return s.map(x=>{peak=Math.max(peak,x.equity);return dd?100*(1-x.equity/peak):x.equity;});});
    let lo=Math.min(...vals.flat()),hi=Math.max(...vals.flat());if(!Number.isFinite(lo))return;
    if(hi===lo){lo-=dd?1:1000;hi+=dd?1:1000;}
    for(let i=0;i<5;i++){const y=20+(h-55)*i/4;ctx.strokeStyle='#243349';ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(w-20,y);ctx.stroke();ctx.fillStyle='#9baec8';ctx.fillText(n(hi-(hi-lo)*i/4,dd?2:0)+(dd?'%':''),5,y+4);}
    const colors=['#7ad6cb','#80abff','#ffcf80','#ce9aff'];vals.forEach((v,j)=>{ctx.strokeStyle=colors[j];ctx.lineWidth=2;ctx.beginPath();v.forEach((p,i)=>{const x=pad+(w-pad-20)*i/Math.max(1,v.length-1),y=20+(h-55)*(hi-p)/(hi-lo);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();});
    ctx.fillStyle='#9baec8';ctx.fillText(series[0][0]?.date||'',pad,h-10);ctx.fillText(series[0].at(-1)?.date||'',w-105,h-10);
  }
  function candleChart(r) {
    const bars=D.bars;let count=240,start=Math.max(0,bars.length-count),drag=null;
    if(r.orders.length){const i=bars.findIndex(b=>b[0]===r.orders.at(-1).timestamp);if(i>=0)start=Math.max(0,i-count+20);}
    const slider=document.getElementById('position'),canvas=document.getElementById('candles');
    function draw(){
      start=Math.max(0,Math.min(Math.round(start),bars.length-count));slider.max=Math.max(0,bars.length-count);slider.value=start;
      const [,ctx,w,h]=canvasSetup('candles'),visible=bars.slice(start,start+count),pad=65,top=20,bottom=45;
      const lo=Math.min(...visible.map(b=>b[3])),hi=Math.max(...visible.map(b=>b[2]));const span=hi-lo||1,dx=(w-pad-20)/visible.length;
      const y=p=>top+(h-top-bottom)*(hi+span*.04-p)/(span*1.08);
      ctx.clearRect(0,0,w,h);ctx.font='12px system-ui';ctx.lineWidth=1;
      for(let j=0;j<5;j++){let p=lo+(hi-lo)*j/4;ctx.strokeStyle='#243349';ctx.beginPath();ctx.moveTo(pad,y(p));ctx.lineTo(w-15,y(p));ctx.stroke();ctx.fillStyle='#9baec8';ctx.fillText(n(p,1),8,y(p)+4);}
      visible.forEach((b,i)=>{const x=pad+(i+.5)*dx;ctx.strokeStyle=ctx.fillStyle=b[4]>=b[1]?'#ff797d':'#61d7ad';ctx.beginPath();ctx.moveTo(x,y(b[2]));ctx.lineTo(x,y(b[3]));ctx.stroke();ctx.fillRect(x-Math.max(.7,dx*.3),Math.min(y(b[1]),y(b[4])),Math.max(1,dx*.6),Math.max(1,Math.abs(y(b[1])-y(b[4]))));});
      r.orders.forEach(o=>{const i=visible.findIndex(b=>b[0]===o.timestamp);if(i<0)return;const x=pad+(i+.5)*dx,py=y(o.price);ctx.fillStyle=o.action==='SELL'?'#ffcf80':'#80abff';ctx.font='bold 17px system-ui';ctx.fillText(o.action==='SELL'?'▼':o.action==='ADD'?'◆':'▲',x-7,py+(o.action==='SELL'?-10:24));});
      ctx.fillStyle='#9baec8';ctx.font='12px system-ui';ctx.fillText(dt(visible[0][0]),pad,h-14);ctx.fillText(dt(visible.at(-1)[0]),Math.max(pad,w-160),h-14);
      document.getElementById('range').textContent=dt(visible[0][0])+' ～ '+dt(visible.at(-1)[0]);
    }
    slider.addEventListener('input',()=>{start=+slider.value;draw();});
    document.getElementById('count').addEventListener('change',e=>{count=+e.target.value;draw();});
    document.getElementById('latest').addEventListener('click',()=>{start=bars.length-count;draw();});
    document.getElementById('date').addEventListener('change',e=>{const i=bars.findIndex(b=>dt(b[0]).startsWith(e.target.value));if(i>=0){start=i;draw();}});
    canvas.addEventListener('wheel',e=>{e.preventDefault();const center=start+count/2;count=Math.max(40,Math.min(1500,Math.round(count*(e.deltaY>0?1.2:.8))));start=center-count/2;draw();},{passive:false});
    canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,start};canvas.setPointerCapture(e.pointerId);});
    canvas.addEventListener('pointerup',()=>drag=null);
    canvas.addEventListener('pointermove',e=>{const rect=canvas.getBoundingClientRect();if(drag){start=drag.start-(e.clientX-drag.x)*count/(rect.width-85);draw();}const i=Math.floor(start+(e.clientX-rect.left-65)*count/(rect.width-85));const b=bars[i];if(b)document.getElementById('hover').textContent=`${dt(b[0])}　開 ${n(b[1],2)}　高 ${n(b[2],2)}　低 ${n(b[3],2)}　收 ${n(b[4],2)}　成交 ${n(b[5])} 股`;});
    draw();
  }
  document.getElementById('slip').addEventListener('change',paint);
  document.getElementById('scope').addEventListener('change',paint);
  let timer;window.addEventListener('resize',()=>{clearTimeout(timer);timer=setTimeout(paint,150);});
  paint();
})();

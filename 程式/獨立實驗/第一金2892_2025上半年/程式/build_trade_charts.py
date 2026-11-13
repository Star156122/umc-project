"""Render existing frozen results only; never replay or modify a strategy."""
import json,sys,hashlib,zipfile
from pathlib import Path
H=Path(__file__).resolve().parents[1];ROOT=H.parents[1]
sys.path.insert(0,str(ROOT))
from trading_system.research_guard import assert_development_period,assert_timestamps
assert_development_period('2025-01-01','2025-06-30')
O=H/'結果'/json.loads((H/'結果/latest.json').read_text(encoding='utf8'))['folder'];OUT=O/'五組買賣點圖';OUT.mkdir(exist_ok=True)
source=O/'data.js';raw=source.read_text(encoding='utf8')
data=json.loads(raw.removeprefix('window.RESEARCH=').removesuffix(';'))
stocks=data['labels'];payload={}
for s,name in stocks.items():
 r=data['results'][s+'_5_full'];bars=data['results'][s+'_bars'];assert_timestamps(b[0] for b in bars)
 assert all('2025-01-01'<=o['time'][:10]<='2025-06-30' for o in r['orders'])
 payload[s]={'name':name,'bars':bars,'orders':r['orders'],'trades':r['trades'],'metrics':r['metrics']}
TEMPLATE='''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>五組股票｜5 分 K 買賣點圖</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#e3edf8;font:15px/1.7 system-ui,"Microsoft JhengHei",sans-serif}main{max-width:1500px;margin:auto;padding:28px}h1{font-size:29px}h2{margin:0;font-size:23px}p{margin:10px 0}a{color:#7edbd1}nav,.tools,.tradebuttons{display:flex;gap:9px;flex-wrap:wrap;align-items:center}nav{margin:20px 0}nav a,button{border:1px solid #3b526c;border-radius:7px;padding:7px 13px;background:#142338;color:#e3edf8;text-decoration:none;cursor:pointer;font:inherit}button:hover{border-color:#82d8ff}button:focus-visible{outline:2px solid #82d8ff}.panel{background:#121f30;border:1px solid #30445c;border-radius:15px;padding:21px;margin:24px 0}.note{color:#a7bad1;font-size:13px}.notice{border-left:3px solid #e6b66b;background:#30291c;padding:12px 18px}.tools{margin:15px 0}input{background:#0d1829;border:1px solid #3b526c;color:#e3edf8;padding:7px;border-radius:6px;font:inherit}canvas{width:100%;height:470px;display:block;touch-action:none;border-radius:8px;background:#0b1726}.buy{color:#6fc4ff}.sell{color:#ffc36e}.tip{min-height:30px;color:#c9e5ff;font-variant-numeric:tabular-nums;margin-top:8px}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:13px}td,th{text-align:left;border-bottom:1px solid #30445c;padding:10px}.tradebuttons{margin:12px 0}small{color:#adbed0}@media(max-width:600px){main{padding:12px}.panel{padding:12px}h1{font-size:24px}canvas{height:380px}}
</style><main><h1>第一金 2025 年 1–6 月｜5 分 K 買賣點圖</h1><p>第一金五組固定策略 · 買賣各 0.05% 滑價 · <span class="buy">▲ 買進</span>／<span class="sell">▼ 賣出</span>。每一根都是原始 5 分 K；藍色標記另註「加碼」。本版取消1%風險額度，現金40%及保留初始20%。</p><p class="notice">標記來自這次回測的模擬成交紀錄；沿用普通 K 線近似零股成交假設。未重新回測，也未更改交易結果。</p><nav id="nav"></nav><p>點「看第幾筆交易」可放大買賣附近；滾輪縮放、拖曳左右移動，游標查看開高低收。日期與時間均為台灣時間。</p><div id="charts"></div><p class="note">既有報告來源：20261114T024658。原始資料與績效保持不變；圖中交易編號對應下方明細。</p></main><script>
const DATA=__DATA__,ONLY=__ONLY__;
const esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=t=>new Date(t*1000+28800000).toISOString().slice(0,16).replace('T',' '),num=(x,d=2)=>Number(x).toLocaleString('zh-TW',{minimumFractionDigits:d,maximumFractionDigits:d});
document.getElementById('nav').innerHTML='<a href="index.html">五組一起看</a>'+Object.entries(DATA).map(([s,r])=>`<a href="${s}.html">${r.name} ${s}</a>`).join('');
window.CHARTS={};
Object.entries(DATA).filter(([s])=>!ONLY||s===ONLY).forEach(([s,r])=>{
const box=document.createElement('section');box.className='panel';box.innerHTML=`<h2>${esc(r.name)} ${s}</h2><p>成本後報酬率 <b>${num(r.metrics.return*100)}%</b> · ${r.trades.length} 筆交易 · 淨損益 ${num(r.metrics.net_pnl)} 元</p><div class="tools"><button data-do="all">完整六個月</button><button data-do="in">＋ 放大</button><button data-do="out">－ 縮小</button><label>開始 <input class="from" type="date" value="2025-01-01" min="2025-01-01" max="2025-06-30"></label><label>結束 <input class="to" type="date" value="2025-06-30" min="2025-01-01" max="2025-06-30"></label><button data-do="dates">查看日期</button></div><div class="tradebuttons">${r.trades.map((t,i)=>`<button data-trade="${i}">看第 ${i+1} 筆交易（${t.entry_day.slice(5)} 買進）</button>`).join('')}</div><canvas aria-label="${esc(r.name)}五分K線與買賣點"></canvas><div class="tip">游標移到 K 線上可查看數值。</div><p class="note count"></p><div class="scroll"><table><thead><tr><th>交易</th><th>買進時間</th><th>首次買進價</th><th>賣出時間</th><th>賣出價</th><th>合計股數</th><th>淨損益</th></tr></thead><tbody>${r.trades.map((t,i)=>`<tr><td>${i+1}</td><td>${esc(t.entry_time.slice(0,16).replace('T',' '))}</td><td>${num(t.entry_price,3)}</td><td>${esc(t.exit_time.slice(0,16).replace('T',' '))}</td><td>${num(t.exit_price,3)}</td><td>${num(t.quantity,0)}</td><td>${num(t.net_pnl)}</td></tr>`).join('')}</tbody></table></div><h3>每筆買進、加碼與賣出明細</h3><div class="scroll"><table><thead><tr><th>時間</th><th>動作</th><th>股數</th><th>成交價</th><th>手續費</th></tr></thead><tbody>${r.orders.map(o=>`<tr><td>${time(o.timestamp)}</td><td>${o.action==='ADD'?'加碼':o.action==='BUY'?'首次買進':'賣出'}</td><td>${num(o.quantity,0)}</td><td>${num(o.price,3)}</td><td>${num(o.fee,0)}</td></tr>`).join('')}</tbody></table></div><p class="note">成交價已含滑價，可能略高於或低於 K 線的原始價格。沒有成交的訊號不畫成買賣點。</p>`;
document.getElementById('charts').appendChild(box);
const c=box.querySelector('canvas'),tip=box.querySelector('.tip'),bars=r.bars;let start=0,end=bars.length,hover=null,drag=null;
const indices=r.orders.map(o=>{let i=bars.findIndex(b=>b[0]>=o.timestamp);if(i<0)i=bars.length-1;return {...o,index:i}});
function clamp(){const size=Math.min(bars.length,Math.max(20,Math.round(end-start)));start=Math.max(0,Math.min(bars.length-size,Math.round(start)));end=start+size;}
function draw(){clamp();const w=Math.max(280,c.clientWidth),h=c.clientHeight,dpr=devicePixelRatio||1;c.width=w*dpr;c.height=h*dpr;const ctx=c.getContext('2d');ctx.scale(dpr,dpr);ctx.fillStyle='#0b1726';ctx.fillRect(0,0,w,h);
const b=bars.slice(start,end),os=indices.filter(o=>o.index>=start&&o.index<end);let low=Math.min(...b.map(z=>z[3]),...os.map(o=>o.price)),high=Math.max(...b.map(z=>z[2]),...os.map(o=>o.price)),pad=(high-low)*.18||1;low-=pad;high+=pad;
const x=i=>64+(w-85)*(i-start+.5)/(end-start),y=p=>h-38-(p-low)/(high-low)*(h-75);
ctx.font='12px sans-serif';for(let j=0;j<6;j++){let p=low+(high-low)*j/5;ctx.fillStyle='#b9cce0';ctx.fillText(p.toFixed(1),3,y(p)+4);ctx.strokeStyle='#263b51';ctx.beginPath();ctx.moveTo(61,y(p));ctx.lineTo(w-15,y(p));ctx.stroke()}
b.forEach((z,j)=>{const i=start+j;ctx.fillStyle=ctx.strokeStyle=z[4]>=z[1]?'#f47c83':'#55c4a4';ctx.beginPath();ctx.moveTo(x(i),y(z[2]));ctx.lineTo(x(i),y(z[3]));ctx.stroke();const cw=Math.max(1,Math.min(13,(w-85)/(end-start)*.65));ctx.fillRect(x(i)-cw/2,Math.min(y(z[1]),y(z[4])),cw,Math.max(1,Math.abs(y(z[1])-y(z[4]))))});
os.forEach(o=>{const buy=o.action!=='SELL',px=x(o.index),py=y(o.price),color=buy?'#70c5ff':'#ffc36e';ctx.fillStyle=color;ctx.beginPath();ctx.moveTo(px,py);ctx.lineTo(px-6,py+(buy?12:-12));ctx.lineTo(px+6,py+(buy?12:-12));ctx.closePath();ctx.fill();const label=(o.action==='ADD'?'加碼':buy?'買進':'賣出')+' '+o.trade_id,tx=Math.max(65,Math.min(w-90,px+9)),ty=py+(buy?29:-20);ctx.font='bold 13px sans-serif';ctx.fillStyle='#0b1726';ctx.fillRect(tx-3,ty-14,ctx.measureText(label).width+6,19);ctx.fillStyle=color;ctx.fillText(label,tx,ty)});
if(hover!==null&&hover>=start&&hover<end){ctx.strokeStyle='#a2b3c6';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(x(hover),20);ctx.lineTo(x(hover),h-35);ctx.stroke();ctx.setLineDash([])}
ctx.font='12px sans-serif';ctx.fillStyle='#b9cce0';for(let j=0;j<4;j++){let i=start+Math.floor((end-start-1)*j/3);ctx.fillText(time(bars[i][0]).slice(0,10),Math.max(64,Math.min(w-95,x(i)-34)),h-12)}
box.querySelector('.count').textContent=`顯示 ${time(b[0][0])} ～ ${time(b.at(-1)[0])}｜${b.length} 根 5 分 K｜${os.filter(o=>o.action!=='SELL').length} 個買進、${os.filter(o=>o.action==='SELL').length} 個賣出標記。紅 K 為上漲、綠 K 為下跌。`;
window.CHARTS[s]={start,end,bars:bars.length,visibleOrders:os.length,orders:r.orders.length};
}
function zoom(factor,anchor=.5){const old=end-start,newsize=Math.max(20,Math.min(bars.length,old*factor)),center=start+old*anchor;start=center-newsize*anchor;end=start+newsize;draw()}
box.querySelectorAll('[data-do]').forEach(btn=>btn.onclick=()=>{const k=btn.dataset.do;if(k==='all'){start=0;end=bars.length}if(k==='in')return zoom(.5);if(k==='out')return zoom(2);if(k==='dates'){let f=box.querySelector('.from').value,t=box.querySelector('.to').value;const chosen=bars.map((b,i)=>[time(b[0]).slice(0,10),i]).filter(([d])=>d>=f&&d<=t);if(!chosen.length){tip.textContent='這段日期沒有可用的 K 線，請重新選擇。';return}start=chosen[0][1];end=chosen.at(-1)[1]+1}draw()});
box.querySelectorAll('[data-trade]').forEach(btn=>btn.onclick=()=>{const t=r.trades[Number(btn.dataset.trade)],os=indices.filter(o=>o.trade_id===t.trade_id);start=Math.min(...os.map(o=>o.index))-30;end=Math.max(...os.map(o=>o.index))+31;const width=end-start;if(start<0){start=0;end=Math.min(bars.length,width)}draw();c.scrollIntoView({behavior:'smooth',block:'center'})});
c.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY<0?.8:1.25,Math.max(0,Math.min(1,(e.offsetX-64)/(c.clientWidth-85))))},{passive:false});
c.onpointerdown=e=>{drag={x:e.clientX,start,end};c.setPointerCapture(e.pointerId)};
c.onpointerup=()=>drag=null;c.onpointercancel=()=>drag=null;
c.onpointermove=e=>{if(drag){let delta=(e.clientX-drag.x)/(c.clientWidth-85)*(drag.end-drag.start);start=drag.start-delta;end=drag.end-delta;draw();return}hover=Math.max(start,Math.min(end-1,Math.floor(start+(e.offsetX-64)/(c.clientWidth-85)*(end-start))));const z=bars[hover],os=indices.filter(o=>o.index===hover);tip.textContent=`${time(z[0])}　開 ${num(z[1])}　高 ${num(z[2])}　低 ${num(z[3])}　收 ${num(z[4])}`+os.map(o=>`　${o.action==='SELL'?'賣出':'買進'} ${num(o.price,3)} 元／${num(o.quantity,0)} 股`).join('');draw()};
c.onpointerleave=()=>{hover=null;draw()};window.addEventListener('resize',draw);draw();
});
</script></html>'''
encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('</','<\\/')
for s in ['',*stocks]:
 page=TEMPLATE.replace('__DATA__',encoded).replace('__ONLY__',json.dumps(s))
 (OUT/((s or 'index')+'.html')).write_text(page,encoding='utf8')
manifest={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_result':'20261114T024658','strategy_replayed':False,'source_results_modified':False,'stocks':{s:{'bars':len(r['bars']),'buy_markers':sum(o['action'] in ['BUY','ADD'] for o in r['orders']),'sell_markers':sum(o['action']=='SELL' for o in r['orders'])} for s,r in payload.items()}}
(OUT/'來源稽核.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
with zipfile.ZipFile(OUT/'五組5分K買賣點圖.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in OUT.glob('*.html'):z.write(p,p.name)
 z.write(OUT/'來源稽核.json','來源稽核.json')
print(json.dumps(manifest,ensure_ascii=False));print(OUT)

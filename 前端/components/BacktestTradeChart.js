import React,{useMemo,useState} from 'react';
import {Platform,Pressable,Text,View} from 'react-native';
import {strategyInfo} from '../src/features/multiBacktestModel';
import {colors,radius,space,type,ui} from '../styles/theme';
export function tradeChartDocument(candles,trades,daily=true){
 const data=JSON.stringify({candles,trades,daily}).replace(/</g,'\\u003c');
 return `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><style>html,body{height:100%}body{margin:0;display:flex;flex-direction:column;background:#0b121c;color:#9fb0c2;font:13px 'Noto Sans TC','Microsoft JhengHei',system-ui,sans-serif}#bar{display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;padding:8px 12px;box-sizing:border-box;border-bottom:1px solid #1a2533;font-variant-numeric:tabular-nums}#legend{margin-left:auto}#quote{order:3;flex-basis:100%;line-height:1.6}#quote b{color:#dbe4ee;font-weight:600}.up{color:#f0474f}.down{color:#1fbf6a}.buy{color:#ff5c8a}.sell{color:#3ee08f}#legend{color:#71849a;font-size:12px}button{border:1px solid #34485e;background:#172332;color:#8db9ff;border-radius:6px;padding:3px 10px;cursor:pointer;font:inherit;font-size:12px}#chart{flex:1;min-height:0}</style><div id="bar"><span id="quote">載入圖表…</span><span id="legend"><span class="buy">▲ 買入</span>　<span class="sell">▼ 賣出</span></span><button id="reset">重設視野</button></div><div id="chart"></div><script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js" onerror="document.getElementById('quote').textContent='圖表載入失敗，請檢查網路。'"></script><script>
 const data=${data};const stamp=v=>typeof v==='number'?v:Date.parse(/[zZ]$|[+-]\\d{2}:?\\d{2}$/.test(v)?v:String(v).replace(' ','T')+'+08:00')/1000;
 const day=t=>new Date((t+28800)*1000).toISOString().slice(0,10);
 const num=(v,d=2)=>Number(v).toLocaleString('zh-TW',{maximumFractionDigits:d});
 const when=t=>{const p=new Date((t+28800)*1000).toISOString();return p.slice(0,10).replace(/-/g,'/')+(data.daily?'':' '+p.slice(11,16));};
 let bars=data.candles.filter(p=>['time','open','high','low','close'].every(k=>typeof p[k]==='number'&&Number.isFinite(p[k]))).sort((a,b)=>a.time-b.time);
 if(data.daily){const grouped=new Map();for(const p of bars){const key=day(p.time),prev=grouped.get(key);if(prev){prev.high=Math.max(prev.high,p.high);prev.low=Math.min(prev.low,p.low);prev.close=p.close;}else grouped.set(key,{...p,time:Date.parse(key+'T00:00:00+08:00')/1000});}bars=[...grouped.values()];}
 const q=document.getElementById('quote');
 if(!bars.length)q.textContent='此期間沒有 K 線資料';
 else if(window.LightweightCharts){const chart=LightweightCharts.createChart(document.getElementById('chart'),{autoSize:true,layout:{background:{color:'#0b121c'},textColor:'#9fb0c2'},grid:{vertLines:{color:'#141e2b'},horzLines:{color:'#141e2b'}},rightPriceScale:{borderColor:'#2c3b4d',scaleMargins:{top:.08,bottom:.08}},timeScale:{timeVisible:!data.daily,secondsVisible:false,borderColor:'#2c3b4d',rightOffset:3,tickMarkFormatter:(t,kind)=>{const d=new Date((t+28800)*1000);return kind===0?d.getUTCFullYear()+'年':kind===1?(d.getUTCMonth()+1)+'月':kind===2?(d.getUTCMonth()+1)+'/'+d.getUTCDate():d.toISOString().slice(11,16);}},localization:{locale:'zh-TW',timeFormatter:when,priceFormatter:p=>num(p)},crosshair:{mode:0}});
 const s=chart.addCandlestickSeries({upColor:'#f0474f',downColor:'#1fbf6a',wickUpColor:'#f0474f',wickDownColor:'#1fbf6a',borderVisible:false});s.setData(bars);
 const markers=[],tradesAt=new Map();for(const t of data.trades){const at=stamp(t.chart_timestamp??t.datetime??t.timestamp);if(!Number.isFinite(at))continue;let lo=0,hi=bars.length;while(lo<hi){const mid=(lo+hi)>>1;if(bars[mid].time<=at)lo=mid+1;else hi=mid;}const bar=bars[lo-1];if(!bar||at>=bar.time+(data.daily?86400:300))continue;const buy=String(t.action).toLowerCase()==='buy',sell=String(t.action).toLowerCase()==='sell';if(!buy&&!sell)continue;if(!tradesAt.has(bar.time))tradesAt.set(bar.time,[]);tradesAt.get(bar.time).push({buy,price:Number(t.price),qty:Number(t.quantity),at});markers.push({time:bar.time,position:buy?'belowBar':'aboveBar',color:buy?'#ff5c8a':'#3ee08f',shape:buy?'arrowUp':'arrowDown',size:1.6,label:buy?'買入':'賣出',price:Number(t.price)});}
 markers.sort((a,b)=>a.time-b.time);
 // 看大範圍時只寫「買入／賣出」避免文字重疊；放大到約 70 根 K 棒以內才加上成交價。
 let withPrice=null;const paint=detail=>{if(detail===withPrice)return;withPrice=detail;s.setMarkers(markers.map(({label,price,...m})=>({...m,text:label+(detail&&Number.isFinite(price)?' '+num(price):'')})));};
 const zoomCheck=r=>paint(!!r&&r.to-r.from<=70);paint(false);chart.timeScale().subscribeVisibleLogicalRangeChange(zoomCheck);
 const index=new Map(bars.map((b,i)=>[b.time,i]));const total=markers.length;
 const show=time=>{const i=index.get(time);if(i===undefined)return;const b=bars[i],prev=bars[i-1],chg=prev?(b.close-prev.close)/prev.close*100:null,cls=chg==null?'':chg>=0?'up':'down';
  const tr=(tradesAt.get(time)||[]).map(x=>'<span class="'+(x.buy?'buy':'sell')+'">'+(x.buy?'買入':'賣出')+(Number.isFinite(x.qty)?' '+num(x.qty,0)+' 股':'')+' @ '+num(x.price)+(data.daily?'（'+new Date((x.at+28800)*1000).toISOString().slice(11,16)+'）':'')+'</span>').join('　');
  q.innerHTML='<b>'+when(time)+'</b>　開 '+num(b.open)+'　高 '+num(b.high)+'　低 '+num(b.low)+'　收 <span class="'+cls+'">'+num(b.close)+(chg==null?'':' ('+(chg>=0?'+':'')+chg.toFixed(2)+'%)')+'</span>'+(tr?'　｜　'+tr:'');};
 const idle=()=>{show(bars[bars.length-1].time);q.innerHTML+='　<span style="color:#71849a">｜ 共 '+total+' 筆成交，滾輪放大可顯示成交價，游標移到箭頭看明細</span>';};
 idle();chart.subscribeCrosshairMove(e=>{if(e.time!==undefined&&index.has(e.time))show(e.time);else idle();});
 const fit=()=>chart.timeScale().fitContent();fit();if(typeof requestAnimationFrame==='function')requestAnimationFrame(()=>requestAnimationFrame(fit));document.getElementById('reset').onclick=fit;}
 </script></html>`;
}
export default function BacktestTradeChart({result}){
 const [daily,setDaily]=useState(true);
 const [selected,setSelected]=useState(result.strategies[0]?.strategy_id);
 const row=result.strategies.find(r=>r.strategy_id===selected)||result.strategies[0];
 const candles=result.market_chart?.candles||[];
 const html=useMemo(()=>tradeChartDocument(candles,row?.details?.trades||row?.trades||[],daily),[candles,row,daily]);
 const text=type.small;
 return <View style={ui.card}>
 <View style={{gap:space.xxs}}><Text style={ui.eyebrow}>TRADE REPLAY</Text><Text style={ui.title}>策略交易回放</Text></View>
 <Text style={text}>{result.stock_code} · {result.backtest_period.start} ～ {result.backtest_period.end} · {daily?'日 K':'5 分 K'}</Text>
 <View style={{flexDirection:'row',alignSelf:'flex-start',padding:3,gap:3,borderRadius:radius.md,backgroundColor:colors.surfaceSunken,borderWidth:1,borderColor:colors.borderSoft}}>{[[true,'日 K / 期間總覽'],[false,'5 分 K / 交易細節']].map(([value,label])=><Pressable key={label} accessibilityRole="button" onPress={()=>setDaily(value)} style={{paddingVertical:7,paddingHorizontal:space.sm,borderRadius:radius.sm,backgroundColor:daily===value?colors.surfaceRaised:'transparent'}}><Text style={[ui.small,{color:daily===value?colors.text:colors.textMuted,fontWeight:daily===value?'700':'400'}]}>{label}</Text></Pressable>)}</View>
 <View style={ui.wrap}>{result.strategies.map(r=>{const on=r.strategy_id===row?.strategy_id,c=strategyInfo(r.strategy_id).color;return <Pressable key={r.strategy_id} accessibilityRole="button" accessibilityState={{selected:on}} onPress={()=>setSelected(r.strategy_id)} style={[ui.chip,{flexDirection:'row',alignItems:'center',gap:6},on&&{borderColor:c,backgroundColor:colors.surfacePressed}]}><View style={{width:8,height:8,borderRadius:4,backgroundColor:c}}/><Text style={[ui.chipText,{color:on?colors.text:colors.textMuted,fontWeight:on?'700':'400'}]}>{strategyInfo(r.strategy_id).name}</Text></Pressable>;})}</View>
 {candles.length&&Platform.OS==='web'?React.createElement('iframe',{title:'回測期間 K 線與買賣點',srcDoc:html,sandbox:'allow-scripts',style:{width:'100%',height:484,border:'1px solid '+colors.borderSoft,borderRadius:radius.md,background:colors.chartBackground}}):<Text style={text}>{result.market_chart?.error||'此份舊報告未保存 K 線。重新執行回測後，將保存同期間 K 線與成交標記。'}</Text>}
 <Text style={ui.caption}>標記來自所選策略的實際回測成交紀錄；無交易時不顯示標記。K 線採用回測資料，日 K 由同日 5 分 K 彙整；績效不因圖表切換而改變。</Text>
 </View>;
}

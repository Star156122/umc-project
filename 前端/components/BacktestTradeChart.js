import React,{useMemo,useState} from 'react';
import {Platform,Pressable,Text,View} from 'react-native';
import {strategyInfo} from '../src/features/multiBacktestModel';
export function tradeChartDocument(candles,trades,daily=true){
 const data=JSON.stringify({candles,trades,daily}).replace(/</g,'\\u003c');
 return `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><style>body{margin:0;background:#080b10;color:#cbd5e1;font:14px Arial}#chart{height:430px}#info{padding:12px;min-height:40px;line-height:1.7}</style><div id="chart"></div><div id="info">紅色向上箭頭：買入；綠色向下箭頭：賣出。拖曳與滾輪縮放，移動游標看開高低收。</div><script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js" onerror="document.getElementById('info').textContent='圖表載入失敗，請檢查網路。'"></script><script>
 const data=${data};const stamp=v=>typeof v==='number'?v:Date.parse(/[zZ]$|[+-]\\d{2}:?\\d{2}$/.test(v)?v:String(v).replace(' ','T')+'+08:00')/1000;
 const day=t=>new Date((t+28800)*1000).toISOString().slice(0,10);
 let bars=data.candles.filter(p=>['time','open','high','low','close'].every(k=>typeof p[k]==='number'&&Number.isFinite(p[k]))).sort((a,b)=>a.time-b.time);
 if(data.daily){const grouped=new Map();for(const p of bars){const key=day(p.time),prev=grouped.get(key);if(prev){prev.high=Math.max(prev.high,p.high);prev.low=Math.min(prev.low,p.low);prev.close=p.close;}else grouped.set(key,{...p,time:Date.parse(key+'T00:00:00+08:00')/1000});}bars=[...grouped.values()];}
 if(window.LightweightCharts&&bars.length){const chart=LightweightCharts.createChart(document.getElementById('chart'),{autoSize:true,layout:{background:{color:'#080b10'},textColor:'#a5b7ca'},grid:{vertLines:{color:'#172332'},horzLines:{color:'#172332'}},timeScale:{timeVisible:true},localization:{locale:'zh-TW',timeFormatter:t=>new Date(t*1000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'})}});const s=chart.addCandlestickSeries({upColor:'#ff5264',downColor:'#32d399',wickUpColor:'#ff5264',wickDownColor:'#32d399',borderVisible:false});s.setData(bars);
 const markers=[];for(const t of data.trades){const at=stamp(t.datetime??t.timestamp);if(!Number.isFinite(at))continue;let lo=0,hi=bars.length;while(lo<hi){const mid=(lo+hi)>>1;if(bars[mid].time<=at)lo=mid+1;else hi=mid;}const bar=bars[lo-1];if(!bar||at>=bar.time+(data.daily?86400:300))continue;const buy=String(t.action).toLowerCase()==='buy',sell=String(t.action).toLowerCase()==='sell';if(!buy&&!sell)continue;markers.push({time:bar.time,position:buy?'belowBar':'aboveBar',color:buy?'#ff5264':'#32d399',shape:buy?'arrowUp':'arrowDown',text:(buy?'買入 ':'賣出 ')+t.price});}s.setMarkers(markers.sort((a,b)=>a.time-b.time));chart.timeScale().fitContent();chart.subscribeCrosshairMove(e=>{const p=e.seriesData.get(s);if(p)document.getElementById('info').textContent=new Date(e.time*1000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'})+'　開 '+p.open+'　高 '+p.high+'　低 '+p.low+'　收 '+p.close;});}
 </script></html>`;
}
export default function BacktestTradeChart({result}){
 const [daily,setDaily]=useState(true);
 const [selected,setSelected]=useState(result.strategies[0]?.strategy_id);
 const row=result.strategies.find(r=>r.strategy_id===selected)||result.strategies[0];
 const candles=result.market_chart?.candles||[];
 const html=useMemo(()=>tradeChartDocument(candles,row?.details?.trades||row?.trades||[],daily),[candles,row,daily]);
 const text={color:'#c9d8e8',lineHeight:24};
 return <View style={{padding:22,borderRadius:18,borderWidth:1,borderColor:'#294052',backgroundColor:'#0d1c29',gap:14}}>
 <Text style={{color:'#76b8ff',fontSize:12,letterSpacing:2}}>TRADE REPLAY</Text><Text style={{color:'#f8fafc',fontSize:25,fontWeight:'800'}}>策略交易回放</Text>
 <Text style={text}>{result.stock_code} · {result.backtest_period.start} ～ {result.backtest_period.end} · {daily?'日 K':'5 分 K'}</Text>
 <View style={{flexDirection:'row',flexWrap:'wrap',gap:12}}>{[[true,'日 K / 期間總覽'],[false,'5 分 K / 交易細節']].map(([value,label])=><Pressable key={label} accessibilityRole="button" onPress={()=>setDaily(value)} style={{padding:10,backgroundColor:daily===value?'#204775':'#172b3e',borderRadius:8}}><Text style={text}>{label}</Text></Pressable>)}</View>
 <View style={{flexDirection:'row',flexWrap:'wrap',gap:8}}>{result.strategies.map(r=><Pressable key={r.strategy_id} accessibilityRole="button" accessibilityState={{selected:r.strategy_id===row?.strategy_id}} onPress={()=>setSelected(r.strategy_id)} style={{padding:12,borderRadius:10,backgroundColor:r.strategy_id===row?.strategy_id?'#204775':'#172b3e'}}><Text style={{color:strategyInfo(r.strategy_id).color}}>{strategyInfo(r.strategy_id).name}</Text></Pressable>)}</View>
 {candles.length&&Platform.OS==='web'?React.createElement('iframe',{title:'回測期間 K 線與買賣點',srcDoc:html,sandbox:'allow-scripts',style:{width:'100%',height:510,border:0,borderRadius:12}}):<Text style={text}>{result.market_chart?.error||'此份舊報告未保存 K 線。重新執行回測後，將保存同期間 K 線與成交標記。'}</Text>}
 <Text style={text}>標記來自所選策略的實際回測成交紀錄；無交易時不顯示標記。K 線採用回測資料，日 K 由同日 5 分 K 彙整；績效不因圖表切換而改變。</Text>
 </View>;
}

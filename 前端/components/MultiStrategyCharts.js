import React,{useMemo,useState} from 'react';
import {Platform,Pressable,Text,View} from 'react-native';
import {formatMetric,numeric,strategyInfo} from '../src/features/multiBacktestModel';
export function equityDocument(rows) {
 const data=JSON.stringify(rows.map(r=>({id:r.strategy_id,...strategyInfo(r.strategy_id),points:r.equity_curve||[]}))).replace(/</g,'\\u003c');
 return `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><style>html,body{margin:0;background:#080d15;color:#e2e8f0;font:13px Arial,"Microsoft JhengHei"}#legend{display:flex;gap:14px;flex-wrap:wrap;padding:12px}label{cursor:pointer}#chart{height:350px}#quote{padding:12px;line-height:1.8;min-height:48px}button{color:#dbeafe;background:#1e3349;border:0;border-radius:6px;padding:7px}</style><div id="legend"></div><div id="chart"></div><div id="quote">移動游標查看每個時間點的資產。可拖曳、滾輪縮放。</div><script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js" onerror="document.getElementById('quote').textContent='圖表載入失敗；請檢查網路後重新載入。績效數字仍可閱讀。'"></script><script>
 const data=${data};const valid=v=>typeof v==='number'&&Number.isFinite(v);
 const stamp=v=>{const s=String(v);return Date.parse(/[zZ]$|[+-]\\d{2}:?\\d{2}$/.test(s)?s:s.replace(' ','T')+'+08:00')/1000;};
 if(window.LightweightCharts){
 const chart=LightweightCharts.createChart(document.getElementById('chart'),{autoSize:true,layout:{background:{color:'#080d15'},textColor:'#aabbd0'},grid:{vertLines:{color:'#1e293b'},horzLines:{color:'#1e293b'}},rightPriceScale:{borderColor:'#334155'},timeScale:{timeVisible:true,borderColor:'#334155'},localization:{locale:'zh-TW',timeFormatter:t=>new Date(t*1000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'})}});
 const entries=[];
 for(const row of data){const series=chart.addLineSeries({title:row.name,color:row.color,lineWidth:2,priceLineVisible:false});const values=[...new Map(row.points.filter(p=>valid(p.equity)&&Number.isFinite(stamp(p.timestamp))).map(p=>[stamp(p.timestamp),{time:stamp(p.timestamp),value:p.equity}])).values()].sort((a,b)=>a.time-b.time);series.setData(values);const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=true;check.onchange=()=>series.applyOptions({visible:check.checked});label.append(check,document.createTextNode(row.name+(values.length?'':'（無曲線）')));label.style.color=row.color;document.getElementById('legend').append(label);entries.push({row,series,check});}
 const reset=document.createElement('button');reset.textContent='重設視野';reset.onclick=()=>chart.timeScale().fitContent();document.getElementById('legend').append(reset);
 chart.subscribeCrosshairMove(e=>{if(!e.time)return;const parts=[new Date(e.time*1000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'})];for(const {row,series,check} of entries){const v=e.seriesData.get(series)?.value;if(check.checked&&valid(v))parts.push(row.name+'：'+v.toLocaleString('zh-TW',{maximumFractionDigits:2})+' 元');}document.getElementById('quote').textContent=parts.join('　｜　');});chart.timeScale().fitContent();
 }</script></html>`;
}
export default function MultiStrategyCharts({rows}) {
 const [retry,setRetry]=useState(0),[metric,setMetric]=useState('total_return_pct');
 const html=useMemo(()=>equityDocument(rows),[rows]);
 const options=[['total_return_pct','總報酬率','%'],['max_drawdown_pct','最大回撤','%'],['win_rate_pct','勝率','%'],['trade_count','交易次數','筆']];
 const selected=options.find(o=>o[0]===metric);
 const max=Math.max(1,...rows.map(r=>numeric(r.summary?.[metric])?Math.abs(r.summary[metric]):0));
 return <View style={{gap:18}}>
  <Text style={{color:'#f8fafc',fontSize:21,fontWeight:'700'}}>資金淨值曲線</Text>
  <Text style={{color:'#9eb0bf'}}>勾選圖例可顯示或隱藏曲線，數值直接使用後端資金曲線。</Text>
  {Platform.OS==='web'?React.createElement('iframe',{key:retry,title:'多策略資金淨值比較',srcDoc:html,sandbox:'allow-scripts',referrerPolicy:'no-referrer',style:{width:'100%',height:530,border:'1px solid #294052',borderRadius:14,background:'#080d15'}}):<Text style={{color:'#9eb0bf'}}>互動資金曲線請使用手機或電腦瀏覽器查看。</Text>}
  <Pressable onPress={()=>setRetry(x=>x+1)}><Text style={{color:'#73b9ff'}}>重新載入曲線圖</Text></Pressable>
  <View style={{flexDirection:'row',flexWrap:'wrap',gap:8}}>{options.map(o=><Pressable accessibilityRole="button" accessibilityState={{selected:metric===o[0]}} key={o[0]} onPress={()=>setMetric(o[0])} style={{padding:10,borderRadius:8,backgroundColor:metric===o[0]?'#204775':'#142534'}}><Text style={{color:'#f8fafc'}}>{o[1]}比較</Text></Pressable>)}</View>
  <Text style={{color:'#9eb0bf'}}>中線為 0；左側為負值，右側為正值。</Text>
  {rows.map(row=>{const info=strategyInfo(row.strategy_id),v=row.summary?.[metric],size=numeric(v)?Math.abs(v)/max*48:0;return <View key={row.strategy_id} style={{gap:7}}><View style={{flexDirection:'row',justifyContent:'space-between'}}><Text style={{color:info.color}}>{info.name}</Text><Text style={{color:'#f8fafc'}}>{formatMetric(v,selected[2])}</Text></View><View accessibilityLabel={`${info.name} ${selected[1]} ${formatMetric(v,selected[2])}`} style={{height:22,backgroundColor:'#142534',borderRadius:4}}><View style={{position:'absolute',left:'50%',height:'100%',width:1,backgroundColor:'#6b7c8d'}}/>{numeric(v)&&<View style={{position:'absolute',left:`${v<0?50-size:50}%`,width:`${size}%`,height:22,borderRadius:3,backgroundColor:info.color}}/>}</View></View>;})}
 </View>;
}

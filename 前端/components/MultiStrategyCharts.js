import React,{useMemo,useState} from 'react';
import {Platform,Pressable,Text,View} from 'react-native';
import {formatMetric,numeric,strategyInfo} from '../src/features/multiBacktestModel';
import {colors,radius,space,type,ui} from '../styles/theme';
export function equityDocument(rows) {
 const data=JSON.stringify(rows.map(r=>({id:r.strategy_id,...strategyInfo(r.strategy_id),points:r.equity_curve||[]}))).replace(/</g,'\\u003c');
 return `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><style>html,body{margin:0;background:#0b121c;color:#dbe4ee;font:13px 'Noto Sans TC','Microsoft JhengHei',system-ui,sans-serif}#legend{display:flex;gap:8px 16px;flex-wrap:wrap;align-items:center;padding:12px 14px;border-bottom:1px solid #1a2533}label{cursor:pointer;display:flex;gap:6px;align-items:center;font-weight:600}input{margin:0}#chart{height:350px}#quote{padding:10px 14px;line-height:1.8;min-height:48px;color:#9fb0c2;border-top:1px solid #1a2533;font-variant-numeric:tabular-nums}button{margin-left:auto;color:#8db9ff;background:#172332;border:1px solid #34485e;border-radius:6px;padding:5px 10px;font:inherit;cursor:pointer}</style><div id="legend"></div><div id="chart"></div><div id="quote">移動游標查看每個時間點的資產。可拖曳、滾輪縮放。</div><script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js" onerror="document.getElementById('quote').textContent='圖表載入失敗；請檢查網路後重新載入。績效數字仍可閱讀。'"></script><script>
 const data=${data};const valid=v=>typeof v==='number'&&Number.isFinite(v);
 const stamp=v=>{const s=String(v);return Date.parse(/[zZ]$|[+-]\\d{2}:?\\d{2}$/.test(s)?s:s.replace(' ','T')+'+08:00')/1000;};
 if(window.LightweightCharts){
 const chart=LightweightCharts.createChart(document.getElementById('chart'),{autoSize:true,layout:{background:{color:'#0b121c'},textColor:'#9fb0c2'},grid:{vertLines:{color:'#1a2533'},horzLines:{color:'#1a2533'}},rightPriceScale:{borderColor:'#2c3b4d'},timeScale:{timeVisible:true,borderColor:'#2c3b4d'},localization:{locale:'zh-TW',timeFormatter:t=>new Date(t*1000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'})}});
 const entries=[];
 for(const row of data){const series=chart.addLineSeries({title:row.name,color:row.color,lineWidth:2,priceLineVisible:false});const values=[...new Map(row.points.filter(p=>valid(p.equity)&&Number.isFinite(stamp(p.timestamp))).map(p=>[stamp(p.timestamp),{time:stamp(p.timestamp),value:p.equity}])).values()].sort((a,b)=>a.time-b.time);series.setData(values);const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=true;check.onchange=()=>series.applyOptions({visible:check.checked});label.append(check,document.createTextNode(row.name+(values.length?'':'（無曲線）')));label.style.color=row.color;document.getElementById('legend').append(label);entries.push({row,series,check});}
 const reset=document.createElement('button');reset.textContent='重設視野';reset.onclick=()=>chart.timeScale().fitContent();document.getElementById('legend').append(reset);
 chart.subscribeCrosshairMove(e=>{if(!e.time)return;const parts=[new Date(e.time*1000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'})];for(const {row,series,check} of entries){const v=e.seriesData.get(series)?.value;if(check.checked&&valid(v))parts.push(row.name+'：'+v.toLocaleString('zh-TW',{maximumFractionDigits:2})+' 元');}document.getElementById('quote').textContent=parts.join('　｜　');});chart.timeScale().fitContent();if(typeof requestAnimationFrame==='function')requestAnimationFrame(()=>requestAnimationFrame(()=>chart.timeScale().fitContent()));
 }</script></html>`;
}
export default function MultiStrategyCharts({rows}) {
 const [retry,setRetry]=useState(0),[metric,setMetric]=useState('total_return_pct');
 const html=useMemo(()=>equityDocument(rows),[rows]);
 const options=[['total_return_pct','總報酬率','%'],['max_drawdown_pct','最大回撤','%'],['win_rate_pct','勝率','%'],['trade_count','交易次數','筆']];
 const selected=options.find(o=>o[0]===metric);
 const max=Math.max(1,...rows.map(r=>numeric(r.summary?.[metric])?Math.abs(r.summary[metric]):0));
 return <View style={{gap:space.md}}>
  <View style={{gap:space.xxs}}><Text style={ui.eyebrow}>EQUITY</Text><Text style={ui.title}>資金淨值曲線</Text><Text style={ui.small}>勾選圖例可顯示或隱藏曲線，數值直接使用後端資金曲線。</Text></View>
  {Platform.OS==='web'?React.createElement('iframe',{key:retry,title:'多策略資金淨值比較',srcDoc:html,sandbox:'allow-scripts',referrerPolicy:'no-referrer',style:{width:'100%',height:500,border:'1px solid '+colors.borderSoft,borderRadius:radius.md,background:colors.chartBackground}}):<Text style={ui.small}>互動資金曲線請使用手機或電腦瀏覽器查看。</Text>}
  <Pressable onPress={()=>setRetry(x=>x+1)} style={ui.buttonGhost}><Text style={ui.link}>重新載入曲線圖</Text></Pressable>
  <View style={{height:1,backgroundColor:colors.borderSoft}}/>
  <View style={ui.wrap}>{options.map(o=><Pressable accessibilityRole="button" accessibilityState={{selected:metric===o[0]}} key={o[0]} onPress={()=>setMetric(o[0])} style={[ui.chip,metric===o[0]&&ui.chipSelected]}><Text style={[ui.chipText,metric===o[0]&&ui.chipTextSelected]}>{o[1]}比較</Text></Pressable>)}</View>
  <Text style={ui.caption}>中線為 0；左側為負值，右側為正值。</Text>
  {rows.map(row=>{const info=strategyInfo(row.strategy_id),v=row.summary?.[metric],size=numeric(v)?Math.abs(v)/max*48:0;return <View key={row.strategy_id} style={{gap:6}}><View style={{flexDirection:'row',justifyContent:'space-between',gap:space.sm}}><Text style={[ui.small,{color:info.color,fontWeight:'600'}]}>{info.name}</Text><Text style={[ui.small,type.number,{color:colors.text,fontWeight:'600'}]}>{formatMetric(v,selected[2])}</Text></View><View accessibilityLabel={`${info.name} ${selected[1]} ${formatMetric(v,selected[2])}`} style={{height:18,backgroundColor:colors.surfaceSunken,borderRadius:radius.sm,overflow:'hidden'}}><View style={{position:'absolute',left:'50%',height:'100%',width:1,backgroundColor:colors.borderStrong}}/>{numeric(v)&&<View style={{position:'absolute',left:`${v<0?50-size:50}%`,width:`${size}%`,height:18,borderRadius:3,backgroundColor:info.color,opacity:.85}}/>}</View></View>;})}
 </View>;
}

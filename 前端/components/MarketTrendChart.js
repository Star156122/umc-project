import React, { useEffect, useMemo, useState } from 'react';
import {apiRequest} from '../src/config/api';
import { ActivityIndicator, Modal, Platform, Pressable, Text, View } from 'react-native';
import { colors, radius, space, ui } from '../styles/theme';

export function buildMarketChart(stock, candles = false) {
  const points = (stock?.history || []).filter(p => typeof p.time === 'string' && p.time.includes('T') && Number.isFinite(Date.parse(p.time)) && p.value != null && Number.isFinite(Number(p.value)));
  const json = JSON.stringify({points,prevClose:stock?.historyFallback || stock?.historyRange==='1mo' ? null : stock?.prevClose,candles,monthly:stock?.historyRange==='1mo',maSeed:stock?.historyRange==='1mo'?(stock?.maSeed||[]).filter(n=>n!=null&&Number.isFinite(Number(n))).map(Number):[]}).replace(/</g,'\\u003c');
  return `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;height:100%;font:13px 'Noto Sans TC','Microsoft JhengHei',system-ui,sans-serif;background:#0b121c;color:#dbe4ee}#info{height:32px;padding:8px 12px;box-sizing:border-box;white-space:nowrap;overflow:hidden}#chart{height:calc(100% - 32px)}#error{padding:24px}#quote{font-variant-numeric:tabular-nums;color:#9fb0c2}button{float:right;border:1px solid #34485e;background:#172332;color:#8db9ff;border-radius:6px;padding:2px 8px;cursor:pointer;font:inherit;font-size:12px}</style><div id="info"><button id="reset">重設視野</button><span id="quote">載入走勢…</span></div><div id="chart"></div><script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js" onerror="document.getElementById('quote').textContent='圖表載入失敗，請檢查網路後重試'"></script><script>
  const input=${json};
  const fmt=t=>new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',...(input.monthly?{month:'2-digit',day:'2-digit'}:{hour:'2-digit',minute:'2-digit',hour12:false})}).format(new Date(t*1000));
  const valid=n=>n!==null&&n!==undefined&&Number.isFinite(Number(n));
  const rows=[...new Map(input.points.map(p=>[Math.floor(Date.parse(p.time)/1000),p])).entries()].sort((a,b)=>a[0]-b[0]);
  if(!rows.length){document.getElementById('quote').textContent='尚無此期間行情，請重新載入';}else if(window.LightweightCharts){
    const chart=LightweightCharts.createChart(document.getElementById('chart'),{autoSize:true,layout:{background:{color:'#0b121c'},textColor:'#9fb0c2'},grid:{vertLines:{color:'#1a2533'},horzLines:{color:'#1a2533'}},rightPriceScale:{borderColor:'#2c3b4d',scaleMargins:{top:.08,bottom:.26}},timeScale:{borderColor:'#2c3b4d',timeVisible:!input.monthly,secondsVisible:false,tickMarkFormatter:fmt},localization:{timeFormatter:fmt,locale:'zh-TW'},crosshair:{mode:0},handleScale:true,handleScroll:true});
    const line=rows.map(([time,p])=>({time,value:Number(p.value)}));
    const bars=rows.filter(([t,p])=>['open','high','low','close'].every(k=>valid(p[k]))).map(([time,p])=>({time,open:Number(p.open),high:Number(p.high),low:Number(p.low),close:Number(p.close)}));
    if(input.candles&&!bars.length)input.candles=false;
    const price=input.candles?chart.addCandlestickSeries({upColor:'#f0474f',downColor:'#1fbf6a',borderVisible:false,wickUpColor:'#f0474f',wickDownColor:'#1fbf6a'}):chart.addLineSeries({color:'#5b9cff',lineWidth:2,priceLineVisible:true,lastValueVisible:true});
    price.setData(input.candles?bars:line);
    const maDefs=[[5,'#f5b942'],[10,'#5b9cff']];
    const closes=[...input.maSeed,...bars.map(b=>b.close)],off=input.maSeed.length;
    const maMaps=input.candles?maDefs.filter(([n])=>closes.length>=n).map(([n,color])=>{const data=bars.map((b,i)=>i+off+1>=n?{time:b.time,value:closes.slice(i+off+1-n,i+off+1).reduce((s,x)=>s+x,0)/n}:null).filter(Boolean);chart.addLineSeries({color,lineWidth:2,title:'MA'+n,priceLineVisible:false,lastValueVisible:true,crosshairMarkerVisible:false}).setData(data);return [n,color,new Map(data.map(d=>[d.time,d.value]))];}):[];
    const maText=time=>maMaps.map(([n,c,m])=>m.has(time)?'MA'+n+' '+m.get(time).toFixed(2):'').filter(Boolean).join('　');
    if(!input.candles&&valid(input.prevClose))price.createPriceLine({price:Number(input.prevClose),color:'#71849a',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'昨收'});
    const volume=chart.addHistogramSeries({priceFormat:{type:'volume'},priceScaleId:'volume'});
    volume.priceScale().applyOptions({scaleMargins:{top:.8,bottom:0},visible:false});
    volume.setData(rows.filter(([t,p])=>valid(p.volume)).map(([time,p])=>({time,value:Number(p.volume),color:input.candles&&p.close<p.open?'rgba(31,191,106,0.45)':'rgba(240,71,79,0.45)'})));
    const label=(time,p)=>{document.getElementById('quote').textContent=new Date(time*1000).toLocaleDateString('zh-TW',{timeZone:'Asia/Taipei'})+' '+fmt(time)+'　'+(input.candles?['開','open','高','high','低','low','收','close'].reduce((s,x,i,a)=>i%2?s:s+(i?'　':'')+x+' '+Number(p[a[i+1]]).toLocaleString('zh-TW',{maximumFractionDigits:2}),''):'價格 '+Number(p.value).toLocaleString('zh-TW',{maximumFractionDigits:2}))+(valid(p.volume)?'　量 '+Number(p.volume).toLocaleString('zh-TW'):'　成交量未提供')+(maText(time)?'　'+maText(time):'');};
    const last=rows[rows.length-1];label(...last);
    chart.subscribeCrosshairMove(e=>{const row=rows.find(([t])=>t===e.time);if(row)label(...row);else label(...last);});
    chart.timeScale().fitContent();if(typeof requestAnimationFrame==='function')requestAnimationFrame(()=>requestAnimationFrame(()=>chart.timeScale().fitContent()));document.getElementById('reset').onclick=()=>chart.timeScale().fitContent();
  }
  </script></html>`;
}

export default function MarketTrendChart({stock,height=240}) {
  const [range,setRange]=useState('1mo'),[monthly,setMonthly]=useState(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[reload,setReload]=useState(0);
  const symbol=stock?.yahooSymbol|| (stock?.code?`${stock.code}.${stock.market==='otc'?'TWO':'TW'}`:'');
  useEffect(()=>{let alive=true;setMonthly(null);setError('');if(range!=='1mo'||!symbol){setLoading(false);return;}setLoading(true);apiRequest(`/api/market/history?symbol=${encodeURIComponent(symbol)}`,{timeoutMs:35000}).then(data=>{if(alive)setMonthly(data);}).catch(e=>{if(alive)setError(e.message);}).finally(()=>{if(alive)setLoading(false);});return()=>{alive=false;};},[range,symbol,reload]);
  const chartStock=range==='1mo'?{...stock,history:monthly?.history||[],maSeed:monthly?.maSeed||[],historyRange:'1mo'}:stock;
  const periodLabel=range==='1mo'?`最近一個月 · 日 K${monthly?.start?' · '+monthly.start+' ～ '+monthly.end:''}`:`${stock?.historyDate||'最近交易日'} · 5 分 K`;

  const [expanded,setExpanded]=useState(false);
  const [mode,setMode]=useState('candles');
  const line=useMemo(()=>buildMarketChart(chartStock,true),[chartStock]);
  const detail=useMemo(()=>buildMarketChart(chartStock,mode==='candles'),[chartStock,mode]);
  const frame=(html,full)=>React.createElement('iframe',{title:`${stock?.name||stock?.code} ${full?'互動K線':'分時走勢與成交量'}`,srcDoc:html,sandbox:'allow-scripts',referrerPolicy:'no-referrer',style:{width:'100%',height:full?'100%':height,border:0,display:'block',background:colors.chartBackground}});
  return <View style={{width:'100%',borderRadius:radius.md,overflow:'hidden',backgroundColor:colors.chartBackground,borderWidth:1,borderColor:colors.borderSoft}}>
    <View style={{padding:space.sm,gap:space.xs}}><View style={ui.wrap}>{[['1mo','最近一個月'],['1d','最近交易日']].map(([value,label])=><Pressable key={value} accessibilityRole="button" accessibilityState={{selected:range===value}} onPress={()=>setRange(value)} style={[ui.chip,{minHeight:34,paddingHorizontal:space.sm},range===value&&ui.chipSelected]}><Text style={[ui.chipText,{fontSize:13},range===value&&ui.chipTextSelected]}>{label}</Text></Pressable>)}</View><Text style={ui.small}>{periodLabel}</Text><Text style={ui.caption}>上方漲跌幅為當日行情；圖表依所選期間顯示。</Text>{loading&&<ActivityIndicator color={colors.primaryText}/>}{!!error&&<View><Text accessibilityRole="alert" style={[ui.small,{color:colors.dangerText}]}>{error}</Text><Pressable onPress={()=>setReload(v=>v+1)} style={ui.buttonGhost}><Text style={ui.link}>重試月行情</Text></Pressable></View>}</View>
    {Platform.OS==='web'?frame(line,false):<Text style={[ui.muted,{padding:space.lg}]}>請在網頁版查看互動走勢圖</Text>}
    <Pressable accessibilityRole="button" onPress={()=>{setMode('candles');setExpanded(true);}} style={({pressed})=>({padding:space.sm,backgroundColor:pressed?colors.surfacePressed:colors.surfaceRaised,borderTopWidth:1,borderTopColor:colors.borderSoft})}><Text style={[ui.small,{color:colors.primaryText,textAlign:'center',fontWeight:'700'}]}>放大圖表 ↗</Text></Pressable>
    <Modal visible={expanded} onRequestClose={()=>setExpanded(false)} animationType="fade">
      <View style={{flex:1,backgroundColor:colors.background,padding:space.md}}>
        <View style={[ui.wrap,{gap:space.sm,marginBottom:space.sm}]}>
          <Text style={[ui.title,{flex:1,minWidth:200}]}>{stock?.code} {stock?.name} · {periodLabel}</Text>
          {[['candles','K 線'],['line','走勢線']].map(([key,label])=><Pressable key={key} onPress={()=>setMode(key)} style={[ui.chip,mode===key&&ui.chipSelected]}><Text style={[ui.chipText,mode===key&&ui.chipTextSelected]}>{label}</Text></Pressable>)}
          <Pressable accessibilityRole="button" onPress={()=>setExpanded(false)} style={ui.buttonSecondary}><Text style={ui.buttonSecondaryText}>關閉 ✕</Text></Pressable>
        </View>
        <Text style={[ui.small,{marginBottom:space.sm}]}>滾輪縮放、拖曳平移、游標查看開高低收與成交量。Yahoo 行情可能延遲，非回測買賣訊號。</Text>
        <View style={{flex:1,borderRadius:radius.md,overflow:'hidden',borderWidth:1,borderColor:colors.borderSoft}}>{Platform.OS==='web'&&frame(detail,true)}</View>
      </View>
    </Modal>
  </View>;
}

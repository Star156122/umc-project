import React, { useMemo, useState } from 'react';
import { Modal, Platform, Pressable, Text, View } from 'react-native';

export function buildMarketChart(stock, candles = false) {
  const points = (stock?.history || []).filter(p => typeof p.time === 'string' && p.time.includes('T') && Number.isFinite(Date.parse(p.time)) && p.value != null && Number.isFinite(Number(p.value)));
  const json = JSON.stringify({points,prevClose:stock?.prevClose,candles}).replace(/</g,'\\u003c');
  return `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;height:100%;font:13px Microsoft JhengHei,Arial;background:#080b10;color:#e2e8f0}#info{height:32px;padding:8px 12px;box-sizing:border-box;white-space:nowrap;overflow:hidden}#chart{height:calc(100% - 32px)}#error{padding:24px}button{float:right;border:0;background:#222a35;color:#ff626b;padding:2px 8px;cursor:pointer}</style><div id="info"><button id="reset">重設視野</button><span id="quote">載入走勢…</span></div><div id="chart"></div><script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js" onerror="document.getElementById('quote').textContent='圖表載入失敗，請檢查網路後重試'"></script><script>
  const input=${json};
  const fmt=t=>new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(t*1000));
  const valid=n=>n!==null&&n!==undefined&&Number.isFinite(Number(n));
  const rows=[...new Map(input.points.map(p=>[Math.floor(Date.parse(p.time)/1000),p])).entries()].sort((a,b)=>a[0]-b[0]);
  if(!rows.length){document.getElementById('quote').textContent='尚無分時資料，請更新行情';}else if(window.LightweightCharts){
    const chart=LightweightCharts.createChart(document.getElementById('chart'),{autoSize:true,layout:{background:{color:'#080b10'},textColor:'#b5c0ce'},grid:{vertLines:{color:'#202630'},horzLines:{color:'#202630'}},rightPriceScale:{borderColor:'#354050',scaleMargins:{top:.08,bottom:.26}},timeScale:{borderColor:'#354050',timeVisible:true,secondsVisible:false,tickMarkFormatter:fmt},localization:{timeFormatter:fmt,locale:'zh-TW'},crosshair:{mode:0},handleScale:true,handleScroll:true});
    const price=input.candles?chart.addCandlestickSeries({upColor:'#ef4444',downColor:'#00ad69',borderVisible:false,wickUpColor:'#ef4444',wickDownColor:'#00ad69'}):chart.addLineSeries({color:'#ff404f',lineWidth:2,priceLineVisible:true,lastValueVisible:true});
    const line=rows.map(([time,p])=>({time,value:Number(p.value)}));
    const bars=rows.filter(([t,p])=>['open','high','low','close'].every(k=>valid(p[k]))).map(([time,p])=>({time,open:Number(p.open),high:Number(p.high),low:Number(p.low),close:Number(p.close)}));
    price.setData(input.candles?bars:line);
    if(!input.candles&&valid(input.prevClose))price.createPriceLine({price:Number(input.prevClose),color:'#94a3b8',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'昨收'});
    const volume=chart.addHistogramSeries({priceFormat:{type:'volume'},priceScaleId:'volume'});
    volume.priceScale().applyOptions({scaleMargins:{top:.8,bottom:0},visible:false});
    volume.setData(rows.filter(([t,p])=>valid(p.volume)).map(([time,p])=>({time,value:Number(p.volume),color:input.candles&&p.close<p.open?'#00ad69':'#ff4a57'})));
    const label=(time,p)=>{document.getElementById('quote').textContent=new Date(time*1000).toLocaleDateString('zh-TW',{timeZone:'Asia/Taipei'})+' '+fmt(time)+'　'+(input.candles?'開 '+p.open+'　高 '+p.high+'　低 '+p.low+'　收 '+p.close:'價格 '+Number(p.value).toLocaleString('zh-TW',{maximumFractionDigits:2}))+(valid(p.volume)?'　量 '+Number(p.volume).toLocaleString('zh-TW'):'　成交量未提供');};
    const last=rows[rows.length-1];label(...last);
    if(input.candles&&!bars.length)document.getElementById('quote').textContent='這份行情未包含 OHLC；重啟 API 後重新整理行情';
    chart.subscribeCrosshairMove(e=>{const row=rows.find(([t])=>t===e.time);if(row)label(...row);else label(...last);});
    chart.timeScale().fitContent();document.getElementById('reset').onclick=()=>chart.timeScale().fitContent();
  }
  </script></html>`;
}

export default function MarketTrendChart({stock,height=240}) {
  const [expanded,setExpanded]=useState(false);
  const [mode,setMode]=useState('candles');
  const line=useMemo(()=>buildMarketChart(stock,false),[stock]);
  const detail=useMemo(()=>buildMarketChart(stock,mode==='candles'),[stock,mode]);
  const frame=(html,full)=>React.createElement('iframe',{title:`${stock?.name||stock?.code} ${full?'互動K線':'分時走勢與成交量'}`,srcDoc:html,sandbox:'allow-scripts',referrerPolicy:'no-referrer',style:{width:'100%',height:full?'100%':height,border:0,display:'block',background:'#080b10'}});
  return <View style={{width:'100%',borderRadius:10,overflow:'hidden',backgroundColor:'#080b10'}}>
    {Platform.OS==='web'?frame(line,false):<Text style={{padding:20,color:'#e2e8f0'}}>請在網頁版查看互動走勢圖</Text>}
    <Pressable accessibilityRole="button" onPress={()=>{setMode('candles');setExpanded(true);}} style={{padding:10,backgroundColor:'#241419'}}><Text style={{color:'#ff626b',textAlign:'center',fontWeight:'700'}}>點開 K 線圖 ↗</Text></Pressable>
    <Modal visible={expanded} onRequestClose={()=>setExpanded(false)} animationType="fade">
      <View style={{flex:1,backgroundColor:'#080b10',padding:16}}>
        <View style={{flexDirection:'row',flexWrap:'wrap',gap:16,alignItems:'center',marginBottom:12}}>
          <Text style={{flex:1,fontSize:20,fontWeight:'700',color:'#e2e8f0'}}>{stock?.code} {stock?.name} · 當日 5 分 K</Text>
          {[['candles','K 線'],['line','分時線']].map(([key,label])=><Pressable key={key} onPress={()=>setMode(key)} style={{padding:10,backgroundColor:mode===key?'#51212b':'#202630',borderRadius:8}}><Text style={{color:'#e2e8f0'}}>{label}</Text></Pressable>)}
          <Pressable accessibilityRole="button" onPress={()=>setExpanded(false)} style={{padding:10}}><Text style={{color:'#e2e8f0'}}>關閉 ✕</Text></Pressable>
        </View>
        <Text style={{color:'#a5b1c2',marginBottom:10}}>滾輪縮放、拖曳平移、游標查看開高低收與成交量。Yahoo 行情可能延遲，非回測買賣訊號。</Text>
        <View style={{flex:1}}>{Platform.OS==='web'&&frame(detail,true)}</View>
      </View>
    </Modal>
  </View>;
}

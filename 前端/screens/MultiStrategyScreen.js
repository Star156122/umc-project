import usePageLayout from '../styles/usePageLayout';
import {useLocalSearchParams} from 'expo-router';
import React,{useEffect,useRef,useState,useMemo} from 'react';
import {ActivityIndicator,Pressable,ScrollView,StyleSheet,Text,View} from 'react-native';
import {apiRequest} from '../src/config/api';
import {STRATEGIES,STOCKS,METRICS,formatMetric,strategyInfo,resultRows,explainError,fetchRunUpdate} from '../src/features/multiBacktestModel';
import {loadPreferences,subscribePreferences,DEFAULT_PREFERENCES,notifyBacktestCompleted} from '../src/features/preferences';
import {marketColors} from '../styles/marketTheme';
import StockNewsPanel from '../components/StockNewsPanel';
import MultiReportHistory from '../components/MultiReportHistory';
import MultiGeminiPanel from '../components/MultiGeminiPanel';
import BacktestTradeChart from '../components/BacktestTradeChart';
import MultiStrategyCharts from '../components/MultiStrategyCharts';
import MultiStrategyDetails from '../components/MultiStrategyDetails';
const STATUS={pending:'等待執行',running:'執行中',completed:'執行完成',failed:'執行失敗',blocked:'資料不足或研究政策限制',paused:'連線暫停',expired:'工作已失效',submitting:'正在提交'};
export default function MultiStrategyScreen(){
 const layout=usePageLayout();
 const params=useLocalSearchParams();
 const [period,setPeriod]=useState({start:'2025-01-01',end:'2025-06-30'}),[showHistory,setShowHistory]=useState(false),[showTable,setShowTable]=useState(false);
 const [profile,setProfile]=useState(null);
 const [historyVersion,setHistoryVersion]=useState(0),[opening,setOpening]=useState(false),[saving,setSaving]=useState(false);
 const [preferences,setPreferences]=useState(DEFAULT_PREFERENCES),[notice,setNotice]=useState('');
 const [stock,setStock]=useState('2357'),[selected,setSelected]=useState(['rf','xgboost','gru']);
 const [job,setJob]=useState(null),[status,setStatus]=useState(''),[result,setResult]=useState(null),[error,setError]=useState(''),[elapsed,setElapsed]=useState(0),[history,setHistory]=useState([]);
 const token=useRef(0),timer=useRef(null),mounted=useRef(true),busy=useRef(false),failures=useRef(0);
 useEffect(()=>{let alive=true;apiRequest('/api/backtests/multi/profile').then(p=>{if(p.profile_id!=='main-buy-noadd-noextension-common-v1-20261114')throw Error('後端尚未載入新版主要策略，請重新啟動後端');if(alive)setProfile(p);}).catch(e=>{if(alive)setError('主要策略設定讀取失敗：'+e.message);});return()=>{alive=false;};},[]);
 const coverage=profile?.coverage?.[stock];
 const supported=code=>profile?.coverage?.[code]?.start<='2025-01-01'&&profile?.coverage?.[code]?.end>='2025-03-31';
 const periods=[{start:'2025-01-01',end:'2025-12-31',label:'一年'},{start:'2025-01-01',end:'2025-06-30',label:'半年'},{start:'2025-01-01',end:'2025-03-31',label:'三個月'}];
 const formal=profile?.formal_reports?.[stock];
 const available=p=>!!coverage&&((p.start>=coverage.start&&p.end<=coverage.end)||formal?.periods?.some(x=>x.start===p.start&&x.end===p.end));
 const requestedStrategies=['technical',...selected.filter(x=>x!=='technical')];
 useEffect(()=>{if(!profile||busy.current)return;if(!supported(stock)){const next=profile.stocks.find(supported);if(next)setStock(next);}else if(!available(period))setPeriod({start:'2025-01-01',end:'2025-03-31'});},[profile,stock]);
 useEffect(()=>{if(typeof params.stock==='string'&&STOCKS[params.stock]&&!busy.current)setStock(params.stock);},[params.stock]);
 const active=['pending','running','submitting'].includes(status);
 const unresolved=active||status==='paused'||opening||saving;
 const rows=useMemo(()=>resultRows(result),[result]);
 useEffect(()=>{let alive=true;const apply=p=>{if(!alive)return;setPreferences(p);if(!busy.current){setStock(typeof params.stock==='string'?params.stock:p.stock);setSelected(p.strategies.filter(x=>['rf','xgboost','gru'].includes(x)));}};loadPreferences().then(apply).catch(()=>{});const off=subscribePreferences(apply);return()=>{alive=false;off();};},[]);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;token.current++;clearTimeout(timer.current);};},[]);
 useEffect(()=>{if(!active||!job)return;const tick=()=>setElapsed(Math.max(0,Math.floor((Date.now()-job.startedAt)/1000)));tick();const interval=setInterval(tick,1000);return()=>clearInterval(interval);},[active,job]);
 async function poll(current,serial){
  if(!mounted.current||serial!==token.current)return;
  try{
   const update=await fetchRunUpdate(apiRequest,current);
   if(!mounted.current||serial!==token.current)return;
   failures.current=0;setError('');setStatus(update.status);
   if(update.status==='completed'){
    notifyBacktestCompleted(current.stock_code).then(show=>{if(show&&mounted.current&&serial===token.current)setNotice(current.stock_code+' 的多策略回測已完成，可查看下方結果。');});
    setHistoryVersion(v=>v+1);setResult(update.result);setHistory(old=>[update.result,...old.filter(r=>r.run_id!==update.result.run_id)].slice(0,5));busy.current=false;return;
   }
   if(update.status==='failed'||update.status==='blocked'){
    setError(explainError({code:update.error?.code,message:update.error?.message||STATUS[update.status]}));busy.current=false;return;
   }
   timer.current=setTimeout(()=>poll(current,serial),2500);
  }catch(e){
   if(!mounted.current||serial!==token.current)return;
   if(e.status===404){setStatus('expired');setError(explainError(e));busy.current=false;return;}
   failures.current++;
   if(e.status===401||failures.current>=3){setStatus('paused');setError(explainError(e)+'；可稍後按「繼續查詢」，不會重複提交工作。');busy.current=false;return;}
   setError('狀態連線暫時中斷，正在重新查詢（不會重複執行回測）。');timer.current=setTimeout(()=>poll(current,serial),5000);
  }
 }
 async function run(){
  if(busy.current||unresolved)return;
  if(!STOCKS[stock]||!supported(stock)){setError('請選擇已備妥 2025 年資料的股票按鈕');return;}
  if(!periods.some(p=>p.start===period.start&&p.end===period.end)||!available(period)){setError('此股票尚未備妥所選期間的資料');return;}
  if(!selected.length){setError('請至少勾選一種模型策略');return;}
  setNotice('');busy.current=true;clearTimeout(timer.current);const serial=++token.current;
  setStatus('submitting');setError('');setResult(null);setJob(null);setElapsed(0);failures.current=0;
  try{
   const response=await apiRequest('/api/backtests/multi/run',{method:'POST',body:JSON.stringify({stock_code:stock.trim(),strategies:requestedStrategies,start_date:period.start,end_date:period.end}),timeoutMs:30000});
   if(!mounted.current||serial!==token.current)return;
   if(typeof response.run_id!=='string'||!response.run_id)throw Error('後端未回傳工作編號，請確認 API。');
   const current={run_id:response.run_id,stock_code:stock.trim(),strategies:[...requestedStrategies],period:{...period},strategy_profile:formal?.periods?.some(p=>p.start===period.start&&p.end===period.end)?formal.profile_id:profile?.profile_id,startedAt:Date.now()};
   setJob(current);setStatus(response.status==='running'?'running':'pending');poll(current,serial);
  }catch(e){if(mounted.current&&serial===token.current){setStatus('failed');setError(explainError(e)+(!e.status?'。提交結果不明，伺服器可能仍在執行，請勿連續重複送出。':''));}busy.current=false;}
 }
 async function openHistory(entry){
  if(busy.current||unresolved)return;busy.current=true;setOpening(true);setError('');
  try{const saved=await apiRequest(`/api/backtests/multi/${encodeURIComponent(entry.run_id)}/result`,{timeoutMs:45000});
   if(saved.status!=='completed'||saved.run_id!==entry.run_id||saved.stock_code!==entry.stock_code)throw Error('歷史報告格式不符，請重新整理清單');
   token.current++;clearTimeout(timer.current);setResult(saved);setJob(null);setStatus('');setNotice('已讀取資料庫歷史報告');
  }catch(e){setError(e.message);}finally{busy.current=false;setOpening(false);}
 }
 async function retrySave(){if(!result||saving)return;setSaving(true);try{const response=await apiRequest(`/api/backtests/multi/${encodeURIComponent(result.run_id)}/save`,{method:'POST',timeoutMs:30000});setResult(old=>({...old,persistence:response.persistence}));setHistoryVersion(v=>v+1);}catch(e){setError(e.message);}finally{setSaving(false);}}
 function resume(){if(!job||busy.current)return;busy.current=true;failures.current=0;setStatus('running');setError('');poll(job,++token.current);}
 return <ScrollView style={{flex:1,backgroundColor:marketColors.background}} contentContainerStyle={[s.page,layout.page]}>
  {!!notice&&<Pressable onPress={()=>setNotice('')}><Text accessibilityLiveRegion="polite" style={s.period}>{notice}（點擊關閉）</Text></Pressable>}
  <View style={[s.hero,layout.card]}><Text style={s.eyebrow}>STRATEGY LAB / 歷史模擬</Text><Text style={[s.title,layout.title]}>看懂策略，從每一筆交易開始</Text><Text style={s.note}>選股票、選期間、比較策略。先看買賣位置，再了解報酬與風險。</Text><Text style={s.period}>可用資料　{coverage?.start||'讀取中'} — {coverage?.end||'讀取中'}</Text><Text style={s.note}>這是 2025 年歷史模擬，不是最新績效或投資訊號。純技術指標與買進持有固定加入比較。</Text></View>
  <View style={[s.card,layout.card]}><Text style={s.heading}>1　選擇股票</Text><View style={s.wrap}>{Object.entries(STOCKS).map(([code,name])=><Pressable key={code} disabled={unresolved||!supported(code)} accessibilityRole="button" accessibilityState={{disabled:unresolved||!supported(code),selected:stock===code}} onPress={()=>setStock(code)} style={[s.chip,stock===code&&s.chosen,!supported(code)&&{opacity:.4}]}><Text style={s.text}>{code} {name}{!supported(code)?'（2025 資料未備妥）':''}</Text></Pressable>)}</View><Text style={s.note}>主要策略：不加碼、不延期；共同完整日＋三模型合法預測覆蓋。每組25萬元，首次含費最多使用現金40%，保留初始20%，保留2.5%停損。</Text>
  <Text style={s.heading}>2　選擇 2025 年回測期間</Text><View style={s.wrap}>{periods.map(p=><Pressable disabled={unresolved||!available(p)} key={p.label} accessibilityRole="button" accessibilityState={{disabled:unresolved||!available(p),selected:period.end===p.end}} onPress={()=>setPeriod({start:p.start,end:p.end})} style={[s.chip,period.end===p.end&&s.chosen,!available(p)&&{opacity:.4}]}><Text style={s.text}>{p.label}{!available(p)?'（資料未備妥）':''}</Text><Text style={s.note}>{p.start} ～ {p.end}</Text></Pressable>)}</View><Text style={s.note}>三個月為 1–3 月、半年為 1–6 月、一年為 1–12 月。和碩讀取已完成的正式報告；全年包含已使用的上半年及正式驗證下半年，不代表整年皆為未見資料。</Text>
  <Text style={s.heading}>3　勾選策略（可複選）</Text><View style={s.wrap}>{STRATEGIES.filter(item=>item.id!=='technical').map(item=><Pressable key={item.id} accessibilityRole="checkbox" accessibilityLabel={item.name} accessibilityState={{checked:selected.includes(item.id),disabled:unresolved}} disabled={unresolved} onPress={()=>setSelected(old=>old.includes(item.id)?old.filter(x=>x!==item.id):[...old,item.id])} style={[s.strategy,selected.includes(item.id)&&{borderColor:item.color,backgroundColor:'#192d42'}]}><Text style={[s.text,{fontWeight:'700',color:item.color}]}>{selected.includes(item.id)?'☑':'☐'} {item.name}</Text><Text style={s.note}>{item.subtitle}</Text></Pressable>)}</View>
  <Text style={s.note}>比較基準：純技術指標與 Buy & Hold（買進持有）固定加入，不必勾選。</Text>
  <Pressable accessibilityRole="button" disabled={unresolved||!selected.length||!profile} onPress={run} style={[s.run,(unresolved||!selected.length)&&{opacity:.5}]}><Text style={s.runText}>{active?'回測處理中…':status==='paused'?'請先繼續查詢目前工作':`查看 ${selected.length} 種模型策略＋2 項比較基準`}</Text></Pressable>
  </View>
  {!!status&&<View style={[s.card,layout.card]} accessibilityLiveRegion="polite"><View style={s.wrap}>{active&&<ActivityIndicator color="#73b9ff"/>}<Text style={s.heading}>{STATUS[status]||status}</Text></View>{job&&<><Text style={s.note}>本次股票 {job.stock_code} {STOCKS[job.stock_code]||''}　已等待 {Math.floor(elapsed/60)} 分 {elapsed%60} 秒</Text><Text selectable style={s.note}>工作編號：{job.run_id}</Text></>}{active&&<Text style={s.note}>正在等待伺服器完成，頁面會自動更新。模型計算可能需要數分鐘；後端未提供百分比進度。</Text>}{status==='paused'&&<Pressable onPress={resume} style={s.chip}><Text style={s.text}>繼續查詢</Text></Pressable>}</View>}
  {!!error&&<Text accessibilityRole="alert" style={s.error}>{error}</Text>}
  <Pressable accessibilityRole="button" accessibilityState={{expanded:showHistory}} onPress={()=>setShowHistory(!showHistory)} style={s.chip}><Text style={s.text}>{showHistory?'收合':'開啟'}歷史報告　↗</Text></Pressable>
  {showHistory&&<MultiReportHistory onSelect={openHistory} disabled={unresolved} refreshKey={historyVersion}/>}
  {opening&&<ActivityIndicator color="#73b9ff"/>}
  {history.length>0&&<View style={s.wrap}>{history.map(r=><Pressable disabled={unresolved} key={r.run_id} style={[s.chip,result?.run_id===r.run_id&&s.chosen]} onPress={()=>setResult(r)}><Text style={s.text}>{r.stock_code} · {r.strategies.length} 策略 · {r.run_id.slice(-8)}</Text></Pressable>)}</View>}
  {result&&<>
   <BacktestTradeChart key={result.run_id} result={result}/>
   {preferences.showCharts&&<View style={[s.card,layout.card]}><MultiStrategyCharts rows={rows}/></View>}
   <View style={[s.card,layout.card]}><Text style={result.persistence?.status==='saved'?s.note:s.error}>{result.persistence?.status==='saved'?'此報告已永久存入資料庫，可從歷史清單重新開啟。':result.persistence?.message||'此報告尚未確認存入資料庫，請重試儲存。'}</Text>{result.persistence?.status!=='saved'&&<Pressable accessibilityRole="button" disabled={saving} onPress={retrySave} style={s.chip}><Text style={s.text}>{saving?'儲存中…':'重試儲存報告'}</Text></Pressable>}</View>
   <View style={[s.card,layout.card]}><Text style={s.heading}>{result.stock_code} {STOCKS[result.stock_code]||''} · 回測結果</Text><Text style={s.note}>{result.backtest_period.start} ～ {result.backtest_period.end}</Text><Text style={s.note}>資料角色：{result.audit?.dataset_role||result.data_role}（歷史研究）</Text><Text style={s.note}>規則版本：{result.audit?.profile_id||'舊版歷史規則'}</Text><Text style={s.note}>報告識別：{result.run_id}</Text><Text style={s.note}>無交易或不適用的數據顯示「不適用」；0 代表後端明確回傳的零值。</Text>
   <View style={s.wrap}>{rows.map(r=><View key={r.strategy_id} style={[s.strategy,{borderColor:strategyInfo(r.strategy_id).color}]}><Text style={[s.text,{color:strategyInfo(r.strategy_id).color,fontWeight:'700'}]}>{strategyInfo(r.strategy_id).name}</Text><Text style={[s.title,layout.title]}>{formatMetric(r.summary?.total_return_pct,'%')}</Text><Text style={s.note}>淨損益 {formatMetric(r.summary?.net_pnl,'元')}</Text><Text style={s.note}>最大回撤 {formatMetric(r.summary?.max_drawdown_pct,'%')}</Text></View>)}</View>
   <Pressable onPress={()=>setShowTable(!showTable)} accessibilityRole="button" accessibilityState={{expanded:showTable}}><Text style={{color:'#73b9ff'}}>{showTable?'收合':'展開'}完整績效比較表</Text></Pressable>
   {showTable&&<ScrollView horizontal><View><View style={s.tableRow}><Text style={[s.cell,s.label]}>基本績效</Text>{rows.map(r=><Text key={r.strategy_id} style={[s.cell,{color:strategyInfo(r.strategy_id).color,fontWeight:'700'}]}>{strategyInfo(r.strategy_id).name}</Text>)}</View>{METRICS.map(([key,name,unit])=><View key={key} style={s.tableRow}><Text style={[s.cell,s.label]}>{name}</Text>{rows.map(r=><Text selectable key={r.strategy_id} style={s.cell}>{formatMetric(r.summary?.[key],unit)}</Text>)}</View>)}</View></ScrollView>}
   {showTable&&METRICS.map(([,name,,meaning])=><Text key={name} style={s.note}>{name}：{meaning}</Text>)}
   <Text style={s.note}>{result.audit?.profile_id?'買進持有期初最多投入80%，受成交量限制；期末賣出並計費稅，投入比例與主動策略不同。':'Buy & Hold 期末仍持有股票，損益包含未實現損益，未計期末賣出成本；投入資金比例也與主動策略不同。'}</Text></View>

   <MultiGeminiPanel key={'ai:'+result.run_id} result={result} onSaved={()=>setHistoryVersion(v=>v+1)}/>
   <StockNewsPanel key={'news:'+result.run_id} report={{analysisId:result.run_id}} apiBase={`/api/backtests/multi/${encodeURIComponent(result.run_id)}`} />
   <Text style={s.heading}>策略專業細節</Text>{rows.map(r=><MultiStrategyDetails key={result.run_id+':'+r.strategy_id} row={r} audit={result.audit} defaultExpanded={preferences.expandDetails}/>)}
  </>}
 </ScrollView>;
}
const s=StyleSheet.create({page:{flexGrow:1,backgroundColor:marketColors.background,padding:20,paddingBottom:70,gap:18,width:'100%',alignSelf:'center'},hero:{padding:24,borderRadius:20,backgroundColor:'#10283f',gap:12},eyebrow:{fontSize:12,color:'#73b9ff',letterSpacing:2},title:{fontSize:30,fontWeight:'800',color:'#f8fafc'},period:{fontSize:17,fontWeight:'700',color:'#ffcc66'},card:{padding:20,gap:16,borderWidth:1,borderColor:'#294052',backgroundColor:'#0d1c29',borderRadius:18},heading:{color:'#f8fafc',fontSize:20,fontWeight:'700'},text:{color:'#e2e8f0',fontSize:15},note:{color:'#a5b7ca',lineHeight:22,fontSize:14},input:{borderWidth:1,borderColor:'#476581',borderRadius:10,color:'#f8fafc',padding:12,fontSize:20,maxWidth:280,width:'100%',minWidth:0},wrap:{flexDirection:'row',flexWrap:'wrap',gap:10,alignItems:'center'},chip:{padding:11,backgroundColor:'#172d40',borderWidth:1,borderColor:'#294052',borderRadius:9},chosen:{backgroundColor:'#224c77',borderColor:'#73b9ff'},strategy:{padding:17,borderWidth:1,borderColor:'#294052',borderRadius:12,minWidth:0,flexBasis:210,flexGrow:1,gap:8},run:{backgroundColor:'#1670d6',padding:16,borderRadius:12,alignItems:'center'},runText:{color:'white',fontWeight:'700',fontSize:17},error:{color:'#ffb0b8',backgroundColor:'#371c28',padding:16,borderRadius:10,lineHeight:24},tableRow:{flexDirection:'row',borderBottomWidth:1,borderColor:'#294052'},cell:{width:170,padding:14,color:'#e2e8f0',fontSize:15},label:{width:145,backgroundColor:'#142b3d'}});

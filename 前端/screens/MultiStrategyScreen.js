import React,{useEffect,useRef,useState,useMemo} from 'react';
import {ActivityIndicator,Pressable,ScrollView,StyleSheet,Text,TextInput,View} from 'react-native';
import {apiRequest} from '../src/config/api';
import {STRATEGIES,STOCKS,METRICS,formatMetric,strategyInfo,resultRows,explainError,fetchRunUpdate} from '../src/features/multiBacktestModel';
import {loadPreferences,subscribePreferences,DEFAULT_PREFERENCES,notifyBacktestCompleted} from '../src/features/preferences';
import {marketColors} from '../styles/marketTheme';
import StockNewsPanel from '../components/StockNewsPanel';
import MultiGeminiPanel from '../components/MultiGeminiPanel';
import MultiStrategyCharts from '../components/MultiStrategyCharts';
import MultiStrategyDetails from '../components/MultiStrategyDetails';
const STATUS={pending:'等待執行',running:'執行中',completed:'執行完成',failed:'執行失敗',blocked:'資料不足或研究政策限制',paused:'連線暫停',expired:'工作已失效',submitting:'正在提交'};
export default function MultiStrategyScreen(){
 const [preferences,setPreferences]=useState(DEFAULT_PREFERENCES),[notice,setNotice]=useState('');
 const [stock,setStock]=useState('2330'),[selected,setSelected]=useState(['technical','rf','xgboost','gru']);
 const [job,setJob]=useState(null),[status,setStatus]=useState(''),[result,setResult]=useState(null),[error,setError]=useState(''),[elapsed,setElapsed]=useState(0),[history,setHistory]=useState([]);
 const token=useRef(0),timer=useRef(null),mounted=useRef(true),busy=useRef(false),failures=useRef(0);
 const active=['pending','running','submitting'].includes(status);
 const unresolved=active||status==='paused';
 const rows=useMemo(()=>resultRows(result),[result]);
 useEffect(()=>{let alive=true;const apply=p=>{if(!alive)return;setPreferences(p);if(!busy.current){setStock(p.stock);setSelected(p.strategies);}};loadPreferences().then(apply).catch(()=>{});const off=subscribePreferences(apply);return()=>{alive=false;off();};},[]);
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
    setResult(update.result);setHistory(old=>[update.result,...old.filter(r=>r.run_id!==update.result.run_id)].slice(0,5));busy.current=false;return;
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
  if(!/^\d{4,6}$/.test(stock.trim())){setError('請輸入 4～6 位數字的股票代號');return;}
  if(!selected.length){setError('請至少勾選一種策略');return;}
  setNotice('');busy.current=true;clearTimeout(timer.current);const serial=++token.current;
  setStatus('submitting');setError('');setResult(null);setJob(null);setElapsed(0);failures.current=0;
  try{
   const response=await apiRequest('/api/backtests/multi/run',{method:'POST',body:JSON.stringify({stock_code:stock.trim(),strategies:selected}),timeoutMs:30000});
   if(!mounted.current||serial!==token.current)return;
   if(typeof response.run_id!=='string'||!response.run_id)throw Error('後端未回傳工作編號，請確認 API。');
   const current={run_id:response.run_id,stock_code:stock.trim(),strategies:[...selected],startedAt:Date.now()};
   setJob(current);setStatus(response.status==='running'?'running':'pending');poll(current,serial);
  }catch(e){if(mounted.current&&serial===token.current){setStatus('failed');setError(explainError(e)+(!e.status?'。提交結果不明，伺服器可能仍在執行，請勿連續重複送出。':''));}busy.current=false;}
 }
 function resume(){if(!job||busy.current)return;busy.current=true;failures.current=0;setStatus('running');setError('');poll(job,++token.current);}
 return <ScrollView style={{flex:1,backgroundColor:marketColors.background}} contentContainerStyle={s.page}>
  {!!notice&&<Pressable onPress={()=>setNotice('')}><Text accessibilityLiveRegion="polite" style={s.period}>{notice}（點擊關閉）</Text></Pressable>}
  <View style={s.hero}><Text style={s.eyebrow}>HISTORICAL DEVELOPMENT</Text><Text style={s.title}>多策略回測比較</Text><Text style={s.note}>勾選策略，一次比較技術指標與機器學習的表現。</Text><Text style={s.period}>固定回測期間　2024/07/01 — 2024/12/30</Text><Text style={s.note}>這是歷史開發結果，不是最近三個月績效或最新投資訊號。Buy & Hold 自動加入比較。</Text></View>
  <View style={s.card}><Text style={s.heading}>1　選擇股票</Text><TextInput accessibilityLabel="多策略股票代號" value={stock} onChangeText={setStock} editable={!unresolved} keyboardType="number-pad" maxLength={6} style={s.input}/><View style={s.wrap}>{Object.entries(STOCKS).map(([code,name])=><Pressable key={code} disabled={unresolved} onPress={()=>setStock(code)} style={[s.chip,stock===code&&s.chosen]}><Text style={s.text}>{code} {name}</Text></Pressable>)}</View><Text style={s.note}>目前只有 2330 台積電完成四策略實測。其他白名單股票能否執行，以後端的資料檢查結果為準。暫不提供自訂日期。</Text>
  <Text style={s.heading}>2　勾選策略（可複選）</Text><View style={s.wrap}>{STRATEGIES.map(item=><Pressable key={item.id} accessibilityRole="checkbox" accessibilityLabel={item.name} accessibilityState={{checked:selected.includes(item.id),disabled:unresolved}} disabled={unresolved} onPress={()=>setSelected(old=>old.includes(item.id)?old.filter(x=>x!==item.id):[...old,item.id])} style={[s.strategy,selected.includes(item.id)&&{borderColor:item.color,backgroundColor:'#192d42'}]}><Text style={[s.text,{fontWeight:'700',color:item.color}]}>{selected.includes(item.id)?'☑':'☐'} {item.name}</Text><Text style={s.note}>{item.subtitle}</Text></Pressable>)}</View>
  <Text style={s.note}>比較基準：Buy & Hold（買進持有）自動加入，不必勾選。</Text>
  <Pressable accessibilityRole="button" disabled={unresolved||!selected.length} onPress={run} style={[s.run,(unresolved||!selected.length)&&{opacity:.5}]}><Text style={s.runText}>{active?'回測處理中…':status==='paused'?'請先繼續查詢目前工作':`執行 ${selected.length} 種策略回測`}</Text></Pressable>
  </View>
  {!!status&&<View style={s.card} accessibilityLiveRegion="polite"><View style={s.wrap}>{active&&<ActivityIndicator color="#73b9ff"/>}<Text style={s.heading}>{STATUS[status]||status}</Text></View>{job&&<><Text style={s.note}>本次股票 {job.stock_code} {STOCKS[job.stock_code]||''}　已等待 {Math.floor(elapsed/60)} 分 {elapsed%60} 秒</Text><Text selectable style={s.note}>工作編號：{job.run_id}</Text></>}{active&&<Text style={s.note}>正在等待伺服器完成，頁面會自動更新。模型計算可能需要數分鐘；後端未提供百分比進度。</Text>}{status==='paused'&&<Pressable onPress={resume} style={s.chip}><Text style={s.text}>繼續查詢</Text></Pressable>}</View>}
  {!!error&&<Text accessibilityRole="alert" style={s.error}>{error}</Text>}
  <Text style={s.note}>本頁暫存最近 5 次已完成結果；離開或重新整理頁面可能清除。伺服器重啟後工作編號可能失效，新版結果尚未加入永久歷史資料庫。</Text>
  {history.length>0&&<View style={s.wrap}>{history.map(r=><Pressable disabled={unresolved} key={r.run_id} style={[s.chip,result?.run_id===r.run_id&&s.chosen]} onPress={()=>setResult(r)}><Text style={s.text}>{r.stock_code} · {r.strategies.length} 策略 · {r.run_id.slice(-8)}</Text></Pressable>)}</View>}
  {result&&<>
   <View style={s.card}><Text style={s.heading}>{result.stock_code} {STOCKS[result.stock_code]||''} · 回測結果</Text><Text style={s.note}>{result.backtest_period.start} ～ {result.backtest_period.end}</Text><Text style={s.note}>資料角色：{result.data_role}（歷史開發）</Text><Text style={s.note}>工作編號：{result.run_id}</Text><Text style={s.note}>無交易或不適用的數據顯示「不適用」；0 代表後端明確回傳的零值。</Text>
   <ScrollView horizontal><View><View style={s.tableRow}><Text style={[s.cell,s.label]}>基本績效</Text>{rows.map(r=><Text key={r.strategy_id} style={[s.cell,{color:strategyInfo(r.strategy_id).color,fontWeight:'700'}]}>{strategyInfo(r.strategy_id).name}</Text>)}</View>{METRICS.map(([key,name,unit])=><View key={key} style={s.tableRow}><Text style={[s.cell,s.label]}>{name}</Text>{rows.map(r=><Text selectable key={r.strategy_id} style={s.cell}>{formatMetric(r.summary?.[key],unit)}</Text>)}</View>)}</View></ScrollView>
   {METRICS.map(([,name,,meaning])=><Text key={name} style={s.note}>{name}：{meaning}</Text>)}
   <Text style={s.note}>Buy & Hold 期末仍持有股票，損益包含未實現損益，未計期末賣出成本；曝險比例也與 Risk V1 策略不同。</Text></View>
   {preferences.showCharts&&<View style={s.card}><MultiStrategyCharts rows={rows}/></View>}
   <MultiGeminiPanel key={'ai:'+result.run_id} result={result}/>
   <StockNewsPanel key={'news:'+result.run_id} report={{analysisId:result.run_id}} apiBase={`/api/backtests/multi/${encodeURIComponent(result.run_id)}`} />
   <Text style={s.heading}>策略專業細節</Text>{rows.map(r=><MultiStrategyDetails key={result.run_id+':'+r.strategy_id} row={r} audit={result.audit} defaultExpanded={preferences.expandDetails}/>)}
  </>}
 </ScrollView>;
}
const s=StyleSheet.create({page:{flexGrow:1,backgroundColor:marketColors.background,padding:20,paddingBottom:70,gap:18,width:'100%',maxWidth:1440,alignSelf:'center'},hero:{padding:24,borderRadius:20,backgroundColor:'#10283f',gap:12},eyebrow:{fontSize:12,color:'#73b9ff',letterSpacing:2},title:{fontSize:30,fontWeight:'800',color:'#f8fafc'},period:{fontSize:17,fontWeight:'700',color:'#ffcc66'},card:{padding:20,gap:16,borderWidth:1,borderColor:'#294052',backgroundColor:'#0d1c29',borderRadius:18},heading:{color:'#f8fafc',fontSize:20,fontWeight:'700'},text:{color:'#e2e8f0',fontSize:15},note:{color:'#a5b7ca',lineHeight:22,fontSize:14},input:{borderWidth:1,borderColor:'#476581',borderRadius:10,color:'#f8fafc',padding:12,fontSize:20,maxWidth:280},wrap:{flexDirection:'row',flexWrap:'wrap',gap:10,alignItems:'center'},chip:{padding:11,backgroundColor:'#172d40',borderWidth:1,borderColor:'#294052',borderRadius:9},chosen:{backgroundColor:'#224c77',borderColor:'#73b9ff'},strategy:{padding:17,borderWidth:1,borderColor:'#294052',borderRadius:12,minWidth:210,flexGrow:1,gap:8},run:{backgroundColor:'#1670d6',padding:16,borderRadius:12,alignItems:'center'},runText:{color:'white',fontWeight:'700',fontSize:17},error:{color:'#ffb0b8',backgroundColor:'#371c28',padding:16,borderRadius:10,lineHeight:24},tableRow:{flexDirection:'row',borderBottomWidth:1,borderColor:'#294052'},cell:{width:170,padding:14,color:'#e2e8f0',fontSize:15},label:{width:145,backgroundColor:'#142b3d'}});

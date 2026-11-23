import usePageLayout from '../styles/usePageLayout';
import {useLocalSearchParams} from 'expo-router';
import React,{useEffect,useRef,useState,useMemo} from 'react';
import {ActivityIndicator,Pressable,ScrollView,StyleSheet,Text,View} from 'react-native';
import {apiRequest} from '../src/config/api';
import {STRATEGIES,STOCKS,METRICS,formatMetric,numeric,strategyInfo,resultRows,explainError,fetchRunUpdate} from '../src/features/multiBacktestModel';
import {loadPreferences,subscribePreferences,DEFAULT_PREFERENCES,notifyBacktestCompleted} from '../src/features/preferences';
import {colors,radius,space,type,ui} from '../styles/theme';
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
 const [period,setPeriod]=useState({start:'2024-07-01',end:'2025-06-30'}),[showHistory,setShowHistory]=useState(false),[showTable,setShowTable]=useState(false);
 const [profile,setProfile]=useState(null);
 const [historyVersion,setHistoryVersion]=useState(0),[opening,setOpening]=useState(false),[saving,setSaving]=useState(false);
 const [preferences,setPreferences]=useState(DEFAULT_PREFERENCES),[notice,setNotice]=useState('');
 const [stock,setStock]=useState('2357'),[selected,setSelected]=useState(['rf','xgboost','gru']);
 const [job,setJob]=useState(null),[status,setStatus]=useState(''),[result,setResult]=useState(null),[error,setError]=useState(''),[elapsed,setElapsed]=useState(0),[history,setHistory]=useState([]);
 const token=useRef(0),timer=useRef(null),mounted=useRef(true),busy=useRef(false),failures=useRef(0);
 useEffect(()=>{let alive=true;apiRequest('/api/backtests/multi/profile').then(p=>{if(p.profile_id!=='main-buy-noadd-noextension-common-v1-20261114')throw Error('後端尚未載入新版主要策略，請重新啟動後端');if(alive)setProfile(p);}).catch(e=>{if(alive)setError('主要策略設定讀取失敗：'+e.message);});return()=>{alive=false;};},[]);
 const coverage=profile?.coverage?.[stock];
 const FULL_PERIOD={start:'2024-07-01',end:'2025-06-30'};
 // 回測期間一律從 2024/07/01 起算，最長到 2025/06/30（24下半～25上半）。
 const periods=[{start:'2024-07-01',end:'2024-09-30',label:'三個月'},{start:'2024-07-01',end:'2024-12-30',label:'半年'},{start:'2024-07-01',end:'2025-06-30',label:'一年'}];
 const fits=(code,p)=>!!profile?.coverage?.[code]&&p.start>=profile.coverage[code].start&&p.end<=profile.coverage[code].end;
 const supported=code=>periods.some(p=>fits(code,p));
 const formal=profile?.formal_reports?.[stock];
 const available=p=>!!coverage&&((p.start>=coverage.start&&p.end<=coverage.end)||formal?.periods?.some(x=>x.start===p.start&&x.end===p.end));
 const requestedStrategies=['technical',...selected.filter(x=>x!=='technical')];
 useEffect(()=>{if(!profile||busy.current)return;if(!supported(stock)){const next=profile.stocks.find(supported);if(next)setStock(next);}else if(!available(period)){const best=[...periods].reverse().find(available);if(best)setPeriod({start:best.start,end:best.end});}},[profile,stock]);
 useEffect(()=>{if(typeof params.stock==='string'&&STOCKS[params.stock]&&!busy.current)setStock(params.stock);},[params.stock]);
 const active=['pending','running','submitting'].includes(status);
 const unresolved=active||status==='paused'||opening||saving;
 const approvedStocks=useMemo(()=>Object.fromEntries((profile?.stocks||[]).map(code=>[code,profile?.stock_names?.[code]||STOCKS[code]||code])),[profile]);
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
  if(!STOCKS[stock]||!supported(stock)){setError('請選擇股票按鈕');return;}
  if(!periods.some(p=>p.start===period.start&&p.end===period.end)||!available(period)){setError(`${stock} ${STOCKS[stock]||''} 尚未備妥 ${period.start}～${period.end} 的資料（目前可用 ${coverage?.start||'－'}～${coverage?.end||'－'}），資料未補齊前無法執行`);return;}
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
 const periodChosen=p=>period.start===p.start&&period.end===p.end;
 const step=(n,title)=><View style={s.stepHead}><View style={s.stepBadge}><Text style={s.stepNum}>{n}</Text></View><Text style={s.heading}>{title}</Text></View>;
 return <ScrollView style={{flex:1,backgroundColor:colors.background}} contentContainerStyle={[s.page,layout.page]}>
  {!!notice&&<Pressable onPress={()=>setNotice('')} style={[ui.notice,ui.noticeSuccess]}><Text accessibilityLiveRegion="polite" style={ui.noticeSuccessText}>{notice}（點擊關閉）</Text></Pressable>}
  <View style={[s.hero,layout.card]}><Text style={ui.eyebrow}>STRATEGY LAB / 歷史模擬</Text><Text style={[s.title,layout.title]}>看懂策略，從每一筆交易開始</Text><Text style={s.lead}>選股票、選期間、比較策略。先看買賣位置，再了解報酬與風險。</Text><View style={s.periodPill}><Text style={s.periodLabel}>可用資料</Text><Text style={s.period}>{coverage?.start||'讀取中'} — {coverage?.end||'讀取中'}</Text></View><Text style={s.note}>回測期間統一為 2024/07/01～2025/06/30 歷史模擬，不是最新績效或投資訊號。純技術指標與買進持有固定加入比較。</Text></View>
  <View style={[s.card,layout.card]}>
   <View style={s.section}>{step('1','選擇股票')}<View style={s.wrap}>{Object.entries(STOCKS).map(([code,name])=><Pressable key={code} disabled={unresolved||!supported(code)} accessibilityRole="button" accessibilityState={{disabled:unresolved||!supported(code),selected:stock===code}} onPress={()=>setStock(code)} style={[s.chip,stock===code&&s.chosen,!supported(code)&&ui.disabled]}><Text style={[s.text,stock===code&&s.chosenText]}>{code} {name}</Text></Pressable>)}</View><View style={[ui.notice,ui.noticeInfo]}><Text style={ui.noticeInfoText}>主要策略：不加碼、不延期；共同完整日＋三模型合法預測覆蓋。每組25萬元，首次含費最多使用現金40%，保留初始20%，保留2.5%停損。</Text></View></View>
   <View style={s.divider}/>
   <View style={s.section}>{step('2','選擇回測期間')}<View style={s.wrap}>{periods.map(p=><Pressable disabled={unresolved||!available(p)} key={p.label} accessibilityRole="button" accessibilityState={{disabled:unresolved||!available(p),selected:periodChosen(p)}} onPress={()=>setPeriod({start:p.start,end:p.end})} style={[s.chip,s.periodChip,periodChosen(p)&&s.chosen,!available(p)&&ui.disabled]}><Text style={[s.chipTitle,periodChosen(p)&&s.chosenText]}>{p.label}</Text><Text style={s.chipSub}>{p.start} ～ {p.end}</Text></Pressable>)}</View></View>
   <View style={s.divider}/>
   <View style={s.section}>{step('3','勾選策略（可複選）')}<View style={s.wrap}>{STRATEGIES.filter(item=>item.id!=='technical').map(item=><Pressable key={item.id} accessibilityRole="checkbox" accessibilityLabel={item.name} accessibilityState={{checked:selected.includes(item.id),disabled:unresolved}} disabled={unresolved} onPress={()=>setSelected(old=>old.includes(item.id)?old.filter(x=>x!==item.id):[...old,item.id])} style={[s.strategy,selected.includes(item.id)&&{borderColor:item.color,backgroundColor:colors.surfaceRaised}]}><View style={s.strategyHead}><View style={[s.checkbox,selected.includes(item.id)&&{backgroundColor:item.color,borderColor:item.color}]}><Text style={s.checkMark}>{selected.includes(item.id)?'✓':''}</Text></View><Text style={[s.text,{fontWeight:'700',color:item.color}]}>{item.name}</Text></View><Text style={s.note}>{item.subtitle}</Text></Pressable>)}</View>
   <Text style={s.note}>比較基準：純技術指標與 Buy & Hold（買進持有）固定加入，不必勾選。</Text></View>
   <Pressable accessibilityRole="button" disabled={unresolved||!selected.length||!profile} onPress={run} style={({pressed})=>[s.run,pressed&&ui.buttonPrimaryPressed,(unresolved||!selected.length)&&ui.disabled]}><Text style={s.runText}>{active?'回測處理中…':status==='paused'?'請先繼續查詢目前工作':`查看 ${selected.length} 種模型策略＋2 項比較基準`}</Text></Pressable>
  </View>
  {!!status&&<View style={[s.card,layout.card]} accessibilityLiveRegion="polite"><View style={s.wrap}>{active&&<ActivityIndicator color={colors.primaryText}/>}<Text style={s.heading}>{STATUS[status]||status}</Text></View>{job&&<><Text style={s.note}>本次股票 {job.stock_code} {profile?.stock_names?.[job.stock_code]||STOCKS[job.stock_code]||''}　已等待 {Math.floor(elapsed/60)} 分 {elapsed%60} 秒</Text><Text selectable style={[s.note,ui.caption]}>工作編號：{job.run_id}</Text></>}{active&&<Text style={s.note}>正在等待伺服器完成，頁面會自動更新。模型計算可能需要數分鐘；後端未提供百分比進度。</Text>}{status==='paused'&&<Pressable onPress={resume} style={[ui.buttonSecondary,{alignSelf:'flex-start'}]}><Text style={ui.buttonSecondaryText}>繼續查詢</Text></Pressable>}</View>}
  {!!error&&<View style={[ui.notice,ui.noticeError]}><Text accessibilityRole="alert" style={s.error}>{error}</Text></View>}
  <Pressable accessibilityRole="button" accessibilityState={{expanded:showHistory}} onPress={()=>setShowHistory(!showHistory)} style={({pressed})=>[ui.buttonSecondary,{alignSelf:'flex-start'},pressed&&ui.buttonSecondaryPressed]}><Text style={ui.buttonSecondaryText}>{showHistory?'收合':'開啟'}歷史報告　↗</Text></Pressable>
  {showHistory&&<MultiReportHistory onSelect={openHistory} disabled={unresolved} refreshKey={historyVersion}/>}
  {opening&&<ActivityIndicator color={colors.primaryText}/>}
  {history.length>0&&<View style={s.wrap}>{history.map(r=><Pressable disabled={unresolved} key={r.run_id} style={[s.chip,result?.run_id===r.run_id&&s.chosen]} onPress={()=>setResult(r)}><Text style={[s.text,result?.run_id===r.run_id&&s.chosenText]}>{r.stock_code} · {r.strategies.length} 策略 · {r.run_id.slice(-8)}</Text></Pressable>)}</View>}
  {result&&<>
   <BacktestTradeChart key={result.run_id} result={result}/>
   {preferences.showCharts&&<View style={[s.card,layout.card]}><MultiStrategyCharts rows={rows}/></View>}
   <View style={[ui.notice,result.persistence?.status==='saved'?ui.noticeSuccess:ui.noticeError,{alignItems:'center',flexWrap:'wrap'}]}><Text style={result.persistence?.status==='saved'?ui.noticeSuccessText:ui.noticeErrorText}>{result.persistence?.status==='saved'?'此報告已永久存入資料庫，可從歷史清單重新開啟。':result.persistence?.message||'此報告尚未確認存入資料庫，請重試儲存。'}</Text>{result.persistence?.status!=='saved'&&<Pressable accessibilityRole="button" disabled={saving} onPress={retrySave} style={ui.buttonSecondary}><Text style={ui.buttonSecondaryText}>{saving?'儲存中…':'重試儲存報告'}</Text></Pressable>}</View>
   <View style={[s.card,layout.card]}><Text style={ui.eyebrow}>RESULT</Text><Text style={s.resultTitle}>{result.stock_code} {profile?.stock_names?.[result.stock_code]||STOCKS[result.stock_code]||''} · 回測結果</Text>
   <View style={s.metaGrid}>{[['期間',`${result.backtest_period.start} ～ ${result.backtest_period.end}`],['資料角色',`${result.audit?.dataset_role||result.data_role}（歷史研究）`],['規則版本',result.audit?.profile_id||'舊版歷史規則'],['報告識別',result.run_id]].map(([k,v])=><View key={k} style={s.metaItem}><Text style={ui.caption}>{k}</Text><Text selectable style={s.metaValue}>{v}</Text></View>)}</View>
   <Text style={ui.caption}>無交易或不適用的數據顯示「不適用」；0 代表後端明確回傳的零值。</Text>
   <View style={s.wrap}>{rows.map(r=><View key={r.strategy_id} style={[s.resultCard,{borderTopColor:strategyInfo(r.strategy_id).color}]}><Text style={[s.text,{color:strategyInfo(r.strategy_id).color,fontWeight:'700'}]}>{strategyInfo(r.strategy_id).name}</Text><Text style={[s.bigNumber,{color:numeric(r.summary?.total_return_pct)?(r.summary.total_return_pct>0?colors.rising:r.summary.total_return_pct<0?colors.falling:colors.text):colors.textMuted}]}>{formatMetric(r.summary?.total_return_pct,'%')}</Text><View style={s.resultLine}><Text style={ui.caption}>淨損益</Text><Text style={s.resultValue}>{formatMetric(r.summary?.net_pnl,'元')}</Text></View><View style={s.resultLine}><Text style={ui.caption}>最大回撤</Text><Text style={s.resultValue}>{formatMetric(r.summary?.max_drawdown_pct,'%')}</Text></View></View>)}</View>
   <Pressable onPress={()=>setShowTable(!showTable)} accessibilityRole="button" accessibilityState={{expanded:showTable}} style={ui.buttonGhost}><Text style={ui.link}>{showTable?'收合':'展開'}完整績效比較表</Text></Pressable>
   {showTable&&<ScrollView horizontal style={s.tableFrame}><View><View style={ui.tableHeader}><Text style={[ui.tableHeadCell,s.cell,s.label]}>基本績效</Text>{rows.map(r=><Text key={r.strategy_id} style={[ui.tableHeadCell,s.cell,s.numCell,{color:strategyInfo(r.strategy_id).color}]}>{strategyInfo(r.strategy_id).name}</Text>)}</View>{METRICS.map(([key,name,unit],i)=><View key={key} style={[ui.tableRow,i%2===1&&ui.tableRowAlt]}><Text style={[ui.tableCell,s.cell,s.label]}>{name}</Text>{rows.map(r=><Text selectable key={r.strategy_id} style={[ui.tableCell,s.cell,s.numCell]}>{formatMetric(r.summary?.[key],unit)}</Text>)}</View>)}</View></ScrollView>}
   {showTable&&<View style={s.glossary}>{METRICS.map(([,name,,meaning])=><Text key={name} style={s.note}><Text style={{color:colors.text,fontWeight:'600'}}>{name}</Text>：{meaning}</Text>)}</View>}
   <Text style={s.note}>{result.audit?.profile_id?'買進持有期初最多投入80%，受成交量限制；期末賣出並計費稅，投入比例與主動策略不同。':'Buy & Hold 期末仍持有股票，損益包含未實現損益，未計期末賣出成本；投入資金比例也與主動策略不同。'}</Text></View>

   <MultiGeminiPanel key={'ai:'+result.run_id} result={result} onSaved={()=>setHistoryVersion(v=>v+1)}/>
   <StockNewsPanel key={'news:'+result.run_id} report={{analysisId:result.run_id}} apiBase={`/api/backtests/multi/${encodeURIComponent(result.run_id)}`} />
   <Text style={s.sectionTitle}>策略專業細節</Text>{rows.map(r=><MultiStrategyDetails key={result.run_id+':'+r.strategy_id} row={r} audit={result.audit} defaultExpanded={preferences.expandDetails}/>)}
  </>}
 </ScrollView>;
}
const s=StyleSheet.create({
 page:{flexGrow:1,backgroundColor:colors.background,paddingTop:space.lg,paddingBottom:96,gap:space.md,width:'100%',alignSelf:'center'},
 hero:{...StyleSheet.flatten(ui.hero)},
 title:{...type.display},
 lead:{...type.body,color:colors.textMuted,fontSize:16,lineHeight:26},
 periodPill:{flexDirection:'row',flexWrap:'wrap',alignItems:'center',gap:space.xs,alignSelf:'flex-start',paddingHorizontal:space.sm,paddingVertical:6,borderRadius:radius.pill,backgroundColor:colors.warningSurface,borderWidth:1,borderColor:colors.warningBorder},
 periodLabel:{...type.caption,color:colors.warningText},
 period:{...type.small,...type.number,fontWeight:'700',color:colors.warning},
 card:{...StyleSheet.flatten(ui.card)},
 section:{gap:space.sm},
 divider:{height:1,backgroundColor:colors.borderSoft},
 stepHead:{flexDirection:'row',alignItems:'center',gap:space.sm},
 stepBadge:{width:28,height:28,borderRadius:radius.pill,backgroundColor:colors.primarySoft,borderWidth:1,borderColor:colors.primary,alignItems:'center',justifyContent:'center'},
 stepNum:{...type.small,color:colors.primaryText,fontWeight:'800',lineHeight:16},
 heading:{...type.heading},
 sectionTitle:{...type.title,marginTop:space.md},
 text:{...type.body,fontSize:14,lineHeight:22},
 note:{...type.small},
 wrap:{flexDirection:'row',flexWrap:'wrap',gap:space.xs,alignItems:'center'},
 chip:{...StyleSheet.flatten(ui.chip)},
 periodChip:{minWidth:150,paddingVertical:10,gap:2},
 chipTitle:{...type.subheading,fontSize:15},
 chipSub:{...type.caption,...type.number},
 chosen:{...StyleSheet.flatten(ui.chipSelected)},
 chosenText:{color:colors.primaryText,fontWeight:'700'},
 strategy:{padding:space.md,borderWidth:1,borderColor:colors.border,borderRadius:radius.md,backgroundColor:colors.surfaceSunken,minWidth:0,flexBasis:220,flexGrow:1,gap:space.xs},
 strategyHead:{flexDirection:'row',alignItems:'center',gap:space.xs},
 checkbox:{width:20,height:20,borderRadius:6,borderWidth:1.5,borderColor:colors.borderStrong,alignItems:'center',justifyContent:'center'},
 checkMark:{color:colors.background,fontSize:13,lineHeight:16,fontWeight:'900'},
 run:{...StyleSheet.flatten(ui.buttonPrimary),minHeight:52},
 runText:{...StyleSheet.flatten(ui.buttonPrimaryText)},
 error:{...StyleSheet.flatten(ui.noticeErrorText),fontSize:14,lineHeight:22},
 resultTitle:{...type.title,marginTop:-space.xs},
 metaGrid:{flexDirection:'row',flexWrap:'wrap',gap:space.xs},
 metaItem:{flexGrow:1,flexBasis:220,minWidth:0,padding:space.sm,borderRadius:radius.sm,backgroundColor:colors.surfaceSunken,borderWidth:1,borderColor:colors.borderSoft,gap:2},
 metaValue:{...type.small,color:colors.text,...type.number},
 resultCard:{padding:space.md,borderWidth:1,borderTopWidth:3,borderColor:colors.border,borderRadius:radius.md,backgroundColor:colors.surfaceSunken,minWidth:140,flexBasis:140,flexGrow:1,gap:6},
 bigNumber:{...type.display,...type.number,fontSize:26,lineHeight:34},
 resultLine:{flexDirection:'row',flexWrap:'wrap',justifyContent:'space-between',alignItems:'baseline',columnGap:space.xs},
 resultValue:{...type.small,...type.number,color:colors.text},
 tableFrame:{borderWidth:1,borderColor:colors.border,borderRadius:radius.sm},
 glossary:{gap:space.xxs,padding:space.sm,borderRadius:radius.sm,backgroundColor:colors.surfaceSunken},
 cell:{width:160},
 numCell:{textAlign:'right'},
 label:{width:140,color:colors.textMuted},
});

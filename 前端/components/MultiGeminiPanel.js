import ReportText from './ReportText';
import React,{useEffect,useState,useRef} from 'react';
import {ActivityIndicator,Pressable,Text,View,Platform} from 'react-native';
import {apiRequest} from '../src/config/api';
import {STOCKS} from '../src/features/multiBacktestModel';
import {colors,space,type,ui} from '../styles/theme';
export default function MultiGeminiPanel({result,onSaved}){
 const [persistence,setPersistence]=useState(null);
 const [analysis,setAnalysis]=useState(null),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const sequence=useRef(0),inFlight=useRef(false);
 const url=`/api/backtests/multi/${encodeURIComponent(result.run_id)}/analysis`;
 useEffect(()=>{const id=++sequence.current;apiRequest(url).then(x=>{if(id===sequence.current){setAnalysis(x.analysis);setPersistence(x.persistence);}}).catch(e=>{if(id===sequence.current)setError(e.message);}).finally(()=>{if(id===sequence.current)setLoading(false);});return()=>{sequence.current++;};},[url]);
 async function generate(){if(inFlight.current)return;inFlight.current=true;setBusy(true);setError('');const id=sequence.current;try{const x=await apiRequest(url,{method:'POST',timeoutMs:145000});if(id===sequence.current){setAnalysis(x.analysis);setPersistence(x.persistence);if(x.persistence?.status==='saved')onSaved?.();}}catch(e){if(id===sequence.current)setError(e.message);}finally{inFlight.current=false;if(id===sequence.current)setBusy(false);}}
 function download(){const content=`${result.stock_code} ${STOCKS[result.stock_code]||''} 多策略回測分析\n${result.backtest_period.start} ～ ${result.backtest_period.end}\n${analysis.model} · ${analysis.generatedAt}\n\n${analysis.text}`;const link=document.createElement('a'),objectUrl=URL.createObjectURL(new Blob([content],{type:'text/plain;charset=utf-8'}));link.href=objectUrl;link.download=`${result.stock_code}-Gemini-${result.run_id}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);}
 const text=type.body;
 return <View style={ui.card}>
  <View style={{gap:space.xxs}}><Text style={ui.eyebrow}>AI INSIGHT</Text><Text style={ui.title}>Gemini 智慧分析</Text></View>
  <Text style={[ui.small,type.number]}>{result.stock_code} {STOCKS[result.stock_code]||''} · {result.backtest_period.start} ～ {result.backtest_period.end}</Text>
  {loading&&<ActivityIndicator color={colors.primaryText}/>}
  {!!error&&<Text accessibilityRole="alert" style={[ui.small,{color:colors.dangerText}]}>{error}</Text>}
  {analysis?<><ReportText text={analysis.text}/><Text style={ui.caption}>{analysis.model} · {new Date(analysis.generatedAt).toLocaleString('zh-TW')}</Text>{Platform.OS==='web'&&<Pressable accessibilityRole="button" onPress={download} style={({pressed})=>[ui.buttonSecondary,{alignSelf:'flex-start'},pressed&&ui.buttonSecondaryPressed]}><Text style={ui.buttonSecondaryText}>下載本次 Gemini 分析</Text></Pressable>}</>:<><Text style={text}>比較本次所選策略與 Buy & Hold，整理績效、近期交易及風險。僅解讀歷史回測，不使用最新新聞推論歷史報酬。</Text><Text style={ui.small}>按下產生後，會將本次回測摘要與各策略最近 10 筆交易送至 Google Gemini，可能產生 API 費用；不會傳送帳號或密碼。</Text><Pressable accessibilityRole="button" disabled={busy||loading} onPress={generate} style={({pressed})=>[ui.buttonPrimary,{alignSelf:'flex-start'},pressed&&ui.buttonPrimaryPressed,(busy||loading)&&ui.disabled]}><Text style={ui.buttonPrimaryText}>{busy?'Gemini 正在分析，請稍候…':'產生 Gemini 智慧分析'}</Text></Pressable>{busy&&<ActivityIndicator color={colors.primaryText}/>}</>}
  {analysis&&<Text style={[ui.small,{color:persistence?.status==='saved'?colors.successText:colors.dangerText}]}>{persistence?.status==='saved'?'Gemini 分析已存入此份歷史報告。':persistence?.message||'分析尚未確認存入資料庫。'}</Text>}
  {analysis&&persistence?.status!=='saved'&&<Pressable disabled={busy} onPress={generate} style={ui.buttonGhost}><Text style={ui.link}>{busy?'保存中…':'重試保存分析（不重新產生）'}</Text></Pressable>}
  <Text style={ui.caption}>新聞在下方分開呈現，不受研究日期限制，也不會納入回測或模型訓練。</Text>
 </View>;
}

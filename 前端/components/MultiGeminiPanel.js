import React,{useEffect,useState,useRef} from 'react';
import {ActivityIndicator,Pressable,Text,View,Platform} from 'react-native';
import {apiRequest} from '../src/config/api';
import {STOCKS} from '../src/features/multiBacktestModel';
export default function MultiGeminiPanel({result,onSaved}){
 const [persistence,setPersistence]=useState(null);
 const [analysis,setAnalysis]=useState(null),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const sequence=useRef(0),inFlight=useRef(false);
 const url=`/api/backtests/multi/${encodeURIComponent(result.run_id)}/analysis`;
 useEffect(()=>{const id=++sequence.current;apiRequest(url).then(x=>{if(id===sequence.current){setAnalysis(x.analysis);setPersistence(x.persistence);}}).catch(e=>{if(id===sequence.current)setError(e.message);}).finally(()=>{if(id===sequence.current)setLoading(false);});return()=>{sequence.current++;};},[url]);
 async function generate(){if(inFlight.current)return;inFlight.current=true;setBusy(true);setError('');const id=sequence.current;try{const x=await apiRequest(url,{method:'POST',timeoutMs:145000});if(id===sequence.current){setAnalysis(x.analysis);setPersistence(x.persistence);if(x.persistence?.status==='saved')onSaved?.();}}catch(e){if(id===sequence.current)setError(e.message);}finally{inFlight.current=false;if(id===sequence.current)setBusy(false);}}
 function download(){const content=`${result.stock_code} ${STOCKS[result.stock_code]||''} 多策略回測分析\n${result.backtest_period.start} ～ ${result.backtest_period.end}\n${analysis.model} · ${analysis.generatedAt}\n\n${analysis.text}`;const link=document.createElement('a'),objectUrl=URL.createObjectURL(new Blob([content],{type:'text/plain;charset=utf-8'}));link.href=objectUrl;link.download=`${result.stock_code}-Gemini-${result.run_id}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);}
 const text={color:'#dce7f5',fontSize:15,lineHeight:26};
 return <View style={{padding:22,borderWidth:1,borderColor:'#294052',backgroundColor:'#0d1c29',borderRadius:18,gap:14}}>
  <Text style={[text,{fontSize:23,fontWeight:'700'}]}>Gemini 智慧分析</Text>
  <Text style={text}>{result.stock_code} {STOCKS[result.stock_code]||''} · {result.backtest_period.start} ～ {result.backtest_period.end}</Text>
  {loading&&<ActivityIndicator color="#73b9ff"/>}
  {!!error&&<Text accessibilityRole="alert" style={[text,{color:'#ffb0b8'}]}>{error}</Text>}
  {analysis?<><Text selectable style={text}>{analysis.text}</Text><Text style={[text,{color:'#98adc2',fontSize:12}]}>{analysis.model} · {new Date(analysis.generatedAt).toLocaleString('zh-TW')}</Text>{Platform.OS==='web'&&<Pressable accessibilityRole="button" onPress={download} style={{padding:12,backgroundColor:'#184873',borderRadius:10}}><Text style={text}>下載本次 Gemini 分析</Text></Pressable>}</>:<><Text style={text}>比較本次所選策略與 Buy & Hold，整理績效、近期交易及風險。僅解讀歷史回測，不使用最新新聞推論歷史報酬。</Text><Text style={[text,{color:'#98adc2',fontSize:13}]}>按下產生後，會將本次回測摘要與各策略最近 10 筆交易送至 Google Gemini，可能產生 API 費用；不會傳送帳號或密碼。</Text><Pressable accessibilityRole="button" disabled={busy||loading} onPress={generate} style={{padding:14,backgroundColor:'#184873',borderRadius:10,opacity:busy||loading?.5:1}}><Text style={text}>{busy?'Gemini 正在分析，請稍候…':'產生 Gemini 智慧分析'}</Text></Pressable>{busy&&<ActivityIndicator color="#73b9ff"/>}</>}
  {analysis&&<Text style={[text,{color:persistence?.status==='saved'?'#8ce0b0':'#ffb0b8'}]}>{persistence?.status==='saved'?'Gemini 分析已存入此份歷史報告。':persistence?.message||'分析尚未確認存入資料庫。'}</Text>}
  {analysis&&persistence?.status!=='saved'&&<Pressable disabled={busy} onPress={generate}><Text style={text}>{busy?'保存中…':'重試保存分析（不重新產生）'}</Text></Pressable>}
  <Text style={[text,{color:'#98adc2',fontSize:13}]}>新聞在下方分開呈現，不受研究日期限制，也不會納入回測或模型訓練。</Text>
 </View>;
}

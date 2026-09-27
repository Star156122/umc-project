import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { apiRequest } from '../src/config/api';
import { marketColors as colors } from '../styles/marketTheme';

function NewsGroup({analysisId, mode, onSaved}) {
  const [data,setData]=useState(null);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [summarizing,setSummarizing]=useState(false);
  const sequence=useRef(0);
  async function load(summarize=false) {
    const id=++sequence.current;
    setError('');
    summarize?setSummarizing(true):setLoading(true);
    try {
      const result=await apiRequest(`/api/reports/analysis/${analysisId}/news/${mode}${summarize?'/summary':''}`,{method:summarize?'POST':'GET',timeoutMs:summarize?150000:25000});
      if(sequence.current===id) {setData(result.news); onSaved?.();}
    } catch(e) {if(sequence.current===id) setError(e.message);}
    finally {if(sequence.current===id) {setLoading(false);setSummarizing(false);}}
  }
  useEffect(()=>{load();return ()=>{sequence.current++;};},[analysisId,mode]);
  return <View style={styles.card}>
    <Text style={styles.title}>{mode==='latest'?'最新消息｜最近 30 天':'回測期間消息'}</Text>
    {data && <Text style={styles.meta}>{data.start} ～ {data.end} · 查詢於 {new Date(data.fetchedAt).toLocaleString('zh-TW')}</Text>}
    <Text style={styles.note}>{data?.note || (mode==='latest'?'現在的消息與歷史回測分開呈現。':'只顯示回測期間發布的新聞，不代替完整歷史資料。')}</Text>
    {loading && <ActivityIndicator color={colors.primary}/>}
    {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
    {!!data?.warning && <Text style={styles.note}>{data.warning}</Text>}
    {!!data?.summary && <View style={styles.summary}>
      <Text style={styles.title}>Gemini 新聞標題摘要</Text>
      <Text style={styles.text}>{data.summary.text}</Text>
      <Text style={styles.meta}>依下列編號新聞標題整理，未讀取全文。{data.summary.model}</Text>
    </View>}
    {data?.items?.map((item,index)=><Pressable key={item.url} accessibilityRole="link" onPress={()=>Linking.openURL(item.url).catch(()=>setError('無法開啟新聞連結'))} style={styles.item}>
      <Text style={styles.link}>[{index+1}] {item.title}</Text>
      <Text style={styles.meta}>{item.publishedDate} · {item.source} · 開啟來源 ↗</Text>
    </Pressable>)}
    {data && !data.items.length && <Text style={styles.text}>此期間未找到符合條件的新聞；不代表沒有事件發生。</Text>}
    {!!data?.coverage && <Text style={styles.meta}>{data.coverage}</Text>}
    <View style={styles.actions}>
      <Pressable disabled={loading||summarizing} onPress={()=>load()} style={styles.button}><Text style={styles.link}>重新載入新聞</Text></Pressable>
      {!!data?.items?.length && !data.summary && <Pressable disabled={loading||summarizing} onPress={()=>load(true)} style={styles.button}><Text style={styles.link}>{summarizing?'Gemini 正在整理…':'產生 Gemini 摘要'}</Text></Pressable>}
    </View>
    {!data?.summary && !!data?.items?.length && <Text style={styles.meta}>產生摘要會將上述公開標題送至 Gemini，使用已設定的 API；可能產生費用。</Text>}
  </View>;
}
export default function StockNewsPanel({report,onSaved}) {
  return <View>
    <Text style={styles.heading}>個股新聞與事件</Text>
    <NewsGroup key={`${report.analysisId}:latest`} analysisId={report.analysisId} mode="latest" onSaved={onSaved}/>
    <NewsGroup key={`${report.analysisId}:historical`} analysisId={report.analysisId} mode="historical" onSaved={onSaved}/>
  </View>;
}
const styles=StyleSheet.create({
  heading:{fontSize:22,fontWeight:'700',color:colors.text,marginVertical:18},
  card:{backgroundColor:colors.surface||'#0d1e29',borderWidth:1,borderColor:colors.border||'#294455',borderRadius:20,padding:22,marginBottom:18,gap:12},
  title:{fontSize:18,fontWeight:'700',color:colors.text||'#f1f5f9'},
  text:{fontSize:15,lineHeight:25,color:colors.text||'#f1f5f9'},
  note:{fontSize:14,lineHeight:23,color:colors.textMuted},
  meta:{fontSize:12,lineHeight:20,color:colors.textMuted},
  error:{fontSize:14,color:colors.danger||'#ff9c9c'},
  link:{fontSize:15,lineHeight:24,color:colors.primary},
  item:{paddingVertical:10,borderBottomWidth:1,borderBottomColor:colors.border||'#294455',gap:4},
  summary:{gap:10,paddingVertical:12},
  actions:{flexDirection:'row',flexWrap:'wrap',gap:12},
  button:{padding:10,borderWidth:1,borderColor:colors.primary,borderRadius:10},
});

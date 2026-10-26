import React,{useState,useEffect} from 'react';
import {Pressable,ScrollView,Text,View} from 'react-native';
import {formatMetric,strategyInfo} from '../src/features/multiBacktestModel';
const text={color:'#dce7f5',lineHeight:23};
const labels={datetime:'時間',timestamp:'時間戳',trade_id:'交易編號',code:'股票',action:'買賣',price:'成交價',quantity:'股數',gross_amount:'成交金額',fee:'手續費',tax:'交易稅',net_cash_flow:'淨現金流',cash_before:'交易前現金',cash_after:'交易後現金',model_version:'模型版本',training_signal_range:'訓練訊號期間',validation_signal_range:'驗證訊號期間',training_parameters:'訓練參數',training_samples:'訓練樣本數',validation_samples:'驗證樣本數',training_target_max:'訓練標籤最晚日期',validation_target_max:'驗證標籤最晚日期',version:'版本',risk_per_trade:'單筆風險比例',max_position_fraction:'持倉上限比例',stop_loss_pct:'停損比例',sizing_reference:'部位計算依據',gap_risk_note:'跳空風險說明',decision:'風控決策',final_quantity:'最終股數',technical_signal_time:'技術訊號時間',ml_prediction:'模型預測',hybrid_action:'放行／阻擋',source_files:'資料來源檔案',known_limitations:'研究限制',experiment_id:'實驗編號'};
function valueText(v){if(v==null)return '不適用';if(typeof v==='boolean')return v?'是':'否';if(typeof v==='number')return formatMetric(v);if(v==='Buy')return '買入';if(v==='Sell')return '賣出';return String(v);}
function DataTree({value}) {
 if(value==null)return <Text style={text}>不適用</Text>;
 if(typeof value!=='object')return <Text selectable style={text}>{valueText(value)}</Text>;
 return <View style={{gap:8,paddingLeft:10,borderLeftWidth:1,borderColor:'#294052'}}>{Object.entries(value).map(([k,v])=><View key={k}><Text style={{color:'#9eb0bf'}}>{labels[k]||k}</Text>{typeof v==='object'&&v!==null?<DataTree value={v}/>:<Text selectable style={text}>{valueText(v)}</Text>}</View>)}</View>;
}
function Fold({title,children}){const [open,setOpen]=useState(false);return <View style={{gap:12}}><Pressable accessibilityRole="button" accessibilityState={{expanded:open}} onPress={()=>setOpen(!open)}><Text style={{color:'#73b9ff',fontSize:16}}>{open?'－':'＋'} {title}</Text></Pressable>{open&&children}</View>;}
function Records({rows}) {
 const [page,setPage]=useState(0); const data=Array.isArray(rows)?rows:[];
 if(!data.length)return <Text style={text}>沒有紀錄。</Text>;
 const columns=[...new Set(data.flatMap(r=>Object.keys(r)))];const pages=Math.ceil(data.length/20);
 return <View style={{gap:12}}><Text style={text}>共 {data.length} 筆 · 第 {page+1} / {pages} 頁（每頁 20 筆）</Text><ScrollView horizontal><View><View style={{flexDirection:'row',backgroundColor:'#20374a'}}>{columns.map(k=><Text key={k} style={{...text,width:170,padding:10,fontWeight:'700'}}>{labels[k]||k}</Text>)}</View>{data.slice(page*20,page*20+20).map((r,i)=><View key={i} style={{flexDirection:'row',backgroundColor:i%2?'#102334':'#0d1c29'}}>{columns.map(k=><Text selectable key={k} style={{...text,width:170,padding:10}}>{typeof r[k]==='object'&&r[k]!==null?JSON.stringify(r[k]):valueText(r[k])}</Text>)}</View>)}</View></ScrollView><View style={{flexDirection:'row',gap:24}}><Pressable disabled={page===0} onPress={()=>setPage(p=>p-1)}><Text style={{color:page===0?'#617083':'#73b9ff'}}>上一頁</Text></Pressable><Pressable disabled={page===pages-1} onPress={()=>setPage(p=>p+1)}><Text style={{color:page===pages-1?'#617083':'#73b9ff'}}>下一頁</Text></Pressable></View></View>;
}
export default function MultiStrategyDetails({row,audit,defaultExpanded=false}) {
 const [open,setOpen]=useState(defaultExpanded);useEffect(()=>setOpen(defaultExpanded),[defaultExpanded]);const d=row.details||{},info=strategyInfo(row.strategy_id);
 const metrics=[['profit_factor','獲利因子'],['sharpe_ratio','夏普比率'],['average_win','平均獲利'],['average_loss','平均虧損'],['payoff_ratio','盈虧比'],['commission_total','手續費'],['transaction_tax_total','交易稅'],['total_transaction_cost','總交易成本']];
 return <View style={{padding:20,borderWidth:1,borderColor:'#294052',borderRadius:14,gap:16,backgroundColor:'#0d1c29'}}>
  <View style={{flexDirection:'row',flexWrap:'wrap',justifyContent:'space-between',gap:12}}><Text style={{color:info.color,fontSize:19,fontWeight:'700'}}>{info.name}</Text><Pressable accessibilityRole="button" accessibilityState={{expanded:open}} onPress={()=>setOpen(!open)}><Text style={{color:'#73b9ff'}}>{open?'收合細節':'展開專業細節'}</Text></Pressable></View>
  <Text selectable style={{color:'#9eb0bf'}}>策略版本：{row.strategy_version||'未提供'}</Text>
  {open&&<>
   <View style={{flexDirection:'row',flexWrap:'wrap',gap:18}}>{metrics.map(([key,name])=><View key={key} style={{minWidth:140,flexGrow:1}}><Text style={{color:'#9eb0bf'}}>{name}</Text><Text style={text}>{formatMetric(d[key])}</Text>{d[key]===null&&!!d[key+'_reason']&&<Text style={{color:'#9eb0bf',fontSize:12}}>{d[key+'_reason']}</Text>}</View>)}</View>
   {row.strategy_id==='buy_and_hold'&&<Text style={text}>買進持有以可負擔的整股買入並持有至期末，以收盤價估值，沒有期末賣出，所以未計賣出手續費與交易稅。沒有 Risk V1 停損或 40% 持倉上限；已完成交易次數與勝率不適用。</Text>}
   <Fold title="ML Gate 放行／阻擋統計">{d.gate_applicable?<View style={{gap:8}}><Text style={text}>已評估候選：{formatMetric(d.gate_evaluated_candidate_count)}　未評估：{formatMetric(d.gate_not_evaluated_candidate_count)}　放行進場：{formatMetric(d.gate_entry_count)}</Text><DataTree value={d.gate_block_counts}/><Text style={text}>{d.gate_not_evaluated_reason||''}</Text><Records rows={d.gate_decisions}/></View>:<Text style={text}>不適用（純技術策略或買進持有基準）。</Text>}</Fold>
   <Fold title="Risk V1 風控資訊"><DataTree value={d.risk_contract}/><Records rows={d.risk_decisions}/></Fold>
   <Fold title="模型版本與訓練期間"><DataTree value={d.model_training}/></Fold>
   <Fold title="完整買入、賣出交易明細"><Records rows={d.trades||row.trades}/>{row.strategy_id==='buy_and_hold'&&<DataTree value={{entry_timestamp:d.entry_timestamp,entry_price:d.entry_price,shares_held:d.shares_held,transaction_count:d.transaction_count,exposure_assumption:d.exposure_assumption}}/>}</Fold>
   <Fold title="策略與研究資料來源"><DataTree value={d.strategy_source||row.audit}/><DataTree value={{experiment_id:audit?.experiment_id,source_files:audit?.source_files,known_limitations:audit?.known_limitations}}/></Fold>
  </>}
 </View>;
}

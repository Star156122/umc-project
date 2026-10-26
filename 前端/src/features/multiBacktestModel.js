export const STRATEGIES = [
  {id:'technical',name:'純技術指標',subtitle:'Technical V2-B',color:'#ff6574'},
  {id:'rf',name:'Random Forest',subtitle:'RF',color:'#53b7ff'},
  {id:'xgboost',name:'XGBoost',subtitle:'機器學習',color:'#ffcc66'},
  {id:'gru',name:'GRU',subtitle:'深度學習',color:'#ba97ff'},
];
export const BENCHMARK = {id:'buy_and_hold',name:'Buy & Hold',subtitle:'買進持有',color:'#47d7ab'};
export const STOCKS = {'2303':'聯電','2330':'台積電','2379':'瑞昱','2454':'聯發科','3034':'聯詠'};
export const METRICS = [
  ['initial_capital','初始本金','元','回測開始時的資金'],
  ['final_equity','最終資產','元','回測結束時的資金總值'],
  ['total_return_pct','總報酬率','%','期間資產增減的百分比'],
  ['net_pnl','淨損益','元','計入交易成本後的資產損益'],
  ['win_rate_pct','勝率','%','獲利的已完成交易占比'],
  ['trade_count','交易次數','筆','已完成的交易筆數'],
  ['max_drawdown_pct','最大回撤','%','資產從高點下跌的最大幅度'],
];
export function numeric(value) {return typeof value==='number' && Number.isFinite(value);}
export function formatMetric(value,unit='') {
  return numeric(value)?`${value.toLocaleString('zh-TW',{maximumFractionDigits:unit==='筆'?0:2})}${unit?' '+unit:''}`:'不適用';
}
export function strategyInfo(id){return [...STRATEGIES,BENCHMARK].find(x=>x.id===id)||{id,name:id,color:'#d1d5db'};}
export function resultRows(result){return [...(result?.strategies||[]),...(result?.buy_and_hold?[result.buy_and_hold]:[])];}
export function validateResult(result,expected) {
  if(result?.status!=='completed'||result?.api_version!=='multi_strategy_backtest_v1'||!Array.isArray(result.strategies)||!result.buy_and_hold)throw Error('後端結果格式不完整，未顯示不確定的績效。');
  if(result.run_id!==expected.run_id||String(result.stock_code)!==String(expected.stock_code))throw Error('回測結果與目前工作不一致，請重新查詢。');
  if(result.strategies.map(x=>x.strategy_id).sort().join(',')!==[...expected.strategies].sort().join(','))throw Error('回傳策略與提交選項不同，請由後端確認。');
  if(result.data_role!=='historical_development'||result.backtest_period?.start!=='2024-07-01'||result.backtest_period?.end!=='2024-12-30')throw Error('後端資料期間或角色與目前開發模式不符，已停止顯示。');
  return result;
}
export function explainError(error) {
  const code=error?.data?.error?.code||error?.code;
  const message=error?.data?.error?.message||error?.message||'請求失敗';
  const hints={BACKTEST_RUN_NOT_FOUND:'這份工作已失效，可能是 API 重新啟動或工作超過保存期限。請重新執行；舊版歷史報告仍可查閱。',BACKTEST_QUEUE_BUSY:'伺服器正在處理另一份回測，請等它結束後再提交。',BACKTEST_BLOCKED:'資料不足或研究政策限制，這次無法執行。',DEVELOPMENT_DEFAULTS_ONLY:'目前僅支援固定的 2024 年歷史開發期間。'};
  return hints[code]?`${hints[code]}${code==='BACKTEST_BLOCKED'?' '+message:''}`:message;
}
export async function fetchRunUpdate(request,job) {
  const status=await request(`/api/backtests/multi/${encodeURIComponent(job.run_id)}/status`,{timeoutMs:20000});
  if(status.status==='completed'){
    const result=await request(`/api/backtests/multi/${encodeURIComponent(job.run_id)}/result`,{timeoutMs:45000});
    if(result.status==='pending'||result.status==='running')return {status:result.status};
    return {status:'completed',result:validateResult(result,job)};
  }
  if(!['pending','running','failed','blocked'].includes(status.status))throw Error('後端回傳未知工作狀態，請稍後重新查詢。');
  return status;
}

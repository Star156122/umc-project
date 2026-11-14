// Read completed, frozen reports only. This module never runs a backtest.
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const PROFILE='pegatron4938-formal-2025h2-20261114-exp001';
const WINDOWS=[{id:'Q1',start:'2025-01-01',end:'2025-03-31'},{id:'H1',start:'2025-01-01',end:'2025-06-30'},{id:'YEAR',start:'2025-01-01',end:'2025-12-31'}];
const folder=root=>path.join(root,'獨立實驗','和碩4938_2025正式保留測試');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
function location(root){
 const base=folder(root),done=read(path.join(base,'稽核/completed.json')),used=read(path.join(base,'稽核/consumed.json'));
 const frozen=fs.readFileSync(path.join(base,'稽核/freeze.json'));
 if(done.state!=='COMPLETED_CONSUMED'||done.code_or_model_changed_after_holdout_access!==false||used.state!=='CONSUMED'||crypto.createHash('sha256').update(frozen).digest('hex')!==used.freeze_sha256)throw Error('正式報告稽核紀錄不符');
 if(!/^[0-9T]+$/.test(done.report_folder))throw Error('正式報告位置不符');
 return {base,out:path.join(base,'結果',done.report_folder),freeze:JSON.parse(frozen)};
}
function formalProfile(root){try{const {out}=location(root);if(!fs.existsSync(path.join(out,'data.js')))return {};return {'4938':{profile_id:PROFILE,periods:WINDOWS}};}catch{return {};}}
function replay(root,job){
 const win=WINDOWS.find(w=>w.start===job.period.start&&w.end===job.period.end);
 if(job.stock_code!=='4938'||!win)throw Error('此正式報告不涵蓋要求的股票或期間');
 const {out,freeze}=location(root);
 const raw=fs.readFileSync(path.join(out,'data.js'),'utf8');
 if(!raw.startsWith('window.FORMAL='))throw Error('正式圖表格式不符');
 const data=JSON.parse(raw.slice('window.FORMAL='.length).trim().replace(/;$/,''));
 const codes={technical:'TECH',rf:'RF',xgboost:'XGB',gru:'GRU',buy_and_hold:'BH'};
 function row(id){
  const r=read(path.join(out,`${win.id}_${codes[id]}_5.json`)),m=r.metrics;
  if(m.stock!=='4938'||m.start!==win.start||m.end!==win.end||m.slippage_each_side!==.0005)throw Error('正式結果識別或成本版本不符');
  const pct=v=>v==null?null:v*100;
  const trades=r.orders.map(o=>({...o,datetime:o.time,action:o.action==='BUY'?'Buy':'Sell',...(['END_OF_WINDOW','BUY_HOLD_END_CLOSE'].includes(o.reason)?{chart_timestamp:o.timestamp-1}:{})}));
  return {strategy_id:id,strategy_version:PROFILE,
   summary:{initial_capital:m.initial_capital,final_equity:m.final_equity,total_return_pct:pct(m.return),net_pnl:m.net_pnl,trade_count:m.trade_count,win_rate_pct:id==='buy_and_hold'?null:pct(m.win_rate),max_drawdown_pct:pct(m.max_drawdown)},
   details:{profit_factor:m.profit_factor,sharpe_ratio:m.sharpe,average_win:m.average_win,average_loss:m.average_loss,commission_total:m.fees,transaction_tax_total:m.tax,total_transaction_cost:m.fees+m.tax,slippage_cost:m.slippage_cost,trades,closed_trades:r.trades,
    risk_contract:id==='buy_and_hold'?{sizing_reference:'期初最多投入80%，受成交量限制；期末賣出並計費稅',stop_loss_pct:null}:{stop_loss_pct:freeze.config.stop_pct,cash_allocation_fraction:freeze.config.cash_allocation_fraction,initial_cash_reserve_fraction:freeze.config.initial_cash_reserve_fraction,max_hold_bars:freeze.config.max_hold_bars,adds_enabled:false,holding_extension_enabled:false},
    model_training:{version:'正式測試凍結模型',training_period:'2023–2024',retrained_on_2025:false},gate_applicable:['rf','xgboost','gru'].includes(id),gate_decisions:r.events,filter_funnel:r.funnel},
   trades,equity_curve:r.equity.map(e=>({timestamp:new Date(e.timestamp*1000).toISOString(),equity:e.equity}))};
 }
 const strategies=job.strategies.map(row),buy_and_hold=row('buy_and_hold');
 const audit={profile_id:PROFILE,experiment_id:PROFILE,data_role:'formal_report_replay',dataset_role:win.id==='YEAR'?'mixed_seen_and_formal_holdout':'validation_seen',stock_code:'4938',periods:{backtest:job.period},formal_report_replay:true,source_files:[path.relative(root,out)],known_limitations:['全年包含已使用的上半年與正式驗證下半年，並非全年未見資料。','只重現既有正式結果，不重訓或重跑。','普通5分K近似零股成交，不保證實際撮合。'],formal_verdicts:read(path.join(out,'formal_verdicts.json'))};
 return {api_version:'multi_strategy_backtest_v1',run_id:job.run_id,stock_code:'4938',status:'completed',data_role:audit.data_role,backtest_period:job.period,strategies,buy_and_hold,audit,comparison:{rows:[...strategies,buy_and_hold].map(r=>({strategy_id:r.strategy_id,summary:r.summary}))},market_chart:{source:'已完成正式報告的5分K',interval_minutes:5,period:job.period,candles:data.bars.filter(b=>{const d=new Date(b[0]*1000+8*3600000).toISOString().slice(0,10);return d>=win.start&&d<=win.end;}).map(b=>({time:b[0],open:b[1],high:b[2],low:b[3],close:b[4]}))}};
}
module.exports={formalProfile,replay,PROFILE};

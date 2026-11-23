const test=require('node:test'),assert=require('node:assert/strict'),path=require('path'),fs=require('fs');
const {formalProfile,replay,PROFILE}=require('../src/formalReportReplay');
const {validateMultiRunRequest,createMultiStrategyBacktestService}=require('../src/multiStrategyBacktestService');
const root=path.resolve(__dirname,'../../../程式');
const config=JSON.parse(fs.readFileSync(path.join(root,'hybrid/config/web_main_strategy_v1.json'),'utf8'));
const formal=formalProfile(root);
test('prepared yearly coverage starts an ordinary run and archived reports remain a fallback',()=>{
 const request={stock_code:'4938',strategies:['technical','rf'],start_date:'2025-01-01',end_date:'2025-12-31'};
 assert.equal(validateMultiRunRequest(request,config,formal).formal_replay,undefined);
 const fallback=structuredClone(config);
 fallback.datasets['4938'].end='2025-06-30';
 assert.equal(validateMultiRunRequest(request,fallback,formal).formal_replay,true);
 assert.throws(()=>validateMultiRunRequest({...request,start_date:'2025-07-01'},fallback,formal));
});
test('all periods preserve source metrics and candles',()=>{
 for(const [window,end] of [['Q1','2025-03-31'],['H1','2025-06-30'],['YEAR','2025-12-31']]){
  const r=replay(root,{run_id:'test',stock_code:'4938',strategies:['technical','rf','xgboost','gru'],period:{start:'2025-01-01',end}});
  assert.equal(r.strategies.length,4);assert.equal(r.buy_and_hold.strategy_id,'buy_and_hold');assert.equal(r.audit.profile_id,PROFILE);
  const base=path.join(root,r.audit.source_files[0]);
  const raw=JSON.parse(fs.readFileSync(path.join(base,`${window}_RF_5.json`),'utf8'));
  assert.equal(r.strategies[1].summary.net_pnl,raw.metrics.net_pnl);
  assert.equal(r.strategies[1].equity_curve.length,raw.equity.length);
  assert.ok(r.market_chart.candles.length>0);
  for(const b of r.market_chart.candles){const day=new Date(b.time*1000+28800000).toISOString().slice(0,10);assert.ok(day>='2025-01-01'&&day<=end);}
 }
});
test('service schedules a fresh full-year run even for a stock with an archived report',()=>{
 let scheduled=false;
 const service=createMultiStrategyBacktestService({root,schedule(){scheduled=true;}});
 const job=service.submit({stock_code:'4938',strategies:['technical','gru'],start_date:'2025-01-01',end_date:'2025-12-31'});
 assert.equal(job.formal_replay,false);
 assert.equal(scheduled,true);
});

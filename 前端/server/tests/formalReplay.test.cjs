const test=require('node:test'),assert=require('node:assert/strict'),path=require('path'),fs=require('fs');
const {formalProfile,replay,PROFILE}=require('../src/formalReportReplay');
const {validateMultiRunRequest,createMultiStrategyBacktestService}=require('../src/multiStrategyBacktestService');
const root=path.resolve(__dirname,'../../../程式');
const config=JSON.parse(fs.readFileSync(path.join(root,'hybrid/config/web_main_strategy_v1.json'),'utf8'));
const formal=formalProfile(root);
test('formal replay allows only exact completed stock and windows',()=>{
 assert.equal(validateMultiRunRequest({stock_code:'4938',strategies:['technical','rf'],start_date:'2025-01-01',end_date:'2025-12-31'},config,formal).formal_replay,true);
 for(const code of ['2357','3231','2892'])assert.throws(()=>validateMultiRunRequest({stock_code:code,strategies:['rf'],start_date:'2025-01-01',end_date:'2025-12-31'},config,formal));
 assert.throws(()=>validateMultiRunRequest({stock_code:'4938',strategies:['rf'],start_date:'2025-07-01',end_date:'2025-12-31'},config,formal));
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
test('service reads completed report without starting Python and persists for owner',async()=>{
 let saved=false;
 const service=createMultiStrategyBacktestService({root,spawnImpl(){throw Error('must not run Python');},reportStore:{async saveResult(job){assert.equal(job.ownerUserId,7);saved=true;}}});
 const job=service.submit({stock_code:'4938',strategies:['technical','gru'],start_date:'2025-01-01',end_date:'2025-12-31'});job.ownerUserId=7;
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(job.status,'completed');assert.equal(saved,true);assert.equal(job.persistence.status,'saved');assert.equal(job.result.strategies.length,2);
});

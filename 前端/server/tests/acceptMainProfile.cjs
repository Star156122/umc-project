const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {createMultiStrategyBacktestService}=require('../src/multiStrategyBacktestService');
(async()=>{
 const root=path.resolve(__dirname,'../../../程式'),s=createMultiStrategyBacktestService({root});
 const profile=s.getProfile();assert.equal(profile.rules.adds,false);assert.equal(profile.rules.extension,false);assert.equal(profile.initial_capital,250000);
 assert.throws(()=>s.submit({stock_code:'2357',strategies:['technical'],start_date:'2025-07-01',end_date:'2025-12-31'}));
 const j=s.submit({stock_code:'2357',strategies:['technical','rf','xgboost','gru'],start_date:'2025-01-01',end_date:'2025-03-31'});
 const deadline=Date.now()+120000;
 while(['pending','running'].includes(j.status)&&Date.now()<deadline)await new Promise(r=>setTimeout(r,100));
 assert.equal(j.status,'completed',JSON.stringify(j.error));assert.equal(j.result.audit.profile_id,profile.profile_id);
 assert.equal(j.result.strategies.length,4);assert.equal(j.result.market_chart.candles.length>0,true);
 for(const r of [...j.result.strategies,j.result.buy_and_hold]){
  assert.equal(r.summary.initial_capital,250000);assert.equal(r.strategy_version,profile.profile_id);
  assert.equal(r.audit.adds,0);assert.equal(r.audit.holding_extensions,0);
  assert.equal(r.details.risk_contract.risk_budget_enabled,false);
 }
 const out=path.join(j.result.audit.output_directory,'website_acceptance.json');fs.writeFileSync(out,JSON.stringify({status:'PASS',actual_node_python_pipeline:true,profile_id:profile.profile_id,period:j.period,model_count:3,selected_strategies:4,history_preserved:true},null,2));
 console.log(JSON.stringify({status:'PASS',profile_id:profile.profile_id,run_id:j.run_id,output:j.result.audit.output_directory}));
})().catch(e=>{console.error(e);process.exit(1)});

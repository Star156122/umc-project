const test=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),path=require('path');
const {validateMultiRunRequest,normalizeRunPayload}=require('../src/multiStrategyBacktestService');
const config=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../../../程式/hybrid/config/web_main_strategy_v1.json'),'utf8'));
test('new main profile validates prepared per-stock coverage and forbids holdout',()=>{
 assert.equal(config.revised_max_adds,0);assert.equal(config.holding_extension.enabled,false);assert.equal(config.initial_capital,250000);
 assert.deepEqual(validateMultiRunRequest({stock_code:'2357',strategies:['technical']},config).period,{start:'2025-01-01',end:'2025-06-30'});
 assert.deepEqual(validateMultiRunRequest({stock_code:'2330',strategies:['rf']},config).period,{start:'2024-07-01',end:'2024-12-30'});
 for(const [start,end] of [['2025-07-01','2025-12-31'],['2026-07-01','2026-12-31'],['2024-01-01','2024-06-30']])assert.throws(()=>validateMultiRunRequest({stock_code:'2357',strategies:['technical'],start_date:start,end_date:end},config));
 assert.throws(()=>validateMultiRunRequest({stock_code:'2357',strategies:['technical'],initial_capital:100000},config));
});
test('changed website JSX parses and reflects new rule identity',()=>{
 const parser=require('@babel/parser');
 for(const file of ['screens/MultiStrategyScreen.js','components/MultiStrategyDetails.js','components/BacktestTradeChart.js'])parser.parse(fs.readFileSync(path.join(__dirname,'../..',file),'utf8'),{sourceType:'module',plugins:['jsx']});
 const src=fs.readFileSync(path.join(__dirname,'../../screens/MultiStrategyScreen.js'),'utf8');assert.match(src,/不加碼、不延期/);assert.match(src,/strategy_profile/);
});

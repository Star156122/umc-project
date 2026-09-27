const assert = require('node:assert/strict');
const { test } = require('node:test');
const Module = require('node:module');
const fs = require('node:fs');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const writes = []; const spawned = [];
const connection = {
 beginTransaction: async()=>{}, commit: async()=>{}, rollback: async()=>{}, release:()=>{},
 execute: async(sql,args)=>{ writes.push({sql,args}); return [{insertId:17}]; }
};
const original = Module._load;
Module._load = function(id,parent,...rest) {
 if (id === './db') return { getConnection: async()=>connection };
 if (id === 'child_process') return {spawn(command,args,options) {
  spawned.push({args,options}); const child=new EventEmitter();
  child.stdout=new EventEmitter(); child.stderr=new EventEmitter(); child.kill=()=>{};
  setImmediate(()=>{
   const strategy=args[args.indexOf('--strategy')+1];
   const dir=path.join(options.env.REPORT_DIR,'2303',`20260927_120000_2303_${strategy}_2026-01-01_2026-06-30`);
   fs.mkdirSync(dir,{recursive:true});
   fs.writeFileSync(path.join(dir,'pnl.csv'),'\ufeffcode,net_pnl,return_pct,sell_datetime\n2303,123,0.01,2026-01-05\n');
   fs.writeFileSync(path.join(dir,'summary.json'),JSON.stringify({
    strategy, strategy_label:strategy, backtest_start:'2026-01-01',backtest_end:'2026-06-30',
    initial_capital:200000,final_assets:200321,net_pnl:123,completed_trades:1,
    total_return:0.001605,win_rate:1,max_drawdown:0.04,sharpe_ratio:0.72,profit_factor:null
   })); child.emit('close',0);
  }); return child;
 }};
 return original.call(this,id,parent,...rest);
};
const engine=require('../src/pythonBacktestService');
Module._load=original;
test('six strategies use new backend, matching profiles, isolated output and official summary',async()=>{
 assert.equal(engine.RUNNABLE_STRATEGIES.length,6);
 assert.ok(fs.existsSync(path.join(engine.PYTHON_BACKTEST_ROOT,'app_backtest.py')));
 for (const strategy of engine.RUNNABLE_STRATEGIES) {
  const result=await engine.executePythonBacktest({userId:1,code:'2303',strategy});
  const run=spawned.at(-1);
  assert.equal(run.args[run.args.indexOf('--profile')+1],`2303_${strategy}`);
  assert.ok(run.args.includes('--db-disabled')); assert.ok(run.args.includes('--only-backtest'));
  assert.ok(!run.args.includes('--disable-rsi'));
  assert.equal(result.report.summary.maxDrawdownPct,4);
  assert.equal(result.report.summary.sharpeRatio,0.72);
  assert.equal(result.report.summary.totalReturnPct,0.1605);
  assert.equal(result.report.pnlRows[0].code,'2303');
  assert.equal(result.report.summary.initialCapital,200000);
  const saved=writes.findLast(x=>x.sql.includes('INSERT INTO backtest_results'));
  assert.equal(saved.args[4],200000); assert.equal(saved.args[5],200321);
  assert.equal(fs.existsSync(run.options.env.REPORT_DIR),false);
 }
});
test('invalid ranges and locked boundaries rejected before process launch',async()=>{
 const n=spawned.length;
 for (const [startDate,endDate] of [
  ['2025-07-01','2025-07-01'],['2025-12-31','2026-01-01'],['2025-01-01','2026-06-30'],
  ['2026-02-30','2026-03-01'],['2026-05-01','2026-01-01']
 ]) await assert.rejects(engine.executePythonBacktest({userId:1,code:'2303',strategy:'ma',startDate,endDate}),e=>e.status===400);
 await assert.rejects(engine.executePythonBacktest({userId:1,code:'../2303'}),e=>e.status===400);
 await assert.rejects(engine.executePythonBacktest({userId:1,code:'2303',strategy:'unknown'}),e=>e.status===400);
 assert.equal(spawned.length,n);
});

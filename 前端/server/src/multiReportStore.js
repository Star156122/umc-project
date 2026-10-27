const zlib=require('node:zlib');
function createMultiReportStore(pool){
 let ready;
 async function ensure(){if(!ready)ready=pool.execute(`CREATE TABLE IF NOT EXISTS multi_backtest_reports (
 run_id VARCHAR(64) NOT NULL PRIMARY KEY, user_id INT NOT NULL, stock_code VARCHAR(12) NOT NULL,
 strategies_json TEXT NOT NULL, period_start DATE NOT NULL, period_end DATE NOT NULL,
 result_blob LONGBLOB NOT NULL, analysis_json LONGTEXT NULL, created_at DATETIME(3) NOT NULL,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 INDEX user_history (user_id, created_at)
 ) CHARACTER SET utf8mb4`).catch(e=>{ready=null;throw e;});await ready;}
 async function saveResult(job){
  await ensure();if(!Number.isInteger(job.ownerUserId)||job.ownerUserId<=0)throw Error('Missing report owner');
  const r=job.result,blob=zlib.gzipSync(Buffer.from(JSON.stringify(r)));
  await pool.execute(`INSERT INTO multi_backtest_reports (run_id,user_id,stock_code,strategies_json,period_start,period_end,result_blob,created_at)
 VALUES (?,?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE run_id=VALUES(run_id)`,[job.run_id,job.ownerUserId,r.stock_code,JSON.stringify(r.strategies.map(x=>x.strategy_id)),r.backtest_period.start,r.backtest_period.end,blob,new Date(job.created_at)]);
 }
 async function saveAnalysis(job){await ensure();const [result]=await pool.execute('UPDATE multi_backtest_reports SET analysis_json=? WHERE run_id=? AND user_id=?',[JSON.stringify(job.geminiAnalysis),job.run_id,job.ownerUserId]);if(!result.affectedRows)throw Error('Report not saved');}
 async function load(runId,userId){await ensure();const [rows]=await pool.execute('SELECT run_id,user_id,result_blob,analysis_json,created_at FROM multi_backtest_reports WHERE run_id=? AND user_id=?',[runId,userId]);if(!rows[0])return null;const row=rows[0],result=JSON.parse(zlib.gunzipSync(row.result_blob).toString('utf8'));return {run_id:row.run_id,ownerUserId:row.user_id,stock_code:result.stock_code,strategies:result.strategies.map(x=>x.strategy_id),status:'completed',result,geminiAnalysis:row.analysis_json?JSON.parse(row.analysis_json):null,created_at:new Date(row.created_at).toISOString(),updated_at_ms:Date.now(),persistence:{status:'saved'},analysisPersistence:{status:row.analysis_json?'saved':'none'}};}
 async function list(userId,page=0){await ensure();const limit=20,offset=page*limit;const [rows]=await pool.query(`SELECT run_id,stock_code,strategies_json,DATE_FORMAT(period_start,'%Y-%m-%d') AS period_start,DATE_FORMAT(period_end,'%Y-%m-%d') AS period_end,created_at,analysis_json IS NOT NULL AS has_analysis FROM multi_backtest_reports WHERE user_id=? ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?`,[userId,limit+1,offset]);return {page,hasMore:rows.length>limit,reports:rows.slice(0,limit).map(r=>({run_id:r.run_id,stock_code:r.stock_code,strategies:JSON.parse(r.strategies_json),start:String(r.period_start instanceof Date?r.period_start.toLocaleDateString('en-CA'):r.period_start).slice(0,10),end:String(r.period_end instanceof Date?r.period_end.toLocaleDateString('en-CA'):r.period_end).slice(0,10),created_at:r.created_at,has_analysis:Boolean(r.has_analysis)}))};}
 return {ensure,saveResult,saveAnalysis,load,list};
}
module.exports={createMultiReportStore};

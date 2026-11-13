/* Isolated browser UI fixture; no real login, account writes, or live API requests. */
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('node:assert/strict'),{createRequire}=require('module');
const {chromium}=createRequire(process.argv[2]+'/package.json')('playwright');
const {createMultiStrategyBacktestService,normalizeRunPayload}=require('../src/multiStrategyBacktestService');
(async()=>{
 const frontend=path.resolve(__dirname,'../..'),root=path.resolve(frontend,'../程式'),dist=path.join(frontend,'dist-main-profile-20261114');
 const service=createMultiStrategyBacktestService({root}),profile=service.getProfile();
 const candidates=fs.readdirSync(path.join(root,'exports')).filter(n=>n.startsWith('web_main_')&&fs.existsSync(path.join(root,'exports',n,'website_acceptance.json'))).sort();
 const result=normalizeRunPayload('ui-fixture','2357',path.join(root,'exports',candidates.at(-1)));
 const qa=path.join(root,'exports','main_strategy_website_qa');fs.mkdirSync(qa,{recursive:true});
 const server=http.createServer((req,res)=>{let name=decodeURIComponent((req.url||'/').split('?')[0]);let p=path.resolve(dist,'.'+name);if(!p.startsWith(dist+path.sep)&&p!==dist){res.writeHead(403);res.end();return}if(fs.existsSync(p)&&fs.statSync(p).isDirectory())p=path.join(p,'index.html');if(!fs.existsSync(p))p=fs.existsSync(p+'.html')?p+'.html':path.join(dist,'index.html');const type={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.ttf':'font/ttf'}[path.extname(p)]||'application/octet-stream';res.writeHead(200,{'Content-Type':type});res.end(fs.readFileSync(p))});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1440,height:1000}});let posts=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{if(window===window.top)localStorage.setItem('authToken','ui-fixture-not-real-authentication');});
 await page.route('**/api/**',async route=>{const req=route.request(),url=new URL(req.url());let body={};
  if(url.pathname==='/api/backtests/multi/profile')body=profile;
  else if(url.pathname==='/api/account')body={settings:{stock:'2357',strategies:['technical','rf','xgboost','gru'],showCharts:true,expandDetails:false,completionNotice:false,browserNotice:false}};
  else if(url.pathname==='/api/backtests/multi/run'){posts++;body={run_id:'ui-fixture',status:'pending'}}
  else if(url.pathname.endsWith('/ui-fixture/status'))body={status:'completed'};
  else if(url.pathname.endsWith('/ui-fixture/result'))body=result;
  else if(url.pathname.includes('/news/'))body={news:{items:[],start:'2025-01-01',end:'2025-03-31',fetchedAt:new Date().toISOString()}};
  else if(url.pathname.endsWith('/analysis'))body={analysis:null,persistence:null};
  else body={reports:[],items:[],news:[],hasMore:false};
  await route.fulfill({status:200,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'authorization,content-type','Access-Control-Allow-Methods':'GET,POST,OPTIONS'},body:JSON.stringify(body)});
 });
 try{
  await page.goto('http://127.0.0.1:'+server.address().port+'/analysis');await page.getByText(/主要策略：不加碼、不延期/).waitFor();
  await page.getByText('2330 台積電',{exact:true}).click();await page.waitForFunction(()=>document.querySelector('[aria-label="開始日期"]').value==='2024-07-01');
  await page.getByText('2357 華碩',{exact:true}).click();await page.waitForFunction(()=>document.querySelector('[aria-label="開始日期"]').value==='2025-01-01');
  await page.getByLabel('開始日期').fill('2025-07-01');await page.getByLabel('結束日期').fill('2025-12-31');await page.getByRole('button',{name:'執行 4 種策略回測'}).click();
  await page.getByText(/請選擇 2025-01-01～2025-06-30/).waitFor();assert.equal(posts,0);
  await page.getByLabel('開始日期').fill('2025-01-01');await page.getByLabel('結束日期').fill('2025-03-31');await page.getByRole('button',{name:'執行 4 種策略回測'}).click();
  await page.getByText('規則版本：'+profile.profile_id,{exact:true}).waitFor({timeout:20000});assert.equal(posts,1);
  await page.screenshot({path:path.join(qa,'desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(qa,'mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(qa,'ui_checks.json'),JSON.stringify({status:'PASS',api_mocked_for_ui:true,actual_python_result_rendered:true,stock_date_switch:true,holdout_blocked_before_post:true,profile_identity_checked:true,javascript_errors:errors},null,2));console.log('UI_PASS '+qa);
 }finally{await browser.close();await new Promise(r=>server.close(r))}
})().catch(e=>{console.error(e);process.exit(1)});

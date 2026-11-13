const fs=require('fs'),path=require('path'),{pathToFileURL}=require('url'),{createRequire}=require('module');
const {chromium}=createRequire(process.argv[2]+'/package.json')('playwright');
(async()=>{const out=path.resolve(process.argv[3]),browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[],checks=[];
page.on('pageerror',e=>errors.push(e.message));
for(const s of ['index','2303','2330','2379','2454','3034']){
 await page.goto(pathToFileURL(path.join(out,s+'.html')).href);await page.waitForSelector('canvas');
 let states=await page.evaluate(()=>window.CHARTS);if(Object.keys(states).length!==(s==='index'?5:1))throw Error('Missing chart');
 if(!Object.values(states).every(x=>x.visibleOrders===x.orders&&x.orders>=2))throw Error('Missing markers');
 if(s!=='index'){
  await page.locator('[data-trade="0"]').click();states=await page.evaluate(()=>window.CHARTS);
  if(states[s].end-states[s].start>=states[s].bars||states[s].visibleOrders<2)throw Error('Trade zoom lost markers');
  await page.locator('canvas').hover();await page.mouse.wheel(0,-100);
  await page.locator('[data-do="all"]').click();
  if(s==='2303'){
   await page.locator('[data-trade="0"]').click();await page.screenshot({path:path.join(out,'聯電_買賣放大預覽.png'),fullPage:true});
  }
 }
 if(s==='index')await page.screenshot({path:path.join(out,'五檔總覽預覽.png'),fullPage:true});
 checks.push({page:s+'.html',markers:'PASS',trade_zoom:'PASS'});
}
await page.setViewportSize({width:390,height:844});await page.goto(pathToFileURL(path.join(out,'2303.html')).href);await page.locator('[data-trade="0"]').click();await page.screenshot({path:path.join(out,'手機預覽.png'),fullPage:true});
await browser.close();if(errors.length)throw Error(errors.join('\n'));fs.writeFileSync(path.join(out,'html_qa.json'),JSON.stringify({pages:checks,javascript_errors:errors},null,2));console.log(JSON.stringify({pages:checks.length,errors:errors.length}));
})().catch(e=>{console.error(e);process.exit(1)});

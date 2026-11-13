const fs=require('fs'),path=require('path'),{pathToFileURL}=require('url'),{createRequire}=require('module');
const {chromium}=createRequire(process.argv[2]+'/package.json')('playwright');
(async()=>{const H=path.resolve(process.argv[3]),latest=JSON.parse(fs.readFileSync(path.join(H,'結果/latest.json'),'utf8')),O=path.join(H,'結果',latest.folder),browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1450,height:1050}}),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(pathToFileURL(path.join(O,'index.html')).href);await page.waitForSelector('#slip');
for(const s of ['5','0']){await page.selectOption('#slip',s);if(await page.evaluate(()=>document.body.innerText.includes('NaN')||document.querySelectorAll('#content table').length!==5))throw Error('Bad summary')}
await page.selectOption('#slip','5');await page.screenshot({path:path.join(O,'總覽預覽.png'),fullPage:true});checks.push({page:'index.html',status:'PASS'});
for(const w of ['Q1','H1','YEAR','H2'])for(const arm of ['index','BH','TECH','RF','XGB','GRU']){
 await page.goto(pathToFileURL(path.join(O,'買賣圖_'+w,arm+'.html')).href);await page.waitForSelector('canvas');
 const states=await page.evaluate(()=>window.CHARTS);
 if(!Object.values(states).every(x=>x.visibleOrders===x.orders))throw Error('Lost markers');
 if(arm!=='index'&&states[arm].orders>0){await page.locator('[data-trade="0"]').click();if(!await page.locator('.count').innerText())throw Error('Zoom failed')}
 if(w==='YEAR'&&arm==='GRU')await page.screenshot({path:path.join(O,'全年GRU預覽.png'),fullPage:true});
 checks.push({page:'買賣圖_'+w+'/'+arm+'.html',status:'PASS'});
}
await page.setViewportSize({width:390,height:844});await page.goto(pathToFileURL(path.join(O,'index.html')).href);await page.screenshot({path:path.join(O,'手機預覽.png'),fullPage:true});
await browser.close();if(errors.length)throw Error(errors.join('\n'));fs.writeFileSync(path.join(O,'html_qa.json'),JSON.stringify({pages:checks,javascript_errors:errors},null,2));console.log(JSON.stringify({pages:checks.length,errors:errors.length}));
})().catch(e=>{console.error(e);process.exit(1)});

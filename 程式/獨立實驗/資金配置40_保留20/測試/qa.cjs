const fs=require('fs'),path=require('path'),{pathToFileURL}=require('url'),{createRequire}=require('module');
const {chromium}=createRequire(process.argv[2]+'/package.json')('playwright');
(async()=>{
 const root=path.resolve(process.argv[3]),latest=JSON.parse(fs.readFileSync(path.join(root,'結果/latest.json'),'utf8')),out=path.join(root,'結果',latest.folder);
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[],checks=[];
 page.on('pageerror',e=>errors.push(e.message));
 for(const file of ['index',...['2303','2330','2379','2454','3034'].flatMap(s=>[s+'_funnel',s+'_analysis'])]){
  await page.goto(pathToFileURL(path.join(out,file+'.html')).href);await page.waitForSelector('#slip');
  for(const slip of ['5','0']){
   await page.selectOption('#slip',slip);
   const ok=await page.evaluate(()=>!document.body.innerText.includes('NaN')&&!document.body.innerText.includes('undefined')&&document.querySelector('#content table')?.rows.length>1);
   if(!ok)throw Error('Bad rendering '+file);
  }
  await page.selectOption('#slip','5');
  if(file.endsWith('_analysis')){
   const ok=await page.evaluate(()=>{
    const c=document.querySelector('#chart'),p=c.getContext('2d').getImageData(0,0,c.width,c.height).data,colors=new Set();
    for(let i=0;i<p.length;i+=16)colors.add(p[i]+','+p[i+1]+','+p[i+2]);return colors.size>10;
   });if(!ok)throw Error('Empty chart '+file);
   await page.locator('#from').fill('2024-11-01');await page.locator('#from').dispatchEvent('change');
   if(!await page.locator('#chartnote').innerText())throw Error('Chart filter failed');
   await page.locator('#from').fill('2024-10-01');await page.locator('#from').dispatchEvent('change');
  }
  if(['index','2330_analysis','2379_funnel'].includes(file))await page.screenshot({path:path.join(out,'preview_'+file+'.png'),fullPage:true});
  checks.push({page:file+'.html',status:'PASS'});
 }
 await page.setViewportSize({width:390,height:844});await page.goto(pathToFileURL(path.join(out,'index.html')).href);
 await page.screenshot({path:path.join(out,'preview_mobile.png'),fullPage:true});
 await browser.close();if(errors.length)throw Error(errors.join('\n'));
 fs.writeFileSync(path.join(out,'html_qa.json'),JSON.stringify({pages:checks,javascript_errors:errors,mobile_preview:true},null,2));
 console.log(JSON.stringify({pages:checks.length,errors:errors.length,out}));
})().catch(e=>{console.error(e);process.exit(1)});

const fs=require('fs');
const path=require('path');
const {pathToFileURL}=require('url');
const {createRequire}=require('module');
const req=createRequire(process.argv[2]+'/package.json');
const {chromium}=req('playwright');
(async()=>{
  const root=path.resolve(process.argv[3]);
  const latest=JSON.parse(fs.readFileSync(path.join(root,'結果/latest.json'),'utf8'));
  const out=path.join(root,'結果',latest.folder);
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let checks=[];
  for(const name of ['index',...['C','D'].flatMap(v=>[v+'_funnel',v+'_analysis'])]){
    await page.goto(pathToFileURL(path.join(out,name+'.html')).href);
    await page.waitForSelector('#scope');
    if(!await page.locator('h1').innerText())throw Error('Missing heading');
    for(const scope of ['full','common']){
      await page.selectOption('#scope',scope);
      for(const slip of ['5','0']){
        await page.selectOption('#slip',slip);
        const bad=await page.evaluate(()=>document.body.innerText.includes('NaN')||document.body.innerText.includes('undefined'));
        if(bad)throw Error('Invalid rendered numbers '+name);
        const returnCards=await page.evaluate(()=>{
          const scope=document.querySelector('#scope').value,slip=document.querySelector('#slip').value;
          return ['C','D'].every(v=>{
            const m=window.EXPERIMENT.results[v+'_'+slip+'_'+scope].metrics;
            const value=m.return*100;
            const shown=parseFloat(document.querySelector('#nav-return-'+v)?.textContent.replaceAll(',',''));
            const tolerance=(Math.abs(value)>0&&Math.abs(value)<.01)?.00005:.005;
            return Number.isFinite(shown)&&Math.abs(shown-value)<=tolerance;
          });
        });
        if(!returnCards)throw Error('Strategy return cards do not match selected metrics '+name);
        const ledgerErrors=await page.evaluate(()=>{
          const errs=[];
          for(const [key,r] of Object.entries(window.EXPERIMENT.results)){
            const m=r.metrics;
            if(Math.abs(m.net_pnl-m.closed_trade_net_pnl-m.unrealized_pnl)>.02)errs.push(key+': PnL reconciliation');
            for(const o of r.orders){
              if(!Number.isInteger(o.quantity)||o.quantity<=0)errs.push(key+': quantity');
              if(o.time.slice(11,16)<'09:10')errs.push(key+': before odd open');
              if(o.action==='SELL'){
                const buys=r.orders.filter(b=>b.trade_id===o.trade_id&&b.action!=='SELL');
                if(buys.some(b=>b.time.slice(0,10)>=o.time.slice(0,10)))errs.push(key+': odd same-day exit');
              }
            }
          }
          return errs;
        });
        if(ledgerErrors.length)throw Error(ledgerErrors.join(';'));
      }
    }
    await page.selectOption('#scope','full');await page.selectOption('#slip','5');
    if(name.endsWith('_analysis')){
      await page.locator('#date').fill('2023-06-20');
      await page.locator('#date').dispatchEvent('change');
      await page.selectOption('#count','540');
      await page.locator('#latest').click();
      const lastEntry=await page.evaluate(()=>{
        const v=document.body.dataset.variant;
        return window.EXPERIMENT.results[v+'_5_full'].trades.at(-1)?.entry_day;
      });
      if(lastEntry){await page.locator('#date').fill(lastEntry);await page.locator('#date').dispatchEvent('change');}
      const pixels=await page.locator('#candles').evaluate(c=>{
        const a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let nonzero=0;
        for(let i=3;i<a.length;i+=4)if(a[i]>0)nonzero++;return nonzero;
      });
      if(pixels<500)throw Error('Empty K-line chart');
    }
    if(name==='index'||name==='D_funnel'||name==='D_analysis'){
      await page.screenshot({path:path.join(out,'preview_'+name+'.png'),fullPage:true});
    }
    checks.push({page:name+'.html',controls:'PASS',javascript:'PASS'});
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto(pathToFileURL(path.join(out,'D_analysis.html')).href);
  await page.screenshot({path:path.join(out,'preview_mobile.png'),fullPage:true});
  await browser.close();
  if(errors.length)throw Error(errors.join('\n'));
  fs.writeFileSync(path.join(out,'html_qa.json'),JSON.stringify({pages:checks,javascript_errors:errors,mobile_preview:true,ledger_checks:'share quantities, no same-day odd exits, no pre-09:10 executions, PnL reconciliation'},null,2));
  console.log(JSON.stringify({html_pages:checks.length,javascript_errors:errors.length,out}));
})().catch(e=>{console.error(e);process.exit(1);});

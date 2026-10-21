// Real public market data + local paper ledger. No model or exchange credentials.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const origin=process.env.PREVIEW_URL||'http://127.0.0.1:8790';
const artifacts=process.env.CHECK_ARTIFACTS_DIR||'/tmp/vela-paper-checks';
fs.mkdirSync(artifacts,{recursive:true});

async function until(task,predicate,description,timeout=20000){
  const end=Date.now()+timeout;
  while(Date.now()<end){const value=await task();if(predicate(value))return value;await new Promise(r=>setTimeout(r,300));}
  throw new Error('Timed out: '+description);
}

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({viewport:{width:1440,height:1050}});
  await context.addInitScript(()=>{window.__walletCalls=[];window.ethereum={request:async args=>{window.__walletCalls.push(args.method);throw Error('Wallet must never be used by paper trading');}};});
  const page=await context.newPage();
  const errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>requests.push({url:r.url(),method:r.method()}));
  const read=()=>page.evaluate(async()=>{
    const token=localStorage.getItem('vela:paper:session:v1');
    const r=await fetch('/api/paper/account',{headers:{'X-Vela-Paper':token}});return r.json();
  });
  let result;
  try{
    await page.goto(origin+'/paper');
    await page.getByRole('button',{name:'Place paper long',exact:false}).waitFor();
    await until(()=>page.getByRole('button',{name:'Place paper long',exact:false}).isEnabled(),x=>x,'live data ready',45000);
    assert.equal(await page.getByRole('link',{name:'Powered by Lighter'}).getAttribute('href'),'https://lighter.xyz');
    assert((await page.locator('body').innerText()).includes('PAPER TRADING'));
    assert.equal((await read()).totals.trades,0,'Fresh account has no invented results');
    const health=await (await page.request.get(origin+'/api/paper/health')).json();
    if(!health.modelReady){
      await page.getByRole('button',{name:'Analyze & start AI'}).click();
      await page.getByText('250 verified closed candles').waitFor({timeout:15000});
      assert((await read()).positions.length===0,'Disconnected model must not create AI trades');
    }
    const entries=[];
    for(const [symbol,side] of [['BTC','long'],['ETH','short'],['SOL','long']]){
      await page.getByRole('button',{name:new RegExp(symbol+'PERP')}).click();
      await page.getByRole('button',{name:side==='long'?'↗ Long':'↘ Short',exact:true}).click();
      await page.locator('#paper-notional').fill('100');
      await page.getByRole('button',{name:'Place paper '+side,exact:false}).click();
      const state=await until(read,a=>a.positions?.some(p=>p.symbol===symbol+'/USD'),'position '+symbol);
      const position=state.positions.find(p=>p.symbol===symbol+'/USD');entries.push({symbol,side,qty:position.qty,entry:position.entry,stop:position.stop});
      assert.equal(position.side,side);
      assert(Number(position.riskBudget)<=2);
    }
    await page.screenshot({path:path.join(artifacts,'desktop-open-positions.png'),fullPage:true});
    await page.reload();
    await page.getByRole('tab',{name:'Positions (3)'}).waitFor();
    let a=await read();assert.equal(a.positions.length,3,'Reload retains ledger');
    const btcRow=page.locator('tbody tr').filter({hasText:'BTC PERP'});
    await btcRow.getByRole('button',{name:'Close 50%',exact:true}).click();
    a=await until(read,a=>a.trades?.length===1,'partial close');
    assert.equal(a.positions.length,3);assert(Number(a.positions.find(p=>p.symbol==='BTC/USD').qty)<Number(entries[0].qty));
    for(const symbol of ['BTC','ETH','SOL']){
      await page.locator('tbody tr').filter({hasText:symbol+' PERP'}).getByRole('button',{name:'Close',exact:true}).click();
      await until(read,a=>!a.positions.some(p=>p.symbol===symbol+'/USD'),'close '+symbol);
    }
    await page.getByRole('button',{name:/BTCPERP/}).click();
    await page.getByRole('button',{name:'Limit',exact:true}).click();
    await page.locator('#paper-limit').fill('1000');
    await page.getByRole('button',{name:'Place paper long',exact:false}).click();
    await until(read,a=>a.orders.some(o=>o.status==='pending'&&o.payload.type==='limit'),'pending limit');
    await page.getByRole('tab',{name:/Orders/}).click();
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    await until(read,a=>!a.orders.some(o=>o.status==='pending'),'cancelled limit');
    await page.getByRole('tab',{name:'Trade history'}).click();
    assert.equal(await page.locator('tbody tr').count(),4);
    await page.getByRole('tab',{name:'Performance'}).click();
    assert(await page.getByText('Today (UTC)',{exact:true}).isVisible());
    await page.getByRole('tab',{name:'Agent activity'}).click();
    assert(await page.getByText('position opened',{exact:true}).count()>=3);
    await page.getByRole('button',{name:'Market',exact:true}).click();
    await context.setOffline(true);
    await until(()=>page.getByRole('button',{name:'Place paper long',exact:false}).isDisabled(),x=>x,'offline gate',20000);
    await context.setOffline(false);
    await until(()=>page.getByRole('button',{name:'Place paper long',exact:false}).isEnabled(),x=>x,'automatic reconnect',30000);
    for(const width of [1440,1024,768,390,360]){
      await page.setViewportSize({width,height:950});
      await page.waitForTimeout(150);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'Overflow at '+width);
      await page.screenshot({path:path.join(artifacts,`paper-${width}.png`),fullPage:true});
    }
    await page.getByRole('button',{name:'Toggle navigation'}).click();
    assert.equal(await page.getByRole('button',{name:'Toggle navigation'}).getAttribute('aria-expanded'),'true');
    a=await read();
    assert.equal(a.trades.length,4);assert.equal(a.positions.length,0);
    assert(Math.abs(Number(a.balance)-Number(a.initial)-a.trades.reduce((n,t)=>n+Number(t.pnl),0))<.000001,'Funding and closed-fill P&L reconcile');
    assert.equal(await page.evaluate(()=>window.__walletCalls.length),0);
    assert(!requests.some(r=>/sendTx|start-quote|wallet-|mydoge|whytradingbot|api\.openai|bybit/i.test(r.url)));
    assert.deepEqual(errors,[]);
    result={verifiedAt:new Date().toISOString(),livePublicMarketData:true,realModel:false,execution:'PAPER',
      trades:4,scope:'One isolated engineering browser session; closed fills include one partial exit',
      pnl:a.pnl,entries,positionsRemaining:0,walletRequests:0,errors,widths:[1440,1024,768,390,360]};
    fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify(result,null,2)+'\n');
    console.log('PASS: real feed; three markets; long/short; partial/full exits; limit/cancel; persistence; P&L; disconnect/reconnect; responsive views; zero wallet calls.');
  }finally{
    await context.setOffline(false).catch(()=>{});
    // Close only this test's simulated positions if an assertion interrupted the test.
    await page.evaluate(async()=>{
      const headers={'Content-Type':'application/json','X-Vela-Paper':localStorage.getItem('vela:paper:session:v1')};
      const a=await(await fetch('/api/paper/account',{headers})).json();
      for(const p of a.positions||[])await fetch(`/api/paper/positions/${p.id}/close`,{method:'POST',headers,body:JSON.stringify({clientId:crypto.randomUUID(),fraction:'1'})});
    }).catch(()=>{});
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exit(1);});

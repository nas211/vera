// Mock wallet provider + real EIP-191 signatures. Live public market data,
// isolated demo ledger and optionally the configured real development model.
// Never a real wallet approval, deposit, exchange order or profit track record.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const {generatePrivateKey,privateKeyToAccount}=require('viem/accounts');
const origin=process.env.PREVIEW_URL||'http://127.0.0.1:8791';
const artifacts=process.env.CHECK_ARTIFACTS_DIR||'/tmp/vela-portal-checks';
fs.mkdirSync(artifacts,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(task,predicate,label,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const v=await task();if(predicate(v))return v;await sleep(350);}throw Error('Timed out: '+label);}

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'/opt/google/chrome/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({viewport:{width:1440,height:1080}});
  const signer=privateKeyToAccount(generatePrivateKey());
  const allWalletMethods=[];await context.exposeFunction('__trackWalletMethod',method=>allWalletMethods.push(method));
  await context.exposeFunction('__signFixture',data=>signer.signMessage({message:{raw:data}}));
  await context.addInitScript(({address})=>{
    const events={};let chain='0x1',connected=false;
    window.__walletCalls=[];window.__reject=false;
    window.__walletEvent=(name,value)=>{if(name==='accountsChanged'&&!value.length)connected=false;(events[name]||[]).forEach(f=>f(value));};
    window.ethereum={isMetaMask:true,on:(name,fn)=>(events[name]||=[]).push(fn),removeListener:(name,fn)=>{events[name]=(events[name]||[]).filter(f=>f!==fn);},request:async({method,params})=>{
      window.__walletCalls.push(method);window.__trackWalletMethod(method);
      if(method==='eth_requestAccounts'){if(window.__reject){window.__reject=false;throw Object.assign(Error('Rejected'),{code:4001});}connected=true;return [address];}
      // Restore a provider permission across page loads, like an approved wallet.
      if(method==='eth_accounts')return [address];
      if(method==='eth_chainId')return sessionStorage.getItem('fixture-chain')||chain;
      if(method==='wallet_switchEthereumChain'){chain=params[0].chainId;sessionStorage.setItem('fixture-chain',chain);window.__walletEvent('chainChanged',chain);return null;}
      if(method==='personal_sign'){if(window.__rejectSignature){window.__rejectSignature=false;throw Object.assign(Error('Rejected signature'),{code:4001});}if(window.__holdSignature)await new Promise(resolve=>window.__releaseSignature=resolve);return window.__signFixture(params[0]);}
      if(method==='eth_getBalance')return '0xa688906bd8b00000';
      if(method==='wallet_revokePermissions'){connected=false;return null;}
      throw Error('Unexpected wallet call: '+method);
    }};
  },{address:signer.address});
  const page=await context.newPage(),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push({url:r.url(),method:r.method()}));
  const read=()=>page.evaluate(async()=>{const r=await fetch('/api/portal/account');return r.json();});
  const post=(route,body={})=>page.evaluate(async({route,body})=>{const r=await fetch('/api/portal/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-Vela-Portal':'1'},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};},{route,body});
  const nav=name=>page.getByRole('navigation',{name:'Trading navigation'}).getByRole('button',{name,exact:true}).click();
  const result={verifiedAt:new Date().toISOString(),wallet:'mock provider with real EIP-191 signatures',marketData:'live public Lighter',execution:'PAPER',realWalletApproval:false,productionDeployed:false,liveProtectionChanged:false,errors,widths:[]};
  try{
    await page.goto(origin+'/');
    assert.equal((await page.locator('.hero-actions a').first().innerText()).replace(/\s+/g,' '),'START TRADING ↗');
    await page.getByRole('link',{name:'START TRADING'}).click();
    await page.getByRole('heading',{name:'Welcome to Vela'}).waitFor();
    assert.equal(await page.getByRole('navigation').count(),0);
    assert.equal(await page.getByTestId('equity').count(),0);
    assert.equal(await page.locator('.portal-wallet-options>button').count(),2);
    for(const width of [320,390,768,1440]){
      await page.setViewportSize({width,height:640});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Onboarding fits '+width);
      await page.screenshot({path:path.join(artifacts,`wallet-${width}.png`),fullPage:true});
    }
    await page.setViewportSize({width:1440,height:1080});
    assert(await page.getByRole('button',{name:/DogeOS \/ MyDoge/}).isDisabled());
    assert((await page.locator('.portal-connect-card').innerText()).includes('Coming Soon — Mobile Only'));
    await page.evaluate(()=>window.__reject=true);
    await page.getByRole('button',{name:'Connect MetaMask',exact:true}).click();
    await page.getByText('Cancelled in your wallet. You can try again.').waitFor();
    await page.evaluate(()=>window.__rejectSignature=true);
    await page.getByRole('button',{name:'Connect MetaMask',exact:true}).click();
    await page.getByText('Cancelled in your wallet. You can try again.').waitFor();
    assert.equal(await page.getByTestId('equity').count(),0);
    await page.evaluate(()=>window.__holdSignature=true);
    await page.getByRole('button',{name:'Connect MetaMask',exact:true}).click();
    await page.getByText('Sign the authentication message in MetaMask…').waitFor();
    assert.equal(await page.getByRole('navigation').count(),0);
    assert.equal(await page.getByTestId('equity').count(),0);
    assert.equal(await page.getByRole('button',{name:'+ Deposit',exact:true}).count(),0);
    await page.evaluate(()=>window.__releaseSignature());
    await page.getByTestId('equity').waitFor();
    let a=await read();assert.equal(a.wallet,signer.address.toLowerCase());assert.equal(Number(a.balance),0);assert.equal(a.totals.trades,0);
    assert((await context.cookies()).find(c=>c.name==='vela_demo_session').httpOnly);
    assert(!(await page.evaluate(()=>document.cookie)).includes('vela_demo_session'));
    await page.getByRole('button',{name:'+ Deposit',exact:true}).click();
    await page.getByRole('group',{name:'Demo asset'}).waitFor();
    assert.match(await page.getByRole('dialog').innerText(),/Simulated funds only/);
    assert(!/USDT|ETH|DOGE/.test(await page.getByRole('dialog').innerText()));
    for(const width of [320,360,390,768,1024,1440]){
      await page.setViewportSize({width,height:640});
      const box=await page.getByRole('dialog').evaluate(d=>{const r=d.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,vw:innerWidth,vh:innerHeight,scroll:d.scrollWidth,client:d.clientWidth};});
      assert(Math.abs(box.x-(box.vw-box.w)/2)<2,'Centered horizontally at '+width);
      assert(Math.abs(box.y-(box.vh-box.h)/2)<2,'Centered vertically at '+width);
      assert(box.x>=15&&box.y>=19&&box.y+box.h<=box.vh-19,'Inside viewport at '+width);
      assert(box.scroll<=box.client+1,'No horizontal modal overflow at '+width);
      await page.getByRole('button',{name:'Add demo funds'}).scrollIntoViewIfNeeded();
    }
    await page.setViewportSize({width:1440,height:1080});
    await page.getByRole('button',{name:'Add demo funds'}).click();
    await until(read,x=>Number(x.balance)===1000,'demo deposit');
    assert.equal(Number((await read()).pnl),0);
    await page.reload();await page.getByTestId('equity').waitFor();
    assert.equal((await page.evaluate(()=>window.__walletCalls)).filter(x=>x==='personal_sign').length,0,'Restore must not ask for another signature');
    await until(()=>page.locator('.portal-feed').innerText(),x=>x==='Markets live','fresh market data',45000);
    await nav('History');await nav('Account');
    await page.goBack();await page.waitForURL('**/trade?view=history');
    assert.equal(await page.getByRole('navigation').getByRole('button',{name:'History',exact:true}).getAttribute('aria-current'),'page');
    await page.goForward();await page.waitForURL('**/trade?view=account');
    await nav('Trades');
    await page.locator('summary').filter({hasText:'Optional manual paper order'}).click();
    const form=page.locator('.portal-advanced form');
    await form.getByLabel('Notional · USD').fill('100');
    await form.getByRole('button',{name:'Place paper order'}).click();
    await until(read,x=>x.positions?.length===1,'long fill');
    await form.getByLabel('Market',{exact:true}).selectOption('ETH/USD');
    await form.getByLabel('Direction').selectOption('short');
    await form.getByRole('button',{name:'Place paper order'}).click();
    a=await until(read,x=>x.positions?.length===2,'short fill');
    assert.deepEqual(a.positions.map(p=>p.side).sort(),['long','short']);
    result.manualPositions=a.positions.map(p=>({symbol:p.symbol,side:p.side,entry:p.entry,stop:p.stop,riskBudget:p.riskBudget}));
    for(const p of a.positions)assert(Number(p.riskBudget)<=2);
    await nav('Dashboard');
    await page.screenshot({path:path.join(artifacts,'desktop-positions.png'),fullPage:true});
    const p=a.positions[0];assert.equal((await post(`positions/${p.id}/close`,{clientId:crypto.randomUUID(),fraction:'.5'})).status,202);
    await until(read,x=>x.trades.length===1,'partial close');
    const sol=await page.evaluate(async()=>{const m=await(await fetch('/api/paper/markets')).json();return m.markets.find(x=>x.symbol==='SOL/USD');});
    const limit=(Math.floor(Number(sol.price)/2/Number(sol.tick))*Number(sol.tick)).toFixed(3);
    assert.equal((await post('orders',{clientId:crypto.randomUUID(),symbol:'SOL/USD',side:'long',type:'limit',notional:'100',limitPrice:limit})).status,202);
    assert.equal((await post('stop')).status,200);
    a=await read();assert.equal(a.positions.length,2);assert(!a.orders.some(o=>o.status==='pending'&&!o.payload.reduceOnly));
    await page.getByRole('button',{name:'Close all ↗',exact:true}).click();
    await page.getByRole('button',{name:'Pause & close all positions'}).click();
    a=await until(read,x=>x.positions.length===0,'confirmed close all');
    assert.equal(a.totals.trades,3);assert.equal(a.today.trades,3);
    assert(Math.abs(Number(a.balance)-Number(a.initial)-a.trades.reduce((v,t)=>v+Number(t.pnl),0))<.00001);
    result.manualClosedFills=a.trades.length;result.manualPnl=a.totals.realized;result.resultScope='This isolated engineering account; partial exits count as closed fills. Not AI strategy performance.';
    if(process.env.PORTAL_REAL_AI==='1'){
      await until(()=>page.getByRole('button',{name:'Start Trading',exact:false}).isEnabled(),Boolean,'AI ready');
      await page.getByRole('button',{name:'Start Trading',exact:false}).click();
      a=await until(read,x=>x.preferences?.autonomous,'AI active');
      a=await until(read,x=>Boolean(x.decision),'genuine model decision',120000);
      result.realModelDecision=a.decision;
      if(a.decision.phase==='research'&&a.decision.answer.strategies?.length){
        a=await until(read,x=>x.decision?.phase==='direction'||x.decision?.phase==='review'&&x.decision.answer.verdict!=='confirm','model review / direction',120000);
        result.realModelDecision=a.decision;
        if(a.decision.phase==='direction'&&a.decision.answer.action==='open')await sleep(1500);
      }
      result.realModelPositions=(await read()).positions;
      await page.getByRole('button',{name:'Stop Trading',exact:false}).click();
      await until(read,x=>!x.preferences.autonomous,'AI stopped');
      await post('close-all',{clientId:crypto.randomUUID(),confirm:true});await until(read,x=>!x.positions.length,'AI position cleanup');
    }
    for(const width of [1440,1024,768,390,360]){
      await page.setViewportSize({width,height:950});await nav('Dashboard');await sleep(150);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No horizontal overflow at '+width);
      await page.screenshot({path:path.join(artifacts,`portal-${width}.png`),fullPage:true});result.widths.push(width);
    }
    await nav('History');assert((await page.locator('main').innerText()).includes('Today · UTC'));
    await nav('Account');assert((await page.locator('main').innerText()).includes(signer.address.toLowerCase()));
    await page.getByRole('button',{name:'Withdraw',exact:true}).click();await page.getByLabel('Demo amount').fill('100');
    await page.getByRole('button',{name:'Confirm demo withdrawal'}).click();await until(read,x=>x.transactions.some(t=>t.kind==='demo_withdrawal'),'withdrawal');
    await page.getByRole('button',{name:'Reset demo account',exact:true}).click();await page.getByRole('button',{name:'Confirm reset',exact:true}).click();
    a=await until(read,x=>Number(x.balance)===0&&x.trades.length===0,'reset');assert.equal(a.totals.trades,0);
    await context.setOffline(true);await sleep(2000);
    await page.getByText('Account connection interrupted. Reconnecting…',{exact:true}).waitFor();
    await context.setOffline(false);await until(()=>page.locator('.portal-feed').innerText(),x=>x==='Markets live','reconnect',45000);
    await page.route('**/api/portal/auth/logout',r=>r.abort());
    await page.getByRole('button',{name:'Disconnect wallet'}).click();
    await page.getByRole('heading',{name:'Welcome to Vela'}).waitFor();
    assert.equal(await page.getByRole('navigation').count(),0);
    assert.equal(await page.getByTestId('equity').count(),0);
    await page.locator('main [role="alert"]').filter({hasText:'Server disconnect could not be confirmed.'}).waitFor();
    assert.equal((await page.request.get(origin+'/api/portal/account')).status(),200);
    await page.unroute('**/api/portal/auth/logout');
    await page.getByRole('button',{name:'Retry disconnect'}).click();await page.getByText('Wallet disconnected. Connect to continue.').waitFor();
    assert.equal((await page.request.get(origin+'/api/portal/account')).status(),401);
    const calls=allWalletMethods;
    assert(!calls.some(m=>/sendTransaction|signTransaction|eth_send|eth_sign$/.test(m)));
    assert(!requests.some(r=>/why-api|bybit|okx|mt5|api\/v1\/sendTx/i.test(r.url)));
    assert.deepEqual(errors,[]);
    result.walletMethods=[...new Set(calls)];result.positionsRemaining=0;result.agentPaused=true;result.passed=true;
    fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  }catch(e){await page.screenshot({path:path.join(artifacts,'failure.png'),fullPage:true}).catch(()=>{});throw e;}finally{
    try{await post('stop');await post('close-all',{clientId:crypto.randomUUID(),confirm:true});await sleep(700);}catch{}
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1;});

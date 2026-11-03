// Synthetic wallet approvals/model/feed; real isolated ASGI ledgers and signatures.
// DogeOS queries use the existing read-only gateway. Nothing mutates production.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const out=(await readFile('../.runtime/current-ux-wallet','utf8')).trim(),base='https://defivela.xyz';
const prefix=(await readFile(out+'/asset-prefix.txt','utf8')).trim();
await mkdir(out+'/browser',{recursive:true});
const browser=await chromium.launch({executablePath:chromium.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const results=[];
try{
 for(const width of (process.env.VELLA_TEST_WIDTHS||'1440,390').split(',').map(Number)){
  const child=spawn('../.venv/bin/python',[out+'/browser-fixture.py'],{stdio:['pipe','pipe','inherit']});
  const pending=[];createInterface({input:child.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.shift();if(r.fixtureError)p.reject(Error(r.fixtureError));else p.resolve(r);});
  const rpc=body=>new Promise((resolve,reject)=>{pending.push({resolve,reject});child.stdin.write(JSON.stringify(body)+'\n');});
  child.on('exit',code=>{for(const p of pending.splice(0))p.reject(Error('Fixture worker exited '+code));});
  const identity=await rpc({control:'identity'}),context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
  let failNextConfig=false,page;
  try{
   await context.exposeFunction('fixtureSign',async(network,message)=>(await rpc({control:'sign',network,message})).signature);
   await context.addInitScript(({identity,standard})=>{
    let chain=Number(localStorage.getItem('fixture:chain')||1),connected=localStorage.getItem('fixture:connected')==='yes';const events=new Map();
    window.walletTest={calls:[],reject:'',change:()=>{connected=false;for(const f of events.get('accountsChanged')||[])f([]);},solanaChange:()=>{}};
    const emit=(name,...args)=>(events.get(name)||[]).forEach(fn=>fn(...args));
    const mm={isMetaMask:true,on:(k,fn)=>events.set(k,[...(events.get(k)||[]),fn]),removeListener:(k,fn)=>events.set(k,(events.get(k)||[]).filter(x=>x!==fn)),request:async({method,params})=>{
     window.walletTest.calls.push({method,params});
     if(window.walletTest.reject===method){window.walletTest.reject='';throw Object.assign(Error('User rejected request'),{code:4001});}
     if(method==='eth_requestAccounts'){connected=true;localStorage.setItem('fixture:connected','yes');return [identity.evm];}
     if(method==='eth_accounts')return connected?[identity.evm]:[];
     if(method==='eth_chainId')return '0x'+chain.toString(16);
     if(method==='wallet_switchEthereumChain'){chain=Number(params[0].chainId);localStorage.setItem('fixture:chain',String(chain));emit('chainChanged','0x'+chain.toString(16));return;}
     if(method==='eth_getBalance')return '0x0';
     if(method==='personal_sign'){const message=new TextDecoder().decode(Uint8Array.from(params[0].slice(2).match(/../g),x=>parseInt(x,16)));return '0x'+await window.fixtureSign(localStorage.getItem('vella:network')||'dogeos',message);}
     throw Error('Forbidden synthetic wallet method '+method);
    }};
    // A competing extension owns window.ethereum; MetaMask must be discovered.
    window.ethereum={isOtherWallet:true,request:async()=>{throw Error('Wrong provider selected');}};
    const announce=()=>window.dispatchEvent(new CustomEvent('eip6963:announceProvider',{detail:{info:{uuid:'test-mm',rdns:'io.metamask',name:'MetaMask',icon:''},provider:mm}}));
    window.addEventListener('eip6963:requestProvider',announce);announce();
    const se=new Map(),solSign=async bytes=>Uint8Array.from((await window.fixtureSign('solana',new TextDecoder().decode(bytes))).match(/../g),x=>parseInt(x,16));
    if(standard){
     const account={address:identity.solana,publicKey:new Uint8Array(32),chains:['solana:mainnet'],features:['solana:signMessage']};
     const w={name:'Phantom',version:'1.0.0',icon:'',chains:['solana:mainnet'],accounts:[],features:{
      'standard:connect':{version:'1.0.0',connect:async()=>{w.accounts=[account];return {accounts:w.accounts};}},
      'standard:events':{version:'1.0.0',on:(event,fn)=>{se.set(fn,event);return()=>se.delete(fn);}},
      'solana:signMessage':{version:'1.0.0',signMessage:async({message})=>[{signature:await solSign(message),signedMessage:message}]},
     }};
     window.walletTest.solanaChange=()=>{w.accounts=[];for(const fn of se.keys())fn({accounts:[]});};
     window.addEventListener('wallet-standard:app-ready',e=>e.detail.register(w));
     window.dispatchEvent(new CustomEvent('wallet-standard:register-wallet',{detail:api=>api.register(w)}));
    }else{
     const sol={isPhantom:true,isConnected:false,publicKey:null,connect:async()=>{sol.isConnected=true;sol.publicKey={toString:()=>identity.solana};return {publicKey:sol.publicKey};},signMessage:async bytes=>({signature:await solSign(bytes)}),on:(k,fn)=>se.set(fn,k),removeListener:(_k,fn)=>se.delete(fn)};
     window.walletTest.solanaChange=()=>{sol.isConnected=false;sol.publicKey=null;for(const [fn,event] of se)if(event==='accountChanged')fn(null);};
     setTimeout(()=>{window.phantom={solana:sol};},400);
    }
   },{identity,standard:width<1000});
   await context.route(base+'/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname.startsWith('/api/')){
     if(url.pathname==='/api/config'&&failNextConfig){failNextConfig=false;await route.fulfill({status:503,json:{error:'Synthetic network configuration outage'}});return;}
     const r=await rpc({path:url.pathname,method:req.method(),headers:req.headers(),body:req.postData()});await route.fulfill({status:r.status,headers:r.headers,body:r.body});return;
    }
    const file=url.pathname.startsWith(prefix)?out+'/publish/assets/'+path.basename(url.pathname):out+'/publish/index.html';
    await route.fulfill({body:await readFile(file),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
   });
   page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow ${width}`);
   const network=async id=>{const trigger=page.locator('[data-network-selector]');if(await trigger.getAttribute('data-network-selector')===id)return;await trigger.click();const names={dogeos:'DogeOS',base:'Base',solana:'Solana',ethereum:'Ethereum',bnb:'BNB Chain',arbitrum:'Arbitrum',robinhood:'Robinhood Chain Testnet'};await page.getByRole('option',{name:new RegExp('^'+names[id])}).click();await page.waitForFunction(id=>document.querySelector('[data-network-selector]')?.dataset.networkSelector===id,id);};
   const tab=async id=>{await page.locator('#tab-'+id).click();await page.locator('#panel-'+id).waitFor();assert.equal(new URL(page.url()).pathname,{vault:'/vault',borrow:'/borrow',trading:'/trading',agent:'/repay',overview:'/overview'}[id]);};
   const confirm=async()=>{await page.getByRole('dialog').getByRole('button',{name:'Confirm',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});};
   const account=async id=>JSON.parse((await rpc({path:'/api/account',method:'GET',headers:{'X-Vella-Network':id}})).body);
   await page.goto(base+'/vault');await page.locator('[data-network-selector]').waitFor();
   assert.equal(await page.getByRole('button',{name:'Coming Soon',exact:true}).isDisabled(),true);
   assert.equal(await page.locator('a[href*="mydoge"]').count(),0);
   const trigger=page.locator('[data-network-selector]');await trigger.focus();await page.keyboard.press('ArrowDown');await page.getByRole('listbox').waitFor();assert.equal(await page.getByRole('option').count(),7);
   const bounds=await page.getByRole('listbox').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width&&bounds.y>=0&&bounds.y+bounds.height<=1000);
   assert.notEqual(await page.getByRole('listbox').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 255, 255)');
   await page.screenshot({path:out+`/browser/network-menu-${width}.png`});
   await page.keyboard.press('Escape');assert.equal(await trigger.evaluate(el=>el===document.activeElement),true);
   await trigger.click();await page.mouse.click(4,300);assert.equal(await page.getByRole('listbox').count(),0);
   await network('base');await page.evaluate(()=>window.walletTest.reject='eth_requestAccounts');await page.getByRole('button',{name:'MetaMask — Connect',exact:true}).click();await page.getByRole('alert').filter({hasText:'declined'}).waitFor();assert.equal(await page.getByRole('tablist').count(),0);
   await page.evaluate(()=>window.walletTest.reject='wallet_switchEthereumChain');await page.getByRole('button',{name:'MetaMask — Connect',exact:true}).click();await page.getByRole('alert').filter({hasText:'declined'}).waitFor();assert.equal(await page.getByRole('tablist').count(),0);
   for(const id of ['base','solana','ethereum','bnb','arbitrum','robinhood']){
    await network(id);await page.getByRole('button',{name:id==='solana'?'Phantom — Connect':'MetaMask — Connect',exact:true}).click();await page.getByRole('tablist').waitFor();await tab('vault');
    const asset=id==='solana'?'SOL':id==='bnb'?'BNB':'ETH';
    assert.equal(await page.locator('#panel-vault details[open]').count(),0);
    await page.getByLabel('How much do you want to add?',{exact:true}).fill('10');await page.getByRole('button',{name:'Add to vault',exact:true}).click();
    assert.match(await page.getByRole('dialog').innerText(),new RegExp('Add 10 demo '+asset+' to your vault'));await confirm();
    await page.getByRole('heading',{name:'Deposit Successful',exact:true}).waitFor();assert.match(await page.locator('.deposit-success').innerText(),new RegExp('10 demo '+asset));assert.match(await page.locator('.receipt-reference').innerText(),/^[a-f0-9-]{36}$/);assert.match(await page.locator('.deposit-success').innerText(),/No real funds moved/);
    if(id==='base')await page.evaluate(()=>window.scrollTo(0,0));
    if(id==='base')await page.screenshot({path:out+`/browser/deposit-${width}.png`,fullPage:true});
    await page.getByRole('button',{name:'View Vault',exact:true}).click();await tab('borrow');
    assert.match(await page.locator('#panel-borrow').innerText(),/Demo loan only\. No real USDC is issued\./);
    const before=await account(id),borrowField=page.getByLabel('How much do you want to borrow?',{exact:true}),borrowButton=page.locator('#panel-borrow').getByRole('button',{name:'Borrow',exact:true});
    await borrowField.fill(String(Number(before.health.borrowCapacity)+1));assert.equal(await borrowButton.isDisabled(),true);
    await borrowField.fill('400');await borrowButton.click();await confirm();
    const borrowed=await account(id);assert.equal(Number(borrowed.health.principal),400);assert.equal(Number(borrowed.paper.balance),400);
    if(id==='base')await page.evaluate(()=>window.scrollTo(0,0));
    if(id==='base')await page.screenshot({path:out+`/browser/borrow-${width}.png`,fullPage:true});
    await page.reload();await page.getByRole('tablist').waitFor();assert.equal(new URL(page.url()).pathname,'/borrow');assert.match(await page.locator('#panel-borrow').innerText(),/\$400\.00/);
    await tab('trading');
    assert.equal(await page.locator('details[open]').count(),0);assert.match(await page.locator('#panel-trading').innerText(),/The AI will trade your borrowed \$400\.00/);assert.match(await page.locator('#panel-trading').innerText(),/Max loss \$2\.00 per trade, fees included/);
    await page.getByRole('button',{name:'Start AI Trading',exact:true}).click();assert.match(await page.getByRole('dialog').innerText(),/max loss of \$2\.00 per trade including fees/);await confirm();
    await page.locator('.quick-ai.is-running').waitFor();const started=await account(id);assert.equal(started.paper.preferences.autonomous,true);
    assert.deepEqual([started.vault.limits.positionSize,started.vault.limits.maxLoss,started.vault.limits.leverage],['100000000','2000000','1']);
    assert.equal((await rpc({control:'trade',network:id})).positions,1);
    await page.getByRole('button',{name:'Stop AI Trading',exact:true}).click();await page.locator('.quick-ai:not(.is-running)').waitFor();
    const stopped=await account(id);assert.equal(stopped.paper.preferences.autonomous,false);assert.equal(stopped.paper.positions.length,1);
    if(id==='base')await page.evaluate(()=>window.scrollTo(0,0));
    if(id==='base')await page.screenshot({path:out+`/browser/trading-${width}.png`,fullPage:true});
    await rpc({control:'profit',network:id});await tab('borrow');await page.locator('#panel-borrow').getByRole('button',{name:'Repay →',exact:true}).click();await page.locator('#panel-agent').waitFor();
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('#panel-agent button')).some(b=>b.textContent.startsWith('Repay everything')&&!b.disabled));await page.getByRole('button',{name:/^Repay everything/}).click();await confirm();
    const repaid=await account(id);assert.equal(Number(repaid.health.debt),0);assert.equal(repaid.health.loan.state,'REPAID');
    const takeBack=page.getByRole('button',{name:'Take back my '+asset,exact:true});await page.waitForFunction(a=>Array.from(document.querySelectorAll('#panel-agent button')).some(b=>b.textContent===a&&!b.disabled),'Take back my '+asset);
    await takeBack.click();await confirm();assert.equal(Number((await account(id)).health.collateral),0);
    await noOverflow();results.push({width,network:id,deposit:true,borrow:true,repay:true,paperStartStop:true,protectivePositionRetained:true,sessionRestored:true,syntheticWallet:true});
    console.log(`PASS ${id} at ${width}px: connection → vault → loan → AI start/stop → repayment`);
   }
   await network('dogeos');await page.getByRole('button',{name:'MetaMask — Connect',exact:true}).click();await page.getByRole('tablist').waitFor();await tab('vault');
   await page.locator('#panel-vault summary').filter({hasText:'Advanced'}).click();await page.locator('.dogeos-read-status').filter({hasText:'Verified from DogeOS RPC'}).waitFor({timeout:45000});
   await page.getByLabel('How much demo money do you want to add?',{exact:true}).fill('1000');await page.getByRole('button',{name:'Add demo money',exact:true}).click();await confirm();await page.getByRole('heading',{name:'Deposit Successful'}).waitFor();assert.match(await page.locator('.deposit-success').innerText(),/1000 demo USDC/);
   // One-tap AI start: amount → Start → one confirmation; limits are saved and the agent starts together.
   await page.getByRole('button',{name:'View Vault',exact:true}).click();await tab('trading');const card=page.locator('.quick-ai');
   assert.match(await card.innerText(),/How much do you want to trade\?/);await card.getByRole('radio',{name:'$500'}).click();assert.equal(await card.getByRole('radio',{name:'$500'}).getAttribute('aria-checked'),'true');
   const fundedBefore=await account('dogeos');await card.getByRole('button',{name:'Start AI Trading',exact:true}).click();
   assert.match(await page.getByRole('dialog').innerText(),/\$500\.00 of demo money[\s\S]*max loss of \$2\.00 per trade/);await confirm();
   await page.locator('.quick-ai.is-running').waitFor();const quick=await account('dogeos');assert.equal(quick.paper.preferences.autonomous,true);
   assert.deepEqual([quick.tradingPermission.allocation,quick.tradingPermission.positionSize,quick.tradingPermission.maxLoss,quick.tradingPermission.leverage],['500000000','100000000','2000000','1']);
   assert.equal(quick.directAllocation,fundedBefore.directAllocation);assert.match(await page.locator('.quick-ai').innerText(),/AI is running/);await noOverflow();
   assert.equal((await rpc({control:'trade',network:'dogeos'})).positions,1);await page.locator('.quick-ai-trade').filter({hasText:'Open trade:'}).waitFor({timeout:15000});
   await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:out+`/browser/quick-ai-running-${width}.png`,fullPage:true});
   await page.locator('.quick-ai').getByRole('button',{name:'Stop AI Trading',exact:true}).click();await page.locator('.quick-ai:not(.is-running)').waitFor();
   const quickStopped=await account('dogeos');assert.equal(quickStopped.paper.preferences.autonomous,false);assert.equal(quickStopped.paper.positions.length,1);
   await page.screenshot({path:out+`/browser/quick-ai-stopped-${width}.png`,fullPage:true});
   await tab('borrow');assert.match(await page.locator('#panel-borrow').innerText(),/Borrowing on DogeOS is coming soon\./);assert.equal(new URL(page.url()).pathname,'/borrow');
   const methods=await page.evaluate(()=>window.walletTest.calls.map(c=>c.method));assert.ok(!methods.includes('eth_sendTransaction'));
   await tab('vault');await page.getByRole('link',{name:'How It Works',exact:true}).click();assert.equal(new URL(page.url()).pathname,'/how-it-works');await page.getByRole('heading',{name:'Big possibilities. Simple steps.'}).waitFor();assert.equal(await page.locator('.how-steps article').count(),8);assert.equal(await page.locator('video').count(),0);await noOverflow();await page.screenshot({path:out+`/browser/how-${width}.png`,fullPage:true});
   await page.getByRole('link',{name:/START TRADING/}).last().click();await page.getByRole('tablist').waitFor();assert.equal(new URL(page.url()).pathname,'/vault');
   await network('base');await page.getByRole('button',{name:'MetaMask — Connect',exact:true}).click();await page.getByRole('tablist').waitFor();failNextConfig=true;await trigger.click();await page.getByRole('option',{name:/^Arbitrum/}).click();await page.getByRole('alert').filter({hasText:'Synthetic network configuration outage'}).waitFor();assert.equal(await trigger.getAttribute('data-network-selector'),'base');assert.equal(await page.evaluate(()=>localStorage.getItem('vella:network')),'base');
   await network('solana');await page.getByRole('button',{name:'Phantom — Connect',exact:true}).click();await page.getByRole('tablist').waitFor();await page.evaluate(()=>window.walletTest.solanaChange());await page.getByRole('button',{name:'Phantom — Connect',exact:true}).waitFor();assert.equal(await page.getByRole('tablist').count(),0);
   assert.deepEqual(errors,[]);results.push({width,network:'dogeos',oneTapAIStart:true,readOnlyRPC:true,demoDeposit:true,transactionsSent:false,howItWorks:true,switchRecovery:true,accountChangeInvalidates:true,phantomAdapter:width<1000?'Wallet Standard':'injected'});
  }catch(error){if(page){await page.screenshot({path:out+`/browser/failure-${width}.png`,fullPage:true});await writeFile(out+`/browser/failure-${width}.html`,await page.content());}throw error;}
  finally{await context.close();child.stdin.end();}
 }
 await writeFile(out+'/product-browser.json',JSON.stringify({status:'PASS',scope:'Synthetic wallet approvals/model/feed; real isolated ASGI and demo ledgers. Genuine wallet approvals remain unverified.',results},null,2));
}catch(error){await writeFile(out+'/product-browser.json',JSON.stringify({status:'FAIL',error:error.stack,results},null,2));throw error;}
finally{await browser.close();}

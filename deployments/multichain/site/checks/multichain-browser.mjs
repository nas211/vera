// Chromium + real in-process ASGI backend. Wallet/model/markets are synthetic.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const base='https://vella-fixture.invalid',out='../snapshot/multichain/browser';
const report=process.env.VELLA_BROWSER_REPORT||'../snapshot/multichain/browser.json',startedAt=new Date().toISOString();
await mkdir(out,{recursive:true});
let browser;
const results=[];
try{
 browser=await chromium.launch({executablePath:chromium.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of (process.env.VELLA_TEST_WIDTHS||'320,390,768,1440').split(',').map(Number)){
  const child=spawn('../.venv/bin/python',['../tools/multichain_browser_fixture.py'],{stdio:['pipe','pipe','inherit']});
  const pending=[];createInterface({input:child.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.shift();if(r.fixtureError)p.reject(Error(r.fixtureError));else p.resolve(r);});
  const rpc=body=>new Promise((resolve,reject)=>{pending.push({resolve,reject});child.stdin.write(JSON.stringify(body)+'\n');});
  child.on('exit',code=>{for(const p of pending.splice(0))p.reject(Error('Fixture worker exited '+code));});
  const identity=await rpc({control:'identity'}),context=await browser.newContext({viewport:{width,height:1000}});
  let failNextNetworkConfig=false;
  try{
   await context.exposeFunction('fixtureSign',async(network,message)=>(await rpc({control:'sign',network,message})).signature);
   await context.addInitScript(({identity})=>{
    let chain=8453;const events={};
    const currentNetwork=()=>localStorage.getItem('vella:network')||'dogeos';
    window.ethereum={on:(k,fn)=>events[k]=fn,removeListener:k=>delete events[k],request:async({method,params})=>{
     if(['eth_accounts','eth_requestAccounts'].includes(method))return [identity.evm];
     if(method==='eth_chainId')return '0x'+chain.toString(16);
     if(method==='wallet_switchEthereumChain'){chain=Number(params[0].chainId);events.chainChanged?.('0x'+chain.toString(16));return;}
     if(method==='eth_getBalance')return '0x0';
     if(method==='personal_sign'){const msg=new TextDecoder().decode(Uint8Array.from(params[0].slice(2).match(/../g),x=>parseInt(x,16)));return '0x'+await window.fixtureSign(currentNetwork(),msg);}
     throw Error('Forbidden fixture wallet method '+method);
    }};
    const se={};window.phantom={solana:{publicKey:{toString:()=>identity.solana},isConnected:true,connect:async()=>({publicKey:{toString:()=>identity.solana}}),on:(k,fn)=>se[k]=fn,removeListener:k=>delete se[k],signMessage:async bytes=>({signature:Uint8Array.from((await window.fixtureSign('solana',new TextDecoder().decode(bytes))).match(/../g),x=>parseInt(x,16))})}};
    window.fixtureAccountChange=()=>{events.accountsChanged?.([]);se.accountChanged?.(null);};
   },{identity});
   await context.route(base+'/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname.startsWith('/api/')){
     if(url.pathname==='/api/config'&&failNextNetworkConfig){failNextNetworkConfig=false;await route.fulfill({status:503,json:{error:'Synthetic network configuration outage'}});return;}
     const result=await rpc({path:url.pathname,method:request.method(),headers:request.headers(),body:request.postData()});
     await route.fulfill({status:result.status,headers:result.headers,body:result.body});return;
    }
    const file=path.join(process.env.VELLA_BROWSER_BUILD||'multichain',url.pathname.startsWith('/assets/')?url.pathname.slice(1):'index.html');
    await route.fulfill({body:await readFile(file),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
   });
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(base+'/vault');await page.getByLabel('Network',{exact:true}).waitFor();
   for(const network of ['base','solana','ethereum','bnb','arbitrum','robinhood']){
    await page.getByLabel('Network',{exact:true}).selectOption(network);
    await page.getByRole('button',{name:network==='solana'?'Connect Solana wallet':'Connect browser wallet',exact:true}).click();
    await page.getByRole('tab',{name:'Vault',exact:false}).click();
    await page.getByRole('button',{name:'Create Vault',exact:true}).click();
    await page.getByRole('heading',{name:'Create simulated vault'}).waitFor();
    assert.match(await page.getByRole('dialog').innerText(),/not redeemable/);
    await page.getByRole('button',{name:'Confirm',exact:true}).click();
    const asset=network==='solana'?'SOL':network==='bnb'?'BNB':'ETH';
    await page.getByRole('button',{name:'Allocate demo '+asset,exact:true}).click();
    await page.getByLabel('Demo '+asset+' amount',{exact:true}).fill('10');
    await page.getByRole('button',{name:'Confirm',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByRole('tab',{name:'Borrow',exact:false}).click();
    await page.getByRole('button',{name:'Borrow USDC',exact:true}).click();
    await page.getByLabel('USDC amount',{exact:true}).fill('400');
    await page.getByRole('button',{name:'Confirm',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByRole('tab',{name:'Vault',exact:false}).click();await page.getByRole('button',{name:'Authorize AI',exact:true}).click();
    await page.getByRole('button',{name:'Confirm',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByRole('tab',{name:'AI Trade',exact:false}).click();await page.getByRole('button',{name:'Start AI Trading',exact:true}).click();
    await page.getByRole('button',{name:'Confirm',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    const trade=await rpc({control:'trade',network});assert.equal(trade.positions,1);
    await page.getByRole('tab',{name:'Borrow',exact:false}).click();
    await page.getByLabel('Repayment amount · USDC').fill('100');await page.getByRole('button',{name:'Review partial repayment'}).click();
    await page.getByRole('button',{name:'Confirm',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByRole('tab',{name:'Agent Mode',exact:false}).click();
    await page.getByLabel('Custom percentage',{exact:true}).fill('25');await page.getByLabel('Repayment frequency').selectOption('weekly');
    await page.getByLabel(/I authorize simulated repayments/).check();await page.getByRole('button',{name:'Authorize repayment rules'}).click();
    await page.getByRole('button',{name:'Authorize repayment rules'}).waitFor({state:'visible'});
    await rpc({control:'profit',network});
    await page.getByRole('button',{name:'Stop & Settle',exact:true}).click();await page.getByRole('button',{name:'Confirm',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'Withdraw '+asset,exact:true}).click();
    await page.getByRole('button',{name:'Confirm',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    const snap=await rpc({path:'/api/account',method:'GET',headers:{'X-Vella-Network':network}});
    const account=JSON.parse(snap.body);assert.equal(account.health.collateral,'0');assert.equal(Number(account.health.debt),0);assert.equal(account.health.loan.state,'REPAID');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow ${network} ${width}`);
    results.push({width,network,status:'PASS',closedFills:account.paper.totals.trades,simulated:true});
    console.log(`PASS: ${network} at ${width}px; simulated vault → borrow → paper trade → repay → unlock`);
    await page.screenshot({path:`${out}/${network}-${width}.png`,fullPage:true});
   }
   await page.getByLabel('Network',{exact:true}).selectOption('base');await page.getByRole('button',{name:'Connect browser wallet',exact:true}).click();await page.getByRole('tablist').waitFor();
   const snapshot=JSON.parse((await rpc({path:'/api/account',method:'GET',headers:{'X-Vella-Network':'base'}})).body);assert.equal(snapshot.health.collateral,'0');assert.equal(snapshot.health.loan.state,'REPAID');
   failNextNetworkConfig=true;
   await page.getByLabel('Network',{exact:true}).selectOption('arbitrum');
   await page.getByRole('alert').filter({hasText:'Synthetic network configuration outage'}).waitFor();
   assert.equal(await page.getByLabel('Network',{exact:true}).inputValue(),'base');
   assert.equal(await page.evaluate(()=>localStorage.getItem('vella:network')),'base');
   assert.equal(await page.getByRole('tablist').count(),0);
   await page.getByLabel('Network',{exact:true}).selectOption('solana');
   await page.getByRole('button',{name:'Connect Solana wallet',exact:true}).click();await page.getByRole('tablist').waitFor();
   await page.evaluate(()=>window.fixtureAccountChange());await page.getByRole('heading',{name:'Welcome to VELA.'}).waitFor();assert.equal(await page.getByRole('tablist').count(),0);
   assert.deepEqual(errors,[]);
  }catch(error){const page=context.pages().at(-1);if(page){await page.screenshot({path:`${out}/failure-${width}.png`,fullPage:true});await writeFile(`${out}/failure-${width}.html`,await page.content());}throw error;}finally{await context.close();child.stdin.end();}
 }
 await writeFile(report,JSON.stringify({status:'PASS',startedAt,finishedAt:new Date().toISOString(),scope:'Chromium with real ASGI and shared ledgers; synthetic wallets, signatures, model and market fixtures. No genuine wallet or blockchain verification.',results},null,2));
 console.log('PASS: six networks, four viewports, wallet signatures, vault, borrow, shared AI paper engine, repayments, automation, settlement, withdrawal and switching. Synthetic external integrations.');
}catch(error){await writeFile(report,JSON.stringify({status:browser?'FAIL':'BLOCKED',startedAt,finishedAt:new Date().toISOString(),phase:browser?'journey':'browser launch',error:error.stack,results},null,2));throw error;}
finally{await browser?.close();}

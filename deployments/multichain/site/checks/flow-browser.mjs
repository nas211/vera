// Full UI journey against isolated real ASGI services. Wallet, model, chain and
// price changes are explicit fixtures. Never connects to the public deployment.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),base='https://defivela.xyz',out=root+'/verification/browser';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.VELA_TEST_CHROME?{executablePath:process.env.VELA_TEST_CHROME}:{}),args:['--no-sandbox']});
const results=[];
try{
 for(const width of [1440,390]){
  const child=spawn(root+'/.venv/bin/python',[root+'/tools/flow_browser_fixture.py'],{cwd:root,stdio:['pipe','pipe','inherit']});
  const pending=[];
  createInterface({input:child.stdout}).on('line',line=>{const p=pending.shift();try{const r=JSON.parse(line);r.fixtureError?p.reject(Error(r.fixtureError)):p.resolve(r);}catch(e){p?.reject(e);}});
  const rpc=body=>new Promise((resolve,reject)=>{pending.push({resolve,reject});child.stdin.write(JSON.stringify(body)+'\n');});
  child.on('exit',code=>{for(const p of pending.splice(0))p.reject(Error('Fixture exited '+code));});
  const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
  let page;
  try{
   const identity=await rpc({control:'identity'});
   await context.exposeFunction('fixtureSign',async(network,message)=>(await rpc({control:'sign',network,message})).signature);
   await context.exposeFunction('fixtureNative',async tx=>(await rpc({control:'native',tx})).hash);
   await context.addInitScript(({owner})=>{
    let connected=localStorage.getItem('fixture:connected')==='yes',chain=Number(localStorage.getItem('fixture:chain')||6281971);
    const listeners=new Map();
    const wallet={isMetaMask:true,on:(k,f)=>listeners.set(k,[...(listeners.get(k)||[]),f]),removeListener:(k,f)=>listeners.set(k,(listeners.get(k)||[]).filter(x=>x!==f)),request:async({method,params})=>{
     if(method==='eth_requestAccounts'){connected=true;localStorage.setItem('fixture:connected','yes');return [owner];}
     if(method==='eth_accounts')return connected?[owner]:[];
     if(method==='eth_chainId')return '0x'+chain.toString(16);
     if(method==='wallet_switchEthereumChain'){chain=Number(params[0].chainId);localStorage.setItem('fixture:chain',String(chain));return;}
     if(method==='personal_sign'){const text=new TextDecoder().decode(Uint8Array.from(params[0].slice(2).match(/../g),b=>parseInt(b,16)));return '0x'+await window.fixtureSign(localStorage.getItem('vella:network')||'dogeos',text);}
     if(method==='eth_getBalance')return '0x8ac7230489e80000';
     if(method==='eth_estimateGas')return '0x186a0';if(method==='eth_gasPrice')return '0x3b9aca00';
     if(method==='eth_getTransactionCount')return '0x0';if(method==='eth_call')return '0x16345785d8a0000';
     if(method==='eth_sendTransaction')return window.fixtureNative(params[0]);
     throw Error('Unexpected fixture wallet request '+method);
    }};
    window.ethereum=wallet;
    const announce=()=>window.dispatchEvent(new CustomEvent('eip6963:announceProvider',{detail:{info:{uuid:'fixture-wallet',rdns:'io.metamask',name:'MetaMask',icon:''},provider:wallet}}));
    window.addEventListener('eip6963:requestProvider',announce);announce();
   },{owner:identity.evm});
   await context.route(base+'/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    const r=await rpc({path:url.pathname+url.search,method:req.method(),headers:req.headers(),body:req.postData()});
    await route.fulfill({status:r.status,headers:r.headers,body:r.body});
   });
   page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const tab=async name=>{await page.locator('#tab-'+name).click();await page.locator('#panel-'+name).waitFor();};
   const login=async()=>{await page.getByRole('button',{name:'MetaMask — Connect',exact:true}).click();await page.getByRole('tablist').waitFor();};
   const confirm=async()=>{const d=page.getByRole('dialog');await d.getByRole('button',{name:'Confirm',exact:true}).click();await d.waitFor({state:'hidden'});};
   const account=async network=>JSON.parse((await rpc({path:'/api/account',method:'GET',headers:{'X-Vella-Network':network}})).body);
   await page.goto(base+'/vault');await login();
   const native=page.getByRole('article',{name:'Native test DOGE vault'});
   await native.getByRole('button',{name:'Create my testnet vault'}).click();await native.getByRole('button',{name:'Approve in wallet'}).click();
   await native.getByLabel('Test DOGE amount',{exact:true}).waitFor();
   const before=await account('dogeos');
   await native.getByLabel('Test DOGE amount',{exact:true}).fill('1');await native.getByRole('button',{name:'Review deposit',exact:true}).click();await native.getByRole('button',{name:'Approve in wallet'}).click();
   await native.getByText(/Confirmed deposit on DogeOS/).waitFor();await page.reload();await page.getByRole('tablist').waitFor();
   assert.equal((await account('dogeos')).paper.balance,before.paper.balance);
   await native.getByLabel('Test DOGE amount',{exact:true}).fill('0.5');await native.getByRole('button',{name:'Review withdrawal',exact:true}).click();await native.getByRole('button',{name:'Approve in wallet'}).click();await native.getByText(/Confirmed withdraw on DogeOS/).waitFor();
   assert.equal(await native.getByRole('link',{name:/official faucet/}).getAttribute('href'),'https://faucet.testnet.dogeos.com/');
   await tab('borrow');assert.match(await page.locator('#panel-borrow').innerText(),/USDC borrowing is unavailable/);
   await tab('vault');await page.screenshot({path:out+`/native-${width}.png`,fullPage:true});
   await page.locator('[data-network-selector]').click();await page.getByRole('option',{name:/^Base/}).click();await login();await tab('vault');
   await page.getByLabel('How much do you want to add?',{exact:true}).fill('10');await page.getByRole('button',{name:'Add to vault',exact:true}).click();await confirm();
   await page.getByRole('button',{name:'View Vault',exact:true}).click();await tab('borrow');
   await page.getByLabel('How much do you want to borrow?',{exact:true}).fill('400');await page.locator('#panel-borrow').getByRole('button',{name:'Borrow',exact:true}).click();await confirm();
   assert.equal(Number((await account('base')).health.principal),400);await page.reload();await page.getByRole('tablist').waitFor();
   await tab('trading');await page.getByRole('button',{name:'Start AI Trading',exact:true}).click();await confirm();await page.locator('.quick-ai.is-running').waitFor();
   await tab('agent');await page.locator('#panel-agent > details > summary').click();
   await page.getByLabel('Profit Sweep percentage',{exact:true}).selectOption('100');await page.getByLabel('Minimum remaining free balance',{exact:true}).fill('0');
   await page.getByLabel(/I authorize simulated repayments/).check();await page.getByRole('button',{name:'Authorize repayment rules',exact:true}).click();
   await page.getByText(/^Permission active/).waitFor();
   await page.getByRole('button',{name:/Disconnect$/}).click();
   await rpc({control:'trade',network:'base'});await rpc({control:'profit',network:'base'});
   await login();const swept=await account('base');assert.ok(Number(swept.health.debt)<400);assert.equal(swept.paper.today.trades,1);assert.ok(swept.repayment.policy.enabled);
   await tab('trading');await page.getByRole('button',{name:'Stop AI Trading',exact:true}).click();await page.locator('.quick-ai:not(.is-running)').waitFor();
   await tab('agent');await page.getByLabel('How much do you want to repay?',{exact:true}).fill('1');await page.getByRole('button',{name:'Repay',exact:true}).click();await confirm();
   await page.getByRole('button',{name:/^Repay everything/}).click();await confirm();assert.equal(Number((await account('base')).health.debt),0);
   await page.getByRole('button',{name:'Take back my ETH',exact:true}).click();await confirm();assert.equal(Number((await account('base')).health.collateral),0);
   for(const name of ['overview','vault','borrow','trading','agent']){await tab(name);await page.reload();await page.getByRole('tablist').waitFor();}
   await page.screenshot({path:out+`/repaid-${width}.png`,fullPage:true});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
   results.push({width,nativeCreateDepositWithdraw:true,noPaperCreditFromNativeDeposit:true,loanStartReload:true,tradingAfterLogout:true,realizedProfitSweep:true,partialAndFullRepayment:true,collateralWithdrawal:true,syntheticWalletModelAndChain:true});
  }catch(e){if(page){await page.screenshot({path:out+`/failure-${width}.png`,fullPage:true});await writeFile(out+`/failure-${width}.html`,await page.content());}throw e;}
  finally{await context.close();child.stdin.end();}
 }
 await writeFile(out+'/results.json',JSON.stringify({status:'PASS',scope:'UI clicks with synthetic wallet/model/chain; real isolated authentication, ASGI and paper ledgers. No genuine wallet or live protection verification.',results},null,2));
}finally{await browser.close();}

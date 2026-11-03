// Real Chromium + ASGI gateway; fixture wallets/model/markets, live read-only
// DogeOS RPC. This is explicitly NOT a genuine physical wallet approval.
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root='/root/VellaMultichain';
const out=process.env.VELA_DOGEOS_REPORT_DIR||(await readFile(root+'/.runtime/current-dogeos-connected','utf8')).trim();
const base='https://defivela.xyz';
await mkdir(out+'/browser',{recursive:true});
const browser=await chromium.launch({executablePath:chromium.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const results=[];
try{
 for(const width of [390,1440]){
  const child=spawn(root+'/.venv/bin/python',[root+'/tools/dogeos_browser_fixture.py'],{stdio:['pipe','pipe','inherit']});
  const pending=[];
  createInterface({input:child.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.shift();r.fixtureError?p.reject(Error(r.fixtureError)):p.resolve(r);});
  child.on('exit',code=>{for(const p of pending.splice(0))p.reject(Error('Fixture exited '+code));});
  const rpc=body=>new Promise((resolve,reject)=>{pending.push({resolve,reject});child.stdin.write(JSON.stringify(body)+'\n');});
  const identity=await rpc({control:'identity'});
  const context=await browser.newContext({viewport:{width,height:1000}});
  try{
   await context.exposeFunction('fixtureSign',async(network,message)=>(await rpc({control:'sign',network,message})).signature);
   await context.addInitScript(({identity})=>{
    let chain=Number(sessionStorage.getItem('fixture:chain')||1);const events={};window.walletRequests=[];
    const network=()=>localStorage.getItem('vella:network')||'dogeos';
    window.ethereum={on:(k,f)=>events[k]=f,removeListener:k=>delete events[k],request:async({method,params})=>{
     window.walletRequests.push(method);
     if(['eth_accounts','eth_requestAccounts'].includes(method))return[identity.evm];
     if(method==='eth_chainId')return '0x'+chain.toString(16);
     if(method==='wallet_switchEthereumChain'){chain=Number(params[0].chainId);sessionStorage.setItem('fixture:chain',String(chain));events.chainChanged?.('0x'+chain.toString(16));return;}
     if(method==='eth_getBalance')return'0x0';
     if(method==='personal_sign')return'0x'+await window.fixtureSign(network(),new TextDecoder().decode(Uint8Array.from(params[0].slice(2).match(/../g),x=>parseInt(x,16))));
     throw Error('Forbidden synthetic wallet method '+method);
    }};
    const se={};window.phantom={solana:{publicKey:{toString:()=>identity.solana},isConnected:true,connect:async()=>({publicKey:{toString:()=>identity.solana}}),on:(k,f)=>se[k]=f,removeListener:k=>delete se[k],signMessage:async bytes=>({signature:Uint8Array.from((await window.fixtureSign('solana',new TextDecoder().decode(bytes))).match(/../g),x=>parseInt(x,16))})}};
    window.fixtureDisconnect=()=>events.disconnect?.();
   },{identity});
   await context.route(base+'/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    const r=await rpc({path:url.pathname+url.search,method:request.method(),headers:request.headers(),body:request.postData()});
    await route.fulfill({status:r.status,headers:r.headers,body:r.body});
   });
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   page.setDefaultTimeout(25000);
   await page.goto(base+'/vault#vault');
   await page.getByLabel('Network',{exact:true}).waitFor();
   await page.locator('.dogeos-read-status').filter({hasText:'Verified from DogeOS RPC'}).waitFor();
   assert.match(await page.locator('#dogeos-contract-panel').innerText(),/0x533beF9B9453914b584241F53aBA40f106Ac3125/);
   await page.screenshot({path:out+`/browser/onboarding-${width}.png`,fullPage:true});
   await page.getByText('Alternative EVM testnet wallet',{exact:true}).click();
   await page.getByRole('button',{name:'Connect browser wallet',exact:true}).click();
   await page.getByRole('tab',{name:'Vault',exact:false}).click();
   await page.locator('.dogeos-facts').filter({hasText:'No registered vault for this wallet'}).waitFor();
   assert.match(await page.locator('.dogeos-facts').innerText(),new RegExp(identity.evm,'i'));
   assert.equal(await page.getByRole('button',{name:'Deposit test DOGE',exact:true}).isVisible().catch(()=>false),false);
   await page.getByRole('button',{name:'Deposit Demo Funds',exact:true}).click();
   await page.getByRole('button',{name:'Confirm demo deposit',exact:true}).click();
   await page.locator('.dogeos-equity').filter({hasText:'$500.00'}).waitFor();
   await page.getByRole('button',{name:'Open AI Trading →',exact:true}).click();
   await page.getByLabel('Authorize paper execution within these limits for 24 hours. No real orders.').check();
   await page.getByRole('button',{name:'Save trading limits',exact:true}).click();
   await page.getByRole('button',{name:'Start AI Trading',exact:true}).click();
   await page.getByRole('button',{name:'Confirm',exact:true}).click();
   await page.getByRole('dialog').waitFor({state:'hidden'});
   assert.equal((await rpc({control:'trade',network:'dogeos'})).positions,1);
   await page.getByRole('button',{name:'Stop Trading',exact:true}).click();
   let a=JSON.parse((await rpc({path:'/api/account',method:'GET',headers:{'X-Vella-Network':'dogeos'}})).body);
   assert.equal(a.paper.preferences.autonomous,false);assert.equal(a.paper.positions.length,1);
   await rpc({control:'profit',network:'dogeos'});
   a=JSON.parse((await rpc({path:'/api/account',method:'GET',headers:{'X-Vella-Network':'dogeos'}})).body);
   assert.equal(a.paper.positions.length,0);assert.ok(Number(a.paper.pnl)>0);assert.equal(a.vault,null);
   await page.getByRole('button',{name:'Withdraw available demo equity',exact:true}).click();
   await page.getByRole('dialog').getByRole('textbox').fill('10');
   await page.getByRole('button',{name:'Confirm',exact:true}).click();
   await page.getByRole('dialog').waitFor({state:'hidden'});
   await page.screenshot({path:out+`/browser/trading-${width}.png`,fullPage:true});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.reload();await page.getByRole('tablist').waitFor();
   await page.getByRole('tab',{name:'Vault',exact:false}).click();
   await page.locator('.dogeos-facts').filter({hasText:'No registered vault for this wallet'}).waitFor();
   await page.screenshot({path:out+`/browser/vault-${width}.png`,fullPage:true});
   await page.evaluate(()=>window.fixtureDisconnect());
   await page.getByRole('heading',{name:'Welcome to VELA.'}).waitFor();
   assert.doesNotMatch(await page.locator('.dogeos-equity').innerText(),/500/);
   const seven=[];
   for(const network of ['solana','base','ethereum','bnb','arbitrum','robinhood']){
    await page.getByLabel('Network',{exact:true}).selectOption(network);
    await page.getByRole('button',{name:network==='solana'?'Connect Solana wallet':'Connect browser wallet',exact:true}).click();
    await page.getByRole('tablist').waitFor();
    assert.equal(await page.locator('#dogeos-contract-panel').count(),0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,network+' overflow');
    seven.push(network);
   }
   await page.getByLabel('Network',{exact:true}).selectOption('dogeos');
   await page.getByRole('button',{name:'Connect MyDoge',exact:false}).waitFor();
   const calls=await page.evaluate(()=>walletRequests);
   assert.ok(calls.includes('personal_sign')&&calls.includes('wallet_switchEthereumChain'));
   assert.equal(calls.some(x=>/sendTransaction|sendRawTransaction|approve/i.test(x)),false);
   assert.deepEqual(errors,[]);
   results.push({width,status:'PASS',networks:['dogeos',...seven],contractSource:'live DogeOS read-only RPC',wallet:'existing cryptographic fixture; no physical approval',modelAndMarkets:'deterministic fixture',realizedPnl:a.paper.pnl,stopPreservedPosition:true,sessionRestoreAndDisconnect:true,customerTransactions:0});
   console.log('PASS',width);
  }catch(error){
   for(const page of context.pages())await page.screenshot({path:out+`/browser/failure-${width}.png`,fullPage:true}).catch(()=>{});
   throw error;
  }finally{await context.close();child.stdin.end();}
 }
 await writeFile(out+'/browser-results.json',JSON.stringify({status:'PASS',results,physicalWalletApproval:false},null,2)+'\n');
}finally{await browser.close();}

// Mock wallet approvals and prices; real local HTTP handlers and isolated ledger.
import {chromium} from 'playwright';
import {privateKeyToAccount} from 'viem/accounts';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.PRODUCTS_TEST_URL||'http://127.0.0.1:18917';
assert.equal(new URL(base).hostname,'127.0.0.1','Fixture wallets must never target a public service');
const out=process.env.CHECK_ARTIFACTS_DIR||'/tmp/vela-products-check';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:chromium.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const results=[];
try{
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/paper');
  await page.locator('.feed-status.positive').waitFor({timeout:20000});
  await page.getByRole('button',{name:'Place paper long'}).click();
  await page.getByRole('button',{name:'Close 50%',exact:true}).waitFor({timeout:10000});
  await page.getByRole('button',{name:'Close 50%',exact:true}).click();
  await page.waitForTimeout(800);
  await page.getByRole('button',{name:'Close',exact:true}).click();
  await page.getByText('Your next position starts here.',{exact:true}).waitFor();
  await page.getByRole('tab',{name:'Trade history',exact:true}).click();
  assert.equal(await page.locator('#paper-records tbody tr').count(),2);
  await page.reload();await page.locator('.feed-status.positive').waitFor();
  await page.getByRole('tab',{name:'Trade history',exact:true}).click();
  assert.equal(await page.locator('#paper-records tbody tr').count(),2);
  await context.setOffline(true);await page.waitForTimeout(1500);await context.setOffline(false);
  await page.locator('.feed-status.positive').waitFor({timeout:25000});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await page.screenshot({path:out+'/paper-'+width+'.png',fullPage:true});
  const wallet=privateKeyToAccount('0x'+(width===1440?'04':'05').repeat(32));
  await context.exposeFunction('fixtureSign',message=>wallet.signMessage({message:{raw:message}}));
  await context.addInitScript(address=>{
   let connected=false;const listeners=new Map();
   window.ethereum={isMetaMask:true,on:(name,fn)=>listeners.set(name,fn),removeListener:name=>listeners.delete(name),request:async({method,params})=>{
    if(method==='eth_requestAccounts'){connected=true;return [address];}
    if(method==='eth_accounts')return connected?[address]:[];
    if(method==='eth_chainId')return '0x5fdaf3';
    if(method==='personal_sign')return window.fixtureSign(params[0]);
    if(method==='wallet_revokePermissions'){connected=false;return null;}
    throw Error('Unexpected fixture wallet method '+method);
   }};
  },wallet.address);
  await page.goto(base+'/earn');await page.getByRole('button',{name:'Connect MetaMask',exact:true}).click();
  await page.getByRole('button',{name:'Add demo funds',exact:true}).waitFor();
  assert.equal(await page.getByLabel('Staking network').locator('option').count(),7);
  assert.match(await page.getByLabel('Staking network').locator('option').first().innerText(),/DogeOS/);
  await page.getByLabel('Demo staking amount').fill('1000');
  await page.getByRole('button',{name:'Add demo funds',exact:true}).click();
  await page.getByTestId('earn-balance').filter({hasText:'1,000'}).waitFor();
  await page.getByRole('button',{name:'Stake demo USDC',exact:true}).click();
  await page.getByTestId('earn-principal').filter({hasText:'1,000'}).waitFor();
  const claim=page.getByRole('button',{name:'Claim demo reward',exact:true});
  await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Claim demo reward')?.disabled,{},{timeout:15000});
  await claim.click();await page.getByText('Demo reward credited to your available balance.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Withdraw principal',exact:true}).click();
  await page.getByTestId('earn-principal').filter({hasText:/^0$/}).waitFor();
  await page.getByRole('button',{name:'Disconnect',exact:true}).click();
  await page.getByRole('button',{name:'Connect MetaMask',exact:true}).click();
  await page.getByRole('button',{name:'Add demo funds',exact:true}).waitFor();
  assert.equal(await page.getByTestId('earn-principal').innerText(),'0');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await page.screenshot({path:out+'/earn-'+width+'.png',fullPage:true});
  assert.deepEqual(errors,[]);
  results.push({width,paperManualFill:true,partialAndFullClose:true,historyPersistence:true,reconnection:true,stakingDepositClaimWithdraw:true,walletReconnect:true,networkChoices:7,noOverflow:true,realWalletApproval:false});
  await context.close();
 }
 await writeFile(out+'/results.json',JSON.stringify({results,scope:'Local fixture E2E; no real wallet approvals or funds'},null,2));
 console.log(JSON.stringify({passed:true,results}));
}finally{await browser.close();}

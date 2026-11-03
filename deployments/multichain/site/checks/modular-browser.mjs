// Real Chromium interactions with explicitly synthetic wallet/API observations.
// No server socket, exchange, wallet approval, or testnet write is exercised.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const base='https://vella-fixture.invalid',out='../snapshot/modular-browser';
await mkdir(out,{recursive:true});
const owner='0x1111111111111111111111111111111111111111';
const config={moduleApiVersion:1,brand:{name:'VELA',shortName:'VELA',accent:'#cbef8b'},network:{chainId:6281971,blockExplorerUrls:['https://dogeos-testnet.l2scan.co']},factory:'0x2222222222222222222222222222222222222222',modelConfigured:true,attestorConfigured:true,myDogeClientId:'',risk:{maxLtv:'.4',dogePrice:'.20',annualInterest:'.05',liquidationThreshold:'.65',reduceHealthBelow:1.4,blockHealthBelow:1.2,liquidationDiscount:'.05'}};
const data={address:owner,vault:{address:config.factory,owner,loanState:2,collateral:'10000000000000000000000',trading:true,paused:false,limits:{allocation:'800000000',positionSize:'200000000',maxLoss:'2000000',leverage:'1',expiresAt:'9999999999'}},health:{collateral:'10000',price:'.20',collateralValue:'2000',principal:'800',interest:'2',debt:'802',borrowCapacity:'0',withdrawableDoge:'0',healthFactor:'1.62',ltv:'.401',liquidationThreshold:'.65',loan:{state:'ACTIVE',paid:'0',seized:'0'}},paper:{equity:'790',available:'790',balance:'790',pnl:'-10',preferences:{autonomous:false},today:{realized:'-10',trades:2,wins:1,losses:1},totals:{trades:3,wins:2,losses:1},positions:[],trades:[],activity:[]},repayment:{freeEquity:'790',maxRepayable:'790',debt:'802',policy:null},events:[],transactions:[],demoSurplus:'0',directAllocation:'0'};
const report=process.env.VELLA_BROWSER_REPORT||`../snapshot/modular-browser${process.env.VELLA_TEST_WIDTHS?'-'+process.env.VELLA_TEST_WIDTHS:''}.json`,startedAt=new Date().toISOString();
let browser;const results=[];
try{
 browser=await chromium.launch({executablePath:chromium.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 for(const width of (process.env.VELLA_TEST_WIDTHS||'320,390,768,1440').split(',').map(Number)){
  const context=await browser.newContext({viewport:{width,height:900}});
  let authenticated=false,expired=false,direct=false;
  const calls=[],errors=[];
  await context.addInitScript(({owner})=>{
   const listeners={};
   window.ethereum={request:async({method})=>{
    if(['eth_accounts','eth_requestAccounts'].includes(method))return [owner];
    if(method==='eth_chainId')return '0x'+(6281971).toString(16);
    if(method==='eth_getBalance')return '0x0';
    throw Error('Unexpected synthetic wallet method: '+method);
   },on:(name,fn)=>listeners[name]=fn,removeListener:name=>delete listeners[name]};
   window.fixtureDisconnect=()=>listeners.disconnect?.();
  },{owner});
  await context.route(base+'/**',async route=>{
   const url=new URL(route.request().url()),pathname=url.pathname;
   if(pathname.startsWith('/api/')){
    let body={};
    if(route.request().method()==='POST'){body=route.request().postDataJSON();calls.push({pathname,body,key:route.request().headers()['idempotency-key']});}
    if(pathname==='/api/config')return route.fulfill({json:config});
    if(pathname==='/api/markets')return route.fulfill({json:{markets:[]}});
    if(pathname==='/api/logout'){authenticated=false;return route.fulfill({json:{authenticated:false}});}
    if(!authenticated||expired)return route.fulfill({status:401,json:{error:'Session expired'}});
    if(pathname==='/api/session')return route.fulfill({json:{address:owner,authenticated:true}});
    if(pathname==='/api/account'){
     const account=structuredClone(data);
     if(direct){account.vault=null;account.health=null;account.repayment.debt='0';account.repayment.maxRepayable='0';account.tradingPermission={allocation:'500000000',positionSize:'100000000',maxLoss:'2000000',leverage:'1',expiresAt:'9999999999'};}
     return route.fulfill({json:account});
    }
    if(pathname==='/api/repay')return route.fulfill({json:{amount:body.amount,health:{debt:'702'},simulated:true}});
    return route.fulfill({json:{status:'OK',simulated:true}});
   }
   const relative=pathname.startsWith('/assets/')?pathname.slice(1):'index.html';
   const file=path.join(process.env.VELLA_BROWSER_BUILD||'prepared',relative);
   return route.fulfill({body:await readFile(file),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow at ${width}`);
  await page.goto(base);await page.getByRole('heading',{name:'DeFi, Reimagined.'}).waitFor();
  await noOverflow();assert.equal(await page.locator('.simulation-notice').count(),0);
  assert.equal(await page.locator('.vella-home > section').count(),3);
  await page.locator('.home-actions .how-link').click();assert.equal(new URL(page.url()).hash,'#how-it-works');
  await page.locator('.vella-hero .vella-launch').click();await page.getByRole('heading',{name:'Welcome to VELA.'}).waitFor();
  assert.equal(await page.getByRole('tablist').count(),0);
  authenticated=true;
  await page.evaluate(()=>localStorage.setItem('autodefi.wallet.connector','injected'));
  await page.goto(base+'/vault#overview');await page.reload();await page.getByRole('tab',{name:'Overview'}).waitFor();
  assert.equal(await page.locator('.simulation-notice').count(),0);
  for(const [id,label] of [['vault','Vault'],['borrow','Borrow'],['trading','AI Trade'],['agent','Agent Mode']]){
   await page.getByRole('tab',{name:label,exact:false}).click();
   await page.locator(`#panel-${id}`).waitFor();await noOverflow();
   if(['borrow','trading'].includes(id)){
    const box=await page.locator('.simulation-notice').boundingBox();
    assert.ok(box.y>=0&&box.y+box.height<=900,`notice below fold: ${id} ${width}`);
    assert.equal(await page.locator('.simulation-notice').count(),1);
   }else assert.equal(await page.locator('.simulation-notice').count(),0);
   const openGuide=page.getByRole('button',{name:/Open walkthrough:/}).last();
   await openGuide.click();await page.getByRole('dialog').waitFor();
   await page.getByRole('button',{name:'Next',exact:true}).click();
   assert.equal(await page.getByText('STEP 2 / 4',{exact:false}).count(),1);
   await page.getByRole('button',{name:'Back',exact:true}).click();
   await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
   await page.getByRole('button',{name:'Dismiss tutorial'}).last().click();
   await page.getByRole('button',{name:/View walkthrough/}).last().click();
   await page.getByRole('button',{name:'Skip tutorial'}).click();
   await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));
   await page.screenshot({path:`${out}/${id}-${width}.png`,fullPage:true});
  }
  await page.getByRole('tab',{name:'Borrow'}).click();
  await page.getByLabel('Repayment amount · USDC').fill('100');
  await page.getByRole('button',{name:'Review partial repayment'}).click();
  await page.getByRole('heading',{name:'Repay simulated USDC'}).waitFor();
  assert.match(await page.getByRole('dialog').innerText(),/No real stablecoin/);
  await page.getByRole('button',{name:'Confirm',exact:true}).click();
  await page.getByRole('status').filter({hasText:'Simulated repayment of'}).waitFor();
  assert.ok(calls.some(c=>c.pathname==='/api/repay'&&c.body.amount==='100'&&c.key));
  await page.getByRole('tab',{name:'Agent Mode'}).click();
  await page.getByLabel('Custom percentage',{exact:true}).fill('37.5');
  await page.getByLabel('Repayment frequency').selectOption('weekly');
  await page.getByLabel(/I authorize simulated repayments/).check();
  await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/repayment/policy'),page.getByRole('button',{name:'Authorize repayment rules'}).click()]);
  assert.ok(calls.some(c=>c.pathname==='/api/repayment/policy'&&c.body.frequency==='weekly'&&c.body.sweepPercent==='37.5'));
  direct=true;
  await page.goto(base+'/vault#trading');await page.getByRole('button',{name:'Allocate demo capital'}).waitFor();
  await page.getByRole('button',{name:'Allocate demo capital'}).click();
  assert.match(await page.getByRole('dialog').innerText(),/No real deposit/);
  await page.getByRole('button',{name:'Close dialog'}).click();
  await page.getByRole('button',{name:'Start AI Trading',exact:true}).click();
  assert.match(await page.getByRole('dialog').innerText(),/No real exchange orders/);
  await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/trading/start'),page.getByRole('button',{name:'Confirm',exact:true}).click()]);
  assert.ok(calls.some(c=>c.pathname==='/api/trading/start'));
  await page.getByRole('dialog').waitFor({state:'hidden'});
  expired=true;
  await page.getByRole('heading',{name:'Welcome to VELA.'}).waitFor();
  assert.equal(await page.getByRole('tablist').count(),0);
  authenticated=true;expired=false;
  await page.evaluate(()=>localStorage.setItem('autodefi.wallet.connector','injected'));
  await page.reload();await page.getByRole('tablist').waitFor();
  await page.evaluate(()=>window.fixtureDisconnect());
  await page.getByRole('heading',{name:'Welcome to VELA.'}).waitFor();
  assert.equal(await page.getByRole('tablist').count(),0);
  assert.deepEqual(errors,[]);
  results.push({width,status:'PASS',requests:calls.length});console.log(`PASS: ${width}px fixture workflow`);await context.close();
 }
 await writeFile(report,JSON.stringify({status:'PASS',startedAt,finishedAt:new Date().toISOString(),scope:'Real Chromium, synthetic API/provider. No genuine wallet, live HTTP, exchange or testnet verification.',results},null,2));
 console.log('PASS: mobile/desktop, module switching, visible notices, CTA, tutorial navigation, confirmations, repayment configuration, session expiry and wallet disconnect. Synthetic API/provider only.');
}catch(error){const failed=browser?.contexts().at(-1)?.pages().at(-1);if(failed){await failed.screenshot({path:out+'/failure.png',fullPage:true});await writeFile(out+'/failure.html',await failed.content());}await writeFile(report,JSON.stringify({status:browser?'FAIL':'BLOCKED',startedAt,finishedAt:new Date().toISOString(),phase:browser?'journey':'browser launch',results,error:error.stack},null,2));throw error;}
finally{await browser?.close();}

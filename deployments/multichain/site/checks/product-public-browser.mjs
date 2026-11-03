import {chromium} from 'playwright';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const out=(await readFile('../.runtime/current-ux-wallet','utf8')).trim(),base='https://defivela.xyz',publicMode=process.argv.includes('--public');
const prefix=(await readFile(out+'/asset-prefix.txt','utf8')).trim(),routes=['/','/vault','/borrow','/trading','/repay','/overview','/how-it-works'];
const browser=await chromium.launch({executablePath:chromium.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const results=[],mode=publicMode?'public':'candidate-live';await mkdir(out+'/browser',{recursive:true});
try{
 for(const width of [1440,390,320]){
  const context=await browser.newContext({viewport:{width,height:950},reducedMotion:'reduce'});
  if(!publicMode)await context.route(base+'/**',async route=>{
   const url=new URL(route.request().url());
   if(routes.includes(url.pathname)||url.pathname.startsWith(prefix)){
    const file=url.pathname.startsWith(prefix)?out+'/publish/assets/'+path.basename(url.pathname):out+'/publish/index.html';
    await route.fulfill({body:await readFile(file),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
   }else await route.continue();
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${width}: ${page.url()}`);
  try{
   await page.goto(base);await page.getByRole('heading',{name:'DeFi, Reimagined.',exact:true}).waitFor();assert.equal(await page.locator('.hero-subheadline').innerText(),'Intelligent Trading. Limitless Possibilities.');assert.equal(await page.locator('.home-networks li').count(),7);assert.equal(await page.getByRole('link',{name:/START TRADING/}).count(),3);await noOverflow();
   await page.screenshot({path:out+`/browser/${mode}-home-${width}.png`,fullPage:true});
   await page.locator('.home-actions .how-link').click();await page.getByRole('heading',{name:'Big possibilities. Simple steps.'}).waitFor();assert.equal(new URL(page.url()).pathname,'/how-it-works');assert.equal(await page.locator('.how-steps article').count(),8);assert.match(await page.locator('.video-intro').innerText(),/has not been generated/);assert.equal(await page.locator('video').count(),0);await noOverflow();
   await page.screenshot({path:out+`/browser/${mode}-how-${width}.png`,fullPage:true});
   for(const [route,title] of [['/vault','Welcome to VELA.'],['/borrow','Borrow with confidence.'],['/trading','Your AI. Your limits.'],['/repay','Repay & Manage'],['/overview','Your Vela overview.']]){
    await page.goto(base+route);await page.getByRole('heading',{name:title,exact:true}).waitFor();assert.equal(new URL(page.url()).pathname,route);await page.locator('[data-network-selector]').waitFor();const header=await page.locator('.portal-shell>header').boundingBox(),selector=await page.locator('[data-network-selector]').boundingBox();assert.ok(selector.y>=header.y&&selector.y+selector.height<=header.y+header.height);assert.equal(await page.getByRole('link',{name:'How It Works',exact:true}).isVisible(),true);await noOverflow();
    for(const target of ['/vault','/borrow','/trading','/repay'])assert.ok(await page.locator(`a[href="${target}"]`).count()>0);
   }
   await page.goto(base+'/vault');const trigger=page.locator('[data-network-selector]');await trigger.waitFor();await page.getByRole('button',{name:'MetaMask — Connect',exact:true}).click();await page.getByText('MetaMask was not detected in this browser.',{exact:false}).waitFor();assert.equal(await page.getByRole('tablist').count(),0);assert.equal(await page.getByRole('button',{name:'Coming Soon',exact:true}).isDisabled(),true);assert.equal(await page.locator('a[href*="mydoge"]').count(),0);
   await trigger.click();const menu=page.getByRole('listbox');await menu.waitFor();assert.equal(await page.getByRole('option').count(),7);const bounds=await menu.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width&&bounds.y>=0&&bounds.y+bounds.height<=950);await page.screenshot({path:out+`/browser/${mode}-selector-${width}.png`});
   await page.keyboard.press('End');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('[data-network-selector]')?.dataset.networkSelector==='robinhood');assert.match(await trigger.innerText(),/Robinhood Chain Testnet/);
   const names={dogeos:'DogeOS',solana:'Solana',base:'Base',ethereum:'Ethereum',bnb:'BNB Chain',arbitrum:'Arbitrum',robinhood:'Robinhood Chain Testnet'};
   for(const [id,name] of Object.entries(names)){
    if(await trigger.getAttribute('data-network-selector')!==id){await trigger.click();await page.getByRole('option',{name:new RegExp('^'+name)}).click();await page.waitForFunction(id=>document.querySelector('[data-network-selector]')?.dataset.networkSelector===id,id);}
    assert.match(await trigger.innerText(),/Demo/);const wallet=id==='solana'?'Phantom':'MetaMask';await page.getByRole('button',{name:wallet+' — Connect',exact:true}).waitFor();assert.equal(await page.getByRole('link',{name:'Install '+wallet+' ↗',exact:true}).count(),1);await noOverflow();
    if(id==='solana'){await page.getByRole('button',{name:'Phantom — Connect',exact:true}).click();await page.getByText('Phantom was not detected in this browser.',{exact:false}).waitFor();}
    if(id==='dogeos'){await page.locator('.dogeos-read-status').filter({hasText:'Verified from DogeOS RPC'}).waitFor({timeout:45000});assert.match(await page.locator('.wallet-card').innerText(),/6281971/);await page.screenshot({path:out+`/browser/${mode}-vault-${width}.png`,fullPage:true});}
   }
   assert.deepEqual(errors,[]);results.push({width,status:'PASS',sevenNetworks:true,directRoutes:true,homepagePreserved:true,selectorKeyboard:true,selectorWithinViewport:true,walletInstallationFallback:true,myDogeDisabled:true,dogeosReadOnlyRPC:true,genuineWalletApproval:'NOT_TESTED'});
  }catch(error){await page.screenshot({path:out+`/browser/${mode}-failure-${width}.png`,fullPage:true});throw error;}
  finally{await context.close();}
 }
 const report={status:'PASS',publicMode,scope:'Actual public API and DogeOS RPC; browser without wallet extensions. No approval or customer transaction claimed.',results};await writeFile(out+`/${mode}-browser.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}

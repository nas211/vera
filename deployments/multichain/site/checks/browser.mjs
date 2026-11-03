import {chromium} from 'playwright';
import {access,mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const output='../snapshot';await mkdir(output,{recursive:true});
let browser;
try{await access(chromium.executablePath());browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});}
catch(error){await writeFile(output+'/guided-browser.json',JSON.stringify({status:'NOT VERIFIED',stage:'browser availability',error:error.message},null,2));console.error('Browser verification unavailable:',error.message.slice(0,500));process.exit(2);}
try{
 // Existing private preview only. No wallet mock, second server or TLS bypass.
 const base=process.env.VERA_PROTOCOL_TEST_URL||process.env.ADP_TEST_URL;
 if(!base)throw Error('Set VERA_PROTOCOL_TEST_URL to the privately accessible VELLA origin.');
 const page=await browser.newPage();const errors=[],broken=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 page.on('response',r=>{if(new URL(r.url()).origin===new URL(base).origin&&r.status()>=400)broken.push(`${r.status()} ${new URL(r.url()).pathname}`);});
 const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
 const noPrivate=async()=>{assert.equal(await page.getByRole('tablist').count(),0);assert.equal(await page.getByRole('button',{name:'Stop Trading',exact:true}).count(),0);assert.equal(await page.locator('#simulator').count(),0);};
 for(const width of [320,390,768,1440]){
  await page.setViewportSize({width,height:900});await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.getByRole('heading',{name:'DeFi, Reimagined.'}).waitFor();
  assert.equal(await page.locator('.vella-home > section').count(),3);
  assert.equal(await page.locator('.vella-home img').count(),0);
  await page.locator('.protocol-vault').waitFor();
  const nav=page.getByRole('navigation',{name:'VELA application sections'});
  assert.equal(await nav.getByRole('link').count(),5);
  for(const label of ['Overview','Vault','Borrow','AI Trade','Agent Mode'])assert.equal(await nav.getByRole('link',{name:label,exact:true}).isVisible(),true);
  await page.getByRole('button',{name:/Unlock capital/}).click();
  await page.locator('.protocol-visual.phase-borrow').waitFor();
  await page.getByRole('button',{name:/Repay & unlock/}).click();
  await page.locator('.protocol-visual.phase-repay').waitFor();
  await noOverflow();await noPrivate();
  await page.locator('.home-actions .how-link').click();assert.equal(new URL(page.url()).hash,'#how-it-works');
  await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
  await page.screenshot({path:`${output}/vella-expanded-home-${width}.png`,fullPage:true});
  await page.locator('.vella-hero a[href="/vault"]').click();
  await page.getByRole('heading',{name:'Welcome to VELA.'}).waitFor();await noPrivate();await noOverflow();
  await page.screenshot({path:`${output}/vella-wallet-${width}.png`,fullPage:true});
  await page.goBack();await page.locator('.vella-hero').waitFor();
  await page.getByRole('navigation',{name:'VELA application sections'}).getByRole('link',{name:'Borrow',exact:true}).click();
  await page.getByRole('heading',{name:'Welcome to VELA.'}).waitFor();assert.equal(new URL(page.url()).hash,'#borrow');await noPrivate();
  assert.equal(await page.getByRole('navigation',{name:'VELA application sections'}).getByRole('link',{name:'Borrow',exact:true}).getAttribute('aria-current'),'location');
  await page.goBack();await page.locator('.vella-hero').waitFor();
 }
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await page.locator('.vella-hero h1').evaluate(el=>getComputedStyle(el).animationName),'none');
 await page.getByRole('button',{name:'Play protocol animation'}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Play protocol animation'}).isDisabled(),true);
 assert.deepEqual(errors,[]);assert.deepEqual(broken,[]);
 await writeFile(output+'/guided-browser.json',JSON.stringify({status:'PASS',widths:[320,390,768,1440],scope:'Expanded homepage, visible top links, 3D protocol chapter controls, no overflow, reduced motion, console and wallet-first destination links. Authenticated tabs, genuine wallet approval and on-chain flows require a signed-in browser and are NOT VERIFIED by this script.'},null,2));
}finally{await browser.close();}

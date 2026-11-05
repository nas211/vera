// Public, read-only regression: portal-to-home navigation must keep the latest flow.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';

const base=process.env.VELA_TEST_ORIGIN||'https://defivela.xyz';
const out=process.env.CHECK_ARTIFACTS_DIR||'/tmp/vela-homepage-navigation';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:chromium.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const results=[];
let sharedScript,sharedStyle;
try{
 for(const width of [390,1440]){
  for(const entry of ['/','/vault','/borrow','/trading','/repay','/overview','/how-it-works']){
   const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
   const page=await context.newPage(),errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   const response=await page.goto(base+entry);
   assert.equal(response.status(),200);
   const script=await page.locator('script[type="module"]').getAttribute('src');
   const style=await page.locator('link[rel="stylesheet"]').first().getAttribute('href');
   sharedScript??=script;sharedStyle??=style;
   assert.equal(script,sharedScript,entry+' must use the same app as the homepage');
   assert.equal(style,sharedStyle,entry+' must use the same stylesheet as the homepage');
   if(entry!=='/'&&entry!=='/how-it-works'){
    const network=page.getByRole('button',{name:'Network: DogeOS',exact:true});
    await network.waitFor();
    assert.match(await network.innerText(),/Featured/);
    await page.getByRole('link',{name:'Home',exact:true}).click();
   }else if(entry==='/how-it-works'){
    await page.getByRole('link',{name:'VELA homepage',exact:true}).click();
   }
   await page.locator('.visual-chapters').waitFor();
   assert.equal(new URL(page.url()).pathname,'/');
   assert.deepEqual(await page.locator('.visual-chapters button').allTextContents(),['01Lock & earn APY','02Borrow USDC','03Let AI trade','04Profits repay loan']);
   assert.equal(await page.locator('.capital-anchor>span:first-child').innerText(),'BORROW USDC');
   assert.equal(await page.locator('.capital-anchor>small').innerText(),'COLLATERAL LOAN');
   await page.getByRole('button',{name:'04 Profits repay loan',exact:true}).click();
   assert.equal(await page.locator('.stage-label>span').innerText(),'PROFITS → REPAY LOAN → UNLOCK COLLATERAL');
   assert.match(await page.locator('.protocol-visual figcaption').innerText(),/Once the full debt is settled, unlock your collateral/);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
   assert.deepEqual(errors,[]);
   if(entry==='/vault')await page.locator('.protocol-visual').screenshot({path:out+'/from-vault-'+width+'.png'});
   results.push({entry,width,sharedScript:true,currentFlow:true,noOverflow:true,noJavaScriptErrors:true});
   await context.close();
  }
 }
 await writeFile(out+'/results.json',JSON.stringify({passed:true,scope:'Public page loads and navigation only; no wallet or trading actions',sharedScript,sharedStyle,results},null,2));
 console.log(JSON.stringify({passed:true,journeys:results.length,sharedScript,sharedStyle}));
}finally{await browser.close();}

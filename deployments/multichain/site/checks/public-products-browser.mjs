// Read-only public page checks. Opening /paper creates only its empty demo desk.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const out=process.env.CHECK_ARTIFACTS_DIR||'/tmp/vela-public-products';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:chromium.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const results=[];
try{
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  for(const route of ['/','/vault','/borrow','/trade','/paper','/earn']){
   const response=await page.goto('https://defivela.xyz'+route);assert.equal(response.status(),200);
   if(route==='/paper')await page.locator('.feed-status.positive').waitFor({timeout:25000});
   if(route==='/earn'){
    await page.getByRole('button',{name:'Connect MetaMask',exact:true}).waitFor();
    await page.waitForFunction(()=>document.querySelector('select[aria-label="Staking network"]')?.options.length===7);
    assert.equal(await page.getByLabel('Staking network').locator('option').count(),7);
    assert.match(await page.getByLabel('Staking network').locator('option').first().innerText(),/Featured.*DogeOS/);
   }
   if(['/vault','/borrow','/trade'].includes(route)){
    const network=page.getByRole('button',{name:'Network: DogeOS',exact:true});await network.waitFor();
    assert.match(await network.innerText(),/Featured/);await network.click();
    assert.equal(await page.getByRole('option').count(),7);
    assert.match(await page.getByRole('option').first().innerText(),/DogeOS/);
    await page.keyboard.press('Escape');
   }
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,route+' mobile overflow');
   await page.screenshot({path:out+'/'+(route.slice(1)||'home')+'-'+width+'.png',fullPage:true});
   results.push({route,width,httpStatus:200,noOverflow:true});
  }
  assert.deepEqual(errors,[]);await context.close();
 }
 await writeFile(out+'/results.json',JSON.stringify({passed:true,results,realWalletDeviceApproval:false},null,2));
 console.log(JSON.stringify({passed:true,pages:results.length,realWalletDeviceApproval:false}));
}finally{await browser.close();}

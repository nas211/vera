const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const page=await browser.newPage({viewport:{width:390,height:844}});
 const errors=[],responses=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',r=>{const u=new URL(r.url());if(/mydoge|dogeos/.test(u.hostname)) responses.push({host:u.hostname,path:u.pathname,status:r.status()});});
 const origin='https://defivela.uuuiiaaaa.chatgpt.site';
 await page.route(origin+'/**',route=>{
  const pathname=new URL(route.request().url()).pathname;
  const file=path.join(process.cwd(),'dist',pathname==='/'?'index.html':pathname);
  const ext=path.extname(file);
  if(!file.startsWith(path.join(process.cwd(),'dist')+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:'Not found'});
  return route.fulfill({contentType:({'.html':'text/html','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml'})[ext]||'application/octet-stream',body:fs.readFileSync(file)});
 });
 await page.goto(origin+'/mydoge.html',{waitUntil:'domcontentloaded'});
 await page.waitForSelector('#mydoge-open',{timeout:45000});
 await page.locator('#mydoge-open').click();
 await page.locator('[role="dialog"]').first().waitFor({state:'visible',timeout:30000});
 await page.waitForTimeout(8000);
 const folder=process.env.CHECK_ARTIFACTS_DIR||'/tmp/vela-checks';fs.mkdirSync(folder,{recursive:true});
 await page.screenshot({path:path.join(folder,'vela-mydoge-sdk.png'),fullPage:true});
 console.log(JSON.stringify({result:errors.length?'errors':'SDK modal opened',errors,responses,visibleText:(await page.locator('body').innerText()).slice(-1800),realWalletUsed:false},null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

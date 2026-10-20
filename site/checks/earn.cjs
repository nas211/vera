const assert=require("node:assert/strict");
const {chromium}=require("playwright");
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||"/opt/google/chrome/chrome",headless:true,args:["--no-sandbox"]});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    const origin=process.env.PREVIEW_URL||"http://127.0.0.1:8769";
    await page.goto(origin+"/earn.html",{waitUntil:"networkidle"});
    assert.match(await page.title(),/Staking/);
    await page.locator("#earn-deposit").fill("10000");
    await page.locator("#earn-rate").fill("10");
    await page.locator("#earn-months").selectOption("12");
    assert.equal(await page.locator("#earn-output").textContent(),"1,000");
    await page.locator("#earn-months").selectOption("24");
    assert.equal(await page.locator("#earn-output").textContent(),"2,100");
    await page.locator("#earn-rate").fill("0");
    assert.equal(await page.locator("#earn-output").textContent(),"0");
    await page.locator("#earn-rate").fill("31");
    assert.equal(await page.locator("#earn-output").textContent(),"—");
    assert(await page.getByRole("button",{name:"Staking opens soon"}).isDisabled());
    for(const width of [320,390,768,1440]){
      await page.setViewportSize({width,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow ${width}`);
    }
    assert.deepEqual(errors,[]);
    console.log("PASS: dedicated Staking page, compounded APY scenarios, invalid input, disabled deposits and four responsive widths.");
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});

const assert=require("node:assert/strict");
const {chromium}=require("playwright");
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||"/opt/google/chrome/chrome",headless:true,args:["--no-sandbox"]});
  try {
    const page=await browser.newPage({viewport:{width:390,height:844}}), errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    await page.route("**/backend-config.js",r=>r.fulfill({contentType:"application/javascript",body:'window.VELA_BACKEND_URL="";'}));
    await page.route("**/api/**",r=>r.fulfill({status:503,body:"Isolated wallet test"}));
    await page.addInitScript(()=>{
      const handlers={},state=window.injectedFixture={chain:"0x1",calls:[],installed:false};
      window.ethereum={isMetaMask:true,on:(n,fn)=>{handlers[n]=fn;},removeListener:n=>{delete handlers[n];},request:async args=>{
        state.calls.push(args);
        if(args.method==="eth_requestAccounts")return ["0x1111111111111111111111111111111111111111"];
        if(args.method==="eth_chainId")return state.chain;
        if(args.method==="eth_getBalance")return "0x8ac7230489e80000";
        if(args.method==="wallet_addEthereumChain"){state.installed=true;return null;}
        if(args.method==="wallet_switchEthereumChain"){
          if(!state.installed)throw {code:4902};
          state.chain=args.params[0].chainId;handlers.chainChanged?.(state.chain);return null;
        }
        throw new Error("Unexpected wallet request");
      }};
    });
    await page.goto(`${process.env.PREVIEW_URL||"http://127.0.0.1:8769"}/mydoge.html?wallet=metamask`);
    await page.waitForSelector("#injected-connect");
    assert.equal(await page.evaluate(()=>injectedFixture.calls.length),0);
    await page.locator("#injected-connect").click();
    await page.locator("#injected-switch").click();
    await page.waitForFunction(()=>document.body.innerText.includes("10.0000 DOGE"));
    const added=await page.evaluate(()=>injectedFixture.calls.find(c=>c.method==="wallet_addEthereumChain").params[0]);
    assert.equal(added.chainId,"0x5fdaf3");assert.deepEqual(added.rpcUrls,["https://rpc.testnet.dogeos.com/"]);
    await page.getByRole("button",{name:"Disconnect",exact:true}).click();
    await page.waitForSelector("#injected-connect");
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    assert.deepEqual(errors,[]);
    console.log("PASS: explicit MetaMask fallback, network add/switch, DOGE balance, disconnect and mobile layout; mocked provider.");
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});

// Real local API + public prices + configured AI. No wallet transaction RPC.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const {chromium}=require("playwright");
(async()=>{
  const b=await chromium.launch({executablePath:"/opt/google/chrome/chrome",headless:true,args:["--no-sandbox"]});
  let p,token,stopped=false;
  const origin=process.env.PREVIEW_URL||"http://127.0.0.1:8787";
  try{
    p=await b.newPage({viewport:{width:390,height:844}});
    const errors=[],writes=[];
    p.on("pageerror",e=>errors.push(e.message));
    p.on("request",r=>{
      if(r.url().includes("rpc.testnet.dogeos.com")&&/eth_(send|sign)/.test(r.postData()||""))writes.push(r.url());
    });
    await p.goto(origin+"/mydoge.html");
    const start=p.locator("#practice-start");
    await start.waitFor({timeout:60000});
    await p.waitForFunction(()=>!document.querySelector("#practice-start")?.disabled,{timeout:60000});
    await p.locator("#practice-amount").fill("1000");
    await start.click();
    await p.locator("#practice-stop").waitFor({timeout:30000});
    token=await p.evaluate(()=>localStorage.getItem("vela:practice:v1"));
    await p.reload();
    await p.locator("#practice-stop").waitFor({timeout:30000});
    let data;
    const read=async()=>p.evaluate(async token=>{
      const base=window.VELA_BACKEND_URL||"";
      return(await fetch(base+"/api/practice",{headers:{"X-Vela-Practice":token}})).json();
    },token);
    for(let i=0;i<60;i++){
      data=await read();
      if(data.positions?.length)break;
      if(i%6===0)console.log("Waiting for genuine AI decision:",data.decision?.phase||"research",data.decision?.answer?.action||data.decision?.answer?.verdict||"");
      await p.waitForTimeout(5000);
    }
    const opened=data.positions?.[0]||null;
    await p.locator("#practice-stop").click();
    await p.getByRole("status").filter({hasText:"Portfolio stopped"}).waitFor({timeout:60000});
    stopped=true;
    data=await read();
    assert.equal(data.user.status,"settled");
    assert.equal(writes.length,0);
    assert.equal(data.positions.length,0);
    assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),"Mobile overflow");
    await p.screenshot({path:"../.runtime/practice-mobile.png",fullPage:true});
    const report={verifiedAt:new Date().toISOString(),origin,realModel:true,livePublicPrices:true,
      walletTransactions:writes.length,opened:opened?{symbol:opened.symbol,side:opened.side,entry:opened.entry}:null,
      closedTrades:data.trades.length,pnl:data.user.pnl,scope:"One engineering verification practice session",
      decision:data.decision,errors,stopped};
    fs.writeFileSync("../.runtime/practice-live-e2e.json",JSON.stringify(report,null,2)+"\n");
    assert(opened,"AI abstained; no AI-opened trade was observed during verification");
    assert.equal(data.trades.length,1);
    assert.equal(data.trades[0].chain_status,"offchain");
    console.log("PASS: real AI opened and closed at live prices; reload persisted, P&L saved, zero wallet transactions.",JSON.stringify({market:opened.symbol,side:opened.side,pnl:data.user.pnl}));
  }finally{
    if(p&&token&&!stopped)await p.evaluate(async token=>{
      await fetch((window.VELA_BACKEND_URL||"")+"/api/practice/stop",{method:"POST",headers:{"X-Vela-Practice":token}});
    },token).catch(()=>{});
    await b.close();
  }
})().catch(e=>{console.error(e.message);process.exit(1)});

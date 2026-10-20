// Browser integration with deterministic API/provider fixtures; no real wallet signing.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {build} = require("esbuild");
const {chromium} = require("playwright");
const config = require("../src/chain-config.json");
const abi = require("viem").parseAbi(["function startTrading(uint256 strategyId) payable", "function stopTrading()"]);
const wallet = "0x1111111111111111111111111111111111111111";
const startHash = "0x" + "11".repeat(32), stopHash = "0x" + "22".repeat(32), settledHash = "0x" + "33".repeat(32);

(async()=>{
  const output = await build({
    stdin:{contents:`
      import React from "react";import{createRoot}from"react-dom/client";
      import{TradingPanel}from"./src/trading-panel.jsx";
      window.testWallet={calls:[],chain:"0x5fdaf3",reject:false,accounts:["${wallet}"],receipt:null};
      const provider={async request(args){
        testWallet.calls.push(args);
        if(args.method==="eth_chainId")return testWallet.chain;
        if(args.method==="eth_accounts")return testWallet.accounts;
        if(args.method==="eth_getTransactionReceipt")return testWallet.receipt;
        if(args.method==="eth_sendTransaction"){
          if(testWallet.reject)throw{code:4001};
          return args.params[0].value==="0x0"?"${stopHash}":"${startHash}";
        }throw Error("Unexpected method");
      }};
      createRoot(document.getElementById("mydoge-root")).render(
        <TradingPanel address="${wallet}" provider={provider} onDogeOS={true}/>);
    `,resolveDir:process.cwd(),loader:"jsx"},
    write:false,bundle:true,format:"esm",jsx:"automatic",
    define:{"process.env.NODE_ENV":'"production"'}
  });
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH||"/opt/google/chrome/chrome",headless:true,args:["--no-sandbox","--disable-dev-shm-usage"]});
  try {
    const page = await browser.newPage({viewport:{width:390,height:844}});
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    let state={user:null,positions:[],trades:[],equity:[]}, ready=true, failApi=false;
    await page.route("**/backend-config.js",r=>r.fulfill({contentType:"application/javascript",body:'window.VELA_BACKEND_URL="";'}));
    await page.route("**/assets/mydoge.js",r=>r.fulfill({contentType:"application/javascript",body:output.outputFiles[0].text}));
    await page.route("https://rpc.testnet.dogeos.com/**",r=>{
      const q=r.request().postDataJSON();const result=q.method==="eth_call"?`0x${(10n**16n).toString(16).padStart(64,"0")}`:{eth_chainId:"0x5fdaf3",eth_getBalance:"0x8ac7230489e80000",eth_estimateGas:"0x3d090",eth_gasPrice:"0x3b9aca00",eth_getTransactionCount:"0x0"}[q.method];
      return r.fulfill({contentType:"application/json",body:JSON.stringify({jsonrpc:"2.0",id:q.id,result})});
    });
    await page.route("**/api/**",r=>{
      if(failApi)return r.fulfill({status:503,body:"Unavailable"});
      const p=new URL(r.request().url()).pathname;
      if(p==="/api/start-quote")return r.fulfill({status:202,contentType:"application/json",body:JSON.stringify({id:"a".repeat(32),status:"pending"})});
      if(p==="/api/start-quote/"+"a".repeat(32))return r.fulfill({contentType:"application/json",body:JSON.stringify({status:"ready",quote:{user:wallet,vault:config.deployment.vault,chainId:config.chainId,strategyId:1,dogeIn:"5000000000000000000",price:"10000000",deadline:String(Math.floor(Date.now()/1000)+120),version:1}})});
      const body=p==="/api/config"?{chain:config,mode:"testnet-paper",deployment:config.deployment,abi,ready}:
        p==="/api/strategies"?[{id:1,name:"Vela AI Agents",agent:"Signals and risk",risk:"Managed",market:"DOGE · BTC · ETH · SOL",available:true,modelReady:false,availabilityReason:"Signal and risk agents running · AI research model not connected"}]:
        p==="/api/prices"?[{symbol:"DOGE/USD",value:".1",safe:true}]:
        p==="/api/leaderboard"?{rows:[]}:state;
      return r.fulfill({contentType:"application/json",body:JSON.stringify(body)});
    });
    await page.goto((process.env.PREVIEW_URL||"http://127.0.0.1:8787")+"/mydoge.html");
    await page.locator("#start-trading").waitFor();
    assert.equal(await page.getByRole("radio").count(),1);
    assert.equal(await page.getByRole("radio",{name:/Vela AI Agents/}).isEnabled(),true);
    await page.evaluate(()=>testWallet.chain="0x1");
    await page.locator("#start-trading").click();
    await page.getByRole("alert").filter({hasText:"Switch to DogeOS"}).waitFor();
    assert.equal(await page.evaluate(()=>testWallet.calls.filter(c=>c.method==="eth_sendTransaction").length),0);
    await page.evaluate(()=>{testWallet.chain="0x5fdaf3";testWallet.reject=true;});
    await page.locator("#start-trading").click();
    await page.locator("#confirm-transaction").click();
    await page.getByRole("alert").filter({hasText:"Cancelled"}).waitFor();
    await page.evaluate(()=>{testWallet.reject=false;testWallet.calls=[];});
    await page.locator("#doge-amount").fill("5");
    await page.locator("#start-trading").click();
    await page.locator("#confirm-transaction").click();
    await page.getByText("Converting DOGE → USDC",{exact:false}).waitFor();
    const sent = await page.evaluate(()=>testWallet.calls.filter(c=>c.method==="eth_sendTransaction"));
    assert.equal(sent.length,1);assert.equal(sent[0].params[0].chainId,"0x5fdaf3");
    assert.equal(BigInt(sent[0].params[0].value),5n*10n**18n);
    assert.equal(sent[0].params[0].to,config.deployment.vault);
    assert.equal(await page.locator("#start-trading").count(),0);
    await page.reload(); // Pending hash survives reload and does not resubmit.
    await page.getByText("Converting DOGE → USDC",{exact:false}).waitFor();
    assert.equal(await page.evaluate(()=>testWallet.calls.filter(c=>c.method==="eth_sendTransaction").length),0);
    state={user:{status:"active",start_tx:startHash,equity:".5",pnl:"0",pnlPercent:"0",dogeEquivalent:"5"},
      positions:[],trades:[],equity:[{equity:".5"},{equity:".5001"}]};
    await page.locator("#stop-trading").waitFor({timeout:15000});
    failApi=true;await page.getByText("Live service unavailable.",{exact:false}).waitFor({timeout:15000});
    assert.equal(await page.locator("#stop-trading").isEnabled(),true);
    failApi=false;
    await page.locator("#stop-trading").click();
    await page.locator("#confirm-transaction").click();
    await page.getByText("Closing positions",{exact:false}).waitFor();
    const stopped=await page.evaluate(()=>testWallet.calls.filter(c=>c.method==="eth_sendTransaction"));
    assert.equal(stopped.length,1);assert.equal(stopped[0].params[0].value,"0x0");
    state.user={...state.user,status:"stopping",stop_tx:stopHash};
    await page.waitForTimeout(3500);
    state.user={...state.user,status:"settled",settlement_tx:settledHash,doge_out:"4999000000000000000"};
    await page.locator("#settlement-link").waitFor({timeout:15000});
    assert.match(await page.locator("#settlement-link").getAttribute("href"),new RegExp(settledHash));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,"Mobile overflow");
    const artifacts=process.env.CHECK_ARTIFACTS_DIR||"/tmp/vela-checks";
    fs.mkdirSync(artifacts,{recursive:true});
    await page.screenshot({path:path.join(artifacts,"vela-trading-mobile.png"),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:path.join(artifacts,"vela-trading-desktop.png"),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log("PASS: network gate, cancellation, single START/STOP signature, pending reload, service outage exit, confirmed settlement, mobile layout. Mock provider/API; no real wallet approval.");
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});

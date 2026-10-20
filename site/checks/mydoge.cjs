const assert = require("node:assert/strict");
const { build } = require("esbuild");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const fs = require("node:fs");
const path = require("node:path");

(async () => {
  const mockSdk = `
    import {useSyncExternalStore} from "react";
    const listeners = new Set();
    const state = window.mockDoge = {
      calls: [], hold: false, resolve: null, switchError: null,
      wallet: {isConnected:false,isConnecting:false,isWalletReady:false,walletStatus:"disabled"},
      account: {address:null,chainType:"evm",chainId:6281971,currentWallet:{info:{name:"MyDoge"}},currentProvider:null},
      publish(patch) {
        this.account = {...this.account,...patch};
        listeners.forEach(fn=>fn());
      },
      emit(wallet) {
        this.wallet = {...this.wallet,...wallet};
        listeners.forEach(fn=>fn());
      }
    };
    const provider = {
      async request(args) {
        state.calls.push(args);
        if(args.method==="eth_chainId") return state.observedChain || "0x5fdaf3";
        if(args.method==="eth_getBalance") {
          if(state.hold) return new Promise(resolve=>{state.resolve=resolve;});
          return "0x8ac7230489e80000";
        }
        throw new Error("Unexpected method");
      }
    };
    const subscribe = fn => {listeners.add(fn);return ()=>listeners.delete(fn);};
    const openModal = async () => {
      if(state.connectError) throw {code:4001};
      state.publish({address:"0x1111111111111111111111111111111111111111",currentProvider:provider,chainType:"evm"});
      state.emit({isConnected:true});
    };
    const disconnect = async () => {
      if(state.disconnectError) throw new Error("Unavailable");
      state.publish({address:null,currentProvider:null});state.emit({isConnected:false});
    };
    const switchChain = async args => {
      state.calls.push({method:"switchChain",params:args});
      if(state.switchError) throw {code:state.switchError};
      state.observedChain="0x5fdaf3";
      state.publish({chainType:"evm",chainId:args.chainInfo.id});
    };
    export const ChainTypeEnum={EVM:"evm"};
    export function WalletConnectProvider({config,children}) {state.config=config;return children;}
    export function useWalletConnect(){
      return {...useSyncExternalStore(subscribe,()=>state.wallet),openModal,disconnect};
    }
    export function useAccount(){
      return {...useSyncExternalStore(subscribe,()=>state.account),switchChain};
    }
  `;
  const output = await build({
    stdin: { contents: `
      import React from "react";
      import {createRoot} from "react-dom/client";
      import {MyDogeApp} from "./src/mydoge-app.jsx";
      createRoot(document.getElementById("mydoge-root")).render(<MyDogeApp clientId="mock-public-client-id" />);
    `, resolveDir: process.cwd(), loader: "jsx" },
    write: false, bundle: true, format: "esm", jsx: "automatic",
    define: { "process.env.NODE_ENV": '"production"' },
    plugins: [{
      name: "mock-dogeos-provider",
      setup(plugin) {
        plugin.onResolve({filter:/^@dogeos\/dogeos-sdk$/},()=>({path:"mock-sdk",namespace:"mock"}));
        plugin.onLoad({filter:/.*/,namespace:"mock"},()=>({contents:mockSdk,loader:"jsx",resolveDir:process.cwd()}));
        plugin.onResolve({filter:/@dogeos\/dogeos-sdk\/style.css/},()=>({path:"empty-css",namespace:"empty"}));
        plugin.onLoad({filter:/.*/,namespace:"empty"},()=>({contents:"",loader:"js"}));
      }
    }]
  });
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || undefined,
    headless: true, args: ["--no-sandbox","--disable-dev-shm-usage"]
  });
  const page = await browser.newPage({viewport:{width:390,height:844}});
  const errors=[], external=[];
  page.on("pageerror",error=>errors.push(error.message));
  await page.route("**/assets/mydoge.js",route=>route.fulfill({contentType:"application/javascript",body:output.outputFiles[0].text}));
  await page.route("**/backend-config.js",route=>route.fulfill({contentType:"application/javascript",body:'window.VELA_BACKEND_URL="";'}));
  // Wallet-only suite: the separate trading suite covers the backend contract.
  await page.route("**/api/**",route=>route.fulfill({status:503,body:"Offline wallet fixture"}));
  await page.route(/https:\/\/(?!fonts\.googleapis\.com|fonts\.gstatic\.com)/, route => {external.push(route.request().url());return route.abort();});
  const origin=process.env.PREVIEW_URL || "http://127.0.0.1:8769";
  await page.goto(`${origin}/mydoge.html`,{waitUntil:"networkidle"});
  await page.waitForSelector("#mydoge-open");
  assert.deepEqual(await page.evaluate(()=>mockDoge.calls),[]);
  assert.equal(await page.evaluate(()=>mockDoge.config.clientId),"mock-public-client-id");
  assert.deepEqual(await page.evaluate(()=>mockDoge.config.login),{basicLogins:["email","externalWallets"],socialLogins:[{type:"google"}]});
  assert.deepEqual(await page.evaluate(()=>Object.keys(mockDoge.config.chains)),["evm"]);
  assert.equal(await page.evaluate(()=>mockDoge.config.chains.evm[0].id),6281971);
  await page.evaluate(()=>{mockDoge.connectError=true;});
  await page.locator("#mydoge-open").click();
  await page.waitForFunction(()=>document.querySelector("#mydoge-message")?.textContent.includes("cancelled"));
  await page.evaluate(()=>{mockDoge.connectError=false;});
  await page.locator("#mydoge-open").click();
  await page.waitForFunction(()=>document.querySelector("#mydoge-balance")?.textContent==="10.0000 DOGE");
  assert.equal(await page.evaluate(()=>mockDoge.wallet.isWalletReady),false,"Native connection must not depend on embedded readiness");
  const artifacts=process.env.CHECK_ARTIFACTS_DIR || "/tmp/vela-checks";
  fs.mkdirSync(artifacts,{recursive:true});
  await page.screenshot({path:path.join(artifacts,"vela-mydoge-connected.png"),fullPage:true});
  await page.evaluate(()=>{
    mockDoge.publish({address:"0x2222222222222222222222222222222222222222"});
  });
  await page.waitForFunction(()=>document.querySelector("#mydoge-address")?.title.startsWith("0x222"));
  await page.waitForFunction(()=>document.querySelector("#mydoge-balance")?.textContent==="10.0000 DOGE");
  assert.equal(await page.evaluate(()=>mockDoge.calls.filter(x=>x.method==="eth_getBalance").at(-1).params[0]),"0x2222222222222222222222222222222222222222");
  await page.evaluate(()=>{mockDoge.hold=true;});
  await page.locator("#mydoge-refresh").click();
  await page.waitForFunction(()=>mockDoge.resolve!==null);
  await page.evaluate(()=>{
    mockDoge.observedChain="0x1";
    mockDoge.publish({chainId:1});
    mockDoge.resolve("0xffffffffffffffff");
  });
  await page.waitForSelector("#mydoge-switch");
  assert.equal(await page.locator("#mydoge-balance").textContent(),"—");
  await page.evaluate(()=>{mockDoge.hold=false;mockDoge.switchError=4001;});
  await page.locator("#mydoge-switch").click();
  await page.waitForFunction(()=>document.querySelector("#mydoge-message")?.textContent.includes("cancelled"));
  await page.evaluate(()=>{mockDoge.switchError=null;});
  await page.locator("#mydoge-switch").click();
  await page.waitForFunction(()=>document.querySelector("#mydoge-balance")?.textContent==="10.0000 DOGE");
  assert.equal(await page.evaluate(()=>mockDoge.calls.find(x=>x.method==="switchChain").params.chainInfo.id),6281971);
  await page.evaluate(()=>mockDoge.publish({chainType:"dogecoin",address:"DInvalidEvmAddress"}));
  await page.waitForSelector("#mydoge-open");
  assert.equal(await page.locator("#mydoge-balance").count(),0);
  await page.locator("#mydoge-open").click();
  await page.waitForSelector("#mydoge-disconnect");
  await page.evaluate(()=>{mockDoge.disconnectError=true;});
  await page.locator("#mydoge-disconnect").click();
  await page.waitForSelector("#mydoge-open");
  assert.equal(await page.locator("#mydoge-balance").count(),0);
  await page.evaluate(()=>{mockDoge.disconnectError=false;});
  await page.locator("#mydoge-open").click();
  await page.waitForSelector("#mydoge-disconnect");
  await page.locator("#mydoge-disconnect").click();
  await page.waitForSelector("#mydoge-open");
  for(const width of [320,390,768,1440]){
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`Overflow at ${width}`);
  }
  assert.deepEqual(errors,[]);
  assert.deepEqual(external,[]);
  await browser.close();
  console.log(JSON.stringify({result:"passed",coverage:["SDK config","explicit connection","rejected connection and retry","native connected with embedded wallet disabled","account updates","18-decimal balance","stale response after network change","rejected and successful chain switch","non-EVM account exclusion","disconnect failure and success","four screen widths"],realWalletUsed:false,externalWalletCalls:0},null,2));
})().catch(error=>{console.error(error);process.exit(1);});

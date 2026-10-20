const assert=require("node:assert/strict");
const {build}=require("esbuild");
const {chromium}=require("playwright");
const {generatePrivateKey,privateKeyToAccount}=require("viem/accounts");
const {keccak256}=require("viem");
(async()=>{
  const account=privateKeyToAccount(generatePrivateKey()); // Ephemeral local fixture only.
  const source=await build({stdin:{contents:'import * as tx from "./src/transaction.js";window.transactions=tx;',resolveDir:process.cwd()},bundle:true,format:"iife",write:false});
  const b=await chromium.launch({executablePath:process.env.CHROME_PATH||"/opt/google/chrome/chrome",headless:true,args:["--no-sandbox"]});
  try{
    const p=await b.newPage();await p.goto("about:blank");await p.addScriptTag({content:source.outputFiles[0].text});
    await p.exposeFunction("signFixture",async tx=>account.signTransaction({
      chainId:6281971,type:"legacy",to:tx.to,data:tx.data,value:BigInt(tx.value),
      nonce:Number(BigInt(tx.nonce)),gas:BigInt(tx.gas),gasPrice:BigInt(tx.gasPrice)}));
    await p.exposeFunction("hashFixture",raw=>keccak256(raw));
    const result=await p.evaluate(async address=>{
      const calls=[],remembered=[],tx={from:address,to:"0x1111111111111111111111111111111111111111",value:"0x1",data:"0x12345678",chainId:"0x5fdaf3"};
      const read=async(method,params=[])=>{
        calls.push(method);
        const values={eth_chainId:"0x5fdaf3",eth_getBalance:"0xde0b6b3a7640000",eth_estimateGas:"0x5208",eth_gasPrice:"0x3b9aca00",eth_getTransactionCount:"0x2"};
        if(method==="eth_sendRawTransaction"){window.signedBytes=params[0];return window.expectedHash;}
        return values[method];
      };
      const prepared=await transactions.prepareTransaction(tx,[],read);
      let signCalls=0;
      const provider={request:async args=>{signCalls++;if(args.method!=="eth_signTransaction")throw Error("Wrong signing method");const raw=await signFixture(args.params[0]);window.fixtureRaw=raw;return raw;}};
      // Return the deterministic hash using a browser-exposed helper.
      const broadcast=async(method,params)=>method==="eth_sendRawTransaction"?(window.signedBytes=params[0],await hashFixture(params[0])):read(method,params);
      const hash=await transactions.submitTransaction(provider,prepared,true,h=>remembered.push(h),broadcast);
      let wrongRecipientRejected=false;
      try{await transactions.submitTransaction({request:()=>Promise.resolve(window.fixtureRaw)},{...prepared,to:"0x2222222222222222222222222222222222222222"},true,()=>{},broadcast);}
      catch(e){wrongRecipientRejected=/does not match/.test(e.message);}
      let emptyWalletRejected=false;
      try{await transactions.prepareTransaction(tx,[],async m=>m==="eth_chainId"?"0x5fdaf3":"0x0");}
      catch(e){emptyWalletRejected=/wallet has 0 DOGE/.test(e.message);}
      const singleSignCalls=signCalls, rejectedHashes=[];
      let rejected=false;
      try {await transactions.submitTransaction(provider,prepared,true,h=>rejectedHashes.push(h),async method=>{
        if(method==="eth_sendRawTransaction")throw Object.assign(new Error("insufficient funds"),{code:-32000});
        return null;
      });} catch(e){rejected=/network rejected/.test(e.message);}
      const timeoutHash=await transactions.submitTransaction(provider,prepared,true,()=>{},async()=>{throw new Error("network timeout");});
      return {signCalls:singleSignCalls,hash,remembered,broadcastSame:window.fixtureRaw===window.signedBytes,wrongRecipientRejected,emptyWalletRejected,rejected,rejectedHashes,timeoutHash};
    },account.address);
    assert.equal(result.signCalls,1);assert.equal(result.broadcastSame,true);
    assert.deepEqual(result.remembered,[result.hash]);assert(result.wrongRecipientRejected);assert(result.emptyWalletRejected);
    assert(result.rejected);assert.deepEqual(result.rejectedHashes,[result.hash,null]);assert.equal(result.timeoutHash,result.hash);
    console.log("PASS: email-wallet single signature, verified signer/transaction, direct RPC broadcast, mismatched payload rejection, unfunded-wallet explanation.");
  }finally{await b.close();}
})().catch(e=>{console.error(e);process.exit(1)});

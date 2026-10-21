// Provider fixtures are not genuine MetaMask approvals or browser verification.
import test, {after, beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {generatePrivateKey, privateKeyToAccount} from 'viem/accounts';
import {recoverMessageAddress} from 'viem';
const dir=await mkdtemp(fileURLToPath(new URL('.portal-auth-',import.meta.url)));
const output=await build({stdin:{contents:"export {App} from './portal-entry.jsx'; export * from './portal-wallet.js';",resolveDir:fileURLToPath(new URL('../src',import.meta.url)),loader:'jsx'},bundle:true,platform:'node',format:'esm',external:['react','react-dom/client'],write:false});
await writeFile(dir+'/fixture.mjs',output.outputFiles[0].text);
const {App,api,authenticate,assertAccount,switchNetwork}=await import(pathToFileURL(dir+'/fixture.mjs'));
after(()=>rm(dir,{recursive:true,force:true}));
const signer=privateKeyToAccount(generatePrivateKey()),other=privateKeyToAccount(generatePrivateKey());
const chain=6281971,hexChain='0x'+chain.toString(16);
let requests,calls,wallet,network,verifyResult,rejectSignature,changeAfterSignature,changeAfterVerify;
const account=()=>({wallet:signer.address.toLowerCase(),mode:'PAPER',demo:true,realFundingEnabled:false});
const challenge=()=>({challengeId:'a'.repeat(64),message:`defivela.xyz wants you to sign in with your Ethereum account:\n${signer.address}\n\nSign in to Vela Demo.\n\nURI: https://defivela.xyz/trade\nVersion: 1\nChain ID: ${chain}\nNonce: 123456`});
const provider={request:async({method,params})=>{
  calls.push({method,params});
  if(method==='eth_accounts')return [wallet];
  if(method==='eth_chainId')return network;
  if(method==='wallet_switchEthereumChain'){network=params[0].chainId;return null;}
  if(method==='personal_sign'){
    if(rejectSignature)throw Object.assign(new Error('Rejected signature'),{code:4001});
    const signature=await signer.signMessage({message:{raw:params[0]}});
    if(changeAfterSignature)wallet=other.address;
    return signature;
  }
  throw Error('Unexpected wallet method: '+method);
}};
beforeEach(()=>{
  globalThis.location=new URL('https://defivela.xyz/trade');requests=[];calls=[];wallet=signer.address;network=hexChain;
  verifyResult=account();rejectSignature=false;changeAfterSignature=false;changeAfterVerify=false;
  globalThis.fetch=async(url,options)=>{
    requests.push({url,...options});
    if(url.endsWith('auth/verify')&&changeAfterVerify)wallet=other.address;
    return Response.json(url.endsWith('auth/challenge')?challenge():url.endsWith('auth/verify')?verifyResult:url.endsWith('account')?account():{disconnected:true});
  };
});
test('every unauthenticated portal tab renders only wallet onboarding',()=>{
  for(const view of ['','dashboard','account','trades','history']){
    location.search=view?'?view='+view:'';
    const html=renderToStaticMarkup(React.createElement(App));
    assert.match(html,/Welcome to Vela/);assert.match(html,/Connect your wallet to access your AI trading dashboard/);
    assert.match(html,/data-auth-state="DISCONNECTED"/);
    assert.match(html,/<button disabled=""><span class="wallet-glyph doge"/);
    assert.match(html,/Coming Soon — Mobile Only/);
    for(const hidden of ['Trading navigation','data-testid="equity"','Available balance','Total demo equity','Today’s P','Deposit','Withdraw','Start Trading','Stop Trading','Demo trading balance','Your wallet</h2>'])assert(!html.includes(hidden),hidden);
  }
  assert.equal(requests.length,0);
});
test('correct domain and URI are signed, verified, then accepted for the same wallet',async()=>{
  const phases=[];
  assert.deepEqual(await authenticate(provider,signer.address,()=>true,p=>phases.push(p)),account());
  assert.deepEqual(phases,['SIGNING']);
  assert.deepEqual(requests.map(r=>r.url),['/api/portal/auth/challenge','/api/portal/auth/verify']);
  const signed=calls.find(c=>c.method==='personal_sign');
  const body=JSON.parse(requests[1].body);
  assert.equal((await recoverMessageAddress({message:{raw:signed.params[0]},signature:body.signature})).toLowerCase(),signer.address.toLowerCase());
  assert.equal(new TextDecoder().decode(Uint8Array.from(signed.params[0].slice(2).match(/../g),h=>parseInt(h,16))),challenge().message);
  assert.equal(requests[1].credentials,'same-origin');assert.equal(requests[1].cache,'no-store');
});
test('signature rejection never verifies or authenticates',async()=>{
  rejectSignature=true;
  await assert.rejects(authenticate(provider,signer.address,()=>true),{code:4001});
  assert(!requests.some(r=>r.url.endsWith('verify')));
});
test('a foreign-domain challenge is never signed',async()=>{
  globalThis.fetch=async()=>Response.json({...challenge(),message:challenge().message.replace('https://defivela.xyz','https://evil.invalid')});
  await assert.rejects(authenticate(provider,signer.address,()=>true),/invalid sign-in message/);
  assert(!calls.some(c=>c.method==='personal_sign'));
});
test('account change during signing aborts before verification',async()=>{
  changeAfterSignature=true;
  await assert.rejects(authenticate(provider,signer.address,()=>true),/wallet changed/);
  assert(!requests.some(r=>r.url.endsWith('verify')));
});
test('network mismatch cannot request a challenge; approved switching is rechecked',async()=>{
  network='0x1';
  await assert.rejects(authenticate(provider,signer.address,()=>true),/wallet changed/);
  assert.equal(requests.length,0);
  await switchNetwork(provider);assert.equal(network,hexChain);
  assert(calls.some(c=>c.method==='wallet_switchEthereumChain'));
});
test('wallet change while verifying logs out the new session',async()=>{
  changeAfterVerify=true;
  await assert.rejects(authenticate(provider,signer.address,()=>true),/wallet changed/);
  assert.equal(requests.at(-1).url,'/api/portal/auth/logout');
  assert.equal(requests.at(-1).headers['X-Vela-Wallet'],signer.address);
});
test('server snapshot for another wallet is rejected and signed-in session revoked',async()=>{
  verifyResult={...account(),wallet:other.address};
  await assert.rejects(authenticate(provider,signer.address,()=>true),{status:403});
  assert.equal(requests.at(-1).url,'/api/portal/auth/logout');
});
test('account requests bind cookie ownership to the current wallet',async()=>{
  await api('account',undefined,undefined,signer.address);
  assert.equal(requests[0].headers['X-Vela-Wallet'],signer.address);
  assert.equal(requests[0].headers['X-Vela-Portal'],'1');
  assert.throws(()=>assertAccount({...account(),wallet:other.address},signer.address),{status:403});
  for(const patch of [{mode:'LIVE'},{demo:false},{realFundingEnabled:true},{liveEnabled:true}])
    assert.throws(()=>assertAccount({...account(),...patch},signer.address),/unavailable/);
});
test('expired session errors preserve HTTP status for immediate UI invalidation',async()=>{
  globalThis.fetch=async()=>Response.json({detail:'Session expired'},{status:401});
  await assert.rejects(api('account',undefined,undefined,signer.address),{status:401});
});

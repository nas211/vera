import test from 'node:test';
import assert from 'node:assert/strict';
import {authenticate,current,prepareWallet,restoreSession,request,selectWalletNetwork,loadWalletNetwork,walletNetwork,CHAIN_ID} from '../src/wallet.js';
import {networkTutorial,tutorials} from '../src/tutorials.js';
import {preferenceKey} from '../src/guided-state.js';
const alice='0x1111111111111111111111111111111111111111';
const sol='6EA6bW1WtLKaRNqJqvG3tM9xjtvpZjJxv39rxM9U8b4N';
test('failed network selection restores the complete adapter and allows retry',async()=>{
 const base={id:'base',family:'evm',chainId:8453};
 const solana={id:'solana',family:'solana',chainId:'solana:mainnet-beta'};
 selectWalletNetwork(base);
 try{
  await assert.rejects(loadWalletNetwork('solana',async()=>{assert.equal(walletNetwork().id,'solana');throw Error('Network unavailable');}),/Network unavailable/);
  assert.equal(walletNetwork(),base);
  const provider={request:async({method})=>method==='eth_chainId'?'0x2105':[alice]};
  assert.equal(await current(provider),alice);
  const config=await loadWalletNetwork('solana',async()=>({chain:solana}));
  assert.equal(config.chain,solana);assert.equal(walletNetwork(),solana);
 }finally{selectWalletNetwork({id:'dogeos',family:'evm',chainId:CHAIN_ID});}
});
test('a mismatched config cannot activate a different wallet network',async()=>{
 const base={id:'base',family:'evm',chainId:8453};selectWalletNetwork(base);
 try{
  await assert.rejects(loadWalletNetwork('solana',async()=>({chain:base})),/configuration mismatch/);
  assert.equal(walletNetwork(),base);
 }finally{selectWalletNetwork({id:'dogeos',family:'evm',chainId:CHAIN_ID});}
});
test('tutorials preserve DogeOS and disclose native simulated collateral',()=>{
 assert.equal(networkTutorial('vault'),tutorials.vault);
 const chain={name:'Solana',collateral:'SOL',capabilities:{simulatedVault:true}};
 for(const tab of Object.keys(tutorials)){
  const tutorial=networkTutorial(tab,chain),text=JSON.stringify(tutorial);
  assert.ok(!text.includes('DOGE')&&!text.includes('MyDoge'));
  assert.equal(tutorial.videoPage,undefined);
 }
 assert.match(networkTutorial('settle',chain).steps.at(-1).narration,/not redeemable/);
 assert.notEqual(preferenceKey('solana:'+sol,'tab'),preferenceKey('solana:'+sol.toLowerCase(),'tab'));
});
test('Solana uses native message signing and preserves public-key case',async()=>{
 selectWalletNetwork({id:'solana',family:'solana',chainId:'solana:mainnet-beta'});
 const calls=[],p={publicKey:{toString:()=>sol},connect:async()=>calls.push('connect'),signMessage:async bytes=>{assert.ok(bytes instanceof Uint8Array);calls.push('signMessage');return {signature:new Uint8Array(64).fill(5)};}};
 assert.equal(await prepareWallet(p,{}),sol);
 const api=async(path,body)=>{calls.push(path);if(path.endsWith('nonce')){assert.equal(body.chainId,'solana:mainnet-beta');assert.equal(body.address,sol);return {nonce:'fixture',message:'Network-bound fixture'};}assert.equal(body.signature,'05'.repeat(64));return {address:sol,network:'solana',authenticated:true};};
 assert.equal(await authenticate(p,api,()=>{},{alreadyConnected:true}),sol);
 assert.ok(calls.includes('signMessage'));
 selectWalletNetwork({id:'dogeos',family:'evm',chainId:CHAIN_ID});
});
test('network changes during signature approval reject the result',async()=>{
 selectWalletNetwork({id:'solana',family:'solana',chainId:'solana:mainnet-beta'});
 let verified=false;
 const p={publicKey:{toString:()=>sol},signMessage:async()=>{selectWalletNetwork({id:'base',family:'evm',chainId:8453});return {signature:new Uint8Array(64)};}};
 const api=async path=>{if(path.endsWith('verify'))verified=true;return {nonce:'fixture',message:'fixture'};};
 await assert.rejects(authenticate(p,api,()=>{},{alreadyConnected:true}),/Network changed/);
 assert.equal(verified,false);
 selectWalletNetwork({id:'dogeos',family:'evm',chainId:CHAIN_ID});
});
test('EVM adapter uses selected chain and never requests token transfers',async()=>{
 for(const [id,chainId] of [['base',8453],['ethereum',1],['bnb',56],['arbitrum',42161],['robinhood',46630]]){
  selectWalletNetwork({id,family:'evm',chainId});let chain=999;const calls=[];
  const p={request:async q=>{calls.push(q.method);if(q.method==='eth_chainId')return '0x'+chain.toString(16);if(q.method==='eth_requestAccounts'||q.method==='eth_accounts')return [alice];if(q.method==='wallet_switchEthereumChain')chain=Number(q.params[0].chainId);else if(!['eth_chainId','eth_requestAccounts','eth_accounts'].includes(q.method))throw Error('Unexpected method');}};
  assert.equal(await prepareWallet(p,{}),alice);assert.equal(chain,chainId);
  assert.ok(!calls.includes('eth_sendTransaction'));
 }
 selectWalletNetwork({id:'dogeos',family:'evm',chainId:CHAIN_ID});
});
test('a session from another network cannot restore a private view',async()=>{
 selectWalletNetwork({id:'base',family:'evm',chainId:8453});let revoked=false;
 const provider={request:async({method})=>method==='eth_chainId'?'0x2105':[alice]};
 await assert.rejects(restoreSession(provider,async path=>{if(path.endsWith('logout'))revoked=true;return {address:alice,network:'arbitrum',authenticated:true};}),/Wallet changed/);
 assert.equal(revoked,true);selectWalletNetwork({id:'dogeos',family:'evm',chainId:CHAIN_ID});
});
test('requests capture the active network and wallet in headers',async()=>{
 const old=globalThis.fetch;let sent;
 globalThis.fetch=async(path,opts)=>{sent=opts;return {ok:true,json:async()=>({})};};
 try{selectWalletNetwork({id:'base',family:'evm',chainId:8453});await request('/api/repay',{amount:'1'},alice,'fixture-key');assert.equal(sent.headers['X-Vella-Network'],'base');assert.equal(sent.headers['X-Wallet-Address'],alice);assert.equal(sent.headers['Idempotency-Key'],'fixture-key');}
 finally{globalThis.fetch=old;selectWalletNetwork({id:'dogeos',family:'evm',chainId:CHAIN_ID});}
});

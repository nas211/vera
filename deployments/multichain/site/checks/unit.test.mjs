import test from 'node:test';import assert from 'node:assert/strict';
import {authenticate,units,calculator,current,CHAIN_ID,restoreSession,sdkMatches,walletBalance,formatDoge,ensureNetwork,prepareWallet} from '../src/wallet.js';
const alice='0x'+'11'.repeat(20),bob='0x'+'22'.repeat(20);
test('first connection approves account access before reading account or requesting authentication',async()=>{
 const calls=[];let approved=false;
 const p={request:async({method})=>{calls.push(method);
  if(method==='eth_requestAccounts'){approved=true;return [alice];}
  if(method==='eth_accounts')return approved?[alice]:[];
  if(method==='eth_chainId')return '0x'+CHAIN_ID.toString(16);
  if(method==='personal_sign')return 'fixture-signature';
  throw Error(method);
 }};
 assert.equal(await prepareWallet(p,{}),alice);
 const api=async path=>{calls.push(path);return path.endsWith('nonce')?{nonce:'fixture',message:'exact challenge'}:{address:alice,authenticated:true};};
 assert.equal(await authenticate(p,api,()=>{},{alreadyConnected:true}),alice);
 assert.equal(calls[0],'eth_requestAccounts');assert.equal(calls.filter(x=>x==='eth_requestAccounts').length,1);
 assert.ok(calls.indexOf('/api/auth/nonce')>calls.indexOf('eth_accounts'));
});
test('rejected first connection cannot continue to network or account reads',async()=>{
 const calls=[];const p={request:async({method})=>{calls.push(method);throw Object.assign(Error('rejected'),{code:4001});}};
 await assert.rejects(prepareWallet(p,{}),e=>e.code===4001);assert.deepEqual(calls,['eth_requestAccounts']);
});
test('logout while wallet approval is pending discards its result',async()=>{
 let active=true;const calls=[];
 const p={request:async({method})=>{calls.push(method);active=false;return [alice];}};
 await assert.rejects(prepareWallet(p,{},{isCurrent:()=>active}),/Wallet changed/);
 assert.deepEqual(calls,['eth_requestAccounts']);
});
test('native MyDoge preparation retains its existing approval',async()=>{
 const calls=[];const p={request:async({method})=>{calls.push(method);if(method==='eth_accounts')return [alice];if(method==='eth_chainId')return '0x'+CHAIN_ID.toString(16);throw Error(method);}};
 assert.equal(await prepareWallet(p,{},{alreadyConnected:true}),alice);
 assert.ok(!calls.includes('eth_requestAccounts'));
});
function fixture(){let account=alice;const calls=[];const p={request:async({method})=>{calls.push(method);if(method==='eth_accounts'||method==='eth_requestAccounts')return [account];if(method==='eth_chainId')return '0x'+CHAIN_ID.toString(16);if(method==='personal_sign')return 'fixture-signature';throw Error(method);}};const api=async(path)=>{calls.push(path);if(path.endsWith('nonce'))return{nonce:'fixture',message:'exact server challenge'};if(path.endsWith('verify'))return{address:account,authenticated:true};return{};};return{p,api,calls,setAccount:a=>account=a};}
test('borrow calculator labels 10,000 DOGE at illustrative $0.20 and 40% as $800',()=>{const c=calculator('10000');assert.equal(c.borrowed,800);assert.equal(c.scenarios[3].health,.8125);});
test('native DOGE and micro USDC conversion never round via floating point',()=>{assert.equal(units('10000'),10000000000000000000000n);assert.equal(units('800.000001',6),800000001n);assert.throws(()=>units('1.0000001',6));assert.throws(()=>units('-1'));assert.throws(()=>units('Infinity'));});
test('authentication requests connection, exact challenge signing, then verified session',async()=>{const f=fixture(),states=[];assert.equal(await authenticate(f.p,f.api,s=>states.push(s)),alice);assert.deepEqual(states,['CONNECTING','SIGNING','AUTHENTICATED']);assert.ok(f.calls.indexOf('personal_sign')<f.calls.indexOf('/api/auth/verify'));});
test('signature rejection never reaches verification',async()=>{const f=fixture(),original=f.p.request;f.p.request=async q=>{if(q.method==='personal_sign')throw Object.assign(Error('rejected'),{code:4001});return original(q);};await assert.rejects(authenticate(f.p,f.api),/rejected/);assert.ok(!f.calls.includes('/api/auth/verify'));});
test('account changes during signing cannot authenticate old account',async()=>{const f=fixture(),original=f.p.request;f.p.request=async q=>{const r=await original(q);if(q.method==='personal_sign')f.setAccount(bob);return r;};await assert.rejects(authenticate(f.p,f.api),/Wallet changed/);assert.ok(!f.calls.includes('/api/auth/verify'));});
test('account changes during verification revoke newly created session',async()=>{const f=fixture();const api=async p=>{const r=await f.api(p);if(p.endsWith('verify'))f.setAccount(bob);return r;};await assert.rejects(authenticate(f.p,api),/Wallet changed/);assert.ok(f.calls.includes('/api/logout'));});
test('wrong chain fails before nonce',async()=>{const f=fixture(),orig=f.p.request;f.p.request=async q=>q.method==='eth_chainId'?'0x1':orig(q);await assert.rejects(authenticate(f.p,f.api),/DogeOS/);assert.ok(!f.calls.includes('/api/auth/nonce'));});
test('SDK signs the exact server challenge without requesting a second connection',async()=>{
 const f=fixture();let message,verified;
 const api=async(path,body)=>{if(path.endsWith('verify'))verified=body;return f.api(path,body);};
 await authenticate(f.p,api,()=>{},{alreadyConnected:true,isCurrent:()=>true,signMessage:async p=>{message=p.message;return new Uint8Array([1,2,255]);}});
 assert.equal(message,'exact server challenge');assert.equal(verified.signature,'0x0102ff');
 assert.ok(!f.calls.includes('eth_requestAccounts')&&!f.calls.includes('personal_sign'));
});
test('SDK provider changes during signing reject the proof',async()=>{
 const f=fixture();let active=true;
 await assert.rejects(authenticate(f.p,f.api,()=>{},{alreadyConnected:true,isCurrent:()=>active,signMessage:async()=>{active=false;return 'fixture';}}),/Wallet changed/);
 assert.ok(!f.calls.includes('/api/auth/verify'));
});
test('SDK disconnect during server verification revokes the resulting session',async()=>{
 const f=fixture();let active=true;
 const api=async(path,body)=>{const r=await f.api(path,body);if(path.endsWith('verify'))active=false;return r;};
 await assert.rejects(authenticate(f.p,api,()=>{},{isCurrent:()=>active}),/Wallet changed/);
 assert.ok(f.calls.includes('/api/logout'));
});
test('session restoration requires matching server session and connected provider without signing',async()=>{
 const f=fixture();const api=async path=>path==='/api/session'?{address:alice,authenticated:true}:f.api(path);
 assert.equal(await restoreSession(f.p,api),alice);
 assert.ok(!f.calls.includes('eth_requestAccounts')&&!f.calls.includes('personal_sign'));
 f.setAccount(bob);await assert.rejects(restoreSession(f.p,api),/Wallet changed/);
 assert.ok(f.calls.includes('/api/logout'));
});
test('a cached address or connected SDK alone cannot restore an expired application session',async()=>{
 const f=fixture();const api=async()=>{throw Object.assign(Error('Session expired'),{status:401});};
 await assert.rejects(restoreSession(f.p,api),e=>e.status===401);
});
test('private SDK view is blocked on account, chain, provider or connection changes',()=>{
 const f=fixture();const sdk={isConnected:true,chainType:'evm',chainId:String(CHAIN_ID),address:alice,provider:f.p};
 assert.equal(sdkMatches(sdk,alice,f.p),true);
 for(const patch of [{address:bob},{chainId:'1'},{chainType:'dogecoin'},{isConnected:false},{provider:{}}])assert.equal(sdkMatches({...sdk,...patch},alice,f.p),false);
});
test('native test DOGE balance is read with 18-decimal precision, separately from demo USDC',async()=>{
 const f=fixture(),orig=f.p.request,wei=10000000000000000000001n;
 f.p.request=async q=>q.method==='eth_getBalance'?(assert.deepEqual(q.params,[alice,'latest']),'0x'+wei.toString(16)):orig(q);
 assert.equal(await walletBalance(f.p,alice),'10000.000000000000000001');
 assert.equal(formatDoge(0n),'0');assert.equal(formatDoge(1n),'0.000000000000000001');
});
test('account changes during balance RPC discard the old balance',async()=>{
 const f=fixture(),orig=f.p.request;
 f.p.request=async q=>{if(q.method==='eth_getBalance'){f.setAccount(bob);return '0x1';}return orig(q);};
 await assert.rejects(walletBalance(f.p,alice),/Wallet changed/);
});
test('network addition requires subsequent confirmation of Chikyū',async()=>{
 let chain='0x1';const calls=[];const network={chainName:'Chikyū',nativeCurrency:{name:'DOGE',symbol:'DOGE',decimals:18},rpcUrls:['https://rpc.testnet.dogeos.com/'],blockExplorerUrls:['https://dogeos-testnet.l2scan.co']};
 const p={request:async q=>{calls.push(q);if(q.method==='eth_chainId')return chain;if(q.method==='wallet_switchEthereumChain')throw Object.assign(Error('Unknown network'),{code:4902});if(q.method==='wallet_addEthereumChain')chain=q.params[0].chainId;}};
 await ensureNetwork(p,network);assert.equal(Number(chain),CHAIN_ID);
 assert.equal(calls.find(c=>c.method==='wallet_addEthereumChain').params[0].nativeCurrency.decimals,18);
 chain='0x1';p.request=async q=>q.method==='eth_chainId'?chain:undefined;
 await assert.rejects(ensureNetwork(p,network),/DogeOS network required/);
});

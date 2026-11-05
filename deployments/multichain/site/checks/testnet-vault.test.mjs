import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareTransaction,submitTransaction,reconcileTransaction,pendingTransaction,pendingKey,attachPendingHash} from '../src/testnet-vault.js';
const owner='0x'+'12'.repeat(20),vault='0x'+'34'.repeat(20),hash='0x'+'ab'.repeat(32),factory='0xD6644a0C4233257b091D669142A7A8db3F2B7C3a';
const config={chain:{id:'dogeos'},dogeosWallet:{enabled:true,chainId:6281971,factory,actions:['createVault','deposit','withdraw']}};
function setup(){
 const state={status:'VERIFIED',chainId:6281971,wallet:owner,factory,block:123,confirmations:3,registered:true,walletBalanceWei:'10000000000000000000',vault:{address:vault,owner,ownershipVerified:true,registrationVerified:true,bytecodeVerified:true,collateralWei:'3000000000000000000',withdrawableWei:'3000000000000000000',loanState:0,paused:false}};
 const map=new Map(),storage={getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};
 const calls=[],provider={async request({method,params}){calls.push({method,params});return ({eth_chainId:'0x'+(6281971).toString(16),eth_accounts:[owner],eth_getBalance:'0x8ac7230489e80000',eth_estimateGas:'0x186a0',eth_gasPrice:'0x3b9aca00',eth_getTransactionCount:'0x0',eth_call:'0x16345785d8a0000',eth_sendTransaction:hash})[method];}};
 return {state,storage,provider,calls,owner,config,readState:async()=>state,withLock:async(key,fn)=>fn()};
}
const quote=(e,action='deposit',amount='1')=>prepareTransaction({...e,action,amount});
const submit=(e,q)=>submitTransaction({...e,quote:q});
function confirmed(e,p,status='CONFIRMED'){e.state.transaction={hash:p.hash,status,sender:owner,to:p.tx.to,valueWei:String(BigInt(p.tx.value)),data:p.tx.data,block:122};}
test('deposit uses native value, reserves data fees and waits for independent confirmed state',async()=>{
 const e=setup(),q=await quote(e);assert.ok(!e.calls.some(c=>c.method==='eth_sendTransaction'));assert.ok(BigInt(q.feeReserveWei)>BigInt(q.dataFeeEstimateWei)*2n);
 const p=await submit(e,q);assert.equal(BigInt(p.tx.value),10n**18n);assert.equal(p.tx.to,vault);e.state.transaction={hash,status:'PENDING'};
 assert.equal((await reconcileTransaction({...e,pending:p})).pending,true);assert.ok(pendingTransaction(e.storage,owner));confirmed(e,p);e.state.vault.collateralWei='4000000000000000000';
 const r=await reconcileTransaction({...e,pending:p});assert.equal(r.pending,false);assert.equal(r.state.vault.collateralWei,'4000000000000000000');assert.equal(pendingTransaction(e.storage,owner),null);
});
test('reload resumes without resending and duplicate approval is blocked',async()=>{
 const e=setup(),q=await quote(e),p=await submit(e,q);await assert.rejects(submit(e,q),/already pending/);const restored=JSON.parse(e.storage.getItem(pendingKey(owner)));confirmed(e,p);
 await reconcileTransaction({...e,pending:restored});assert.equal(e.calls.filter(c=>c.method==='eth_sendTransaction').length,1);
});
test('fee shortage including data/finality fee blocks approval',async()=>{
 const e=setup(),orig=e.provider.request;e.provider.request=a=>a.method==='eth_getBalance'?Promise.resolve('0x'+(11n*10n**17n).toString(16)):orig(a);
 await assert.rejects(quote(e),/Insufficient test DOGE/);assert.ok(!e.calls.some(c=>c.method==='eth_sendTransaction'));
});
test('explicit rejection clears context; uncertain errors retain recoverable context',async()=>{
 for(const code of [4001,-32603]){const e=setup(),q=await quote(e),orig=e.provider.request;e.provider.request=async a=>{if(a.method==='eth_sendTransaction')throw Object.assign(Error('wallet error'),{code});return orig(a);};
 await assert.rejects(submit(e,q));assert.equal(Boolean(pendingTransaction(e.storage,owner)),code!==4001);if(code!==4001){const p=attachPendingHash(e.storage,owner,hash);confirmed(e,p);await reconcileTransaction({...e,pending:p});}}
});
test('reverted and mismatched receipts cannot create a credited success',async()=>{
 const e=setup(),p=await submit(e,await quote(e));confirmed(e,p);e.state.transaction.sender='0x'+'56'.repeat(20);await assert.rejects(reconcileTransaction({...e,pending:p}),/does not match/);assert.ok(pendingTransaction(e.storage,owner));
 confirmed(e,p,'REVERTED');assert.equal((await reconcileTransaction({...e,pending:p})).reverted,true);
});
test('wrong chain/account and unavailable capability cannot broadcast',async()=>{
 for(const field of ['chain','account','capability','owner','code']){const e=setup(),orig=e.provider.request;
 if(field==='chain')e.provider.request=a=>a.method==='eth_chainId'?Promise.resolve('0x1'):orig(a);
 if(field==='account')e.provider.request=a=>a.method==='eth_accounts'?Promise.resolve(['0x'+'56'.repeat(20)]):orig(a);
 if(field==='capability')e.config={...config,dogeosWallet:{...config.dogeosWallet,enabled:false}};
 if(field==='owner')e.state.vault.owner='0x'+'56'.repeat(20);if(field==='code')e.state.vault.bytecodeVerified=false;
 await assert.rejects(quote(e));assert.ok(!e.calls.some(c=>c.method==='eth_sendTransaction'));}
});
test('wallet change during approval retains original wallet recovery context',async()=>{
 const e=setup(),q=await quote(e),orig=e.provider.request;e.provider.request=async a=>{if(a.method==='eth_sendTransaction'){e.provider.request=async a=>a.method==='eth_chainId'?'0x1':orig(a);return hash;}return orig(a);};
 const p=await submit(e,q);assert.equal(pendingTransaction(e.storage,owner).hash,hash);assert.equal(p.owner,owner);
});
test('withdrawals respect unlocked funds; creation targets reviewed factory',async()=>{
 const e=setup(),q=await quote(e,'withdraw','2');assert.equal(q.tx.value,'0x0');assert.equal(q.tx.to,vault);await assert.rejects(quote(e,'withdraw','4'),/unlocked/);
 e.state.vault.withdrawableWei='0';await assert.rejects(quote(e,'withdraw','1'),/unlocked/);e.state.vault=null;e.state.registered=false;const create=await quote(e,'createVault');assert.equal(create.tx.to,factory);assert.equal(create.tx.value,'0x0');
});
test('storage failures and increased quote fees do not broadcast',async()=>{
 const e=setup(),q=await quote(e);e.storage.setItem=()=>{throw Error('storage unavailable');};await assert.rejects(submit(e,q),/storage/);
 const f=setup(),old=await quote(f);old.feeReserveWei='1';await assert.rejects(submit(f,old),/fees changed/);assert.ok(!e.calls.concat(f.calls).some(c=>c.method==='eth_sendTransaction'));
});
test('fractional amounts use exact units and reject excess precision',async()=>{
 const e=setup(),q=await quote(e,'deposit','0.000000000000000001');assert.equal(q.tx.value,'0x1');for(const amount of ['-1','1e3','0','0.0000000000000000001'])await assert.rejects(quote(e,'deposit',amount));
});

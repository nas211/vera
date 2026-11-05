import test from 'node:test';
import assert from 'node:assert/strict';
import {amountProblem,spendableInput,repaymentMaximum,accountReadFence} from '../src/flow-validation.js';
import {walletAttempt} from '../src/wallet-attempt.js';
import {prepareWallet,authenticate,CHAIN_ID} from '../src/wallet.js';
const data={health:{borrowCapacity:'10',debt:'10.000001'},repayment:{maxRepayable:'100.009999',freeEquity:'100.009999',debt:'10.000001'},directAllocation:'9990'};
test('invalid allocations and repayments are blocked before submission',()=>{
 for(const action of ['allocate','repay','borrow','withdrawDemo','quickStart']){
  for(const value of ['-1','0','','NaN','Infinity','1e2','0.0000001'])assert.ok(amountProblem(action,value,data),`${action}: ${value}`);
  assert.equal(amountProblem(action,'1',data),'');
 }
 assert.ok(amountProblem('allocate','10.01',data));assert.ok(amountProblem('borrow','10.01',data));
 assert.ok(amountProblem('repay','100.01',data));assert.ok(amountProblem('repayFull','10.00',data));
 assert.equal(amountProblem('repayFull','10.02',data),'');
 assert.equal(amountProblem('borrow','10',{health:{borrowCapacity:'10.00000000000000000001'}}),'');
 assert.ok(amountProblem('borrow','0.000001',{health:{borrowCapacity:'4E-19'}}));
});
test('cent formatting never rounds withdrawals above free equity and explains full debt ceiling',()=>{
 assert.equal(spendableInput('100.009999'),'100.00');assert.equal(spendableInput('0.009999'),'0.00');
 assert.equal(repaymentMaximum('10'),'10.01');assert.equal(repaymentMaximum('10.000001'),'10.02');
 assert.equal(repaymentMaximum('0.009999'),'0.02');
});
test('pre-stop and out-of-order reads cannot restore obsolete running state',()=>{
 const fence=accountReadFence(),old=fence.begin();fence.invalidate();assert.equal(old(),false);
 const afterStop=fence.begin();assert.equal(afterStop(),true);
 const newer=fence.begin();assert.equal(afterStop(),false);assert.equal(newer(),true);
});
test('timeout aborts the attempt once and permits an independent retry',async()=>{
 let expired=0;const old=walletAttempt(()=>expired++,5);
 await new Promise(r=>setTimeout(r,15));assert.equal(expired,1);assert.equal(old.current(),false);assert.equal(old.signal.aborted,true);
 const retry=walletAttempt(()=>expired++,1000);old.finish();assert.equal(retry.current(),true);retry.cancel();
 assert.equal(retry.current(),false);assert.equal(expired,1);
});
test('cancelled account approval cannot proceed to network changes or authentication',async()=>{
 let approve;const calls=[];const run=walletAttempt(()=>{},1000);
 const provider={request:({method})=>{calls.push(method);return new Promise(r=>{approve=r;});}};
 const pending=prepareWallet(provider,{}, {isCurrent:run.current});run.cancel();approve(['0x'+'11'.repeat(20)]);
 await assert.rejects(pending,/Wallet changed/);assert.deepEqual(calls,['eth_requestAccounts']);
});
test('cancelled signature cannot submit authentication verification',async()=>{
 let sign;const owner='0x'+'11'.repeat(20),calls=[],run=walletAttempt(()=>{},1000);
 const provider={request:async({method})=>method==='eth_chainId'?CHAIN_ID:method==='personal_sign'?new Promise(r=>{sign=r;}):[owner]};
 const api=async path=>{calls.push(path);return {nonce:'fixture',message:'fixture'};};
 const pending=authenticate(provider,api,()=>{},{alreadyConnected:true,isCurrent:run.current});
 while(!sign)await new Promise(r=>setTimeout(r,0));run.cancel();sign('old-signature');
 await assert.rejects(pending,/Wallet changed/);assert.deepEqual(calls,['/api/auth/nonce']);
});

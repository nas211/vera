import test from 'node:test';import assert from 'node:assert/strict';
import {RewardModel,repayInterestFirst} from '../src/reward-model.mjs';
const W=10n**18n,Y=31536000n;
test('funded rewards pay separately without consuming principal',()=>{
 const r=new RewardModel();r.fund(10n*W,0n);r.deposit(100n*W,0n);
 assert.equal(r.claim(Y),5n*W);assert.equal(r.principal,100n*W);assert.equal(r.reserve,5n*W);assert.ok(r.invariant());
});
test('late deposits earn no retroactive rewards',()=>{
 const r=new RewardModel();r.fund(100n*W,0n);r.deposit(100n*W,Y);
 assert.equal(r.claim(Y),0n);assert.equal(r.claim(Y*2n),5n*W);
});
test('empty reserve never accrues debt against later funding',()=>{
 const r=new RewardModel();r.deposit(100n*W,0n);r.fund(10n*W,Y);
 assert.equal(r.claim(Y),0n);assert.equal(r.claim(Y*2n),5n*W);
});
test('reserve exhaustion is capped; a top-up cannot backfill lost time',()=>{
 const r=new RewardModel();r.fund(W,0n);r.deposit(100n*W,0n);
 assert.equal(r.claim(Y),W);r.fund(10n*W,Y*2n);assert.equal(r.claim(Y*2n),0n);assert.ok(r.invariant());
});
test('claims cannot pay twice and withdrawing preserves earned liability',()=>{
 const r=new RewardModel();r.fund(10n*W,0n);r.deposit(100n*W,0n);r.withdraw(100n*W,Y);
 assert.equal(r.claim(Y),5n*W);assert.equal(r.claim(Y*2n),0n);assert.ok(r.invariant());
});
test('rate changes checkpoint the old rate and integer dust is carried',()=>{
 const r=new RewardModel();r.fund(100n*W,0n);r.deposit(101n*W,0n);r.setRate(1000n,Y);
 assert.equal(r.claim(Y*2n),1515n*W/100n);
 const a=new RewardModel(),b=new RewardModel();for(const x of [a,b]){x.fund(W,0n);x.deposit(W,0n);}
 a.checkpoint(1n);a.checkpoint(2n);assert.equal(a.claim(3n),b.claim(3n));
});
test('clock rollback and invalid units fail',()=>{
 const r=new RewardModel({now:5n});assert.throws(()=>r.claim(4n));assert.throws(()=>r.deposit(-1n,5n));assert.throws(()=>r.fund(1,5n));
});
test('repayment pays interest first and respects maximum/free equity',()=>{
 assert.deepEqual(repayInterestFirst({principal:10000000n,interest:20000n,available:1000000n,maximum:500000n}),{paid:500000n,principal:9520000n,interest:0n});
 assert.deepEqual(repayInterestFirst({principal:10000000n,interest:10000n,available:20000000n,maximum:10020000n}),{paid:10010000n,principal:0n,interest:0n});
});

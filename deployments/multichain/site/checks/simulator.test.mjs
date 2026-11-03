import test from 'node:test';import assert from 'node:assert/strict';
import {simulateVault} from '../src/simulator.js';
const base={doge:10000,price:.2,ltv:.4,apr:.05,days:30,performance:0};
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-9,`${a} != ${b}`);
test('10,000 DOGE at $0.20 and 40% LTV unlocks 800 simulated USDC',()=>{
 const r=simulateVault(base);assert.equal(r.baselineValue,2000);assert.equal(r.principal,800);assert.equal(r.borrowingCapacity,800);
 near(r.interest,800*.05*30/365);near(r.debt,800+r.interest);near(r.currentLtv,r.debt/2000);
 assert.ok(r.shortfall>0);assert.equal(r.surplus,0);
});
test('positive and negative trading scenarios change equity without erasing debt',()=>{
 const gain=simulateVault({...base,performance:.10}),loss=simulateVault({...base,performance:-.25});
 near(gain.equity,880);near(loss.equity,600);assert.equal(gain.debt,loss.debt);
 near(gain.surplus,880-gain.debt);near(loss.shortfall,loss.debt-600);
});
test('10%, 25% and 50% DOGE falls preserve principal and reduce health',()=>{
 const normal=simulateVault(base);
 for(const drop of [.1,.25,.5]){
  const r=simulateVault({...base,drop});assert.equal(r.principal,800);assert.equal(r.debt,normal.debt);
  near(r.collateralValue,2000*(1-drop));near(r.healthFactor,normal.healthFactor*(1-drop));
 }
 assert.equal(simulateVault({...base,drop:.5}).liquidationEligible,true);
});
test('one year of simple interest produces $40 interest at 5% APR on $800',()=>{
 const r=simulateVault({...base,days:365});assert.equal(r.interest,40);assert.equal(r.debt,840);
});
test('zero borrowing and zero collateral never fabricate debt or a health ratio',()=>{
 for(const patch of [{doge:0},{ltv:0}]){const r=simulateVault({...base,...patch});assert.equal(r.debt,0);assert.equal(r.healthFactor,null);assert.equal(r.liquidationEligible,false);}
});
test('total trading loss retains debt and combined collateral loss triggers liquidation',()=>{
 const r=simulateVault({...base,performance:-1,drop:.5});assert.equal(r.equity,0);assert.equal(r.shortfall,r.debt);assert.equal(r.liquidationEligible,true);
});
test('invalid numbers and values outside research limits are rejected',()=>{
 for(const patch of [{doge:-1},{price:0},{ltv:.41},{apr:-.1},{days:Infinity},{performance:-1.01},{drop:1},{maxLtv:.7},{price:'not a price'}])assert.throws(()=>simulateVault({...base,...patch}));
});

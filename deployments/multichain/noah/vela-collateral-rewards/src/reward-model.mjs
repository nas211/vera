// Exact-integer accounting REFERENCE. Not Solidity and not a payout service.
const YEAR=31536000n, BPS=10000n, DENOM=YEAR*BPS;
const nonnegative=n=>{if(typeof n!=='bigint'||n<0n)throw Error('Nonnegative bigint required');return n;};
export class RewardModel {
 constructor({now=0n,aprBps=500n}={}){
  this.last=nonnegative(now);this.aprBps=nonnegative(aprBps);
  this.principal=0n;this.reserve=0n;this.accrued=0n;this.carry=0n;
  this.funded=0n;this.paid=0n;
 }
 checkpoint(now){
  nonnegative(now);if(now<this.last)throw Error('Clock moved backwards');
  const numerator=this.principal*this.aprBps*(now-this.last)+this.carry;
  const due=numerator/DENOM,capacity=this.reserve-this.accrued;
  const credit=due<capacity?due:capacity;
  this.accrued+=credit;
  // Discard unfunded time/fractions so later funding never backfills it.
  this.carry=due>=capacity?0n:numerator%DENOM;
  this.last=now;return credit;
 }
 fund(wei,now){nonnegative(wei);this.checkpoint(now);this.reserve+=wei;this.funded+=wei;}
 deposit(wei,now){nonnegative(wei);this.checkpoint(now);this.principal+=wei;}
 withdraw(wei,now){
  nonnegative(wei);if(wei>this.principal)throw Error('Insufficient principal');
  this.checkpoint(now);this.principal-=wei;
  // Actual contracts must independently enforce loan locks and owner checks.
 }
 setRate(aprBps,now){nonnegative(aprBps);this.checkpoint(now);this.aprBps=aprBps;}
 claim(now){this.checkpoint(now);const paid=this.accrued;this.accrued=0n;this.reserve-=paid;this.paid+=paid;return paid;}
 invariant(){return this.reserve>=this.accrued&&this.principal>=0n&&this.funded===this.reserve+this.paid;}
}
export function repayInterestFirst({principal,interest,available,maximum}){
 for(const v of [principal,interest,available,maximum])nonnegative(v);
 const paid=[principal+interest,available,maximum].reduce((a,b)=>a<b?a:b);
 const interestPaid=paid<interest?paid:interest;
 return {paid,principal:principal-(paid-interestPaid),interest:interest-interestPaid};
}

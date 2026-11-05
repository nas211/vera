import {units,formatDoge} from './wallet.js';

// Server-calculated collateral capacity can have more than six decimals or use
// Decimal exponent notation. Floor the limit, without loosening input precision.
function limitUnits(value,decimals){
 const match=String(value).match(/^(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/);
 if(!match)return 0n;
 const [,whole,fraction='',exponent='0']=match,power=decimals+Number(exponent)-fraction.length;
 if(!Number.isInteger(power)||Math.abs(power)>100)return 0n;
 const digits=BigInt(whole+fraction);
 return power>=0?digits*10n**BigInt(power):digits/10n**BigInt(-power);
}

// Display cents without floating-point rounding above the spendable balance.
export function spendableInput(value){
 const n=units(String(value),6),cents=n/10000n;
 return `${cents/100n}.${String(cents%100n).padStart(2,'0')}`;
}
export function repaymentMaximum(debt){
 const n=units(String(debt),6),cents=(n+9999n)/10000n+1n;
 return `${cents/100n}.${String(cents%100n).padStart(2,'0')}`;
}
export function amountProblem(name,amount,data={},config={}){
 if(!['deposit','addCollateral','borrow','authorize','withdraw','repay','repayFull','allocate','withdrawDemo','quickStart'].includes(name))return '';
 const collateral=['deposit','addCollateral','withdraw'].includes(name);
 const decimals=collateral?(config.chain?.family==='solana'?9:18):6;
 let value;try{value=units(String(amount),decimals);}catch{return `Enter a positive decimal amount with at most ${decimals} decimal places.`;}
 if(value<=0n)return 'Enter an amount greater than zero.';
 const h=data.health||{},r=data.repayment||{};
 let max;
 if(name==='borrow')max=h.borrowCapacity||'0';
 if(name==='repay')max=r.maxRepayable||'0';
 if(name==='withdrawDemo')max=r.freeEquity||'0';
 if(name==='withdraw')max=formatDoge(data.vault?.withdrawable||'0');
 if(name==='allocate'){
  const remaining=10000000000n-units(String(data.directAllocation||'0'),6);
  if(value>remaining)return 'Amount exceeds your remaining lifetime demo allocation.';
 }
 if(['authorize','quickStart'].includes(name))max='10000';
 if(collateral&&name!=='withdraw'&&config.chain?.capabilities?.simulatedVault)max='1000000';
 if(max!==undefined&&value>limitUnits(max,decimals))return 'Amount exceeds the currently available limit.';
 if(name==='repayFull'){
  const debt=units(String(r.debt||h.debt||'0'),6);
  if(debt<=0n)return 'There is no outstanding debt.';
  if(value<debt)return 'The authorization must cover current principal and accrued interest.';
  if(units(String(r.maxRepayable||'0'),6)<debt)return 'Free equity does not cover full repayment. Choose partial repayment or review settlement.';
 }
 return '';
}

// Invalidating pending reads prevents pre-mutation poll responses from restoring
// an obsolete running state after Stop has been acknowledged.
export function accountReadFence(){
 let version=0;
 return {invalidate:()=>{version++;},begin:()=>{const ticket=++version;return ()=>ticket===version;}};
}

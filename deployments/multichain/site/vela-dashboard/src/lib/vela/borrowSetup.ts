/** One user intent, resolved against confirmed state before each reviewed action. */
import { parseDecimal, formatUnits } from '../units';
import type { Hex } from 'viem';
import type { NativeAction, NativeState } from './nativeVault';
import type { AccountSnapshot, ServiceAction } from './accountService';
export type BorrowPolicy={price:string;maxLtv:string};
export type BorrowIntent={owner:string;vault:string|null;targetCollateralWei:string;maxDepositWei:string;startBlock:number;amount:string;loanId:Hex};
export type BorrowStep={label:string;native:NativeAction;ledger?:never}|{label:string;ledger:ServiceAction;native?:never}|{label:string;done:true;native?:never;ledger?:never};
function exact(text:string,decimals:number) {const n=parseDecimal(text,decimals);if(!n.ok)throw Error(n.error);return n.value;}
export function borrowPolicy(raw:unknown):BorrowPolicy|null {
  const r=raw as Record<string,unknown>|null;
  if(typeof r?.dogePrice!=='string'||typeof r?.maxLtv!=='string')return null;
  try {if(exact(r.dogePrice,6)<=0n||exact(r.maxLtv,6)<=0n||exact(r.maxLtv,6)>=1_000_000n)return null;return {price:r.dogePrice,maxLtv:r.maxLtv};}catch{return null;}
}
export function estimatedBorrowCapacity(collateralWei:bigint,policy:BorrowPolicy,debt='0') {
  return (collateralWei*exact(policy.price,6)*exact(policy.maxLtv,6)/10n**24n)-exact(debt,6);
}
export function borrowIntent(native:NativeState,collateral:string,amount:string,loanId:Hex,policy:BorrowPolicy,walletBalanceWei=native.walletBalanceWei):BorrowIntent {
  const added=exact(collateral||'0',18),loan=exact(amount,6),existing=BigInt(native.vault?.collateral??'0');
  if(added<0n||added>=BigInt(walletBalanceWei)&&added>0n)throw Error('Leave some DOGE in your wallet for transaction fees.');
  if(loan<=0n||loan>estimatedBorrowCapacity(existing+added,policy))throw Error('Loan exceeds the collateral borrowing limit.');
  if(native.vault && ![0,3].includes(native.vault.loanState))throw Error('Finish the existing loan before starting another.');
  return {owner:native.owner,vault:native.vault?.address??null,targetCollateralWei:String(existing+added),maxDepositWei:String(added),startBlock:native.block,amount,loanId};
}
export function nextBorrowStep(intent:BorrowIntent,state:NativeState,account:AccountSnapshot,depositConfirmed=false):BorrowStep {
  if(intent.owner!==state.owner||intent.owner!==account.address)throw Error('Wallet changed. Review your loan again.');
  const v=state.vault,l=account.health?.loan;
  if(intent.vault && v?.address!==intent.vault)throw Error('Vault changed. Review your loan again.');
  if(!v)return {label:'Create your vault',native:{kind:'create'}};
  if(v.paused)throw Error('Vault is paused.');
  if(l?.id===intent.loanId&&l.state==='REPAID')return {label:'Your loan has been repaid',done:true};
  if(v.loanState===1||v.loanState===2) {
    if(v.loanId!==intent.loanId||BigInt(v.principalMicro)!==exact(intent.amount,6))throw Error('A different loan is already in progress.');
    if(l && l.id===v.loanId && l.state==='CANCELLED')throw Error('This loan request was cancelled. Unlock it before trying again.');
    if(v.loanState===2) {
      if(!l||l.id!==v.loanId||l.state!=='ACTIVE')throw Error('Refresh matching loan credit before continuing.');
      return {label:'Your loan is ready',done:true};
    }
    if(l && l.id===v.loanId && l.state==='ACTIVE')return {label:'Confirm your loan',native:{kind:'acknowledge'}};
    if(l && !['REPAID','CANCELLED'].includes(l.state))throw Error('Settle the existing loan first.');
    return {label:'Receive demo credit',ledger:{kind:'issueBorrow'}};
  }
  if(![0,3].includes(v.loanState)||l&&!['REPAID','CANCELLED'].includes(l.state))throw Error('Settle the existing loan first.');
  const remaining=BigInt(intent.targetCollateralWei)-BigInt(v.collateral);
  if(remaining>0n) {
    if(depositConfirmed||remaining>BigInt(intent.maxDepositWei))throw Error('Collateral changed. Close setup and review a new amount; the deposit will not be repeated.');
    return {label:'Confirm your collateral',native:{kind:'deposit',amount:formatUnits(remaining,18)}};
  }
  return {label:'Confirm your loan amount',native:{kind:'requestBorrow',amount:intent.amount,loanId:intent.loanId}};
}

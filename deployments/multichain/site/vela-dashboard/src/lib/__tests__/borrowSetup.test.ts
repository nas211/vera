import { describe, it, expect } from 'vitest';
import { borrowIntent, borrowPolicy, estimatedBorrowCapacity, nextBorrowStep } from '../vela/borrowSetup';
import type { NativeState } from '../vela/nativeVault';
import type { AccountSnapshot } from '../vela/accountService';
const owner='0x'+'11'.repeat(20),address=`0x${'22'.repeat(20)}` as const,loanId=`0x${'33'.repeat(32)}` as const;
const policy={price:'1.00',maxLtv:'0.70'};
const state=():NativeState=>({owner,block:10,walletBalanceWei:String(11n*10n**18n),vault:null} as NativeState);
const vault=()=>({address,collateral:'0',loanState:0,principalMicro:'0',loanId:`0x${'00'.repeat(32)}`,paused:false} as NativeState['vault']);
const account=():AccountSnapshot=>({address:owner,health:null} as AccountSnapshot);

describe('combined collateral and borrow intent',()=>{
  it('reads the configured policy, uses exact arithmetic and never invents missing terms',()=>{
    expect(borrowPolicy({dogePrice:'1.00',maxLtv:'0.70'})).toEqual(policy);
    expect(borrowPolicy(undefined)).toBeNull();expect(borrowPolicy({dogePrice:'NaN',maxLtv:'.7'})).toBeNull();
    expect(estimatedBorrowCapacity(10n*10n**18n,policy)).toBe(7_000_000n);
    expect(estimatedBorrowCapacity(10n*10n**18n,policy,'1.000001')).toBe(5_999_999n);
  });
  it('guides a new wallet through creation, one deposit, matching loan credit and acknowledgement',()=>{
    const n=state(),a=account(),intent=borrowIntent(n,'10','7',loanId,policy);
    expect(nextBorrowStep(intent,n,a).native).toEqual({kind:'create'});
    n.vault=vault();expect(nextBorrowStep(intent,n,a).native).toEqual({kind:'deposit',amount:'10'});
    n.vault!.collateral=String(10n*10n**18n);
    expect(nextBorrowStep(intent,n,a,true).native).toEqual({kind:'requestBorrow',amount:'7',loanId});
    Object.assign(n.vault!,{loanState:1,principalMicro:'7000000',loanId});
    expect(nextBorrowStep(intent,n,a,true).ledger).toEqual({kind:'issueBorrow'});
    a.health={loan:{id:loanId,state:'ACTIVE',principal:'7',interest:'0'},debt:'7',healthFactor:'1.21'};
    expect(nextBorrowStep(intent,n,a,true).native).toEqual({kind:'acknowledge'});
    n.vault!.loanState=2;expect(nextBorrowStep(intent,n,a,true)).toEqual({label:'Your loan is ready',done:true});
  });
  it('uses existing collateral without asking for another deposit',()=>{
    const n=state();n.vault=vault();n.vault!.collateral=String(10n*10n**18n);
    const intent=borrowIntent(n,'0','7',loanId,policy);
    expect(nextBorrowStep(intent,n,account()).native?.kind).toBe('requestBorrow');
  });
  it('requires room for network fees and rejects excessive loans before any wallet review',()=>{
    expect(()=>borrowIntent(state(),'11','7',loanId,policy)).toThrow('fees');
    expect(()=>borrowIntent(state(),'10','7.000001',loanId,policy)).toThrow('limit');
    expect(()=>borrowIntent(state(),'0','7',loanId,policy)).toThrow('limit');
  });
  it('does not repeat a confirmed deposit after collateral was withdrawn or state disagrees',()=>{
    const n=state();n.vault=vault();n.vault!.collateral=String(5n*10n**18n);
    const intent=borrowIntent(n,'5','7',loanId,policy);
    expect(()=>nextBorrowStep(intent,n,account(),true)).toThrow('will not be repeated');
    n.vault!.collateral='0';expect(()=>nextBorrowStep(intent,n,account())).toThrow('Collateral changed');
  });
  it('fails closed for a different wallet, vault or concurrent loan',()=>{
    const n=state();n.vault=vault();const intent=borrowIntent(n,'10','7',loanId,policy);
    expect(()=>nextBorrowStep(intent,{...n,owner:'other'},account())).toThrow('Wallet changed');
    expect(()=>nextBorrowStep(intent,{...n,vault:{...n.vault!,address:`0x${'44'.repeat(20)}`}},account())).toThrow('Vault changed');
    n.vault!.loanState=1;expect(()=>nextBorrowStep(intent,n,account())).toThrow('different loan');
  });
});

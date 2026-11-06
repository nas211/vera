import { createContext, useContext, useState } from 'react';
import { toHex } from 'viem';
import { Panel } from '@/components/common/Panel';
import { Row } from '@/components/common/Metric';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { Button } from '@/components/ui/button';
import { parseDecimal, formatUnits, usdPrefill } from '@/lib/units';
import { borrowIntent, estimatedBorrowCapacity, nextBorrowStep, type BorrowPolicy, type BorrowIntent, type BorrowStep } from '@/lib/vela/borrowSetup';
import { useAccountDashboard } from './context';
import { useWalletBalance } from './WalletBalance';
import { doge, percent, usd, units } from './presentation';
export const BorrowPolicyContext=createContext<BorrowPolicy|null>(null);

export function BorrowSetup() {
  const {account,native,controller,nativeReason,busy,pending,receipts}=useAccountDashboard();
  const walletBalance=useWalletBalance(native?.walletBalanceWei);
  const configured=useContext(BorrowPolicyContext);
  const policy=configured??(account?.health?.price&&account.health.maxLtv?{price:account.health.price,maxLtv:account.health.maxLtv}:null);
  const [collateral,setCollateral]=useState(''),[amount,setAmount]=useState(''),[intent,setIntent]=useState<BorrowIntent|null>(null),[error,setError]=useState('');
  const parsed=parseDecimal(collateral||'0',18);
  const total=parsed.ok?BigInt(native?.vault?.collateral??'0')+parsed.value:0n;
  const cap=policy&&parsed.ok?estimatedBorrowCapacity(total,policy):0n;
  const loan=units(amount),value=policy?total*(units(policy.price)??0n)/10n**18n:0n;
  const ltv=value>0n&&loan!==null?formatUnits(loan*1_000_000n/value,6):null;
  const deposited=!!intent&&receipts.some(r=>r.action==='deposit'&&r.status==='CONFIRMED'&&r.block>intent.startBlock);
  let step:BorrowStep|null=null,stepError='';
  if(intent&&native&&account){try{step=nextBorrowStep(intent,native,account,deposited);}catch(e){stepError=(e as Error).message;}}
  const proceed=async()=>{
    setError('');if(!native||!account||!policy||walletBalance.balanceWei===null)return;
    try {
      const plan=intent??borrowIntent(native,collateral,amount,toHex(crypto.getRandomValues(new Uint8Array(32))),policy,walletBalance.balanceWei);
      const next=nextBorrowStep(plan,native,account,deposited);setIntent(plan);
      if(next.native)await controller.reviewNative(next.native);
      else if(next.ledger)await controller.reviewLedger(next.ledger);
    }catch(e){setError((e as Error).message);}
  };
  const reason=nativeReason??(walletBalance.balanceWei===null?'Read your wallet balance before borrowing.':!policy?'Borrowing terms unavailable. Refresh to retry.':intent?stepError||null:!parsed.ok||loan===null||loan<=0n||loan>cap?'Choose collateral and a loan within the limit.':null);
  return <Panel title="Borrow"><Row k="Wallet balance" v={walletBalance.loading&&walletBalance.balanceWei===null?'Reading wallet…':doge(walletBalance.balanceWei??undefined)} /><Button size="sm" variant="ghost" disabled={walletBalance.loading} onClick={()=>void walletBalance.refresh()}>Refresh balance</Button>{walletBalance.error&&<p role="alert" className="text-sm text-warning">{walletBalance.error}</p>}{BigInt(native?.vault?.collateral??'0')>0n&&<Row k="Already deposited" v={doge(native?.vault?.collateral)} />}
    {!intent?<><div className="mt-4"><AmountField label="Use as collateral" unit="DOGE" value={collateral} onChange={setCollateral} rules={{inputDecimals:18,decimals:18,max:BigInt(walletBalance.balanceWei??'0'),maxLabel:'wallet balance'}} hint="Choose DOGE from your wallet. Keep a little for network fees." /></div><div className="mt-4"><AmountField label="Loan amount" unit="demo USD" value={amount} onChange={setAmount} rules={{inputDecimals:2,decimals:6,max:cap>0n?cap:0n,maxLabel:'collateral borrowing limit'}} onMax={cap>0n?()=>setAmount(usdPrefill(cap).text):undefined} /></div><Row k="Estimated maximum" v={native&&policy?usd(formatUnits(cap>0n?cap:0n,6)):'Unavailable'} /><Row k="Loan LTV" v={ltv===null?'—':percent(ltv)} /><p className="my-3 text-xs text-muted-foreground">Limit {percent(policy?.maxLtv)}. {policy?`${policy.price} demo USD per test DOGE.`:''} Final capacity and fees are checked before borrowing.</p></>:<div className="my-4 space-y-2"><Row k="Collateral" v={doge(intent.targetCollateralWei)} /><Row k="Loan" v={usd(intent.amount)} /><p className="text-sm">{pending?'Waiting for wallet confirmation…':step?.label??'Checking your loan…'}</p></div>}
    {step && 'done' in step?<p role="status" className="text-sm text-success">Loan ready. Your demo USD is available for trading.</p>:<GatedButton reason={reason} onClick={()=>void proceed()}>{intent?'Continue':'Borrow'}</GatedButton>}
    {intent && <Button variant="ghost" className="mt-3" disabled={busy||!!pending} onClick={()=>{setIntent(null);setError('');}}>Close setup</Button>}
    {(error||stepError)&&<p role="alert" className="mt-3 text-sm text-destructive">{error||stepError}</p>}
  </Panel>;
}

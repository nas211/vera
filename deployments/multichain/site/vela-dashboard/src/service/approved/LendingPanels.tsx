import { useState } from 'react';
import { BorrowSetup } from './BorrowSetup';
import { Panel } from '@/components/common/Panel';
import { Row } from '@/components/common/Metric';
import { Details } from '@/components/common/Details';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { formatUnits } from '@/lib/units';
import { fullRepayAllowance } from '@/lib/repayment';
import { useAccountDashboard } from './context';
import { usd, doge, percent, units, maxCash, amountOk } from './presentation';

export function AccountLoanStatus() {
  const {account,native,controller,ledgerReason}=useAccountDashboard();const h=account?.health;
  return <Panel title="Your loan"><Row k="Loan amount" v={usd(h?.principal??(account?'0':undefined))} /><Row k="To repay" v={usd(account?.repayment.debt)} /><Row k="LTV" v={h?.ltv===null?'0%':percent(h?.ltv)} /><Row k="Borrowing limit" v={percent(h?.maxLtv)} /><Details label="Loan details"><Row k="Deposited collateral" v={doge(native?.vault?.collateral)} /><Row k="Test value per DOGE" v={usd(h?.price)} /><Row k="Available demo liquidity" v={usd(h?.lendingLiquidity?.available)} /><p className="my-3 text-xs text-muted-foreground">Test valuation only. New trades stop above {percent(h?.maxTradingLtv)} LTV. The DOGE rewards pool is separate from demo lending.</p><GatedButton variant="outline" reason={ledgerReason ?? (!account?.vault?'Create a vault first.':null)} onClick={()=>void controller.reviewLedger({kind:'refreshQuote'})}>Refresh valuation</GatedButton></Details></Panel>;
}
export function AccountDemoCash() {
  const { account, controller, ledgerReason }=useAccountDashboard();const [amount,setAmount]=useState(''),[allocation,setAllocation]=useState('');const free=units(account?.repayment.freeEquity);
  return <Panel title="Demo cash"><Row k="Available to withdraw" v={usd(account?.repayment.freeEquity)} /><div className="mt-3"><AmountField label="Withdraw demo cash" unit="USD" value={amount} onChange={setAmount} rules={{inputDecimals:2,decimals:6,max:free??0n,maxLabel:'reported spendable cash'}} onMax={free!==null?()=>setAmount(maxCash(account?.repayment.freeEquity)):undefined} /></div><GatedButton variant="secondary" reason={ledgerReason ?? account?.repayment.restriction ?? (!amountOk(amount,6,free,2)?'Enter an available amount.':null)} onClick={()=>void controller.reviewLedger({kind:'withdraw',amount})}>Withdraw demo cash</GatedButton><Details label="Balance details"><Row k="Exact ledger cash" v={account ? `${account.paper.balance} USD`:'Unavailable'} /><p className="my-3 text-xs text-muted-foreground">Available cash already excludes reserved margin, pending orders, losses and costs. Demo allocation is simulated USD.</p><AmountField label="Allocate demo cash" unit="USD" value={allocation} onChange={setAllocation} rules={{inputDecimals:2,decimals:6}} /><GatedButton reason={ledgerReason ?? (!amountOk(allocation,6,undefined,2)?'Enter an amount.':null)} onClick={()=>void controller.reviewLedger({kind:'allocate',amount:allocation})}>Review allocation</GatedButton></Details></Panel>;
}
export function AccountBorrow() {
  const {account,native,controller,ledgerReason,nativeReason}=useAccountDashboard();const v=native?.vault,loan=account?.health?.loan;
  if(!v||![1,2].includes(v.loanState))return <BorrowSetup />;
  const cancelled=loan?.id===v.loanId&&loan.state==='CANCELLED';
  const credited=loan?.id===v.loanId&&loan.state!=='CANCELLED';
  return <Panel title="Borrow">{v.loanState===2?<p className="text-sm text-muted-foreground">Your loan is active. Repay it before starting another.</p>:<div className="space-y-3"><p className="text-sm">{cancelled?'Unlock your cancelled request.':'Finish your loan setup.'}</p><Row k="Loan amount" v={usd(formatUnits(BigInt(v.principalMicro),6))} />{!credited&&!cancelled&&<GatedButton reason={ledgerReason} onClick={()=>void controller.reviewLedger({kind:'issueBorrow'})}>Continue loan</GatedButton>}{credited&&<GatedButton reason={nativeReason} onClick={()=>void controller.reviewNative({kind:'acknowledge'})}>Confirm loan in wallet</GatedButton>}{cancelled&&<GatedButton reason={nativeReason} onClick={()=>void controller.reviewNative({kind:'cancel'})}>Unlock collateral</GatedButton>}{!credited&&!cancelled&&<Details label="Cancel loan request"><GatedButton variant="outline" reason={ledgerReason} onClick={()=>void controller.reviewLedger({kind:'cancelBorrow'})}>Cancel request</GatedButton></Details>}</div>}</Panel>;
}
export function AccountRepay() {
  const {account,native,controller,ledgerReason,nativeReason}=useAccountDashboard();
  const [amount,setAmount]=useState(''),[full,setFull]=useState(false);
  const debt=units(account?.repayment.debt,6,true),free=units(account?.repayment.freeEquity),max=units(account?.repayment.maxRepayable);
  const allowance=debt!==null && debt>0n?fullRepayAllowance(debt,0n):null;
  const canFull=!!allowance && free!==null && debt!==null && free>=debt;
  const repayable=['ACTIVE','SHORTFALL'].includes(account?.health?.loan?.state??'');
  const reason=ledgerReason ?? (!repayable?'No loan to repay.':account?.repayment.restriction ?? (full?(!canFull?'Insufficient available cash.':null):!amountOk(amount,6,max,2)?'Enter an available amount.':null));
  return <Panel title="Repay"><Row k="Amount to repay" v={usd(account?.repayment.debt)} /><div className="mt-4"><AmountField label="Repay amount" unit="demo USD" value={amount} onChange={value=>{setFull(false);setAmount(value);}} rules={{inputDecimals:2,decimals:6,max:full&&allowance?allowance.maxAllowanceUsd6:max??0n,maxLabel:'available repayment'}} onMax={max!==null?()=>{setFull(canFull);setAmount(canFull&&allowance?formatUnits(allowance.maxAllowanceUsd6,6):maxCash(account?.repayment.maxRepayable));}:undefined} hint={full?'Full repayment. Only the actual debt is paid; the amount includes a small interest buffer.':undefined} /></div><GatedButton reason={reason} onClick={()=>void controller.reviewLedger({kind:full?'repayFull':'repay',amount})}>Repay</GatedButton>
  {native?.vault?.loanState===2 && units(account?.repayment.debt)===0n && <GatedButton className="mt-3" reason={nativeReason} onClick={()=>void controller.reviewNative({kind:'settle'})}>Unlock collateral</GatedButton>}
  {!!account?.paper.positions.length && <Details label="Close trades and repay"><GatedButton variant="outline" reason={ledgerReason} onClick={()=>void controller.reviewLedger({kind:'settle'})}>Stop trading and settle</GatedButton></Details>}</Panel>;
}
export function AccountSweep() {
  const { account, controller, ledgerReason }=useAccountDashboard();const [pct,setPct]=useState(''),[days,setDays]=useState('7'),[floor,setFloor]=useState('');
  const policy=account?.repayment.policy, enabled=policy?.enabled===true;
  const valid=/^\d+$/.test(pct) && +pct<=100 && /^\d+$/.test(days) && +days>=1 && +days<=30 && (floor==='' || /^\d+(\.\d{1,2})?$/.test(floor));
  return <Panel title="Profit-sweep permission"><p className="mb-3 text-sm text-muted-foreground">Use future realized net profit to repay your demo loan.</p><Row k="Status" v={account ? enabled ? `Active · ${String(policy.sweepPercent)}%` : 'Not authorized' : 'Unavailable'} /><div className="mt-3"><Button size="sm" variant="outline" disabled={!!ledgerReason || !enabled} onClick={()=>void controller.reviewLedger({kind:'revokeSweep'})}>Revoke</Button></div><Details label="Permission settings & breakdown"><p className="mb-3 text-xs text-muted-foreground">Only newly realized net profit after fees and funding. Losses must be recovered first. Deposits, principal and DOGE rewards are excluded; scheduled principal repayments stay off.</p><div className="grid grid-cols-2 gap-3"><label className="eyebrow">Percent (0–100)<Input value={pct} onChange={e=>setPct(e.target.value)} inputMode="numeric" /></label><label className="eyebrow">Expires in days (1–30)<Input value={days} onChange={e=>setDays(e.target.value)} inputMode="numeric" /></label></div><div className="mt-3"><AmountField label="Keep at least (optional floor)" unit="USD" value={floor} onChange={setFloor} rules={{inputDecimals:2,decimals:6}} hint="Blank means no floor." /></div><GatedButton reason={ledgerReason ?? (!account?.health?.loan?'No active loan.':!valid?'Complete the permission fields.':null)} onClick={()=>void controller.reviewLedger({kind:'sweep',percent:+pct,days:+days,floor:floor||'0'})}>Review sweep permission</GatedButton><Row k="Current floor" v={usd(typeof policy?.minimum==='string'?policy.minimum:undefined)} /><p className="mt-2 text-xs text-muted-foreground">The existing account worker evaluates authorized sweeps. No local repayment is simulated.</p></Details></Panel>;
}

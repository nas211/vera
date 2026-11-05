import { useState } from 'react';
import { Panel } from '@/components/common/Panel';
import { Metric, Row } from '@/components/common/Metric';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { Input } from '@/components/ui/input';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatUnits, parseDecimal } from '@/lib/units';
import { useAccountDashboard } from './context';
import { doge, exactDoge, amountOk } from './presentation';

export function AccountRewardTerms() {
  const { native } = useAccountDashboard(); const v=native?.vault, r=v?.rewards;
  return <Panel title="Vault rewards" aside={<SourceTag kind="testnet" />}><div className="grid grid-cols-2 gap-5"><Metric label="Your principal" value={doge(v?.collateral)} /><Metric label="Simple APR" value={r ? `${formatUnits(BigInt(r.aprBps),2)}%` : 'Unavailable'} sub="Project-funded test rewards" /></div><p className="mt-3 text-xs text-muted-foreground">No compounding or production yield promised.</p>{v?.kind==='legacy' && <p className="mt-3 text-xs text-warning">Move to a reward vault after withdrawing your collateral and settling your loan.</p>}{r?.availableReserveWei==='0' && <p className="mt-3 text-xs text-warning">Shared reward pool exhausted; new accrual is paused.</p>}<Details label="Reserve & reward terms"><Row k="Shared pool balance" v={doge(r?.reserveWei)} /><Row k="Pool available for new rewards" v={doge(r?.availableReserveWei)} /><Row k="Project pool funded" v={doge(r?.fundedWei)} /><Row k="Claimed on-chain" v={doge(r?.claimedWei)} /><p className="mt-3 text-xs text-muted-foreground">Rewards come from the shared project pool, never collateral. Unfunded time earns nothing. Loan locks do not prevent funded reward claims. Your project manager sets the pool rate for all reward vaults.</p></Details></Panel>;
}
export function AccountDepositWithdraw() {
  const { native, controller, nativeReason }=useAccountDashboard(); const v=native?.vault;
  const [mode,setMode]=useState<'deposit'|'withdraw'>('deposit'),[amount,setAmount]=useState('');
  const maximum=mode==='withdraw' ? v?.withdrawable : native?.walletBalanceWei;
  const max=maximum!==undefined ? BigInt(maximum) : 0n;
  const reason=nativeReason ?? (!v ? 'Create and confirm your vault first.' : v.paused ? 'Vault is paused.' : mode==='withdraw' && max===0n ? 'Collateral is locked or no balance is withdrawable.' : !amountOk(amount,18,max) ? 'Enter an available amount.' : null);
  return <Panel title="Deposit / withdraw" aside={<SourceTag kind="testnet" />}>
    {native?.vault===null && <div className="mb-4"><GatedButton reason={nativeReason} onClick={()=>void controller.reviewNative({kind:'create'})}>Create collateral vault</GatedButton></div>}
    {v?.kind==='legacy' && native?.upgrade && <div className="mb-4"><GatedButton reason={nativeReason ?? (native.upgrade.eligible ? null : native.upgrade.reason)} onClick={()=>void controller.reviewNative({kind:'migrate'})}>Move to reward vault</GatedButton><p className="mt-2 text-xs text-muted-foreground">Same wallet and account history. Review creation, then deposit into your new vault.</p></div>}
    <Tabs value={mode} onValueChange={v=>{setMode(v as typeof mode);setAmount('');}} className="mb-4"><TabsList className="grid w-full grid-cols-2"><TabsTrigger value="deposit">Deposit</TabsTrigger><TabsTrigger value="withdraw">Withdraw</TabsTrigger></TabsList></Tabs>
    <AmountField label={`${mode} amount`} unit="DOGE" value={amount} onChange={setAmount} rules={{inputDecimals:18,decimals:18,max,maxLabel:mode==='withdraw'?'confirmed withdrawable collateral':'wallet balance before fees'}} onMax={mode==='withdraw' && maximum!==undefined ? ()=>setAmount(exactDoge(maximum)) : undefined} hint={mode==='withdraw'?'Loan locks remain until confirmed settlement.':'Deposits never fund rewards. Wallet fees also apply.'} />
    <GatedButton reason={reason} onClick={()=>void controller.reviewNative({kind:mode,amount})}>Review {mode}</GatedButton><Details label="On-chain availability"><Row k="Withdrawable" v={doge(v?.withdrawable)} /><Row k="Vault" v={v?.address??'Unavailable'} /><Row k="Loan state" v={v ? ['Idle','Requested · locked','Active · locked','Settled','Defaulted'][v.loanState] : 'Unavailable'} /></Details>
  </Panel>;
}
export function AccountClaimReceipts() {
  const { receipts }=useAccountDashboard();const claims=receipts.filter(r=>r.kind==='native-reward-claim');
  return claims.length ? <ul className="space-y-2">{claims.map(r=><li key={r.hash} className="rounded-md border border-border/70 p-2.5 text-xs"><p>{r.status} · {r.status==='CONFIRMED' ? `${exactDoge(r.rewardPaidWei)} test DOGE paid` : 'No payout'}</p><p className="mt-1 break-all text-muted-foreground">{r.hash} · block {r.block}</p></li>)}</ul> : <p className="text-xs text-muted-foreground">No reward claims verified in this session.</p>;
}
export function AccountClaim() {
  const { native, controller, nativeReason }=useAccountDashboard();const r=native?.vault?.rewards;
  return <Panel title="Claim test DOGE" aside={<SourceTag kind="testnet" />}><div className="mb-4"><Metric label="Claimable now" value={doge(r?.claimableWei)} tone="positive" /></div><p className="mb-3 text-xs text-muted-foreground">Claims do not reduce demo USD debt.</p><GatedButton reason={nativeReason ?? (!r ? 'No reward-enabled vault.' : r.claimableWei==='0' ? 'No funded rewards to claim.' : null)} onClick={()=>void controller.reviewNative({kind:'claim'})}>Review claim</GatedButton><Details label="Claim details & history"><Row k="Observed confirmations" v={native?.confirmations??'Unavailable'} /><p className="my-3 text-xs text-muted-foreground">A payout requires a matching confirmed event. Submitted transactions and counter estimates are not payouts.</p><AccountClaimReceipts /></Details></Panel>;
}
export function AccountReserve() {
  const { native, controller, nativeReason }=useAccountDashboard();const [amount,setAmount]=useState(''),[rate,setRate]=useState('');
  const pool=native?.rewardPool, max=native ? BigInt(native.walletBalanceWei) : 0n;
  const parsed=parseDecimal(rate,2), validRate=parsed.ok && parsed.value<=10000n;
  const manager=pool?.manager.toLowerCase()===native?.owner.toLowerCase();
  return <Panel title="Project reward pool" aside={<SourceTag kind="testnet" />}><Row k="Shared test DOGE reserve" v={doge(pool?.reserveWei)} /><p className="mt-2 text-sm text-muted-foreground">One funded pool pays rewards across registered vaults.</p><Details label="Pool funding & settings"><Row k="Available for new rewards" v={doge(pool?.availableReserveWei)} /><Row k="Already reserved" v={doge(pool?.reservedWei)} /><Row k="Pool address" v={pool?.address??'Unavailable'} /><AmountField label="Fund project pool" unit="DOGE" value={amount} onChange={setAmount} rules={{inputDecimals:18,decimals:18,max,maxLabel:'wallet balance before fees'}} hint="Shared project funding; an irrevocable donation, not personal principal." /><GatedButton reason={nativeReason ?? (!pool ? 'No verified project reward pool.' : !amountOk(amount,18,max) ? 'Enter an available amount.' : null)} onClick={()=>void controller.reviewNative({kind:'fundReserve',amount})}>Review pool funding</GatedButton>
    {manager && <div className="mt-4"><label className="eyebrow">Project simple APR (%)<Input value={rate} onChange={e=>setRate(e.target.value)} inputMode="decimal" /></label><p className="my-2 text-xs text-muted-foreground">0 pauses new rewards. Existing earned rewards remain claimable.</p><GatedButton reason={nativeReason ?? (!validRate?'Enter a rate from 0 to 100%.':null)} onClick={()=>parsed.ok && void controller.reviewNative({kind:'setRewardRate',aprBps:parsed.value.toString()})}>Review pool rate</GatedButton></div>}
    <p className="mt-3 text-xs text-muted-foreground">Anyone can fund the pool with test DOGE. Claims pay each owner from this contract; no personal payout transfers are needed. Unfunded time earns nothing.</p></Details></Panel>;
}

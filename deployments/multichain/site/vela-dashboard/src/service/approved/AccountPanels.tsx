import { useState } from 'react';
import { ShieldCheck, CirclePause, CirclePlay } from 'lucide-react';
import { Panel } from '@/components/common/Panel';
import { Metric, Row } from '@/components/common/Metric';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { Button } from '@/components/ui/button';
import { GatedButton } from '@/components/common/GatedButton';
import { Input } from '@/components/ui/input';
import { AmountField } from '@/components/common/AmountField';
import { useAccountDashboard } from './context';
import { usd, doge, units, percent, ratio, amountOk } from './presentation';
import { formatUnits } from '@/lib/units';

export function AccountRisk() {
  const { account } = useAccountDashboard(); const health = account?.health;
  const noDebt = health != null && units(health.debt) === 0n;
  const hf = units(health?.healthFactor), quote = health?.quote;
  const fresh = !!quote && Date.now()/1000 - quote.at >= -1 && Date.now()/1000 - quote.at <= 300;
  const message = !health || !fresh ? 'Risk unavailable — refresh the illustrative quote.' : noDebt ? 'No debt' : hf === null ? 'Risk unavailable' : hf < 1_000_000n ? 'Liquidation threshold breached — not an executed liquidation' : hf < 1_150_000n ? 'New entries blocked' : hf < 1_300_000n ? 'Entry size reduced' : 'Healthy';
  const ok = fresh && (noDebt || hf !== null && hf >= 1_300_000n);
  return <div className="space-y-3"><div className="grid grid-cols-2 gap-4"><Metric label="Current LTV" value={fresh ? percent(health?.ltv) : 'Unavailable'} /><Metric label="Health factor" value={fresh ? noDebt ? 'N/A' : ratio(health?.healthFactor) : 'Unavailable'} tone={ok ? 'positive' : 'warning'} /></div><div className={`rounded-md p-2.5 text-xs ${ok ? 'bg-accent text-accent-foreground' : 'bg-warning/10 text-warning'}`}>{message}</div></div>;
}
export function CollateralSummary() {
  const { native } = useAccountDashboard(); const v = native?.vault;
  return <Panel title="Collateral & rewards" aside={<SourceTag kind="testnet" />}><div className="grid grid-cols-2 gap-5"><Metric label="Your vault principal" value={doge(v?.collateral)} /><Metric label="Earned, claimable" value={doge(v?.rewards?.claimableWei)} tone="positive" /></div><Details label="Reward details"><div className="grid grid-cols-2 gap-5"><Metric label="Project reward pool (shared)" value={doge(v?.rewards?.reserveWei)} /><Metric label="Claimed on-chain" value={doge(v?.rewards?.claimedWei)} /></div></Details></Panel>;
}
export function DebtSummary() {
  const { account, native } = useAccountDashboard();
  return <Panel title="Debt & risk" aside={<SourceTag kind="demo" />}><div className="grid gap-5 sm:grid-cols-2"><div className="grid grid-cols-2 gap-4"><Metric label="Current debt" value={usd(account?.repayment.debt)} /><Metric label="Demo collateral" value={doge(native?.vault?.collateral)} sub={native?.vault ? native.vault.withdrawable === '0' ? 'locked' : 'withdrawable amount in Vault' : undefined} /></div><AccountRisk /></div></Panel>;
}
export function ProfitSummary() {
  const { account } = useAccountDashboard(); const p = account?.paper;
  return <Panel title="Trading P&L" aside={<SourceTag kind="demo" label="PAPER" />}><div className="grid grid-cols-2 gap-5"><Metric label="Today (UTC) realized" value={usd(p?.today.realized)} sub="net of fees & funding" /><Metric label="Unrealized" value={usd(p?.unrealized)} sub="not sweepable" /></div><Details label="All-time results"><Metric label="All-time realized" value={usd(p?.totals.realized)} />{p && <><Row k="Today fills · wins / losses" v={`${p.today.trades} · ${p.today.wins} / ${p.today.losses}`} /><Row k="All-time fills · wins / losses" v={`${p.totals.trades} · ${p.totals.wins} / ${p.totals.losses}`} />{Object.entries(p.costs ?? {}).map(([k,v]) => <Row key={k} k={`All-time ${k}`} v={usd(v)} />)}</>}{p?.fundingIncomplete && <p className="text-xs text-warning">Funding history is incomplete.</p>}</Details></Panel>;
}
export function AccountWallet() {
  const { account, native } = useAccountDashboard();
  return <Panel title={account ? 'Connected' : 'Not connected'} aside={<SourceTag kind="off" label={account ? 'ACCOUNT' : 'NO CONNECTION'} />}><Row k="Wallet" v={account ? `${account.address.slice(0,6)}…${account.address.slice(-4)}` : 'Unavailable'} /><Details label="Network details"><Row k="Target network" v="DogeOS testnet · 6281971" /><Row k="Test DOGE balance" v={doge(native?.walletBalanceWei)} /><Row k="Confirmed block" v={native?.block ?? 'Unavailable'} /></Details></Panel>;
}
export function AccountConnections() {
  const { account, native, busy, controller, refreshedAt } = useAccountDashboard();
  return <Panel title="Connections" aside={<SourceTag kind="off" label={account ? 'CONNECTED' : 'OFF'} />}><p className="text-sm text-muted-foreground">{account ? 'Existing Vela account · PAPER / DEMO.' : 'Account services are not connected.'}</p><Button className="mt-3" size="sm" variant="outline" disabled={!controller.service || busy} onClick={() => void controller.refresh()}>Refresh account</Button><Details label="Connection details"><Row k="Account read" v={refreshedAt ? new Date(refreshedAt).toISOString() : 'Unavailable'} /><Row k="Native observation" v={native ? `${native.confirmations} confirmations` : 'Unavailable'} /><p className="mt-2 text-xs text-muted-foreground">Unavailable data is never replaced with sample balances. Live protection and payouts require separate verification.</p></Details></Panel>;
}
export function AccountTradingStatus({ compact = false }: { compact?: boolean }) {
  const { account, native, controller, ledgerReason, nativeReason } = useAccountDashboard();
  const [allocation,setAllocation] = useState(''), [size,setSize] = useState(''), [loss,setLoss] = useState('2');
  const [cash,setCash] = useState('');
  const p = account?.paper, v = native?.vault;
  const running = p?.preferences.autonomous === true;
  const valid = amountOk(allocation,6) && amountOk(size,6,units(allocation)) && amountOk(loss,6,2_000_000n);
  const prior = account?.authorizationMaxLossMicro ?? account?.tradingPermission.maxLoss;
  const priorText = prior && /^\d+(\.0+)?$/.test(prior) ? formatUnits(BigInt(prior.split('.')[0]),6) : '2';
  const needsCash = account && (units(p?.equity) ?? 0n) <= 0n;
  return <Panel title={!account ? 'Trading unavailable' : running ? 'Running' : 'AI stopped'} aside={<SourceTag kind="demo" label="PAPER" />} className={!running ? 'border-destructive/30' : 'border-primary/25'}>
    {p?.entryStop && <div className="mb-3 rounded-md bg-muted/60 p-3 text-sm"><p>{p.entryStop.reason}</p><p className="mt-1 text-muted-foreground">To resume: {p.entryStop.action}</p></div>}
    {needsCash && <p className="mb-3 text-sm text-muted-foreground">Open Trading authorization below to add cash and set your limits, then start trading.</p>}
    <p className="mb-3 flex items-center gap-1.5 text-xs text-muted-foreground"><ShieldCheck className="h-3.5 w-3.5 text-primary" />Protective exits remain available when entries stop.</p>
    <div className="flex flex-wrap gap-2"><Button size="sm" variant={running ? 'destructive' : 'default'} disabled={!!ledgerReason} onClick={() => void controller.reviewLedger({kind:running ? 'stop' : 'start'})}>{running ? <CirclePause className="mr-1 h-4 w-4" /> : <CirclePlay className="mr-1 h-4 w-4" />}{running ? 'Stop new entries' : 'Resume'}</Button></div>
    <Details label="Trading authorization"><Row k="Trading cash" v={usd(p?.balance)} /><div className="my-3"><AmountField label="Add trading cash" unit="USD" value={cash} onChange={setCash} rules={{inputDecimals:2,decimals:6}} /><GatedButton reason={ledgerReason ?? (!amountOk(cash,6,undefined,2)?'Enter an amount.':null)} onClick={()=>void controller.reviewLedger({kind:'allocate',amount:cash})}>Review allocation</GatedButton></div><div className="grid gap-3"><label className="eyebrow">Allocation (demo USD)<Input value={allocation} onChange={e=>setAllocation(e.target.value)} inputMode="decimal" /></label><label className="eyebrow">Position size (demo USD)<Input value={size} onChange={e=>setSize(e.target.value)} inputMode="decimal" /></label><label className="eyebrow">Stop-trigger budget (USD, max 2)<Input value={loss} onChange={e=>setLoss(e.target.value)} inputMode="decimal" /></label></div><p className="my-3 text-xs text-muted-foreground">Includes fees; stricter stored limits apply (account limit ${priorText}). Slippage or funding can exceed the trigger budget.</p><GatedButton reason={ledgerReason ?? (!valid ? 'Enter valid limits, up to $2 per position.' : null)} onClick={()=>void controller.reviewLedger({kind:'permission',allocation,positionSize:size,maxLoss:loss})}>Review paper permission</GatedButton>
      {v?.loanState===2 && <div className="mt-3 space-y-2"><GatedButton reason={nativeReason ?? (!valid ? 'Enter valid limits.' : null)} onClick={()=>void controller.reviewNative({kind:'authorize',allocation,positionSize:size,maxLoss:loss,expiresAt:Math.floor(Date.now()/1000)+86400})}>Review vault permission</GatedButton><GatedButton variant="secondary" reason={nativeReason ?? (v.trading ? 'Vault trading already enabled.' : null)} onClick={()=>void controller.reviewNative({kind:'startTrading'})}>Enable vault trading</GatedButton></div>}
    </Details>{!compact && <Details label="Session details"><p className="text-xs text-muted-foreground">Paper permission, native vault permission and Start are separate reviewed actions. Pending orders are not fills. Live protection is unverified.</p></Details>}
  </Panel>;
}
export function AccountResult() {
  const { account } = useAccountDashboard();
  return <Panel title={account ? 'Reported paper results' : 'No fills observed'} aside={<SourceTag kind="unavailable" label="LIVE UNVERIFIED" />} className="border-destructive/20"><p className="text-sm text-muted-foreground">{account ? 'Balances and closed fills come from the existing Vela ledger.' : 'No account data is connected.'}</p><Details label="Verification details"><p className="text-xs text-muted-foreground">Browser execution, native payouts and protective-stop operation still require observation on the released candidate.</p></Details></Panel>;
}

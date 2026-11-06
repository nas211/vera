import { useState } from 'react';
import { Link } from 'react-router-dom';
import { CirclePause, CirclePlay } from 'lucide-react';
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
  const message = !health || !fresh ? 'Risk unavailable — refresh the illustrative quote.' : noDebt ? 'No debt' : hf === null ? 'Risk unavailable' : hf < 1_000_000n ? 'Liquidation threshold breached — not an executed liquidation' : health?.ltv != null && units(health.ltv)! > (units(health.maxTradingLtv) ?? 800_000n) ? 'New entries blocked' : hf < 1_300_000n ? 'Entry size reduced' : 'Healthy';
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
export function AccountTradingStatus() {
  const { account, controller, ledgerReason } = useAccountDashboard();
  const [showSetup,setShowSetup]=useState(false);
  const running=account?.paper.preferences.autonomous===true, setup=account?.tradingSetup;
  const style=setup?.strategy;
  const paperRequired=setup?.paperRequired ?? !account?.tradingPermission.expiresAt;
  const start=()=>{
    if(running){void controller.reviewLedger({kind:'stop'});return;}
    if(paperRequired){setShowSetup(true);return;}
    void controller.reviewLedger({kind:'start'});
  };
  return <><Panel title="AI Trading" aside={<SourceTag kind="demo" label="PAPER" />}>
    <label className="eyebrow block" htmlFor="trading-strategy">Strategy</label>
    <select id="trading-strategy" className="mt-2 h-11 w-full rounded-md border border-input bg-background px-3 text-sm" value={style?.id??'swing'} disabled={running||!!ledgerReason} aria-describedby={running?'strategy-lock-hint':undefined} onChange={e=>void controller.reviewLedger({kind:'strategy',strategyStyle:e.target.value as 'scalp'|'swing'})}><option value="swing">Swing · 2–6 hours</option><option value="scalp">Scalp · 5–30 minutes</option></select>
    {running && <p id="strategy-lock-hint" className="mt-2 text-sm text-muted-foreground">Stop trading to change strategy.</p>}
    <p className="my-3 text-sm text-muted-foreground">{style?.id==='scalp'?'Quick trades based on short price moves.':'Longer trades based on bigger price moves.'}</p>
    <p className="mb-4 flex items-center gap-2 text-sm"><span className={`h-2 w-2 rounded-full ${running?'bg-success':'bg-muted-foreground'}`} />{running?(account?.paper.positions.length?'Trading is running':'Running · Waiting for a trade'):'Trading is stopped'}</p>
    <div className="flex flex-wrap gap-3"><Button disabled={!!ledgerReason} variant={running?'destructive':'default'} onClick={start}>{running?<CirclePause className="mr-2 h-4 w-4" />:<CirclePlay className="mr-2 h-4 w-4" />}{running?'Stop Trading':'Start Trading'}</Button><Button variant="outline" asChild><Link to="/settings?section=trading">Settings</Link></Button></div>
    {!running && paperRequired && <p className="mt-3 text-sm text-muted-foreground">Start Trading will guide you through setup.</p>}
    <Details label="How it works"><p className="text-xs text-muted-foreground">AI watches BTC, ETH and SOL, checks a trade, then opens it when ready. It may wait. Scalp aims for 5–30 minutes; Swing aims for 2–6 hours. Each position keeps its stricter stop-trigger limit, up to $2 including fees. Slippage and funding can exceed it.</p></Details>
    {account?.paper.entryStop && <p className="mt-3 text-sm text-muted-foreground">{account.paper.entryStop.reason}</p>}
  </Panel>{showSetup&&paperRequired&&<AccountTradingSettings />}</>;
}
export function AccountTradingSettings() {
  const { account, controller, ledgerReason } = useAccountDashboard();
  const [allocation,setAllocation] = useState(''), [size,setSize] = useState(''), [loss,setLoss] = useState('2');
  const [cash,setCash] = useState('');
  const p = account?.paper;
  const valid = amountOk(allocation,6) && amountOk(size,6,units(allocation)) && amountOk(loss,6,2_000_000n);
  const prior = account?.authorizationMaxLossMicro ?? account?.tradingPermission.maxLoss;
  const priorText = prior && /^\d+(\.0+)?$/.test(prior) ? formatUnits(BigInt(prior.split('.')[0]),6) : '2';
  return <Panel title="Trading settings"><Row k="Trading cash" v={usd(p?.balance)} /><Details label="Demo cash"><AmountField label="Add demo trading cash" unit="USD" value={cash} onChange={setCash} rules={{inputDecimals:2,decimals:6}} /><GatedButton reason={ledgerReason ?? (!amountOk(cash,6,undefined,2)?'Enter an amount.':null)} onClick={()=>void controller.reviewLedger({kind:'allocate',amount:cash})}>Add demo cash</GatedButton></Details><div className="grid gap-3"><label className="eyebrow">Allocation (demo USD)<Input value={allocation} onChange={e=>setAllocation(e.target.value)} inputMode="decimal" /></label><label className="eyebrow">Position size (demo USD)<Input value={size} onChange={e=>setSize(e.target.value)} inputMode="decimal" /></label><label className="eyebrow">Stop-trigger budget (USD, max 2)<Input value={loss} onChange={e=>setLoss(e.target.value)} inputMode="decimal" /></label></div><p className="my-3 text-xs text-muted-foreground">Permission lasts 24 hours. Includes fees; stricter stored limits apply (account limit ${priorText}). Slippage or funding can exceed the trigger budget. Saving pauses new entries; return to AI Trading to start.</p><GatedButton reason={ledgerReason ?? (!valid ? 'Enter valid limits, up to $2 per position.' : null)} onClick={()=>void controller.reviewLedger({kind:'permission',allocation,positionSize:size,maxLoss:loss})}>Save trading settings</GatedButton>
</Panel>;
}

export function AccountResult() {
  const { account } = useAccountDashboard();
  return <Panel title={account ? 'Reported paper results' : 'No fills observed'} aside={<SourceTag kind="unavailable" label="LIVE UNVERIFIED" />} className="border-destructive/20"><p className="text-sm text-muted-foreground">{account ? 'Balances and closed fills come from the existing Vela ledger.' : 'No account data is connected.'}</p><Details label="Verification details"><p className="text-xs text-muted-foreground">Browser execution, native payouts and protective-stop operation still require observation on the released candidate.</p></Details></Panel>;
}

import { Panel } from '@/components/common/Panel';
import { Metric, Row } from '@/components/common/Metric';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { Button } from '@/components/ui/button';
import { GatedButton } from '@/components/common/GatedButton';
import { useAccountDashboard } from './context';
import { usd, doge, units, ratio, percent, stamp } from './presentation';

/** Compact view of the same authoritative positions and reviewed close action. */
export function AccountOpenTrades() {
  const {account,refreshing,controller,ledgerReason,closing}=useAccountDashboard();
  const positions=account?.paper.positions;
  return <Panel title="Open trades" className="flex aspect-square min-w-0 flex-col">
    {!positions?.length ? <div className="flex flex-1 items-center justify-center"><p className="text-2xl text-muted-foreground">{account?'None':!controller.service?'Connect your wallet':refreshing?'Loading…':'Unavailable'}</p></div> : <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
      {positions.map(p=><div key={p.id} className="rounded-md border border-border/70 p-3">
        <div className="flex items-center justify-between gap-3"><p className="font-display font-semibold">{p.symbol}</p><span className="text-xs text-muted-foreground">{p.side==='long'?'Price up':'Price down'}</span></div>
        <div className="my-3 flex items-center justify-between gap-3 text-sm"><span className="text-muted-foreground">Open profit / loss</span><span className={`num ${p.fresh&&(units(p.unrealized)??0n)>0n?'text-success':p.fresh&&(units(p.unrealized)??0n)<0n?'text-destructive':''}`}>{p.fresh?usd(p.unrealized):'Unavailable'}</span></div>
        <Button size="sm" variant="outline" disabled={!!ledgerReason||!!closing[p.id]||account?.paper.orders.some(o=>o.status==='pending'&&o.payload?.reduceOnly&&o.payload.positionId===p.id)} onClick={()=>void controller.reviewLedger({kind:'close',positionId:p.id,fraction:'1'})}>{closing[p.id]||account?.paper.orders.some(o=>o.status==='pending'&&o.payload?.reduceOnly&&o.payload.positionId===p.id)?'Closing…':'Close trade'}</Button>
        {(units(p.riskBudget)===null||(units(p.riskBudget)??0n)>2_000_000n)&&<p role="alert" className="mt-2 text-xs text-destructive">Reported protection needs review. You can still close this trade.</p>}
        <Details label="Details"><Row k="Entry price" v={usd(p.entry)} /><Row k="Current price" v={p.fresh?usd(p.mark):'Unavailable'} /><Row k="Stop price" v={usd(p.stop)} /><Row k="Stop-trigger budget" v={usd(p.riskBudget)} /><p className="mt-2 text-xs text-muted-foreground">Includes fees. Slippage and funding can exceed this budget. Live protection is unverified.</p></Details>
      </div>)}
    </div>}
  </Panel>;
}

export function AccountPositions() {
  const { account, controller, ledgerReason }=useAccountDashboard();
  return <Panel title="Positions & protective stops" aside={<SourceTag kind="demo" label="PAPER" />}>
    {!account ? <p className="text-sm text-muted-foreground">Positions unavailable.</p> : account.paper.positions.length===0 && <p className="text-sm text-muted-foreground">No open positions.</p>}
    <div className="space-y-3">{account?.paper.positions.map(p=><div key={p.id} className="rounded-md border border-border/70 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="font-display font-semibold">{p.symbol} <span className={p.side==='long'?'text-success':'text-destructive'}>{p.side.toUpperCase()}</span> <span className="num text-sm text-muted-foreground">{p.qty}</span></p><span className="num text-sm">{p.fresh?usd(p.unrealized):'Unavailable'} unrealized</span></div><div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4"><span>Entry {usd(p.entry)}</span><span>Mark {p.fresh?usd(p.mark):'Unavailable'}</span><span>Stop {usd(p.stop)}</span></div><p className="mt-2 text-xs text-muted-foreground">Stop-trigger budget {usd(p.riskBudget)}, including fees. Slippage or funding can exceed this.</p>{(units(p.riskBudget)===null || (units(p.riskBudget) ?? 0n) > 2_000_000n) && <p role="alert" className="mt-2 text-xs text-destructive">Reported protection requires reconciliation. Close controls remain available.</p>}<div className="mt-2 flex gap-2"><Button size="sm" variant="secondary" disabled={!!ledgerReason} onClick={()=>void controller.reviewLedger({kind:'close',positionId:p.id,fraction:'0.5'})}>Close half</Button><Button size="sm" variant="outline" disabled={!!ledgerReason} onClick={()=>void controller.reviewLedger({kind:'close',positionId:p.id,fraction:'1'})}>Close all</Button></div><Details label="Position details"><Row k="Remaining entry fees" v={usd(p.entry_fee_left)} /><Row k="Funding" v={usd(p.funding)} /><p className="mt-2 text-xs text-muted-foreground">Stop prices come from Vela. Live protective execution is unverified.</p></Details></div>)}</div>
    {account?.paper.orders.some(o=>o.status==='pending') && <Details label="Pending orders">{account.paper.orders.filter(o=>o.status==='pending').map(o=><div key={o.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-xs"><span className="break-all">{o.id}</span><Button size="sm" variant="outline" disabled={!!ledgerReason} onClick={()=>void controller.reviewLedger({kind:'cancel',orderId:o.id})}>Cancel order</Button></div>)}</Details>}
  </Panel>;
}
export function AccountFills() {
  const { account }=useAccountDashboard();const p=account?.paper;
  return <Panel title="Paper fills" aside={<SourceTag kind="demo" label="PAPER" />}><div className="mb-3 grid grid-cols-2 gap-5"><Metric label="Today UTC" value={usd(p?.today.realized)} sub={p?`${p.today.trades} fills · ${p.today.wins} wins / ${p.today.losses} losses`:undefined} /><Metric label="All-time" value={usd(p?.totals.realized)} sub={p?`${p.totals.trades} fills · ${p.totals.wins} wins / ${p.totals.losses} losses`:undefined} /></div><Details label="Fill history">{!p?.trades ? <p className="text-xs text-muted-foreground">Fill history unavailable.</p> : !p.trades.length ? <p className="text-xs text-muted-foreground">No closed fills.</p> : <div className="overflow-x-auto"><table className="w-full min-w-[440px] text-xs"><thead className="text-left text-muted-foreground"><tr><th>Closed (UTC)</th><th>Market</th><th>Side</th><th className="text-right">Net P&L</th></tr></thead><tbody>{p.trades.map(t=><tr key={t.id} className="border-t border-border/60"><td className="py-2">{stamp(t.closed)}</td><td>{t.symbol}</td><td>{t.side}</td><td className="num text-right">{usd(t.pnl)}</td></tr>)}</tbody></table><p className="mt-2 text-xs text-muted-foreground">Latest reported closed fills; summary totals cover their labeled scopes.</p></div>}</Details></Panel>;
}
export function AccountStress() {
  const { account }=useAccountDashboard();const h=account?.health;
  return <Panel title="LTV / HF stress test" aside={<SourceTag kind="demo" />}><p className="text-sm text-muted-foreground">See how a DOGE price drop affects your loan.</p><Details label="Price scenarios"><p className="mb-3 text-xs text-muted-foreground">Illustrative scenarios reported by Vela, using its last demo quote. Not liquidation events.</p>{h?.scenarios ? <div className="overflow-x-auto"><table className="w-full min-w-[300px] text-xs"><thead className="text-left text-muted-foreground"><tr><th>DOGE drop</th><th>Collateral value</th><th>Health factor</th></tr></thead><tbody>{h.scenarios.map(s=><tr key={s.drop} className="border-t border-border/60"><td className="py-2">{percent(s.drop)}</td><td>{usd(s.collateralValue)}</td><td>{s.healthFactor===null && units(h.debt)===0n?'N/A':ratio(s.healthFactor)}</td></tr>)}</tbody></table></div> : <p className="text-xs text-muted-foreground">Scenarios unavailable. Refresh the account quote.</p>}</Details></Panel>;
}
export function AccountRiskActions() {
  const { account,native,controller,ledgerReason }=useAccountDashboard();
  const state=account?.health?.loan?.state;
  return <Panel title="Manage collateral" aside={<SourceTag kind="demo" />}><p className="mb-4 text-sm text-muted-foreground">Owner-approved shortfall resolution. No automatic liquidation.</p><GatedButton variant="secondary" reason={ledgerReason ?? (!['SHORTFALL','DEFAULT_PENDING'].includes(state??'')?'Stop and settle trading to establish a repayable shortfall first.':null)} onClick={()=>void controller.reviewLedger({kind:'liquidate'})}>Review shortfall resolution</GatedButton><p className="mt-3 text-xs text-muted-foreground">Collateral {native?.vault ? native.vault.withdrawable==='0'?'locked':'has a withdrawable balance':'unavailable'}.</p><Details label="Risk rules & sale records"><Row k="Withdrawable" v={doge(native?.vault?.withdrawable)} /><p className="mt-3 text-xs text-muted-foreground">The existing service resolves eligible loan shortfalls. Partial collateral sales are unavailable. A later native settlement requires its own review; residual bad debt is retained.</p></Details></Panel>;
}
export function AccountRepaymentHistory() {
  const { account }=useAccountDashboard();const rows=account?.events.filter(e=>e.kind==='SIMULATED_REPAYMENT_RECEIPT');
  return <Panel title="Repayment history" aside={<SourceTag kind="demo" />}>{!rows ? <p className="text-sm text-muted-foreground">Repayment history unavailable.</p> : rows.length===0 ? <p className="text-sm text-muted-foreground">No repayments recorded.</p> : <ul className="space-y-2 text-xs">{rows.map(r=><li key={r.id} className="border-b border-border/60 py-2"><p className="num">{usd(r.amount)} · demo repayment</p><Details label="Receipt details"><p className="break-words text-muted-foreground">{r.detail}</p></Details></li>)}</ul>}</Panel>;
}
export function AccountActivity() {
  const { account }=useAccountDashboard();
  return <Panel title="Activity log" aside={<SourceTag kind="demo" />}><ol className="max-h-96 space-y-1.5 overflow-y-auto pr-1 text-xs">{account?.events.map(e=><li key={e.id} className="border-b border-border/50 pb-1.5"><span className="mr-1.5 font-mono text-[10px] uppercase text-primary">{e.kind}</span>{usd(e.amount)}<Details label="Event details"><p className="break-words text-muted-foreground">{e.detail}</p></Details></li>)}</ol>{!account && <p className="text-xs text-muted-foreground">Activity unavailable.</p>}{account?.events.length===0 && <p className="text-xs text-muted-foreground">No account events.</p>}</Panel>;
}

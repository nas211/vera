import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Panel } from '@/components/common/Panel';
import { Metric, Row } from '@/components/common/Metric';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { AccountService, type AccountSnapshot, type ReviewedAction, type ServiceAction } from '@/lib/vela/accountService';
import { NativeVaultPanel } from './NativeVaultPanel';
import type { NativeVaultWorkflow } from '@/lib/vela/nativeVault';

const dollars = (s: string | null | undefined) => s == null ? 'Unavailable' : `$${s}`;
const fieldLabels: Record<string, string> = { amount: 'Demo USD amount', full: 'Repay full debt', allocation: 'Trading allocation (USD)', positionSize: 'Position size (USD)', maxLoss: 'Stop-trigger budget (USD)', leverage: 'Leverage', enabled: 'Permission enabled', sweepPercent: 'Profit sweep (%)', days: 'Permission duration (days)', minimum: 'Keep at least (USD)', frequency: 'Scheduled repayments', source: 'Repayment source', fraction: 'Position fraction to close' };
const labels = { allocate: 'Allocate demo cash', withdraw: 'Withdraw demo cash', repay: 'Repay demo loan', repayFull: 'Repay full demo debt', permission: 'Authorize paper trading', sweep: 'Authorize profit sweep', revokeSweep: 'Revoke profit sweep', start: 'Start AI paper trading', stop: 'Stop new entries', close: 'Reduce paper position', cancel: 'Cancel pending order', issueBorrow: 'Issue requested demo credit', cancelBorrow: 'Cancel uncredited request', settle: 'Stop & settle demo account', liquidate: 'Resolve shortfall with collateral', refreshQuote: 'Refresh illustrative collateral quote' };

/** Reads and mutations go through the authoritative service. No fixture provider or local ledger. */
export function ServiceDashboard({ service, native }: { service: AccountService | null; native?: NativeVaultWorkflow | null }) {
  const [account, setAccount] = useState<AccountSnapshot | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState<{ request: ReviewedAction; action: ServiceAction } | null>(null);
  const [amount, setAmount] = useState('');
  const [positionSize, setPositionSize] = useState('');
  const [maxLoss, setMaxLoss] = useState('2');
  const [percent, setPercent] = useState('');
  const [days, setDays] = useState('7');
  const [floor, setFloor] = useState('0');
  const { pathname } = useLocation();
  useEffect(() => {
    let current = true;
    setAccount(null); setReview(null); setNotice(''); setError('');
    if (service) service.refresh().then(s => { if (current) setAccount(s); }).catch(e => { if (current) setError(String(e.message)); });
    return () => { current = false; };
  }, [service]);
  const refresh = async () => {
    if (!service) return;
    setBusy(true); setError('');
    try { setAccount(await service.refresh()); } catch (e) { setAccount(null); setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const prepare = async (action: ServiceAction) => {
    if (!service || busy) return;
    setBusy(true); setError('');
    try {
      setAccount(await service.refresh());
      setReview({ request: service.review(action, crypto.randomUUID()), action });
    }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const submit = async () => {
    if (!service || !review || busy) return;
    setBusy(true); setError('');
    try {
      await service.execute(review.request, true);
      setReview(null); setNotice('Request accepted by Vela. Pending orders are not filled trades.');
      setAccount(await service.refresh());
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  if (!service) return <Panel eyebrow="Prepared integration" title="Vela account services are not activated">
    <p className="text-sm text-muted-foreground">The dashboard is prepared for the existing Vela account and lending services. Deployment and service connection are disabled for this review.</p>
    <p className="mt-3 text-sm">Testnet DOGE · simulated USD · blockchain writes OFF</p>
    <p className="mt-3 text-xs text-muted-foreground">The new reward-enabled collateral vault and owner transaction workflows are prepared locally. Rewards use a separate funded reserve; collateral locks are enforced by the candidate contract. No deployment or live payout is verified.</p>
  </Panel>;
  return <>
    <div className="flex items-center justify-between gap-3"><p className="text-xs text-muted-foreground">Authoritative Vela ledger · DogeOS testnet · PAPER / DEMO</p><Button size="sm" variant="outline" onClick={refresh} disabled={busy}>Refresh account</Button></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {notice && <p role="status" className="text-sm text-success">{notice}</p>}
    {!account && <Panel title="Account unavailable"><p>Refresh to retrieve your authenticated account. No sample balances are substituted.</p></Panel>}
    {account && <>
      {(pathname === '/' || pathname === '/trading') && <>
        <Panel title="Paper account" eyebrow="Simulated USD"><div className="grid gap-4 sm:grid-cols-3">
          <Metric label="Ledger cash" value={dollars(account.paper.balance)} />
          <Metric label="Spendable cash" value={dollars(account.repayment.freeEquity)} sub={account.repayment.restriction ?? 'After position margin, losses, pending orders and costs'} />
          <Metric label="Open P&L" value={dollars(account.paper.unrealized)} sub="Unrealized gains are not spendable" />
        </div></Panel>
        <div className="grid gap-4 sm:grid-cols-2">{([['Today (UTC)', account.paper.today], ['Cumulative', account.paper.totals]] as const).map(([label, scope]) => <Panel key={label} title={label} eyebrow="Closed paper fills">
          <Metric label="Realized net P&L" value={dollars(scope.realized)} /><Row k="Closed fills" v={scope.trades} /><Row k="Winning / losing fills" v={`${scope.wins} / ${scope.losses}`} />
        </Panel>)}</div>
      </>}
      {['/vault', '/borrow', '/trading'].includes(pathname) && native && <NativeVaultPanel workflow={native} onReconciled={refresh} />}
      {(pathname === '/' || pathname === '/borrow') && <Panel title="Borrow & repay" eyebrow="Existing demo loan">
        <p className="mb-3 text-xs text-muted-foreground">Borrowing capacity uses an illustrative demo collateral price, not a live DOGE oracle.</p><Button size="sm" variant="outline" disabled={busy || !account.vault} onClick={() => prepare({ kind: 'refreshQuote' })}>Refresh illustrative quote</Button>
        <Row k="Loan" v={account.health?.loan?.id ?? 'No active loan'} /><Row k="Loan state" v={account.health?.loan?.state ?? 'None'} /><Row k="Current debt" v={dollars(account.repayment.debt)} /><Row k="Maximum repayable now" v={dollars(account.repayment.maxRepayable)} />
        <label className="mt-3 block text-xs">Demo USD amount / full-repayment maximum<Input aria-label="Demo USD amount" value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" /></label>
        <div className="mt-3 flex flex-wrap gap-2">{(['allocate', 'withdraw', 'repay', 'repayFull'] as const).map(kind => <Button key={kind} variant="secondary" disabled={busy} onClick={() => prepare({ kind, amount })}>{labels[kind]}</Button>)}</div>
        <div className="mt-3 flex flex-wrap gap-2">{account.vault?.loanState === 1 && (['issueBorrow', 'cancelBorrow'] as const).map(kind => <Button key={kind} disabled={busy} variant="outline" onClick={() => prepare({ kind })}>{labels[kind]}</Button>)}<Button disabled={busy} variant="outline" onClick={() => prepare({ kind: 'settle' })}>Stop & settle demo account</Button>{account.health?.loan?.state === 'SHORTFALL' && <Button disabled={busy} variant="outline" onClick={() => prepare({ kind: 'liquidate' })}>Review shortfall resolution</Button>}</div>
        <p className="mt-2 text-xs text-muted-foreground">Stop & settle stops new entries, closes positions as market data permits, and repays available demo cash. Refresh until settlement is reconciled, then confirm the native settlement above. Collateral stays locked until then.</p>
        <p className="mt-2 text-xs text-muted-foreground">Full repayment uses your reviewed maximum and rejects a newer debt above it. No stablecoins are transferred.</p>
      </Panel>}
      {pathname === '/borrow' && <Panel title="Profit-sweep permission" eyebrow="Explicit and revocable">
        <p className="mb-3 text-sm text-muted-foreground">Only realized net trading profit after losses and costs. DOGE rewards and borrowed principal are excluded. Scheduled principal repayments are disabled.</p>
        <div className="grid gap-3 sm:grid-cols-3">{[['Percent', percent, setPercent], ['Expiry days (1–30)', days, setDays], ['Keep at least (USD)', floor, setFloor]].map(([label, value, setter]) => <label className="text-xs" key={String(label)}>{String(label)}<Input value={value as string} onChange={e => (setter as (v: string) => void)(e.target.value)} inputMode="decimal" /></label>)}</div>
        <div className="mt-3 flex gap-2"><Button disabled={busy} onClick={() => prepare({ kind: 'sweep', percent: Number(percent || NaN), days: Number(days || NaN), floor })}>Review sweep permission</Button><Button variant="outline" disabled={busy} onClick={() => prepare({ kind: 'revokeSweep' })}>Revoke</Button></div>
      </Panel>}
      {pathname === '/trading' && <>
        <Panel title="Trading session" eyebrow="Server authorization">
          <Row k="New entries" v={account.paper.preferences.autonomous ? 'Enabled by server' : 'Stopped'} />
          {account.paper.entryStop && <p className="text-sm text-muted-foreground">{account.paper.entryStop.reason} {account.paper.entryStop.action}</p>}
          <div className="mt-3 grid gap-3 sm:grid-cols-3"><label className="text-xs">Allocation (USD)<Input value={amount} onChange={e => setAmount(e.target.value)} /></label><label className="text-xs">Position size (USD)<Input value={positionSize} onChange={e => setPositionSize(e.target.value)} /></label><label className="text-xs">Stop-trigger budget (USD, max 2)<Input value={maxLoss} onChange={e => setMaxLoss(e.target.value)} /></label></div>
          <div className="mt-3 flex flex-wrap gap-2"><Button disabled={busy} onClick={() => prepare({ kind: 'permission', allocation: amount, positionSize, maxLoss })}>Review trading permission</Button><Button disabled={busy} variant="secondary" onClick={() => prepare({ kind: 'start' })}>Start</Button><Button disabled={busy} variant="outline" onClick={() => prepare({ kind: 'stop' })}>Stop new entries</Button></div>
          <p className="mt-3 text-xs text-muted-foreground">Every account retains a $2 fee-inclusive stop-trigger budget or its stricter existing limit. This does not guarantee realized losses against slippage or funding.</p>
        </Panel>
        <Panel title="Positions & protective stops" eyebrow="Reported by Vela">
          {account.paper.positions.length === 0 && <p className="text-sm text-muted-foreground">No open positions.</p>}
          {account.paper.positions.map(p => <div key={p.id} className="mb-3 rounded-md border border-border p-3"><p className="font-display">{p.symbol} · {p.side} · {p.qty}</p><Row k="Stop trigger" v={dollars(p.stop)} /><Row k="Reported trigger budget" v={dollars(p.riskBudget)} /><p className="text-xs text-muted-foreground">{p.fresh ? 'Current server observation' : 'Market observation unavailable'} · Live execution protection is unverified.</p><div className="mt-2 flex gap-2"><Button size="sm" disabled={busy} onClick={() => prepare({ kind: 'close', positionId: p.id, fraction: '0.5' })}>Close half</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => prepare({ kind: 'close', positionId: p.id, fraction: '1' })}>Close all</Button></div></div>)}
          {account.paper.orders.filter(o => o.status === 'pending').map(o => <div className="flex justify-between gap-2" key={o.id}><span>Pending order {o.id}</span><Button size="sm" disabled={busy} onClick={() => prepare({ kind: 'cancel', orderId: o.id })}>Cancel order</Button></div>)}
        </Panel>
      </>}
      {pathname === '/risk' && <Panel title="Risk & activity" eyebrow="Authoritative account events"><Row k="Health factor" v={account.health?.healthFactor ?? 'Unavailable'} />{account.events.length === 0 && <p>No account events.</p>}{account.events.map(e => <div className="border-b border-border py-2 text-sm" key={e.id}>{e.kind} · {e.amount}<p className="text-xs text-muted-foreground">{e.detail}</p></div>)}</Panel>}
    </>}
    {review && <div role="dialog" aria-modal="true" aria-labelledby="review-title" className="fixed inset-0 z-50 flex items-center justify-center bg-background/90 p-4"><Panel title={labels[review.action.kind]}><h2 id="review-title" className="font-display">Review this simulated action</h2><Row k="Wallet" v={review.request.context.address} /><Row k="Network" v="DogeOS testnet" />{Object.entries(review.request.body).filter(([k]) => !['confirm', 'vaultId', 'loanId'].includes(k)).map(([k,v]) => <Row key={k} k={fieldLabels[k] ?? k} v={typeof v === 'boolean' ? (v ? 'Yes' : 'No') : v === 'off' ? 'Disabled' : v === 'trading' ? 'Paper trading profits' : String(v)} />)}{review.action.kind === 'liquidate' && <p className="my-3 text-sm">This records a simulated collateral sale to resolve the demo shortfall. Review the exact test DOGE seizure in the subsequent native settlement before signing. Unpaid debt may default the vault.</p>}<p className="my-3 text-xs text-muted-foreground">This affects your existing simulated account. No blockchain transaction is sent.</p>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}<div className="flex gap-2"><Button disabled={busy} onClick={submit}>{busy ? 'Waiting for Vela…' : 'Confirm / retry same request'}</Button><Button variant="outline" disabled={busy} onClick={() => setReview(null)}>Cancel</Button></div></Panel></div>}
  </>;
}

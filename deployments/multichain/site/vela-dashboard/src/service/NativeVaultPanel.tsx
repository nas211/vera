import { useEffect, useRef, useState } from 'react';
import { toHex } from 'viem';
import { Panel } from '@/components/common/Panel';
import { Row } from '@/components/common/Metric';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { formatUnits } from '@/lib/units';
import { NativeVaultWorkflow, type NativeAction, type NativeReview, type NativeState, type NativePending, type NativeReceipt } from '@/lib/vela/nativeVault';

const doge = (value: string) => `${formatUnits(BigInt(value), 18)} test DOGE`;
const titles: Record<NativeAction['kind'], string> = {
  migrate:'Move to reward vault',create: 'Create collateral vault', deposit: 'Deposit collateral', withdraw: 'Withdraw collateral',
  setRewardRate: 'Set project reward rate', fundReserve: 'Fund shared reward pool', claim: 'Claim funded rewards', requestBorrow: 'Request demo loan',
  acknowledge: 'Confirm issued demo loan', cancel: 'Unlock cancelled loan request', settle: 'Confirm loan settlement',
  authorize: 'Authorize vault trading limits', startTrading: 'Enable vault trading',
};

/** Native quantities are read from confirmed chain state; no fixture or USD ledger writes. */
export function NativeVaultPanel({ workflow, onReconciled }: { workflow: NativeVaultWorkflow; onReconciled: () => Promise<void> }) {
  const [state, setState] = useState<NativeState | null>(null);
  const [pending, setPending] = useState<NativePending | null>(null);
  const [receipts, setReceipts] = useState<NativeReceipt[]>([]);
  const [review, setReview] = useState<NativeReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [amount, setAmount] = useState('');
  const [borrow, setBorrow] = useState('');
  const [allocation, setAllocation] = useState('');
  const [size, setSize] = useState('');
  const [loss, setLoss] = useState('2');
  const [hash, setHash] = useState('');
  const mounted = useRef(true);
  const waiter = useRef<AbortController | null>(null);
  const capture = () => { setPending(workflow.pending()); setReceipts(workflow.receipts()); };
  useEffect(() => {
    mounted.current = true;
    let active = true;
    try { setPending(workflow.pending()); setReceipts(workflow.receipts()); } catch (e) { setError((e as Error).message); }
    workflow.refresh().then(s => { if (active) setState(s); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; mounted.current = false; waiter.current?.abort(); };
  }, [workflow]);
  const run = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try { await fn(); } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally {
      if (mounted.current) {
        setBusy(false);
        try { capture(); } catch (e) { setError((e as Error).message); }
      }
    }
  };
  const prepare = (action: NativeAction) => run(async () => { const q = await workflow.review(action); if (mounted.current) setReview(q); });
  const check = () => run(async () => {
    setReview(null);
    if (workflow.pending()?.hash) {
      const result = await workflow.reconcile();
      if (!mounted.current) return;
      setState(result.state);
      setNotice(result.pending ? 'Awaiting canonical confirmations. No balance is credited yet.' : result.receipt?.status === 'REVERTED' ? 'Transaction reverted. No collateral or reward payment was applied; network fees may have been charged.' : 'Transaction confirmed and the existing Vela ledger synchronized.');
      if (!result.pending) await onReconciled();
    } else {
      const s = await workflow.refresh(); if (mounted.current) setState(s);
      await onReconciled();
    }
  });
  const submit = () => run(async () => {
    if (!review) return;
    waiter.current = new AbortController();
    const result = await workflow.submit(review, true, waiter.current.signal);
    if (mounted.current) { setReview(null); setPending(result); setNotice('Submitted. Check confirmation before proceeding.'); }
  });
  const disabled = busy || !workflow.writesEnabled || !!pending || !state;
  const v = state?.vault;
  return <Panel title="Collateral & funded rewards" eyebrow="DogeOS testnet · owner wallet transactions">
    <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy} onClick={check}>Refresh / check transaction</Button>{!workflow.writesEnabled && <span className="text-xs text-muted-foreground">Blockchain writes OFF</span>}</div>
    {error && <p role="alert" className="my-3 text-sm text-destructive">{error}</p>}
    {notice && <p role="status" className="my-3 text-sm">{notice}</p>}
    {!state && <p className="mt-3 text-sm">Confirmed native state unavailable. Refresh to retry.</p>}
    {state && <>
      <Row k="Confirmed block" v={`${state.block} · ${state.confirmations} confirmations`} />
      <Row k="Vault" v={v?.address ?? 'Not created'} />
      {!v && <Button className="mt-3" disabled={disabled} onClick={() => prepare({ kind: 'create' })}>Review vault creation</Button>}
      {v && <>
        <Row k="Collateral" v={doge(v.collateral)} /><Row k="Withdrawable collateral" v={doge(v.withdrawable)} />
        <Row k="Native loan state" v={['Idle', 'Requested — collateral locked', 'Active — collateral locked', 'Settled', 'Defaulted'][v.loanState]} />
        {v.rewards ? <>
          <Row k="Funded reward reserve" v={doge(v.rewards.reserveWei)} /><Row k="Claimable at confirmed block" v={doge(v.rewards.claimableWei)} />
          <Row k="Current annual rate" v={`${formatUnits(BigInt(v.rewards.aprBps), 2)}%`} />
          <p className="my-3 text-xs text-muted-foreground">Collateral earns rewards from a separate funded reserve. The loan locks collateral; accrued rewards remain claimable. The manager can change the rate. Unfunded time earns nothing. Claims transfer test DOGE and do not repay demo USD debt.</p>
          <Button disabled={disabled || v.rewards.claimableWei === '0'} onClick={() => prepare({ kind: 'claim' })}>Review reward claim</Button>
        </> : <p className="my-3 text-sm">Legacy vault: reward accrual is unavailable. Migration requires separate reconciliation.</p>}
        <label className="mt-4 block text-xs">Test DOGE amount<Input aria-label="Test DOGE amount" inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} /></label>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button disabled={disabled} onClick={() => prepare({ kind: 'deposit', amount })}>Review deposit</Button>
          <Button variant="secondary" disabled={disabled || v.withdrawable === '0'} onClick={() => prepare({ kind: 'withdraw', amount })}>Review withdrawal</Button>
          {v.rewards && <Button variant="outline" disabled={disabled} onClick={() => prepare({ kind: 'fundReserve', amount })}>Review reserve donation</Button>}
        </div>
        {[0,3].includes(v.loanState) && <div className="mt-4"><label className="text-xs">New loan amount (demo USD)<Input aria-label="New demo loan amount" inputMode="decimal" value={borrow} onChange={e => setBorrow(e.target.value)} /></label><Button className="mt-2" disabled={disabled} onClick={() => prepare({ kind: 'requestBorrow', amount: borrow, loanId: toHex(crypto.getRandomValues(new Uint8Array(32))) })}>Review borrow request</Button><p className="mt-2 text-xs text-muted-foreground">After confirmation, issue the matching demo credit below, then confirm the issued loan here.</p></div>}
        {v.loanState === 1 && <div className="mt-4 flex flex-wrap gap-2"><Button disabled={disabled} onClick={() => prepare({ kind: 'acknowledge' })}>Review issued-loan confirmation</Button><Button variant="outline" disabled={disabled} onClick={() => prepare({ kind: 'cancel' })}>Review cancelled-request unlock</Button></div>}
        {v.loanState === 2 && <>
          <div className="mt-4 grid gap-3 sm:grid-cols-3"><label className="text-xs">Vault allocation (demo USD)<Input value={allocation} onChange={e => setAllocation(e.target.value)} /></label><label className="text-xs">Position size (demo USD)<Input value={size} onChange={e => setSize(e.target.value)} /></label><label className="text-xs">Stop-trigger budget (USD, max 2)<Input value={loss} onChange={e => setLoss(e.target.value)} /></label></div>
          <div className="mt-3 flex flex-wrap gap-2"><Button disabled={disabled} onClick={() => prepare({ kind: 'authorize', allocation, positionSize: size, maxLoss: loss, expiresAt: Math.floor(Date.now()/1000)+86400 })}>Review 24-hour vault permission</Button><Button variant="secondary" disabled={disabled || v.trading} onClick={() => prepare({ kind: 'startTrading' })}>Review vault trading enable</Button><Button variant="outline" disabled={disabled} onClick={() => prepare({ kind: 'settle' })}>Review settlement & unlock</Button></div>
          <p className="mt-3 text-xs text-muted-foreground">Stop & settle the demo account first. Withdrawal stays locked until the matching settlement transaction is confirmed. Trading also requires the server permission on the Trading screen.</p>
        </>}
      </>}
    </>}
    {pending && <div className="mt-4 rounded-md border border-border p-3" role="status"><p>{titles[pending.action.kind]} · {pending.hash ? 'Submitted, awaiting verification' : 'Wallet response unknown'}</p>{pending.hash ? <p className="break-all text-xs">{pending.hash}</p> : <><p className="my-2 text-xs">Check wallet history before any retry. Stopping the wait does not cancel a transaction. A late hash is saved automatically; refresh to retrieve it.</p><Input aria-label="Recovery transaction hash" placeholder="0x… transaction hash" value={hash} onChange={e => setHash(e.target.value)} /><Button className="mt-2" disabled={busy} variant="outline" onClick={() => run(async () => { await workflow.attachHash(hash); })}>Recover hash</Button></>}</div>}
    {receipts.length > 0 && <div className="mt-4"><p className="text-xs text-muted-foreground">Previously verified receipts saved on this device. Historical records are not a fresh chain observation.</p>{receipts.slice(0,5).map(r => <div key={r.hash} className="mt-2 text-xs"><p>{titles[r.action]} · {r.status} · block {r.block}{r.kind === 'native-reward-claim' ? ` · paid ${doge(r.rewardPaidWei)}` : ''}</p><p className="break-all text-muted-foreground">{r.hash}</p></div>)}</div>}
    {review && <div role="dialog" aria-modal="true" aria-labelledby="native-review-title" className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-background/95 p-4"><Panel className="my-auto max-w-xl"><h2 id="native-review-title" className="font-display">{titles[review.action.kind]}</h2><Row k="Owner" v={review.owner} /><Row k="Target" v={review.tx.to} /><Row k="Native amount sent" v={doge(BigInt(review.tx.value).toString())} /><Row k="Estimated fee reserve" v={doge(review.feeReserveWei)} />
      {'amount' in review.action && <Row k={review.action.kind === 'requestBorrow' ? 'Demo loan requested' : 'Reviewed amount'} v={review.action.kind === 'requestBorrow' ? `$${review.action.amount} demo USD` : `${review.action.amount} test DOGE`} />}
      {review.action.kind === 'authorize' && <><Row k="Allocation / position size" v={`$${review.action.allocation} / $${review.action.positionSize} demo USD`} /><Row k="Maximum stop-trigger budget" v={`$${review.action.maxLoss}; stricter stored limits apply`} /><Row k="Permission expiry" v={new Date(review.action.expiresAt*1000).toISOString()} /></>}
      {review.action.kind === 'claim' && v?.rewards && <Row k="Claim estimate" v={doge(v.rewards.claimableWei)} />}
      {review.certificate && <><Row k="Certified repaid / credited" v={`$${formatUnits(BigInt(review.certificate.a),6)} demo USD`} /><Row k="Collateral transferred to treasury" v={doge(review.certificate.b)} /><Row k="Remaining demo bad debt" v={`$${formatUnits(BigInt(review.certificate.c),6)}`} /></>}
      <p className="my-3 text-xs text-muted-foreground">Your wallet confirms a DogeOS testnet transaction. Final fees and claim payout may differ from the estimate. Claims count as paid only after a confirmed event. No real stablecoin is transferred.</p>
      {review.action.kind === 'fundReserve' && <p className="my-3 text-sm">This is an irrevocable donation to the reward reserve, not collateral. It cannot be withdrawn as principal.</p>}
      {error && <p role="alert" className="my-3 text-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap gap-2"><Button disabled={busy || !!pending} onClick={submit}>{busy ? 'Waiting for wallet…' : 'Confirm in wallet'}</Button><Button variant="outline" onClick={() => { if (busy) waiter.current?.abort(); else setReview(null); }}>{busy ? 'Stop waiting' : 'Cancel review'}</Button></div>
    </Panel></div>}
  </Panel>;
}

import { useRef, useState } from 'react';
import { AlertCircle, PauseCircle } from 'lucide-react';
import { Panel } from '@/components/common/Panel';
import { Row } from '@/components/common/Metric';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { assessRisk, fmtBpsPct, fmtBpsRatio } from '@/lib/riskMath';
import { USD_DECIMALS, fmtUsd, usdPrefill, validateAmount } from '@/lib/units';
import { confirmBorrowDialog } from '@/lib/borrowDialog';
import { useFixture } from '@/state/FixtureProvider';

export function BorrowPanel() {
  const { state, view, act } = useFixture();
  const [amt, setAmt] = useState('');
  const [review, setReview] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  // One idempotency key per review. Every click on Confirm within that review reuses it,
  // so a double-click can never apply two borrows; the reducer ignores a repeated key.
  const [requestId, setRequestId] = useState<string | null>(null);
  const seq = useRef(0);
  const completed = useRef<string | null>(null);
  const cap = view.capacity;
  const rules = { inputDecimals: 2, decimals: USD_DECIMALS, max: cap ?? 0n, maxLabel: 'borrow capacity' };
  const parsed = validateAmount(amt, rules);
  const blocked = cap === null ? 'Price quote missing or stale — borrowing blocked until risk is available.' : cap < 10_000n ? 'No borrow capacity at 40% max LTV.' : null;
  const projected = parsed.ok ? assessRisk({ collateralWei: state.demoCollateralWei, committedToSaleWei: view.committedWei, principalUsd6: view.principalUsd6 + parsed.value, accruedInterestUsd6: view.interestUsd6, quote: state.quote, nowSec: state.clock }) : null;
  const running = state.session.status === 'running';

  const openReview = () => {
    seq.current += 1;
    setRequestId(`borrow-${Date.now()}-${seq.current}`);
    setDialogError(null);
    setMsg(null);
    setReview(true);
  };

  const closeReview = () => { setReview(false); setDialogError(null); setRequestId(null); };

  const confirm = () => {
    const r = confirmBorrowDialog(
      { open: review, error: dialogError, requestId, completedId: completed.current },
      parsed.ok ? parsed.value : null,
      act,
    );
    // ref updates synchronously, so a second click before re-render is skipped as already-completed
    completed.current = r.next.completedId;
    if (!r.recorded) {
      if (r.skipped === null) setDialogError(r.next.error); // failure: dialog stays OPEN with the reason
      return;
    }
    closeReview();
    setAmt('');
    setMsg('Simulated borrow recorded (fixture). New entries are paused until you grant NEW demo financed-trading authorization. Existing positions keep their protective stops.');
  };

  return (
    <Panel eyebrow="Simulated USD" title="Borrow">
      <AmountField label="Borrow amount" unit="USD" value={amt} onChange={setAmt} rules={rules}
        onMax={cap ? () => setAmt(usdPrefill(cap).text) : undefined} hint={cap !== null ? `Capacity ${fmtUsd(cap, 'down')} (rounded down)` : undefined} />
      <GatedButton onClick={openReview} reason={blocked ?? (parsed.ok ? null : 'Enter a valid amount to review.')}>Review demo borrow</GatedButton>
      {msg && <p className="mt-2 text-xs text-fixture">{msg}</p>}
      <Dialog open={review} onOpenChange={(o) => (o ? setReview(true) : closeReview())}>
        <DialogContent className="glass">
          <DialogHeader>
            <DialogTitle className="font-display">Review DEMO borrow</DialogTitle>
            <DialogDescription>Simulated USD from the demo ledger. No wallet signature, no on-chain authorization.</DialogDescription>
          </DialogHeader>
          {parsed.ok && <div>
            <Row k="Borrow" v={fmtUsd(parsed.value)} exact={`${parsed.value} µUSD`} />
            <Row k="Interest" v="5% simple APR, accrues per second" />
            <Row k="Projected LTV" v={projected && 'ltvBps' in projected ? fmtBpsPct(projected.ltvBps) : '—'} />
            <Row k="Projected HF" v={projected && 'hfBps' in projected ? fmtBpsRatio(projected.hfBps) : '—'} />
            <Row k="Quote age" v={`${view.quoteAgeSec}s`} />
          </div>}
          {running && (
            <div className="flex gap-2 rounded-md bg-warning/10 p-3 text-sm text-warning">
              <PauseCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Your demo session is running. Borrowing will pause new entries and ask for a NEW demo trading authorization. Existing consent does not carry into financed trading. Protective stops keep running.</span>
            </div>
          )}
          {dialogError && (
            <div role="alert" className="flex gap-2 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Borrow not recorded: {dialogError}</span>
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={closeReview}>Cancel</Button>
            <Button onClick={confirm} disabled={!parsed.ok}>Confirm demo borrow</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}

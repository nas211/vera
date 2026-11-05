import { useState } from 'react';
import { KeyRound } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { fmtUsd } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';

/** NEW demo trading authorization — off-chain, demo-only. No on-chain signature involved. */
export function AuthorizeTradingDialog() {
  const { state, view, act } = useFixture();
  const [open, setOpen] = useState(false);
  const [ack, setAck] = useState(false);
  const financed = !!state.loan;
  const confirm = () => { act({ type: 'AUTHORIZE_TRADING', scope: financed ? 'financed' : 'unfinanced' }); setOpen(false); setAck(false); };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary" className="gap-1.5"><KeyRound className="h-4 w-4" />Grant new demo authorization</Button>
      </DialogTrigger>
      <DialogContent className="glass">
        <DialogHeader>
          <DialogTitle className="font-display">{financed ? 'Authorize DEMO financed paper trading' : 'Authorize DEMO paper trading'}</DialogTitle>
          <DialogDescription>This is a demo-mode permission recorded by the paper account. It is not an on-chain authorization and no wallet signature is requested.</DialogDescription>
        </DialogHeader>
        {financed && (
          <div className="rounded-md bg-muted/60 p-3 text-sm">
            Paper trading will use a balance that includes <span className="num">{fmtUsd(view.debtUsd6)}</span> of simulated borrowed USD.
            Earlier consent does not carry over to financed trading.
          </div>
        )}
        <label className="flex items-start gap-2 text-sm">
          <Checkbox checked={ack} onCheckedChange={(v) => setAck(v === true)} className="mt-0.5" />
          <span>I understand every position keeps a $2 fee-inclusive stop-trigger budget, and that slippage, gaps and funding can make realized losses larger.</span>
        </label>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button disabled={!ack} onClick={confirm}>Grant demo authorization</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

import { useState } from 'react';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { Metric } from '@/components/common/Metric';
import { SourceTag } from '@/components/common/SourceTag';
import { GatedButton } from '@/components/common/GatedButton';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { GAS_TEXT, useWalletGas } from '@/hooks/useWalletGas';
import { fmtDoge } from '@/lib/units';
import { INTEGRATION } from '@/lib/vela/integrationConfig';
import { onChainBlockReason } from '@/lib/vela/writeGate';
import { useFixture } from '@/state/FixtureProvider';
import { ClaimReceipts } from './ClaimReceipts';
import { usePreviewMode } from '@/state/PreviewMode';

export function ClaimPanel() {
  const preview = usePreviewMode();
  return preview ? <ClaimPanelContent gasReason="Wallet connections are disabled in this preview." /> : <ConnectedClaimPanel />;
}

function ConnectedClaimPanel() {
  const gas = useWalletGas();
  return <ClaimPanelContent gasReason={gas.state !== 'ok' ? GAS_TEXT[gas.state] : null} />;
}

function ClaimPanelContent({ gasReason }: { gasReason: string | null }) {
  const { state, view, act } = useFixture();
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const nothing = view.claimable === 0n;

  const confirm = () => { setErr(act({ type: 'CLAIM' })); setOpen(false); };

  return (
    <Panel eyebrow="Rewards" title="Claim test DOGE" aside={<SourceTag kind="testnet" />}>
      <div className="mb-4">
        <Metric label="Claimable now" value={fmtDoge(view.claimable, 6)} tone="positive" exact={`${view.claimable} wei`} />
      </div>
      <p className="mb-3 text-xs text-muted-foreground">Sample rewards. No payout is sent.</p>
      {state.gasEmpty && <p className="mb-2 text-xs text-warning">Fixture: insufficient gas simulated — on-chain claim would be blocked.</p>}
      <GatedButton onClick={() => setOpen(true)} reason={nothing ? 'No rewards to claim yet.' : null}>Preview claim</GatedButton>
      {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
      <Details label="Claim details & history">
        <Metric label="Required confirmations" value={INTEGRATION.requiredConfirmations} sub="placeholder until verified" />
        <p className="my-3 text-xs text-muted-foreground">A live payout requires a successful canonical transaction and confirmed amount and hash. Claiming DOGE never reduces your demo USD loan.</p>
        <GatedButton reason={onChainBlockReason(gasReason)}>Claim on-chain</GatedButton>
        <ClaimReceipts />
      </Details>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="glass">
          <DialogHeader>
            <DialogTitle className="font-display">Rehearse reward claim</DialogTitle>
            <DialogDescription>Fixture only. No wallet prompt, no transaction, no transaction hash. The record will be labeled as a rehearsal.</DialogDescription>
          </DialogHeader>
          <p className="num text-lg">{fmtDoge(view.claimable, 6)}</p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={confirm}>Record fixture claim</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}

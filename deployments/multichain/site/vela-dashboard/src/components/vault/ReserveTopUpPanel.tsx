import { useState } from 'react';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { DOGE_DECIMALS, validateAmount } from '@/lib/units';
import { onChainBlockReason } from '@/lib/vela/writeGate';
import { useFixture } from '@/state/FixtureProvider';

/** Reserve operator top-up (owner-only on-chain). Checkpoints first; never backfills. */
export function ReserveTopUpPanel() {
  const { act } = useFixture();
  const [amt, setAmt] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const rules = { inputDecimals: DOGE_DECIMALS, decimals: DOGE_DECIMALS, max: 100_000n * 10n ** 18n, maxLabel: 'fixture cap' };
  const parsed = validateAmount(amt, rules);
  const go = () => {
    if (!parsed.ok) return;
    const e = act({ type: 'TOPUP', wei: parsed.value });
    setMsg(e ?? 'Fixture top-up recorded. Accrual resumes from now; past unfunded time stays unpaid.');
    if (!e) setAmt('');
  };
  return (
    <Panel eyebrow="Reserve operator" title="Top up reward reserve" aside={<SourceTag kind="fixture" />}>
      <p className="text-sm text-muted-foreground">Optional funding for test rewards.</p>
      <Details label="Reserve controls">
      <AmountField label="Faucet-funded top-up" unit="DOGE" value={amt} onChange={setAmt} rules={rules} hint="Owner only. Allocated to this vault's reserve only." />
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <GatedButton reason={onChainBlockReason()}>Top up on-chain</GatedButton>
        <GatedButton variant="secondary" onClick={go} reason={parsed.ok ? null : 'Enter a valid amount to rehearse.'}>Rehearse top-up</GatedButton>
      </div>
      {msg && <p className="mt-2 text-xs text-fixture">{msg}</p>}
      </Details>
    </Panel>
  );
}

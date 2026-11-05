import { useState } from 'react';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { Button } from '@/components/ui/button';
import { DOGE_DECIMALS, fmtDoge, fmtUsd, validateAmount } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';

/** Voluntary owner-authorized demo collateral sale vs automatic risk management. */
export function RiskActionsPanel() {
  const { state, view, act } = useFixture();
  const [amt, setAmt] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const free = state.demoCollateralWei - view.committedWei;
  const rules = { inputDecimals: DOGE_DECIMALS, decimals: DOGE_DECIMALS, max: free, maxLabel: 'uncommitted collateral' };
  const parsed = validateAmount(amt, rules);
  return (
    <Panel eyebrow="Risk actions" title="Manage collateral" aside={<SourceTag kind="fixture" />}>
      <p className="mb-4 text-sm text-muted-foreground">Owner-approved sample sales. No automatic liquidation.</p>
      <AmountField label="Voluntary demo collateral sale" unit="DOGE" value={amt} onChange={setAmt} rules={rules} hint={`Uncommitted: ${fmtDoge(free, 2)}`} />
      <GatedButton variant="secondary" reason={parsed.ok ? null : 'Enter a valid amount.'}
        onClick={() => { if (parsed.ok) { const e = act({ type: 'VOLUNTARY_SALE', wei: parsed.value }); setMsg(e ?? 'Sale committed. Collateral is subtracted once from risk until reconciliation.'); if (!e) setAmt(''); } }}>
        Commit voluntary sale (fixture)
      </GatedButton>
      {msg && <p className="mt-2 text-xs text-fixture">{msg}</p>}
      <p className="mt-3 text-xs text-muted-foreground">Collateral {view.collateralLocked ? 'locked' : 'unlocked'}{state.badDebtUsd6 > 0n && <span className="text-destructive"> · Bad debt {fmtUsd(state.badDebtUsd6)}</span>}.</p>
      <Details label="Risk rules & sale records">
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-md bg-muted/50 p-3 text-xs">
          <p className="mb-1 font-semibold text-foreground">Automatic risk management (exists)</p>
          <p className="text-muted-foreground">Reduces new entry size below HF 1.30, blocks new entries below 1.15, blocks borrowing and entries when price is stale. Protective stops keep running.</p>
        </div>
        <div className="rounded-md bg-destructive/10 p-3 text-xs">
          <p className="mb-1 font-semibold text-destructive">Automatic liquidation (does NOT exist)</p>
          <p className="text-muted-foreground">There is no autonomous on-chain liquidation engine. Collateral sales are owner-authorized and simulated.</p>
        </div>
      </div>
      {state.sales.length > 0 && (
        <ul className="mt-3 space-y-2 text-xs">
          {state.sales.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/70 p-2">
              <span className="num">{fmtDoge(s.wei, 2)} → {fmtUsd(s.proceedsUsd6)}</span>
              {s.status === 'committed'
                ? <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setMsg(act({ type: 'RECONCILE_SALE', id: s.id }) ?? 'Reconciled; proceeds repay debt; any shortfall is kept as bad debt.')}>Reconcile</Button>
                : <span className="text-muted-foreground">reconciled</span>}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-muted-foreground">Collateral stays locked while debt, a pending sale or bad debt exists. Residual shortfall is recorded, never erased. Currently: {view.collateralLocked ? 'locked' : 'unlocked'} · bad debt {fmtUsd(state.badDebtUsd6)}.</p>
      </Details>
    </Panel>
  );
}

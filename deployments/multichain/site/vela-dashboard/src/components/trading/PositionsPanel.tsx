import { useState } from 'react';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { Button } from '@/components/ui/button';
import { USD_DECIMALS, fmtSignedUsd, fmtUsd, formatByScale, formatUnitsGrouped } from '@/lib/units';
import { tryStopFor, unrealizedUsd6, type Position } from '@/lib/vela/fixtureEngine';
import { useFixture } from '@/state/FixtureProvider';

// Exact bigint → string display; no Number conversion of monetary or quantity values.
const px = (u: bigint) => `${formatUnitsGrouped(u, USD_DECIMALS)}`;
const qty = (p: Position) => formatByScale(p.qty, p.qtyScale);

/** Open positions with $2 fee-inclusive protective stops. Exits stay available when stopped. */
export function PositionsPanel() {
  const { state, act } = useFixture();
  const [err, setErr] = useState<string | null>(null);
  return (
    <Panel eyebrow="Rehearsal scenario" title="Positions & protective stops" aside={<SourceTag kind="fixture" />}>
      {state.positions.length === 0 && <p className="text-sm text-muted-foreground">No open positions.</p>}
      <div className="space-y-3">
        {state.positions.map((p) => {
          const s = tryStopFor(p);
          const u = unrealizedUsd6(p);
          return (
            <div key={p.id} className="rounded-md border border-border/70 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-display font-semibold">{p.symbol} <span className={p.side === 'long' ? 'text-success' : 'text-destructive'}>{p.side.toUpperCase()}</span> <span className="num text-sm text-muted-foreground">{qty(p)}</span></p>
                <span className={`num text-sm ${u >= 0n ? 'text-success' : 'text-destructive'}`}>{fmtSignedUsd(u)} unrealized</span>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
                <span className="text-muted-foreground">Entry <span className="num text-foreground">{px(p.entryPriceUsd6)}</span></span>
                <span className="text-muted-foreground">Mark <span className="num text-foreground">{px(p.markUsd6)}</span></span>
                <span className="text-muted-foreground">Stop {s.ok ? <span className="num text-warning">{px(s.stopPriceUsd6)}</span> : <span className="text-destructive">{s.reason}</span>}</span>
              </div>
              {s.ok && <p className="mt-2 text-xs text-muted-foreground">Stop-trigger budget {fmtUsd(s.budgetUsd6)}, including fees. Slippage or funding can exceed this.</p>}
              <div className="mt-2 flex gap-2">
                <Button size="sm" variant="secondary" onClick={() => setErr(act({ type: 'CLOSE', positionId: p.id, fraction: 'half' }))}>Close half (fixture fill)</Button>
                <Button size="sm" variant="outline" onClick={() => setErr(act({ type: 'CLOSE', positionId: p.id, fraction: 'all' }))}>Close all (fixture fill)</Button>
              </div>
              <Details label="Position details">
                <p className="text-xs text-muted-foreground">Entry fees + funding: <span className="num">{fmtUsd(p.entryFeeUsd6 + p.fundingUsd6)}</span></p>
                {s.ok && <p className="mt-1 text-xs text-muted-foreground">Fee-inclusive loss at trigger {fmtUsd(s.worstLossAtTriggerUsd6, 'up')} ≤ budget {fmtUsd(s.budgetUsd6)}. Stops round inward; this is not a realized-loss guarantee.</p>}
              </Details>
            </div>
          );
        })}
      </div>
      {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
    </Panel>
  );
}

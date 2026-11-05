import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { assessRisk, entryPolicy, fmtBpsPct, fmtBpsRatio } from '@/lib/riskMath';
import { fmtUsd } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';

const SHOCKS = [0, -10, -25, -40, -50, -65];

/** LTV/HF under price shocks, computed from current fixture debt incl. accrued interest. */
export function StressPanel() {
  const { state, view } = useFixture();
  return (
    <Panel eyebrow="Scenarios" title="LTV / HF stress test" aside={<SourceTag kind="fixture" />}>
      <p className="text-sm text-muted-foreground">See how a DOGE price drop affects your loan.</p>
      {view.risk.status === 'unavailable' && <p className="mb-2 text-xs text-destructive">Current price is stale — stress results below use the last quote and are for illustration only.</p>}
      <Details label="Price scenarios">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[440px] text-xs">
          <thead className="text-left text-muted-foreground"><tr><th className="py-1.5 font-medium">DOGE move</th><th>Collateral value</th><th>LTV</th><th>HF</th><th>Entry policy</th></tr></thead>
          <tbody className="num">
            {SHOCKS.map((pct) => {
              const price = (state.quote.usd6PerDoge * BigInt(100 + pct)) / 100n;
              const r = assessRisk({ collateralWei: state.demoCollateralWei, committedToSaleWei: view.committedWei, principalUsd6: view.principalUsd6, accruedInterestUsd6: view.interestUsd6, quote: { ...state.quote, usd6PerDoge: price, observedAt: state.clock }, nowSec: state.clock });
              const p = entryPolicy(r);
              return (
                <tr key={pct} className="border-t border-border/60">
                  <td className="py-1.5">{pct === 0 ? 'current' : `${pct}%`}</td>
                  <td>{'collateralValueUsd6' in r ? fmtUsd(r.collateralValueUsd6) : '—'}</td>
                  <td>{'ltvBps' in r ? fmtBpsPct(r.ltvBps) : r.status === 'no-debt' ? '0.00%' : '—'}</td>
                  <td className={r.status === 'threshold-breach' ? 'text-destructive' : ''}>{'hfBps' in r ? fmtBpsRatio(r.hfBps) : r.status === 'no-debt' ? 'N/A' : '—'}</td>
                  <td className={`font-sans ${p.decision === 'allowed' ? 'text-success' : p.decision === 'reduced' ? 'text-warning' : 'text-destructive'}`}>{p.decision}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">A threshold breach (HF &lt; 1.00) is shown separately from an executed liquidation. No autonomous on-chain liquidation engine exists.</p>
      </Details>
    </Panel>
  );
}

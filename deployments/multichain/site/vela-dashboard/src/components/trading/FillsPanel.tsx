import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { netOf } from '@/lib/sweepLedger';
import { fmtSignedUsd, fmtUsd } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';

const t = (s: number) => new Date(s * 1000).toISOString().slice(0, 16).replace('T', ' ');

/** Realized fills: gross, fees, funding and net — the only input to profit sweeps. */
export function FillsPanel() {
  const { state, view } = useFixture();
  return (
    <Panel eyebrow="Realized" title="Fixture fills" aside={<SourceTag kind="fixture" />}>
      <div className="mb-3 grid grid-cols-3 gap-2 text-xs">
        <div><p className="eyebrow">Today UTC</p><p className="num text-sm">{fmtSignedUsd(view.todayRealized)}</p></div>
        <div><p className="eyebrow">All-time</p><p className="num text-sm">{fmtSignedUsd(view.allTimeRealized)}</p></div>
        <div><p className="eyebrow">All-time fees</p><p className="num text-sm">{fmtUsd(view.feesPaid)}</p></div>
      </div>
      <Details label="Fill history"><div className="overflow-x-auto">
        <table className="w-full min-w-[480px] text-xs">
          <thead className="text-left text-muted-foreground"><tr><th className="py-1.5 font-medium">Closed (UTC)</th><th>Gross</th><th>Entry fee</th><th>Exit fee</th><th>Funding</th><th className="text-right">Net</th></tr></thead>
          <tbody className="num">
            {state.fills.map((f) => {
              const n = netOf(f);
              return (
                <tr key={f.fillId} className="border-t border-border/60">
                  <td className="py-1.5">{t(f.closedAt)}</td><td>{fmtSignedUsd(f.grossPnlUsd6)}</td><td>{fmtUsd(f.entryFeeUsd6)}</td>
                  <td>{fmtUsd(f.exitFeeUsd6)}</td><td>{fmtUsd(f.fundingUsd6)}</td>
                  <td className={`text-right ${n >= 0n ? 'text-success' : 'text-destructive'}`}>{fmtSignedUsd(n)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div></Details>
    </Panel>
  );
}

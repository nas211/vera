import { Panel } from '@/components/common/Panel';
import { SourceTag } from '@/components/common/SourceTag';
import { USD_DECIMALS, fmtUsd } from '@/lib/units';
import type { SimulatedRepaymentReceipt } from '@/lib/vela/types';
import { useFixture } from '@/state/FixtureProvider';

const t = (s: number) => new Date(s * 1000).toISOString().slice(0, 16).replace('T', ' ');
const u = (x: { units: string }) => BigInt(x.units);

/** Simulated demo-loan repayment receipts — a different record type from reward claims. */
export function RepaymentHistory() {
  const { state } = useFixture();
  const rows = state.receipts.filter((r): r is SimulatedRepaymentReceipt => r.kind === 'demo-loan-repayment');
  return (
    <Panel eyebrow="Simulated loan repayment receipts" title="Repayment history" aside={<><SourceTag kind="demo" /><SourceTag kind="fixture" /></>}>
      {rows.length === 0 ? <p className="text-sm text-muted-foreground">No repayments recorded.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-xs">
            <thead className="text-left text-muted-foreground"><tr><th className="py-1.5 font-medium">Time (UTC)</th><th>Trigger</th><th>Debited</th><th>To interest</th><th>To principal</th><th>Max auth.</th><th>Ledger ref</th></tr></thead>
            <tbody className="num">
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-border/60" title={`exact ${r.amount.units} µUSD (${USD_DECIMALS} dec)`}>
                  <td className="py-1.5">{t(r.at)}</td><td className="font-sans">{r.trigger}</td><td>{fmtUsd(u(r.amount))}</td>
                  <td>{fmtUsd(u(r.toInterest))}</td><td>{fmtUsd(u(r.toPrincipal))}</td>
                  <td>{r.maxAllowance ? fmtUsd(u(r.maxAllowance)) : '—'}</td><td className="text-muted-foreground">{r.ledgerRef ?? 'none (fixture)'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

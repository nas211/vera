import { BatteryWarning } from 'lucide-react';
import { Panel } from '@/components/common/Panel';
import { Metric, Row } from '@/components/common/Metric';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { fmtDoge } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';

export const fmtRunway = (s: bigint | null) => {
  if (s === null) return 'n/a (no principal)';
  if (s === 0n) return 'exhausted';
  const d = Number(s / 86_400n);
  return d >= 1 ? `${d} days` : `${Number(s / 3600n)} hours`;
};

/** Principal vs reward funds, APR source and funding runway. */
export function RewardTermsPanel() {
  const { state, view } = useFixture();
  const v = view.vaultNow;
  const empty = view.reserveAvailable === 0n && v.totalPrincipal > 0n;
  return (
    <Panel eyebrow="Reward terms" title="Vault rewards" aside={<SourceTag kind="fixture" />}>
      <div className="grid grid-cols-2 gap-5">
        <Metric label="Your principal" value={fmtDoge(view.user.principal, 2)} />
        <Metric label="Simple APR" value="5%" sub="Project-funded test rewards" />
      </div>
      <p className="mt-3 text-xs text-muted-foreground">No compounding or production yield promised.</p>
      {empty && (
        <div className="mb-3 flex gap-2 rounded-md bg-warning/10 p-2.5 text-xs text-warning">
          <BatteryWarning className="h-4 w-4 shrink-0" />
          Reward reserve exhausted — accrual is paused. A future top-up resumes accrual from that moment only; unfunded time is never backfilled.
        </div>
      )}
      <Details label="Reserve & reward terms">
      <div className="grid gap-x-6 sm:grid-cols-2">
        <div>
          <p className="eyebrow mt-1">Collateral principal (yours to withdraw)</p>
          <Row k="Vault total principal" v={fmtDoge(v.principalBalance, 2)} exact={v.principalBalance.toString()} />
        </div>
        <div>
          <p className="eyebrow mt-1">Reward reserve (separate funds)</p>
          <Row k="Reserve balance" v={fmtDoge(v.reserveBalance)} exact={v.reserveBalance.toString()} />
          <Row k="Reserved for accrued rewards" v={fmtDoge(v.liabilities)} exact={v.liabilities.toString()} />
          <Row k="Unallocated" v={fmtDoge(view.reserveAvailable)} tone={empty ? 'negative' : 'default'} />
          <Row k="Funding runway at 5%" v={fmtRunway(view.runway)} tone={empty ? 'negative' : 'default'} />
        </div>
      </div>
      <p className="mt-3 text-[11px] text-muted-foreground">
        DOGE itself is not a staking-yield source. Rewards use a separate faucet-funded reserve — never principal, new deposits or borrowed funds. Total funded {fmtDoge(v.totalFunded, 2)}; total claimed {fmtDoge(v.totalClaimed)}. Fixture vault id: {state.vault.vaultId}.
      </p>
      </Details>
    </Panel>
  );
}

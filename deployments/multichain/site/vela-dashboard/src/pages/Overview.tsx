import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { Metric } from '@/components/common/Metric';
import { SourceTag } from '@/components/common/SourceTag';
import { WalletCard } from '@/components/overview/WalletCard';
import { PlaceholderPanel } from '@/components/overview/PlaceholderPanel';
import { RiskSummary } from '@/components/risk/RiskSummary';
import { TradingStatusCard } from '@/components/trading/TradingStatusCard';
import { fmtDoge, fmtSignedUsd, fmtUsd } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';

const tone = (v: bigint) => (v > 0n ? 'positive' : v < 0n ? 'negative' : 'muted');

export default function Overview() {
  const { state, view } = useFixture();
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <div className="space-y-4 lg:col-span-8">
        <Panel eyebrow="Reward vault candidate" title="Collateral & rewards" aside={<><SourceTag kind="testnet" /><SourceTag kind="fixture" /></>}>
          <div className="grid grid-cols-2 gap-5">
            <Metric label="Your vault principal" value={fmtDoge(view.user.principal, 2)} exact={view.user.principal.toString()} />
            <Metric label="Earned, claimable" value={fmtDoge(view.claimable)} tone="positive" exact={view.claimable.toString()} />
          </div>
          <Details label="Reward details"><div className="grid grid-cols-2 gap-5">
            <Metric label="Reward reserve (separate)" value={fmtDoge(view.vaultNow.reserveBalance, 2)} sub={`${fmtDoge(view.reserveAvailable, 2)} unallocated`} />
            <Metric label="Claimed (fixture)" value={fmtDoge(view.user.claimed)} sub="no on-chain claims yet" />
          </div></Details>
        </Panel>
        <Panel eyebrow="Demo lending" title="Debt & risk" aside={<><SourceTag kind="demo" /><SourceTag kind="fixture" /></>}>
          <div className="grid gap-5 sm:grid-cols-2">
            <div className="grid grid-cols-2 gap-4">
              <Metric label="Current debt" value={fmtUsd(view.debtUsd6)} exact={`${view.debtUsd6} µUSD`} sub={state.loan ? 'principal + accrued interest' : 'no active loan'} />
              <Metric label="Demo collateral" value={fmtDoge(state.demoCollateralWei, 0)} sub={view.collateralLocked ? 'locked to loan' : 'unlocked'} />
            </div>
            <RiskSummary risk={view.risk} />
          </div>
        </Panel>
        <Panel eyebrow="Paper account" title="Trading P&L" aside={<SourceTag kind="fixture" />}>
          <div className="grid grid-cols-2 gap-5">
            <Metric label="Today (UTC) realized" value={fmtSignedUsd(view.todayRealized)} tone={tone(view.todayRealized)} sub="net of fees & funding" />
            <Metric label="Unrealized" value={fmtSignedUsd(view.unrealized)} tone={tone(view.unrealized)} sub="not sweepable" />
          </div>
          <Details label="All-time results"><div className="grid grid-cols-2 gap-5">
            <Metric label="All-time realized" value={fmtSignedUsd(view.allTimeRealized)} tone={tone(view.allTimeRealized)} />
            <Metric label="All-time fees + funding" value={fmtUsd(view.feesPaid + view.fundingPaid)} tone="muted" />
          </div></Details>
        </Panel>
      </div>
      <div className="space-y-4 lg:col-span-4">
        <TradingStatusCard compact />
        <WalletCard />
        <PlaceholderPanel />
      </div>
    </div>
  );
}

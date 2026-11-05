import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { Row } from '@/components/common/Metric';
import { SourceTag } from '@/components/common/SourceTag';
import { RISK_PARAMS, fmtBpsPct } from '@/lib/riskMath';
import { fmtDoge, fmtUsd } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';
import { RiskSummary } from '@/components/risk/RiskSummary';

export function LoanStatusPanel() {
  const { state, view } = useFixture();
  const stale = view.capacity === null;
  return (
    <Panel eyebrow="DEMO loan" title={state.loan ? 'Your loan' : 'No active loan'} aside={<SourceTag kind="demo" />}>
      <RiskSummary risk={view.risk} />
      <div className="mt-4">
        <Row k="Current debt" v={fmtUsd(view.debtUsd6)} exact={`${view.debtUsd6} µUSD`} />
        <Row k="Available to borrow" v={stale ? 'unavailable' : fmtUsd(view.capacity!, 'down')} tone={stale ? 'negative' : 'positive'} />
        {state.badDebtUsd6 > 0n && <Row k="Bad debt recorded" v={fmtUsd(state.badDebtUsd6)} tone="negative" />}
      </div>
      <Details label="Loan breakdown">
        <Row k="Loan ID" v={state.loan?.loanId ?? '—'} />
        <Row k="Principal" v={fmtUsd(view.principalUsd6)} exact={`${view.principalUsd6} µUSD`} />
        <Row k="Accrued interest (5% simple)" v={fmtUsd(view.interestUsd6)} exact={`${view.interestUsd6} µUSD`} />
        <Row k="Eligible demo collateral" v={fmtDoge(state.demoCollateralWei, 2)} />
        <Row k="DOGE price (fixture)" v={`$${(Number(state.quote.usd6PerDoge) / 1e6).toFixed(6)}`} />
        <Row k="Quote age" v={Number.isFinite(view.quoteAgeSec) ? `${view.quoteAgeSec}s` : '—'} tone={stale ? 'negative' : 'default'} />
        <Row k={`Borrow capacity (max ${fmtBpsPct(RISK_PARAMS.maxOriginationLtvBps)} LTV)`} v={stale ? 'unavailable' : fmtUsd(view.capacity!, 'down')} tone={stale ? 'negative' : 'positive'} />
        <Row k="Bad debt recorded" v={fmtUsd(state.badDebtUsd6)} tone={state.badDebtUsd6 > 0n ? 'negative' : 'muted'} />
      <p className="mt-3 text-[11px] text-muted-foreground">Illustrative research parameters: 40% max origination LTV, 65% liquidation threshold, 5% annual simple interest. Not validated production risk settings.</p>
      </Details>
    </Panel>
  );
}

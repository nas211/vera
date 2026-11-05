import { useState } from 'react';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { Row } from '@/components/common/Metric';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { fullRepayAllowance } from '@/lib/repayment';
import { USD_DECIMALS, ceilToCents, fmtUsd, minBig, usdPrefill, validateAmount } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';

export function RepayPanel() {
  const { state, view, act } = useFixture();
  const [amt, setAmt] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const noLoan = !state.loan;
  // Shared exact-unit spendable cash (excludes open-position margin, pending orders, costs) — same as the engine.
  const spendable = view.spendableCashUsd6;
  const maxPartial = minBig(ceilToCents(view.debtUsd6), spendable);
  const rules = { inputDecimals: 2, decimals: USD_DECIMALS, max: maxPartial, maxLabel: 'debt or spendable demo cash' };
  const parsed = validateAmount(amt, rules);
  const b = fullRepayAllowance(view.principalUsd6, view.interestUsd6);
  const fullBlocked = noLoan ? 'No active demo loan.' : spendable < b.debtUsd6 ? `Insufficient demo cash: need ${fmtUsd(b.debtUsd6, 'up')}, spendable ${fmtUsd(spendable, 'down')} after open-position margin, pending orders and costs.` : null;

  const run = (a: Parameters<typeof act>[0], ok: string) => { const e = act(a); setMsg(e ?? ok); if (!e) setAmt(''); };

  return (
    <Panel eyebrow="Simulated USD" title="Repay">
      <AmountField label="Partial repayment" unit="USD" value={amt} onChange={setAmt} rules={rules}
        onMax={maxPartial > 0n ? () => setAmt(usdPrefill(maxPartial).text) : undefined} hint="Applied to accrued interest first, then principal." />
      <GatedButton variant="secondary" reason={noLoan ? 'No active demo loan.' : parsed.ok ? null : 'Enter a valid amount.'}
        onClick={() => parsed.ok && run({ type: 'REPAY_PARTIAL', usd6: parsed.value }, 'Fixture partial repayment recorded.')}>Repay partial (fixture)</GatedButton>

      <div className="mt-5 rounded-md border border-border/70 p-3">
        <p className="eyebrow mb-1">Full repayment authorization</p>
        <Row k="Current debt" v={fmtUsd(b.debtUsd6)} exact={`${b.debtUsd6} µUSD`} />
        <Row k="Maximum authorization" v={fmtUsd(b.maxAllowanceUsd6)} tone="warning" exact={`${b.maxAllowanceUsd6} µUSD`} />
        <p className="mt-2 text-xs text-muted-foreground">Only the actual debt is debited. Unused allowance stays in your demo cash.</p>
        <Details label="How this amount is calculated">
        <Row k="Principal" v={fmtUsd(b.principalUsd6)} exact={`${b.principalUsd6} µUSD`} />
        <Row k="Accrued interest" v={fmtUsd(b.interestUsd6)} exact={`${b.interestUsd6} µUSD`} />
        <Row k="Current debt (displayed)" v={fmtUsd(b.debtUsd6)} exact={`${b.debtUsd6} µUSD`} />
        <Row k="Rounded up to cents" v={fmtUsd(b.roundedUpUsd6)} />
        <Row k="+ confirmation-time interest buffer" v={fmtUsd(b.bufferUsd6)} />
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          Why can {fmtUsd(b.debtUsd6)} need {fmtUsd(b.maxAllowanceUsd6)}? The exact debt is rounded UP to whole cents, then $0.01 is added for interest that accrues before confirmation. Only the actual debt at confirmation is debited; any unused buffer stays in your demo cash.
        </p>
        </Details>
        <GatedButton className="mt-2" reason={fullBlocked} onClick={() => run({ type: 'REPAY_FULL', maxAllowanceUsd6: b.maxAllowanceUsd6 }, 'Fixture full repayment recorded; actual debt debited, unused buffer kept.')}>
          Repay in full (fixture)
        </GatedButton>
      </div>
      {msg && <p className="mt-2 text-xs text-fixture">{msg}</p>}
    </Panel>
  );
}

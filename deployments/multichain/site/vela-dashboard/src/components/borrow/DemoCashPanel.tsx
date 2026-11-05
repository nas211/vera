import { useState } from 'react';
import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { Row } from '@/components/common/Metric';
import { AmountField } from '@/components/common/AmountField';
import { GatedButton } from '@/components/common/GatedButton';
import { USD_DECIMALS, fmtUsd, formatUnits, usdPrefill, validateAmount } from '@/lib/units';
import { useFixture } from '@/state/FixtureProvider';

/**
 * Demo cash withdrawal (regression 7): prefill rounds DOWN to cents; ledger precision kept.
 * Limit = shared exact-unit spendable cash (same definition the engine enforces).
 */
export function DemoCashPanel() {
  const { state, view, act } = useFixture();
  const [amt, setAmt] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const cash = state.paper.cashUsd6;
  const spendable = view.spendableCashUsd6;
  const pre = usdPrefill(spendable);
  const rules = { inputDecimals: 2, decimals: USD_DECIMALS, max: spendable, maxLabel: 'spendable demo cash' };
  const parsed = validateAmount(amt, rules);
  return (
    <Panel eyebrow="Paper account" title="Demo cash">
      <Row k="Available to withdraw" v={fmtUsd(spendable, 'down')} exact={`${spendable} µUSD`} />
      <div className="mt-3">
        <AmountField label="Withdraw demo cash" unit="USD" value={amt} onChange={setAmt} rules={rules} onMax={() => setAmt(pre.text)}
          hint={pre.remainder > 0n ? `Max prefills ${pre.text}; the fractional-cent remainder of ${formatUnits(pre.remainder, USD_DECIMALS)} USD stays in the ledger.` : undefined} />
      </div>
      <GatedButton variant="secondary" reason={parsed.ok ? null : 'Enter a valid amount.'}
        onClick={() => { if (parsed.ok) { const e = act({ type: 'WITHDRAW_CASH', usd6: parsed.value }); setMsg(e ?? 'Fixture demo withdrawal recorded.'); if (!e) setAmt(''); } }}>
        Withdraw demo cash (fixture)
      </GatedButton>
      {msg && <p className="mt-2 text-xs text-fixture">{msg}</p>}
      <Details label="Balance details">
        <Row k="Reserved (margin, orders, losses, costs)" v={fmtUsd(view.reservedCashUsd6, 'up')} exact={`${view.reservedCashUsd6} µUSD`} tone="muted" />
        <Row k="Exact ledger value" v={`${formatUnits(cash, USD_DECIMALS)} USD`} tone="muted" />
      </Details>
    </Panel>
  );
}

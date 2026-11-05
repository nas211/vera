import { describe, expect, it } from 'vitest';
import { computeStop, lossAtTrigger, reconcileStop, STOP_BUDGET_USD6, type StopInput } from '../stopBudget';

const long: StopInput = {
  side: 'long', entryPriceUsd6: 60_000_000_000n, qty: 50_000n, qtyScale: 100_000_000n, tickUsd6: 500_000n,
  entryFeeUsd6: 15_000n, exitFeeBps: 5n, observedFundingUsd6: 4_000n, realizedLossFromPartialsUsd6: 0n,
};
const short: StopInput = {
  side: 'short', entryPriceUsd6: 175_000n, qty: 150n * 10n ** 18n, qtyScale: 10n ** 18n, tickUsd6: 10n,
  entryFeeUsd6: 13_125n, exitFeeBps: 5n, observedFundingUsd6: 1_000n, realizedLossFromPartialsUsd6: 0n,
};

describe('$2 fee-inclusive stop-trigger budget', () => {
  for (const [name, i] of [['long', long], ['short', short]] as const) {
    it(`${name}: stop within budget, on tick, rounded inward`, () => {
      const r = computeStop(i);
      if (!r.ok) throw new Error(r.reason);
      expect(r.worstLossAtTriggerUsd6 <= STOP_BUDGET_USD6).toBe(true);
      expect(r.stopPriceUsd6 % i.tickUsd6).toBe(0n);
      const outward = i.side === 'long' ? r.stopPriceUsd6 - i.tickUsd6 : r.stopPriceUsd6 + i.tickUsd6;
      expect(lossAtTrigger(i, outward) > STOP_BUDGET_USD6).toBe(true);
      if (i.side === 'long') expect(r.stopPriceUsd6 < i.entryPriceUsd6).toBe(true);
      else expect(r.stopPriceUsd6 > i.entryPriceUsd6).toBe(true);
    });
  }
  it('stricter existing limit wins; larger limits cannot raise it', () => {
    const strict = computeStop({ ...long, existingLimitUsd6: 1_000_000n });
    const loose = computeStop({ ...long, existingLimitUsd6: 50_000_000n });
    if (!strict.ok || !loose.ok) throw new Error('expected ok');
    expect(strict.budgetUsd6).toBe(1_000_000n);
    expect(loose.budgetUsd6).toBe(STOP_BUDGET_USD6);
    expect(strict.stopPriceUsd6 > loose.stopPriceUsd6).toBe(true);
  });
  it('partial-exit losses tighten the remaining stop (long and short)', () => {
    const a = computeStop(long); const b = computeStop({ ...long, realizedLossFromPartialsUsd6: 1_500_000n });
    const c = computeStop(short); const d = computeStop({ ...short, realizedLossFromPartialsUsd6: 1_500_000n });
    if (!a.ok || !b.ok || !c.ok || !d.ok) throw new Error('expected ok');
    expect(b.stopPriceUsd6 > a.stopPriceUsd6).toBe(true);
    expect(d.stopPriceUsd6 < c.stopPriceUsd6).toBe(true);
  });
  it('fails closed when fees/funding consume the budget', () => {
    expect(computeStop({ ...long, observedFundingUsd6: 2_000_000n }).ok).toBe(false);
  });
  it('reconciliation keeps the stricter existing stop', () => {
    const r = computeStop(long); if (!r.ok) throw new Error();
    const tighter = r.stopPriceUsd6 + 1_000_000n;
    const rec = reconcileStop(long, tighter);
    if (!rec.ok) throw new Error();
    expect(rec.stopPriceUsd6).toBe(tighter);
    const looser = reconcileStop(long, r.stopPriceUsd6 - 1_000_000n);
    if (!looser.ok) throw new Error();
    expect(looser.stopPriceUsd6).toBe(r.stopPriceUsd6);
  });
});

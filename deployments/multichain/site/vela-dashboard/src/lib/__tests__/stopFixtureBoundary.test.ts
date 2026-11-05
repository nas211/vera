/**
 * Regression: /trading crashed with "Cannot mix BigInt and other types" because
 * stopInputFor() sent `realizedLossPartialsUsd6` while StopInput expects
 * `realizedLossFromPartialsUsd6`, so computeStop received `undefined`.
 */
import { describe, expect, it } from 'vitest';
import { computeStop, lossAtTrigger, normalizeStopInput, STOP_BUDGET_USD6 } from '../stopBudget';
import { initialFixture, reduce, stopFor, stopInputFor, tryStopFor } from '../vela/fixtureEngine';

describe('Position → StopInput boundary', () => {
  it('maps every field to a bigint (no undefined keys)', () => {
    for (const p of initialFixture().positions) {
      const i = stopInputFor(p);
      expect(typeof i.realizedLossFromPartialsUsd6).toBe('bigint');
      expect(typeof i.observedFundingUsd6).toBe('bigint');
      expect(i.realizedLossFromPartialsUsd6).toBe(p.realizedLossPartialsUsd6);
      expect(i.observedFundingUsd6).toBe(p.fundingUsd6);
    }
  });

  it('computes a valid $2 stop for the fixture long AND short', () => {
    const [long, short] = initialFixture().positions;
    for (const p of [long, short]) {
      const r = stopFor(p);
      if (!r.ok) throw new Error(r.reason);
      expect(r.worstLossAtTriggerUsd6 <= STOP_BUDGET_USD6).toBe(true);
      expect(r.stopPriceUsd6 % p.tickUsd6).toBe(0n);
      // inward rounding: one tick outward breaches the budget
      const outward = p.side === 'long' ? r.stopPriceUsd6 - p.tickUsd6 : r.stopPriceUsd6 + p.tickUsd6;
      expect(lossAtTrigger(stopInputFor(p), outward) > STOP_BUDGET_USD6).toBe(true);
    }
  });

  it('carries partial-exit losses into the remaining stop (short, losing) and keeps the long valid', () => {
    let s = initialFixture();
    s = reduce(s, { type: 'CLOSE', positionId: 'p-doge', fraction: 'half' });
    s = reduce(s, { type: 'CLOSE', positionId: 'p-btc', fraction: 'half' });
    const doge = s.positions.find((p) => p.id === 'p-doge')!;
    expect(doge.realizedLossPartialsUsd6 > 0n).toBe(true);
    const i = stopInputFor(doge);
    expect(i.realizedLossFromPartialsUsd6).toBe(doge.realizedLossPartialsUsd6);
    for (const p of s.positions) {
      const r = stopFor(p);
      if (!r.ok) throw new Error(r.reason);
      expect(r.worstLossAtTriggerUsd6 <= STOP_BUDGET_USD6).toBe(true);
    }
  });

  it('a stricter existing limit still wins after the boundary', () => {
    const p = initialFixture().positions[0];
    const r = computeStop({ ...stopInputFor(p), existingLimitUsd6: 1_000_000n });
    if (!r.ok) throw new Error(r.reason);
    expect(r.budgetUsd6).toBe(1_000_000n);
  });

  it('stricter existing limit tightens the SHORT stop; looser limits cannot raise $2', () => {
    const short = initialFixture().positions.find((p) => p.side === 'short')!;
    const base = computeStop(stopInputFor(short));
    const strict = computeStop({ ...stopInputFor(short), existingLimitUsd6: 1_000_000n });
    const loose = computeStop({ ...stopInputFor(short), existingLimitUsd6: 9_000_000n });
    if (!base.ok || !strict.ok || !loose.ok) throw new Error('expected ok');
    expect(strict.budgetUsd6).toBe(1_000_000n);
    expect(strict.worstLossAtTriggerUsd6 <= 1_000_000n).toBe(true);
    expect(strict.stopPriceUsd6 < base.stopPriceUsd6).toBe(true); // closer to entry for a short
    expect(loose.budgetUsd6).toBe(STOP_BUDGET_USD6);
    expect(loose.stopPriceUsd6).toBe(base.stopPriceUsd6);
  });

  it('a zero existing limit fails closed for long and short (no stop invented)', () => {
    for (const p of initialFixture().positions) {
      expect(computeStop({ ...stopInputFor(p), existingLimitUsd6: 0n }).ok).toBe(false);
    }
  });

  it('losing partial exit on the LONG is carried into its remaining stop', () => {
    let s = initialFixture();
    s = { ...s, positions: s.positions.map((p) => (p.id === 'p-btc' ? { ...p, markUsd6: 59_990_000_000n } : p)) };
    s = reduce(s, { type: 'CLOSE', positionId: 'p-btc', fraction: 'half' });
    const btc = s.positions.find((p) => p.id === 'p-btc')!;
    expect(btc.realizedLossPartialsUsd6 > 0n).toBe(true);
    const withLoss = stopFor(btc);
    const withoutLoss = computeStop({ ...stopInputFor(btc), realizedLossFromPartialsUsd6: 0n });
    if (!withLoss.ok || !withoutLoss.ok) throw new Error('expected ok');
    expect(withLoss.stopPriceUsd6 > withoutLoss.stopPriceUsd6).toBe(true); // tighter (higher) for a long
    expect(withLoss.worstLossAtTriggerUsd6 <= STOP_BUDGET_USD6).toBe(true);
  });

  it('losing partial exit on the SHORT is carried into its remaining stop', () => {
    const s = reduce(initialFixture(), { type: 'CLOSE', positionId: 'p-doge', fraction: 'half' });
    const doge = s.positions.find((p) => p.id === 'p-doge')!;
    const withLoss = stopFor(doge);
    const withoutLoss = computeStop({ ...stopInputFor(doge), realizedLossFromPartialsUsd6: 0n });
    if (!withLoss.ok || !withoutLoss.ok) throw new Error('expected ok');
    expect(withLoss.stopPriceUsd6 < withoutLoss.stopPriceUsd6).toBe(true); // tighter (lower) for a short
    expect(withLoss.worstLossAtTriggerUsd6 <= STOP_BUDGET_USD6).toBe(true);
  });
});

describe('normalizeStopInput rejects invalid / unsafe values', () => {
  const good = stopInputFor(initialFixture().positions[0]);
  it.each([
    ['missing field', { realizedLossFromPartialsUsd6: undefined }],
    ['fractional number', { entryFeeUsd6: 0.5 }],
    ['unsafe number', { qty: 2 ** 60 }],
    ['decimal string', { tickUsd6: '0.5' }],
    ['NaN', { entryPriceUsd6: Number.NaN }],
    ['negative partial loss', { realizedLossFromPartialsUsd6: -1n }],
    ['negative entry fee', { entryFeeUsd6: -1n }],
    ['negative qty', { qty: -1n }],
    ['zero qtyScale', { qtyScale: 0n }],
  ])('%s', (_n, patch) => {
    expect(() => normalizeStopInput({ ...good, ...patch } as never)).toThrow();
  });
  it('accepts exact integer strings and safe integers without precision loss', () => {
    const n = normalizeStopInput({ ...good, qty: '50000', entryFeeUsd6: 15_000 } as never);
    expect(n.qty).toBe(50_000n);
    expect(n.entryFeeUsd6).toBe(15_000n);
  });
  it('tryStopFor surfaces the real error instead of a zero stop', () => {
    const bad = { ...initialFixture().positions[0], entryFeeUsd6: 0.5 as unknown as bigint };
    const r = tryStopFor(bad);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toContain('entryFeeUsd6');
  });
});

import { describe, expect, it } from 'vitest';
import { initialFixture, reduce, paperCashFor, reconcilePosition, stopFor, type Position } from '../vela/fixtureEngine';
import { lossAtTrigger } from '../stopBudget';
import { stopInputFor } from '../vela/fixtureEngine';
import { deriveView } from '../../state/selectors';
import * as SL from '../sweepLedger';

const entry = (side: 'long' | 'short' = 'long'): Position => ({
  id: 'new', symbol: 'ETH-PERP', side, entryPriceUsd6: 3_000_000_000n, markUsd6: 3_000_000_000n,
  qty: 100_001n, qtyScale: 100_000_000n, tickUsd6: 10_000n, entryFeeUsd6: 1501n,
  fundingUsd6: 0n, realizedLossPartialsUsd6: 0n, openedAt: 0,
});

describe.each(['long', 'short'] as const)('financial boundary: %s', side => {
  it('releases only exact position margin over odd partial quantities, conserving all reserves', () => {
    const base = initialFixture();
    let s = reduce(base, { type: 'OPEN_POSITION', position: entry(side), marginUsd6: 1_000_001n });
    for (let i = 0; i < 3; i++) {
      const p = s.positions.find(p => p.id === 'new')!;
      const release = p.marginUsd6! * (p.qty / 2n) / p.qty;
      const before = s.paper.openMarginUsd6;
      s = reduce(s, { type: 'CLOSE', positionId: 'new', fraction: 'half' });
      expect(s.paper.openMarginUsd6).toBe(before - release);
      expect(s.paper.openMarginUsd6).toBe(s.positions.reduce((a, p) => a + p.marginUsd6!, 0n));
      expect(s.positions.filter(p => p.id !== 'new')).toEqual(base.positions);
    }
    s = reduce(s, { type: 'CLOSE', positionId: 'new', fraction: 'all' });
    expect(s.paper.openMarginUsd6).toBe(base.paper.openMarginUsd6);
  });

  it('preserves stricter account limits and existing stops through entry and partial close', () => {
    const p = { ...entry(side), existingLimitUsd6: 750_000n,
      stopPriceUsd6: side === 'long' ? 2_800_000_001n : 3_199_999_999n };
    let s = reduce(initialFixture(), { type: 'OPEN_POSITION', position: p, marginUsd6: 1_000_000n });
    const first = s.positions.find(p => p.id === 'new')!;
    expect(first.stopPriceUsd6! % first.tickUsd6).toBe(0n);
    s = reduce(s, { type: 'CLOSE', positionId: 'new', fraction: 'half' });
    const rest = s.positions.find(p => p.id === 'new')!;
    const result = stopFor(rest);
    if (!result.ok) throw new Error(result.reason);
    expect(result.budgetUsd6).toBe(750_000n);
    expect(result.worstLossAtTriggerUsd6 <= 750_000n).toBe(true);
    expect(side === 'long' ? rest.stopPriceUsd6! >= first.stopPriceUsd6! : rest.stopPriceUsd6! <= first.stopPriceUsd6!).toBe(true);
  });

  it('reconciles existing positions and caps every larger override at two dollars', () => {
    for (const limit of [500_000n, 2_000_000n, 20_000_000n]) {
      const p = reconcilePosition({ ...entry(side), existingLimitUsd6: limit });
      expect(lossAtTrigger(stopInputFor(p), p.stopPriceUsd6!) <= (limit < 2_000_000n ? limit : 2_000_000n)).toBe(true);
      expect(reconcilePosition(p)).toEqual(p);
    }
  });

  it('rounds fractional partial-exit losses against the remaining stop budget', () => {
    let s = reduce(initialFixture(), { type: 'OPEN_POSITION', position: entry(side), marginUsd6: 1_000_000n });
    s = { ...s, positions: s.positions.map(p => p.id === 'new' ? { ...p, markUsd6: p.entryPriceUsd6 + (side === 'long' ? -1n : 1n) } : p) };
    s = reduce(s, { type: 'CLOSE', positionId: 'new', fraction: 'half' });
    expect(s.fills[0].grossPnlUsd6).toBe(-1n);
    const remaining = s.positions.find(p => p.id === 'new')!;
    expect(remaining.realizedLossPartialsUsd6).toBe(-SL.netOf(s.fills[0]));
    const stop = stopFor(remaining);
    expect(stop.ok && stop.worstLossAtTriggerUsd6 <= 2_000_000n).toBe(true);
  });

  it('rejects zero-quantity half closes without changing state', () => {
    const s = reduce(initialFixture(), { type: 'OPEN_POSITION', position: { ...entry(side), qty: 1n }, marginUsd6: 1n });
    const before = structuredClone(s);
    expect(() => reduce(s, { type: 'CLOSE', positionId: 'new', fraction: 'half' })).toThrow('quantity');
    expect(s).toEqual(before);
  });

  it('fails closed in reduced-risk mode while allowing protective closes', () => {
    const s = reduce(initialFixture(), { type: 'SET_PRICE', usd6: 75_000n });
    expect(() => reduce(s, { type: 'OPEN_POSITION', position: entry(side), marginUsd6: 1_000_000n })).toThrow('Reduced-risk');
    expect(reduce(s, { type: 'CLOSE', positionId: 'p-doge', fraction: 'half' }).positions[1].qty < s.positions[1].qty).toBe(true);
  });
});

it('reserves all open losses and closing costs across withdrawals, repayment, entry, sweep and UI', () => {
  const initial = initialFixture();
  const s = { ...initial, positions: initial.positions.map(p => p.id === 'p-doge' ? { ...p, markUsd6: 675_000n } : p) };
  const before = structuredClone(s);
  expect(paperCashFor(s).unrealizedLossUsd6).toBe(75_099_350n);
  expect(deriveView(s).spendableCashUsd6).toBe(12_160_649n);
  expect(() => reduce(s, { type: 'WITHDRAW_CASH', usd6: 50_000_000n })).toThrow('Insufficient');
  expect(() => reduce(s, { type: 'REPAY_PARTIAL', usd6: 50_000_000n })).toThrow('Insufficient');
  expect(() => reduce(s, { type: 'OPEN_POSITION', position: entry(), marginUsd6: 50_000_000n })).toThrow('Insufficient');
  const gain = { ...s, positions: s.positions.map(p => p.id === 'p-btc' ? { ...p, markUsd6: 1_000_000_000_000n } : p) };
  expect(deriveView(gain).spendableCashUsd6 <= deriveView(s).spendableCashUsd6).toBe(true);
  expect(s).toEqual(before);
  const fill = { fillId: 'large-profit', closedAt: s.clock, grossPnlUsd6: 100_000_000n, entryFeeUsd6: 0n, exitFeeUsd6: 0n, fundingUsd6: 0n };
  const authorized = reduce(s, { type: 'GRANT_SWEEP', percent: 100, days: 7, floorUsd6: 1_000_000n });
  const swept = reduce({ ...authorized, sweep: SL.applyFill(authorized.sweep, fill) }, { type: 'RUN_SWEEP' });
  expect(s.paper.cashUsd6 - swept.paper.cashUsd6).toBe(11_160_649n);
});

it('suspends carried sweeps after losses, rejects stale quotes and pays once after recovery', () => {
  const p: SL.SweepPermission = { id: 'consent', loanId: 'L', percent: 100, expiresAt: 86400, floorUsd6: 0n, createdAt: 0, revokedAt: null };
  const loan = { principalUsd6: 400_000_000n, interestUsd6: 500_000n };
  const cash = { availableUsd6: 100_000_000n, openMarginUsd6: 0n, pendingOrdersUsd6: 0n, estCostsUsd6: 0n };
  const fill = (id: string, gross: bigint): SL.RealizedFill => ({ fillId: id, closedAt: 1, grossPnlUsd6: gross, entryFeeUsd6: 0n, exitFeeUsd6: 0n, fundingUsd6: 0n });
  let l = SL.applyFill(SL.newLedger('L'), fill('profit', 1_165_400n));
  l = SL.commitSweep(l, 's1', SL.quoteSweep(l, p, loan, { ...cash, availableUsd6: 1n }, 1));
  const stale = SL.quoteSweep(l, p, loan, cash, 2);
  l = SL.applyFill(l, fill('loss', -2_000_000n));
  expect(() => SL.commitSweep(l, 'stale', stale)).toThrow('Stale');
  for (const permission of [p, { ...p, id: 'reauthorized' }]) {
    expect(SL.quoteSweep(SL.anchorAtAuthorization(l), permission, loan, cash, 3).amountUsd6).toBe(0n);
  }
  l = SL.applyFill(l, fill('partial-recovery', 1_999_999n));
  expect(SL.quoteSweep(l, p, loan, cash, 4).amountUsd6).toBe(0n);
  l = SL.applyFill(l, fill('final-recovery', 1n));
  for (const permission of [{ ...p, revokedAt: 3 }, { ...p, expiresAt: 4 }, { ...p, loanId: 'other' }, { ...p, percent: 0 }]) {
    expect(SL.quoteSweep(l, permission, loan, cash, 5).amountUsd6).toBe(0n);
  }
  expect(SL.quoteSweep(l, { ...p, floorUsd6: cash.availableUsd6 }, loan, cash, 5).amountUsd6).toBe(0n);
  const q = SL.quoteSweep(l, p, loan, cash, 5);
  expect(q.amountUsd6).toBe(1_165_399n);
  expect(q.permissionId).toBe(p.id);
  l = SL.commitSweep(l, 's2', q);
  expect(l.sweptTotalUsd6).toBe(1_165_400n);
  expect(SL.commitSweep(l, 's2', q)).toBe(l);
  expect(SL.quoteSweep(SL.anchorAtAuthorization(l), { ...p, id: 'p3', percent: 25 }, loan, cash, 6).amountUsd6).toBe(0n);
});


it('carried entitlement retains its consent percentage and records the paying consent terms', () => {
  const permission: SL.SweepPermission = { id: 'p-original', loanId: 'L', percent: 50, expiresAt: 86400, floorUsd6: 1n, createdAt: 0, revokedAt: null };
  const loan = { principalUsd6: 10_000_000n, interestUsd6: 0n };
  const cash = { availableUsd6: 2n, openMarginUsd6: 0n, pendingOrdersUsd6: 0n, estCostsUsd6: 0n };
  let l = SL.applyFill(SL.newLedger('L'), { fillId: 'p', closedAt: 1, grossPnlUsd6: 2_000_000n, entryFeeUsd6: 0n, exitFeeUsd6: 0n, fundingUsd6: 0n });
  l = SL.commitSweep(l, 'first', SL.quoteSweep(l, permission, loan, cash, 1));
  expect(l.owedUsd6).toBe(999_999n);
  for (const percent of [25, 100]) {
    const q = SL.quoteSweep(SL.anchorAtAuthorization(l), { ...permission, id: 'changed', percent }, loan, { ...cash, availableUsd6: 9_000_000n }, 2);
    expect(q.amountUsd6).toBe(0n);
    expect(q.owedAfterUsd6).toBe(999_999n);
  }
  const fresh = { ...permission, id: 'p-fresh', expiresAt: 172800 };
  const q = SL.quoteSweep(l, fresh, loan, { ...cash, availableUsd6: 9_000_000n }, 2);
  l = SL.commitSweep(l, 'final', q);
  expect(l.sweptTotalUsd6).toBe(1_000_000n);
  expect(l.sweepAudit.map(a => a.permission)).toEqual([permission, fresh]);
});

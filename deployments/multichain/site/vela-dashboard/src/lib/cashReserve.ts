/**
 * ONE exact-unit spendable-cash definition (demo USD, 6-dec micro-USD), shared by UI limits
 * and engine validation for repayments, cash withdrawals, profit sweeps and entry margin:
 *
 *   spendable = cash − open-position margin − pending-order reserve − estimated costs − open losses/costs − floor
 *
 * Funds reserved for open positions, pending orders and costs are never spendable.
 * When reserves exceed cash nothing is spendable; the gap is reported as `shortfallUsd6`
 * (visible, never hidden). Negative reserves or floors are invalid input and throw.
 */
export type PaperCash = {
  cashUsd6: bigint;
  openMarginUsd6: bigint;
  pendingOrdersUsd6: bigint;
  estCostsUsd6: bigint;
  unrealizedLossUsd6?: bigint;
};

export type CashBreakdown = {
  reservedUsd6: bigint;
  floorUsd6: bigint;
  spendableUsd6: bigint;
  shortfallUsd6: bigint;
};

const assertNonNegative = (v: bigint, label: string) => {
  if (typeof v !== 'bigint') throw new TypeError(`${label}: must be an exact bigint base-unit amount`);
  if (v < 0n) throw new RangeError(`${label}: cannot be negative (${v})`);
};

export function reservedCashUsd6(p: PaperCash): bigint {
  assertNonNegative(p.openMarginUsd6, 'openMarginUsd6');
  assertNonNegative(p.pendingOrdersUsd6, 'pendingOrdersUsd6');
  assertNonNegative(p.estCostsUsd6, 'estCostsUsd6');
  assertNonNegative(p.unrealizedLossUsd6 ?? 0n, 'unrealizedLossUsd6');
  return p.openMarginUsd6 + p.pendingOrdersUsd6 + p.estCostsUsd6 + (p.unrealizedLossUsd6 ?? 0n);
}

export function cashBreakdown(p: PaperCash, floorUsd6: bigint = 0n): CashBreakdown {
  assertNonNegative(floorUsd6, 'floorUsd6');
  const reservedUsd6 = reservedCashUsd6(p);
  const raw = p.cashUsd6 - reservedUsd6 - floorUsd6;
  return { reservedUsd6, floorUsd6, spendableUsd6: raw > 0n ? raw : 0n, shortfallUsd6: raw < 0n ? -raw : 0n };
}

export const spendableCashUsd6 = (p: PaperCash, floorUsd6: bigint = 0n): bigint => cashBreakdown(p, floorUsd6).spendableUsd6;

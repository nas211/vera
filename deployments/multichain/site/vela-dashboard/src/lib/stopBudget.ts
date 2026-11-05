/**
 * $2 fee-inclusive stop-TRIGGER budget. Applies to EVERY account/position (incl. JP and future
 * accounts). Account size, leverage, strategy or overrides can only make it stricter.
 * This bounds the loss at the trigger price — it is NOT a realized-loss maximum:
 * slippage, gaps and funding can exceed it.
 *
 * Prices are micro-USD per whole unit; qty is base units with `qtyScale` per whole unit.
 */
import { toBaseUnits } from './units';

export const STOP_BUDGET_USD6 = 2_000_000n;
const BPS = 10_000n;

export type StopInput = {
  side: 'long' | 'short';
  entryPriceUsd6: bigint;
  qty: bigint; // remaining open quantity
  qtyScale: bigint;
  tickUsd6: bigint;
  entryFeeUsd6: bigint; // already paid on the open quantity
  exitFeeBps: bigint;
  observedFundingUsd6: bigint; // positive = paid
  realizedLossFromPartialsUsd6: bigint; // net loss already realized on partial exits (>=0)
  existingLimitUsd6?: bigint; // stricter of this and $2 wins
};

export type StopResult =
  | { ok: true; stopPriceUsd6: bigint; budgetUsd6: bigint; worstLossAtTriggerUsd6: bigint }
  | { ok: false; reason: string; budgetUsd6: bigint };

const ceilDiv = (a: bigint, b: bigint) => (a + b - 1n) / b;

/** Loosely-typed input as it may arrive at runtime (numbers, strings, ExactAmount). */
export type RawStopInput = { [K in keyof StopInput]: K extends 'side' ? StopInput[K] : unknown };

/**
 * Normalize every amount to bigint exactly (throws on fractional/unsafe values rather than
 * rounding). Validates scale/tick/fee so the math below can never divide by zero.
 */
export function normalizeStopInput(raw: StopInput | RawStopInput): StopInput {
  if (raw.side !== 'long' && raw.side !== 'short') throw new TypeError(`side: invalid "${String(raw.side)}"`);
  const n: StopInput = {
    side: raw.side,
    entryPriceUsd6: toBaseUnits(raw.entryPriceUsd6, 'entryPriceUsd6'),
    qty: toBaseUnits(raw.qty, 'qty'),
    qtyScale: toBaseUnits(raw.qtyScale, 'qtyScale'),
    tickUsd6: toBaseUnits(raw.tickUsd6, 'tickUsd6'),
    entryFeeUsd6: toBaseUnits(raw.entryFeeUsd6, 'entryFeeUsd6'),
    exitFeeBps: toBaseUnits(raw.exitFeeBps, 'exitFeeBps'),
    observedFundingUsd6: toBaseUnits(raw.observedFundingUsd6, 'observedFundingUsd6'),
    realizedLossFromPartialsUsd6: toBaseUnits(raw.realizedLossFromPartialsUsd6, 'realizedLossFromPartialsUsd6'),
    existingLimitUsd6: raw.existingLimitUsd6 === undefined || raw.existingLimitUsd6 === null ? undefined : toBaseUnits(raw.existingLimitUsd6, 'existingLimitUsd6'),
  };
  if (n.qtyScale <= 0n) throw new RangeError('qtyScale must be > 0');
  if (n.tickUsd6 <= 0n) throw new RangeError('tickUsd6 must be > 0');
  if (n.entryPriceUsd6 <= 0n) throw new RangeError('entryPriceUsd6 must be > 0');
  if (n.exitFeeBps < 0n || n.exitFeeBps >= BPS) throw new RangeError('exitFeeBps must be in [0, 10000)');
  if (n.qty < 0n) throw new RangeError('qty must be >= 0');
  if (n.entryFeeUsd6 < 0n) throw new RangeError('entryFeeUsd6 must be >= 0');
  if (n.realizedLossFromPartialsUsd6 < 0n) throw new RangeError('realizedLossFromPartialsUsd6 must be >= 0 (losses only)');
  if (n.existingLimitUsd6 !== undefined && n.existingLimitUsd6 < 0n) throw new RangeError('existingLimitUsd6 must be >= 0');
  return n;
}

export function effectiveBudget(existing?: bigint): bigint {
  return existing !== undefined && existing < STOP_BUDGET_USD6 ? existing : STOP_BUDGET_USD6;
}

/** Fee-inclusive loss if the stop triggers exactly at `stop` (fees rounded UP). */
export function lossAtTrigger(raw: StopInput | RawStopInput, rawStop: bigint | number | string): bigint {
  const i = normalizeStopInput(raw);
  const stop = toBaseUnits(rawStop, 'stopPriceUsd6');
  const notionalExit = ceilDiv(stop * i.qty, i.qtyScale);
  const exitFee = ceilDiv(notionalExit * i.exitFeeBps, BPS);
  const diff = i.side === 'long' ? i.entryPriceUsd6 - stop : stop - i.entryPriceUsd6;
  const pxLoss = diff > 0n ? ceilDiv(diff * i.qty, i.qtyScale) : -((-diff * i.qty) / i.qtyScale);
  return pxLoss + exitFee + i.entryFeeUsd6 + i.observedFundingUsd6 + i.realizedLossFromPartialsUsd6;
}

export function computeStop(raw: StopInput | RawStopInput): StopResult {
  const i = normalizeStopInput(raw);
  const budget = effectiveBudget(i.existingLimitUsd6);
  if (i.qty <= 0n) return { ok: false, reason: 'No open quantity', budgetUsd6: budget };
  const sunk = i.entryFeeUsd6 + i.observedFundingUsd6 + i.realizedLossFromPartialsUsd6;
  const room = budget - sunk;
  if (room <= 0n) return { ok: false, reason: 'Budget consumed by fees/funding/partial losses — exit now', budgetUsd6: budget };

  let stop: bigint;
  if (i.side === 'long') {
    // (E - S)q + S q f <= R  →  S >= (E q - R s) / (q (1 - f))
    const num = (i.entryPriceUsd6 * i.qty - room * i.qtyScale) * BPS;
    const den = i.qty * (BPS - i.exitFeeBps);
    stop = num <= 0n ? i.tickUsd6 : ceilDiv(num, den);
    stop = ceilDiv(stop, i.tickUsd6) * i.tickUsd6; // round inward (up, toward entry)
    while (lossAtTrigger(i, stop) > budget) stop += i.tickUsd6;
    if (stop >= i.entryPriceUsd6) return { ok: false, reason: 'Budget too small for position size', budgetUsd6: budget };
  } else {
    // (S - E)q + S q f <= R  →  S <= (R s + E q) / (q (1 + f))
    const num = (room * i.qtyScale + i.entryPriceUsd6 * i.qty) * BPS;
    const den = i.qty * (BPS + i.exitFeeBps);
    stop = num / den;
    stop = (stop / i.tickUsd6) * i.tickUsd6; // round inward (down, toward entry)
    while (stop > 0n && lossAtTrigger(i, stop) > budget) stop -= i.tickUsd6;
    if (stop <= i.entryPriceUsd6) return { ok: false, reason: 'Budget too small for position size', budgetUsd6: budget };
  }
  return { ok: true, stopPriceUsd6: stop, budgetUsd6: budget, worstLossAtTriggerUsd6: lossAtTrigger(i, stop) };
}

/** Reconcile an existing stop: keep the stricter (closer to entry) of existing and computed. */
export function reconcileStop(raw: StopInput | RawStopInput, rawExisting: bigint | number | string | null): StopResult {
  const i = normalizeStopInput(raw);
  const r = computeStop(i);
  if (!r.ok || rawExisting === null) return r;
  const existing = toBaseUnits(rawExisting, 'existingStopUsd6');
  if (existing <= 0n) throw new RangeError('existingStopUsd6 must be > 0');
  const existingStopUsd6 = i.side === 'long' ? ceilDiv(existing, i.tickUsd6) * i.tickUsd6 : existing / i.tickUsd6 * i.tickUsd6;
  if (existingStopUsd6 <= 0n) return { ok: false, reason: 'Existing stop cannot be rounded inward — exit now', budgetUsd6: r.budgetUsd6 };
  const stricter = i.side === 'long'
    ? (existingStopUsd6 > r.stopPriceUsd6 ? existingStopUsd6 : r.stopPriceUsd6)
    : (existingStopUsd6 < r.stopPriceUsd6 ? existingStopUsd6 : r.stopPriceUsd6);
  return { ...r, stopPriceUsd6: stricter, worstLossAtTriggerUsd6: lossAtTrigger(i, stricter) };
}

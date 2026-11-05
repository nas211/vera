/**
 * Exact-unit money helpers. All amounts are integer base units (bigint).
 * - native test DOGE: 18 decimals (wei)
 * - demo USD: 6 decimals (micro-USD)
 * Never use floating point for balances.
 */
export const DOGE_DECIMALS = 18;
export const USD_DECIMALS = 6;
export const USD_CENT = 10_000n; // 0.01 USD in 6-dec units
export const ONE_DOGE = 10n ** 18n;
export const ONE_USD = 10n ** 6n;

/** Decimal string with an exact base-unit companion (used on adapter boundaries). */
export type ExactAmount = { units: string; decimals: number };

export type ParseResult = { ok: true; value: bigint } | { ok: false; error: string };

/** Strict decimal parser: no signs, exponents, separators or excess precision. */
export function parseDecimal(input: string, decimals: number): ParseResult {
  const s = input.trim();
  if (s === '') return { ok: false, error: 'Enter an amount.' };
  if (s.startsWith('-')) return { ok: false, error: 'Amount cannot be negative.' };
  if (!/^\d*\.?\d*$/.test(s) || s === '.') {
    return { ok: false, error: 'Use digits and at most one decimal point (no commas, signs or exponents).' };
  }
  const [intPart = '', frac = ''] = s.split('.');
  if (frac.length > decimals) return { ok: false, error: `At most ${decimals} decimal places allowed.` };
  return { ok: true, value: BigInt((intPart || '0') + frac.padEnd(decimals, '0')) };
}

export type AmountRules = {
  /** precision the user may type (e.g. 2 for ordinary USD, 18 for DOGE) */
  inputDecimals: number;
  /** base-unit decimals of the ledger */
  decimals: number;
  max?: bigint;
  maxLabel?: string;
  min?: bigint;
};

/** Client-side validation. The server/contract must validate independently. */
export function validateAmount(input: string, rules: AmountRules): ParseResult {
  const typed = parseDecimal(input, rules.inputDecimals);
  if (!typed.ok) return typed;
  const value = typed.value * 10n ** BigInt(rules.decimals - rules.inputDecimals);
  if (value === 0n) return { ok: false, error: 'Amount must be greater than zero.' };
  if (rules.min !== undefined && value < rules.min) {
    return { ok: false, error: `Minimum is ${formatUnits(rules.min, rules.decimals)}.` };
  }
  if (rules.max !== undefined && value > rules.max) {
    return { ok: false, error: `Exceeds ${rules.maxLabel ?? 'limit'} (${formatUnits(rules.max, rules.decimals)}).` };
  }
  return { ok: true, value };
}

/** Exact decimal string, trailing zeros trimmed. */
export function formatUnits(value: bigint, decimals: number): string {
  const neg = value < 0n;
  const abs = neg ? -value : value;
  const base = 10n ** BigInt(decimals);
  const i = abs / base;
  const f = (abs % base).toString().padStart(decimals, '0').replace(/0+$/, '');
  return `${neg ? '-' : ''}${i.toString()}${f ? '.' + f : ''}`;
}

export type RoundMode = 'down' | 'up' | 'half-up';

/** Round toward zero / away / half-up to `dp` places and return fixed string. */
export function formatFixed(value: bigint, decimals: number, dp: number, mode: RoundMode = 'half-up'): string {
  const neg = value < 0n;
  const abs = neg ? -value : value;
  const step = 10n ** BigInt(decimals - dp);
  let q = abs / step;
  const r = abs % step;
  if (mode === 'up' && r > 0n) q += 1n;
  if (mode === 'half-up' && r * 2n >= step) q += 1n;
  const base = 10n ** BigInt(dp);
  const i = q / base;
  const f = dp > 0 ? '.' + (q % base).toString().padStart(dp, '0') : '';
  const grouped = i.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const isZero = q === 0n;
  return `${neg && !isZero ? '-' : ''}${grouped}${f}`;
}

export const fmtUsd = (u: bigint, mode: RoundMode = 'half-up') => `$${formatFixed(u, USD_DECIMALS, 2, mode)}`;
export const fmtSignedUsd = (u: bigint) => `${u > 0n ? '+' : ''}${fmtUsd(u)}`;
export const fmtDoge = (w: bigint, dp = 4, mode: RoundMode = 'down') => `${formatFixed(w, DOGE_DECIMALS, dp, mode)} DOGE`;

/** Round micro-USD up to whole cents. */
export const ceilToCents = (u: bigint) => ((u + USD_CENT - 1n) / USD_CENT) * USD_CENT;
/** Round micro-USD down to whole cents (spendable prefills). */
export const floorToCents = (u: bigint) => (u / USD_CENT) * USD_CENT;

/**
 * Spendable prefill for an ordinary USD input: rounded DOWN to cents.
 * Returns the typed string and the fractional-cent remainder that stays in the ledger.
 */
export function usdPrefill(balance: bigint): { text: string; value: bigint; remainder: bigint } {
  const value = floorToCents(balance < 0n ? 0n : balance);
  return { text: formatFixed(value, USD_DECIMALS, 2, 'down').replace(/,/g, ''), value, remainder: balance - value };
}

/**
 * Normalize an integer base-unit amount to bigint WITHOUT losing precision.
 * Accepts: bigint; safe-integer number; integer decimal string ("-12", "340"); ExactAmount.
 * Rejects (throws): fractional/unsafe numbers, NaN/Infinity, decimal-point strings, anything else.
 * Fractional values are never rounded here — a caller passing 0.5 as a base-unit amount is a bug.
 */
export function toBaseUnits(v: unknown, label = 'amount'): bigint {
  if (typeof v === 'bigint') return v;
  if (typeof v === 'number') {
    if (!Number.isSafeInteger(v)) throw new TypeError(`${label}: ${v} is not a safe integer base-unit amount`);
    return BigInt(v);
  }
  if (typeof v === 'string') {
    const s = v.trim();
    if (!/^-?\d+$/.test(s)) throw new TypeError(`${label}: "${v}" is not an integer base-unit string`);
    return BigInt(s);
  }
  if (v && typeof v === 'object' && typeof (v as ExactAmount).units === 'string') {
    return toBaseUnits((v as ExactAmount).units, label);
  }
  throw new TypeError(`${label}: unsupported amount type ${v === null ? 'null' : typeof v}`);
}

/** Exact display of a quantity whose scale is a power of 10 (e.g. 1e8 for BTC sats). No floats. */
export function formatByScale(value: bigint, scale: bigint): string {
  if (scale <= 0n) throw new RangeError('scale must be > 0');
  const digits = scale.toString().length - 1;
  if (10n ** BigInt(digits) !== scale) throw new RangeError(`scale ${scale} is not a power of 10`);
  return formatUnits(value, digits);
}

/** Exact decimal string with thousands separators on the integer part only. */
export const formatUnitsGrouped = (value: bigint, decimals: number) =>
  formatUnits(value, decimals).replace(/^(-?)(\d+)/, (_m, sign: string, int: string) => sign + int.replace(/\B(?=(\d{3})+(?!\d))/g, ','));

export const toExact = (units: bigint, decimals: number): ExactAmount => ({ units: units.toString(), decimals });
export const fromExact = (a: ExactAmount): bigint => BigInt(a.units);

export const minBig = (...v: bigint[]) => v.reduce((a, b) => (b < a ? b : a));
export const maxBig = (...v: bigint[]) => v.reduce((a, b) => (b > a ? b : a));

import { formatFixed, formatUnits, parseDecimal, usdPrefill } from '@/lib/units';

// Presentation conversion only. Never compute an account balance, debt or stop locally.
export function units(value: string | null | undefined, decimals = 6, up = false): bigint | null {
  if (typeof value !== 'string' || value.length > 160 || !/^-?\d+(\.\d+)?$/.test(value)) return null;
  const negative = value.startsWith('-'), [whole, fraction = ''] = (negative ? value.slice(1) : value).split('.');
  let result = BigInt(whole) * 10n ** BigInt(decimals) + BigInt(fraction.padEnd(decimals, '0').slice(0, decimals) || '0');
  if (up && /[1-9]/.test(fraction.slice(decimals))) result++;
  return negative ? -result : result;
}
export const usd = (value: string | null | undefined) => { const n = units(value); return n === null ? 'Unavailable' : `$${formatFixed(n, 6, 2)}`; };
export const doge = (value: string | null | undefined) => typeof value === 'string' && /^\d+$/.test(value) ? `${formatFixed(BigInt(value),18,4,'down')} DOGE` : 'Unavailable';
export const percent = (value: string | null | undefined) => { const n = units(value,6); return n === null ? 'Unavailable' : `${formatFixed(n * 100n,6,2)}%`; };
export const ratio = (value: string | null | undefined) => { const n = units(value,6); return n === null ? 'Unavailable' : formatFixed(n,6,2,'down'); };
export const amountOk = (value: string, decimals: number, maximum?: bigint | null, inputDecimals = decimals) => {
  const n = parseDecimal(value,inputDecimals);
  const scaled = n.ok ? n.value * 10n ** BigInt(decimals - inputDecimals) : 0n;
  return n.ok && scaled > 0n && (maximum === undefined || maximum !== null && scaled <= maximum);
};
export const maxCash = (value: string | undefined) => { const n = units(value); return n === null ? '' : usdPrefill(n).text; };
export const exactDoge = (value: string) => formatUnits(BigInt(value),18);
export const stamp = (at: number | undefined) => at !== undefined && Number.isFinite(at) ? new Date(at*1000).toISOString().slice(0,19).replace('T',' ') + ' UTC' : 'Time unavailable';

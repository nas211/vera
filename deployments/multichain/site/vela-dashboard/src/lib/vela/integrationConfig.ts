/**
 * Integration configuration. Every external dependency is an explicit PLACEHOLDER until the
 * user supplies a verified value. Nothing here is invented: null means "not configured".
 */
const env = import.meta.env;
const addr = (v: unknown): `0x${string}` | null =>
  typeof v === 'string' && /^0x[0-9a-fA-F]{40}$/.test(v) ? (v as `0x${string}`) : null;
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);

export const INTEGRATION = {
  /** Stage 1: all blockchain writes OFF. Flip only after deployment is separately authorized. */
  writesEnabled: false as const,
  /** Prepared bundle only. No API connection or wallet auth until a separately approved release. */
  serviceEnabled: false as const,
  /** NEW isolated reward-enabled vault factory candidate — not deployed. */
  rewardVaultFactory: addr(env.VITE_REWARD_VAULT_FACTORY),
  rewardVault: addr(env.VITE_REWARD_VAULT),
  /** Account that funds the separate reward reserve from the faucet. */
  rewardReserveFunder: addr(env.VITE_REWARD_RESERVE_FUNDER),
  /** Vela authoritative service: same-origin route or a narrowly scoped integration API. */
  velaApiBase: str(env.VITE_VELA_API_BASE),
  /** DOGE/USD price source used for demo risk (must expose an observed-at timestamp). */
  priceFeedEndpoint: str(env.VITE_PRICE_FEED_ENDPOINT),
  /** Confirmations required before a claim is shown as paid. Placeholder value. */
  requiredConfirmations: Number(env.VITE_REQUIRED_CONFIRMATIONS ?? 3),
  connectTimeoutMs: 45_000,
};

export type PlaceholderItem = { key: string; label: string; value: string | null; stage: string };

export const PLACEHOLDERS: PlaceholderItem[] = [
  { key: 'VITE_REWARD_VAULT_FACTORY', label: 'Reward vault factory address', value: INTEGRATION.rewardVaultFactory, stage: 'Stage 2 (deploy — needs your authorization)' },
  { key: 'VITE_REWARD_VAULT', label: 'Reward vault address', value: INTEGRATION.rewardVault, stage: 'Stage 2' },
  { key: 'VITE_REWARD_RESERVE_FUNDER', label: 'Reserve funder address', value: INTEGRATION.rewardReserveFunder, stage: 'Stage 2' },
  { key: 'VITE_PRICE_FEED_ENDPOINT', label: 'DOGE/USD price endpoint', value: INTEGRATION.priceFeedEndpoint, stage: 'Stage 3 (verified reads)' },
  { key: 'VITE_VELA_API_BASE', label: 'Vela loan / paper / repayment API', value: INTEGRATION.velaApiBase, stage: 'Stage 4 (Vela adapter)' },
];

import { INTEGRATION } from './integrationConfig';

/** Single source of truth for why an on-chain write button is disabled. */
export function onChainBlockReason(extra?: string | null): string | null {
  if (!INTEGRATION.writesEnabled) return 'Blockchain writes are OFF in this build. The reward vault is not deployed; deployment needs your separate authorization.';
  if (!INTEGRATION.rewardVault) return 'Reward vault address not configured.';
  return extra ?? null;
}

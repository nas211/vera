/**
 * Live adapters — NOT IMPLEMENTED in Stage 1. They fail closed with "integration unavailable"
 * rather than inventing data. Cookies/tokens are never copied across origins.
 */
import { INTEGRATION } from './integrationConfig';
import type { RewardVaultAdapter, VelaServiceAdapter } from './types';

export class IntegrationUnavailableError extends Error {
  constructor(what: string) {
    super(`Integration unavailable: ${what}`);
  }
}

export const velaLiveAdapter: VelaServiceAdapter = {
  mode: INTEGRATION.velaApiBase ? 'live' : 'unavailable',
  async getLoan() { throw new IntegrationUnavailableError('Vela loan service (Stage 4 placeholder)'); },
  async getPaperAccount() { throw new IntegrationUnavailableError('Vela paper account (Stage 4 placeholder)'); },
  async getTradingStatus() { throw new IntegrationUnavailableError('Vela trading status (Stage 4 placeholder)'); },
};

export const rewardVaultLiveAdapter: RewardVaultAdapter = {
  mode: INTEGRATION.rewardVault ? 'live' : 'unavailable',
  writesEnabled: INTEGRATION.writesEnabled,
  async getSnapshot() { throw new IntegrationUnavailableError('Reward vault not deployed (Stage 2/3 placeholder)'); },
};

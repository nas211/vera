/**
 * Adapter boundary types. Amounts crossing the boundary are exact base-unit decimal strings
 * (ExactAmount) — never JS numbers. Two receipt types exist and are never merged:
 * a TESTNET reward claim (native test DOGE) and a SIMULATED demo-loan repayment (demo USD).
 */
import type { ExactAmount } from '../units';

export type AdapterMode = 'fixture' | 'live' | 'unavailable';
export type DataSource = 'fixture' | 'chain' | 'vela';

export type VaultSnapshot = {
  source: DataSource;
  vaultAddress: `0x${string}` | null;
  chainId: number;
  aprBps: string;
  totalPrincipal: ExactAmount; // 18-dec
  reserveBalance: ExactAmount;
  reserveLiabilities: ExactAmount;
  userPrincipal: ExactAmount;
  userClaimable: ExactAmount;
  userClaimed: ExactAmount;
  runwaySeconds: string | null;
  asOf: number;
};

export type RewardClaimReceipt = {
  kind: 'testnet-reward-claim';
  id: string;
  source: DataSource;
  amount: ExactAmount; // test DOGE, 18-dec
  /** null when no transaction was sent (fixture rehearsal). Never fabricated. */
  txHash: `0x${string}` | null;
  confirmations: number;
  requiredConfirmations: number;
  status: 'fixture-rehearsal' | 'pending' | 'confirmed' | 'failed';
  at: number;
};

export type SimulatedRepaymentReceipt = {
  kind: 'demo-loan-repayment';
  id: string;
  source: DataSource;
  loanId: string;
  trigger: 'manual-partial' | 'manual-full' | 'profit-sweep';
  amount: ExactAmount; // demo USD, 6-dec
  toInterest: ExactAmount;
  toPrincipal: ExactAmount;
  maxAllowance: ExactAmount | null;
  /** Vela ledger reference — null in fixture mode. */
  ledgerRef: string | null;
  at: number;
};

export type Receipt = RewardClaimReceipt | SimulatedRepaymentReceipt;

export type LoanSnapshot = {
  source: DataSource;
  loanId: string;
  principal: ExactAmount;
  accruedInterest: ExactAmount;
  aprBps: string;
  openedAt: number;
};

export type PaperAccountSnapshot = {
  source: DataSource;
  cash: ExactAmount;
  openMargin: ExactAmount;
  pendingOrders: ExactAmount;
  estCosts: ExactAmount;
};

export type TradingStatus = {
  source: DataSource;
  status: 'running' | 'stopped';
  entriesPaused: boolean;
  reason: string | null;
  recovery: string | null;
  /** Monotonic sequence. Clients must ignore responses with a lower seq. */
  seq: number;
};

/** Vela authoritative services — the dashboard never keeps its own copy of balances. */
export interface VelaServiceAdapter {
  readonly mode: AdapterMode;
  getLoan(): Promise<LoanSnapshot | null>;
  getPaperAccount(): Promise<PaperAccountSnapshot>;
  getTradingStatus(): Promise<TradingStatus>;
}

/** Reward vault reads/writes (chain). */
export interface RewardVaultAdapter {
  readonly mode: AdapterMode;
  readonly writesEnabled: boolean;
  getSnapshot(user: `0x${string}` | null): Promise<VaultSnapshot>;
}

// Proposed application boundary; implement against verified services/ABIs.
// These are not invented server endpoints. Store secrets only server-side.
export type BaseUnits = string;
export type Evidence = {kind:'fixture'|'testnet-receipt'|'demo-ledger'; reference:string; confirmed:boolean};
export type WalletIdentity = {address:string; chainId:number};
export type RewardState = {
  principalWei:BaseUnits; reserveWei:BaseUnits; claimableWei:BaseUnits;
  aprBps:number; source:'prefunded-testnet-incentive'; compounded:false;
  blockNumber:string; readAt:string; mode:'fixture'|'testnet';
};
export type LoanState = {
  mode:'DEMO'; loanId:string; vaultId:string; network:string;
  principalMicroUsd:BaseUnits; interestMicroUsd:BaseUnits;
  freeEquityMicroUsd:BaseUnits; collateralValueMicroUsd:BaseUnits;
  quoteAt:string; quoteFresh:boolean; state:string;
};
export type SweepPermission = {
  loanId:string; confirm:true; enabled:boolean; percentage:string;
  expiresAt:string; minimumRemainingMicroUsd:BaseUnits;
};
export interface RewardsAdapter {
  read(identity:WalletIdentity):Promise<RewardState>;
  deposit(identity:WalletIdentity,wei:BaseUnits):Promise<Evidence>;
  claim(identity:WalletIdentity):Promise<Evidence>;
  withdraw(identity:WalletIdentity,wei:BaseUnits):Promise<Evidence>;
}
export interface VelaDemoAdapter {
  readAccount():Promise<LoanState>;
  repay(input:{loanId:string;vaultId:string;maxMicroUsd:BaseUnits;full:boolean;confirm:true;idempotencyKey:string}):Promise<Evidence>;
  authorizeSweep(input:SweepPermission):Promise<Evidence>;
  stopNewEntries():Promise<void>;
}
// Convert existing API decimal-string USD to micro USD exactly in the adapter.
// Require explicit wallet context and authenticated network scope for each call.
// Persist pending transactions and reconcile canonical receipts after reload.
// A “submitted” hash is never evidence.confirmed=true.

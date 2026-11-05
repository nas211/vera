import { createContext, useContext, useSyncExternalStore } from 'react';
import { DashboardController } from '@/lib/vela/dashboardController';

export const AccountContext = createContext<DashboardController | null>(null);
export function useAccountDashboard() {
  const controller = useContext(AccountContext);
  if (!controller) throw Error('Account dashboard provider required');
  const state = useSyncExternalStore(controller.subscribe, controller.snapshot, controller.snapshot);
  const ledgerReason = state.busy ? 'Processing your action…' : !state.account ? (state.refreshing ? 'Loading your account…' : 'Account unavailable. Refresh to retry.') : null;
  const nativeReason = ledgerReason ?? (!controller.workflow?.writesEnabled ? 'Vault actions await owner activation.' : state.pending ? 'Check the pending wallet transaction first.' : !state.native ? 'Confirmed vault data unavailable. Refresh to retry.' : null);
  return { ...state, controller, ledgerReason, nativeReason };
}

import { useEffect, type ReactNode } from 'react';
import type { DashboardController } from '@/lib/vela/dashboardController';
import { startDashboardPolling } from '@/lib/vela/dashboardPolling';
import { AccountContext } from './context';

export function AccountProvider({ controller, children }: { controller: DashboardController; children: ReactNode }) {
  useEffect(() => startDashboardPolling(controller), [controller]);
  return <AccountContext.Provider value={controller}>{children}</AccountContext.Provider>;
}

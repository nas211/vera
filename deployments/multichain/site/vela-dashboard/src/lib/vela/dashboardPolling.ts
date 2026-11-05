import type { DashboardController } from './dashboardController';

/** Leave a full idle interval after each completed read, even on a slow RPC. */
export function startDashboardPolling(controller: Pick<DashboardController, 'start' | 'poll' | 'stop'>, interval = 15_000) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const next = () => {
    if (!stopped) timer = setTimeout(() => { void controller.poll().finally(next); }, interval);
  };
  void controller.start().finally(next);
  return () => { stopped = true; clearTimeout(timer); controller.stop(); };
}

import { afterEach, expect, it, vi } from 'vitest';
import { startDashboardPolling } from '../vela/dashboardPolling';

afterEach(() => vi.useRealTimers());
it('waits a full idle interval after slow initial and subsequent reads', async () => {
  vi.useFakeTimers();
  let finishInitial!: () => void, finishPoll!: () => void;
  const controller = {
    start: vi.fn(() => new Promise<void>(resolve => { finishInitial = resolve; })),
    poll: vi.fn(() => new Promise<void>(resolve => { finishPoll = resolve; })),
    stop: vi.fn(),
  };
  const stop = startDashboardPolling(controller);
  await vi.advanceTimersByTimeAsync(60_000); expect(controller.poll).not.toHaveBeenCalled();
  finishInitial(); await vi.advanceTimersByTimeAsync(14_999); expect(controller.poll).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1); expect(controller.poll).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(60_000); expect(controller.poll).toHaveBeenCalledTimes(1);
  finishPoll(); await vi.advanceTimersByTimeAsync(14_999); expect(controller.poll).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1); expect(controller.poll).toHaveBeenCalledTimes(2);
  stop(); finishPoll(); await vi.advanceTimersByTimeAsync(60_000);
  expect(controller.stop).toHaveBeenCalledOnce(); expect(controller.poll).toHaveBeenCalledTimes(2);
});
it('does not restart polling when a stopped initial request completes', async () => {
  vi.useFakeTimers(); let resolve!: () => void;
  const controller = { start: () => new Promise<void>(r => { resolve = r; }), poll: vi.fn(async () => {}), stop: vi.fn() };
  const stop = startDashboardPolling(controller); stop(); resolve();
  await vi.advanceTimersByTimeAsync(60_000); expect(controller.poll).not.toHaveBeenCalled();
});

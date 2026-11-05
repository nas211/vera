import { afterEach, describe, expect, it, vi } from 'vitest';
import { RuntimeConfigStore } from '../vela/runtimeConfig';

const configured = () => ({
  mode: 'PAPER', lending: 'DEMO', liveEnabled: false, realFunding: false,
  chain: { id: 'dogeos', chainId: 6281971 }, nativeVaultActionsEnabled: true,
  nativeWorkflowGatewayEnabled: true, attestorConfigured: true,
  nativeRewardDeployment: {
    factory: `0x${'11'.repeat(20)}`, factoryCodeHash: `0x${'22'.repeat(32)}`,
    vaultCodeHash: `0x${'33'.repeat(32)}`, poolCodeHash: `0x${'44'.repeat(32)}`,
  },
});
afterEach(() => vi.useRealTimers());
describe('live activation refresh', () => {
  it('recovers an already open tab after owner activation and observes deactivation', async () => {
    let config = { ...configured(), nativeVaultActionsEnabled: false };
    const store = new RuntimeConfigStore(async () => structuredClone(config));
    await store.refresh(); expect(store.snapshot().writesEnabled).toBe(false);
    config = configured(); await store.refresh(); expect(store.snapshot().writesEnabled).toBe(true);
    config.nativeVaultActionsEnabled = false;
    await store.refresh(); expect(store.snapshot().writesEnabled).toBe(false);
  });
  it('preserves snapshot identity and subscribers when configuration is unchanged', async () => {
    const store = new RuntimeConfigStore(async () => configured()), listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    await store.refresh(); const state = store.snapshot();
    await store.refresh(); expect(store.snapshot()).toBe(state); expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });
  it('coalesces concurrent focus and dashboard refreshes', async () => {
    let resolve!: (v: unknown) => void;
    const read = vi.fn(() => new Promise(r => { resolve = r; })), store = new RuntimeConfigStore(read);
    const first = store.refresh(), second = store.refresh(); expect(first).toBe(second);
    await Promise.resolve(); resolve(configured()); await first;
    expect(read).toHaveBeenCalledTimes(1); expect(store.snapshot().writesEnabled).toBe(true);
  });
  it('disables writes on a failed refresh and allows recovery, including synchronous failures', async () => {
    const read = vi.fn<() => Promise<unknown>>().mockResolvedValue(configured());
    const store = new RuntimeConfigStore(read); await store.refresh();
    read.mockImplementation(() => { throw Error('offline'); }); await store.refresh();
    expect(store.snapshot().writesEnabled).toBe(false); expect(store.snapshot().error).toContain('Refresh');
    read.mockResolvedValue(configured()); await store.refresh();
    expect(store.snapshot().writesEnabled).toBe(true); expect(store.snapshot().error).toBe('');
  });
  it.each([
    { chain: { id: 'dogeos', chainId: 1 } }, { mode: 'LIVE' }, { realFunding: true },
    { nativeWorkflowGatewayEnabled: false }, { attestorConfigured: false },
    { nativeRewardDeployment: null },
    { nativeRewardDeployment: { ...configured().nativeRewardDeployment, factory: `0x${'00'.repeat(20)}` } },
    { nativeRewardDeployment: { ...configured().nativeRewardDeployment, poolCodeHash: '0x1234' } },
  ])('never enables writes with invalid bindings: %j', async change => {
    const store = new RuntimeConfigStore(async () => ({ ...configured(), ...change }));
    await store.refresh(); expect(store.snapshot().writesEnabled).toBe(false);
  });
  it('fences a late response after unmount and permits StrictMode restart', async () => {
    let resolve!: (v: unknown) => void;
    const read = vi.fn<() => Promise<unknown>>().mockImplementationOnce(() => new Promise(r => { resolve = r; })).mockResolvedValue(configured());
    const store = new RuntimeConfigStore(read), old = store.refresh();
    await Promise.resolve(); store.stop(); await store.refresh();
    resolve({ ...configured(), nativeVaultActionsEnabled: false }); await old;
    expect(store.snapshot().writesEnabled).toBe(true); expect(read).toHaveBeenCalledTimes(2);
  });
  it('bounds a stalled transport, disables writes, and ignores its late result', async () => {
    vi.useFakeTimers();
    let resolve!: (v: unknown) => void;
    const read = vi.fn<() => Promise<unknown>>().mockResolvedValueOnce(configured())
      .mockImplementationOnce(() => new Promise(r => { resolve = r; })).mockResolvedValue(configured());
    const store = new RuntimeConfigStore(read); await store.refresh();
    const stalled = store.refresh(); await vi.advanceTimersByTimeAsync(30000); await stalled;
    expect(store.snapshot().writesEnabled).toBe(false);
    resolve(configured()); await Promise.resolve(); expect(store.snapshot().writesEnabled).toBe(false);
    await store.refresh(); expect(store.snapshot().writesEnabled).toBe(true);
  });
});

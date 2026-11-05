import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useWalletSession } from '../../../src/wallet-session.jsx';
import { request, selectWalletNetwork } from '../../../src/wallet.js';
import { metamaskProvider } from '../../../src/wallet-discovery.js';
import { AccountService, type ApiRequest } from '@/lib/vela/accountService';
import { DashboardFrame } from '@/components/layout/DashboardFrame';
import { Button } from '@/components/ui/button';
import { AccountDashboard } from './approved/AccountDashboard';
import { NativeVaultWorkflow } from '@/lib/vela/nativeVault';
import { INTEGRATION } from '@/lib/vela/integrationConfig';
import { ModeNoticeContext } from '@/components/common/SourceTag';
import { RuntimeConfigStore } from '@/lib/vela/runtimeConfig';

/** Reuses Vela's wallet discovery and auth. The explicit testnet build mounts it;
 * the ordinary preview stays inactive. Backend capability and wallet reviews still gate writes. */
export function ConnectedService({ testnetRelease = false }: { testnetRelease?: boolean }) {
  const configStore = useMemo(() => new RuntimeConfigStore(signal => request('/api/config', undefined, undefined, undefined, {signal,network:{id:'dogeos'}})), []);
  const runtime = useSyncExternalStore(configStore.subscribe,configStore.snapshot,configStore.snapshot);
  const config = runtime.config;
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const wallet = useWalletSession(config, { onClear: () => setNotice(''), onError: setError,
    onNotice: (message: string) => setNotice(message.startsWith('Wallet signature verified.') ? 'Wallet connected.' : message) });
  useEffect(() => {
    const refresh = () => {void configStore.refresh();};
    const visible = () => {if(document.visibilityState==='visible')refresh();};
    refresh();window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',visible);
    return () => {configStore.stop();window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',visible);};
  }, [configStore]);
  const networkSelected = useRef(false);
  useLayoutEffect(() => {
    // Keep the same wallet-network object during authentication and later polls.
    if(config && !networkSelected.current){selectWalletNetwork(config.chain);networkSelected.current=true;}
  }, [config]);
  const generation = wallet.generation.current;
  const generationRef = wallet.generation, addressRef = wallet.addressRef;
  const clearRef = useRef(wallet.clear); clearRef.current = wallet.clear;
  const service = useMemo(() => {
    if (!wallet.authenticated) return null;
    const transport = async (r: ApiRequest) => {
      if (generationRef.current !== r.context.generation || addressRef.current !== r.context.address) throw new Error('Wallet changed');
      const controller = new AbortController();
      const abort = () => controller.abort();
      r.signal.addEventListener('abort', abort, { once: true });
      // Canonical vault reads contain several RPC calls; model preflight may
      // also outlast an ordinary account read. Both remain independently cancellable.
      const timeout = r.path.startsWith('/api/vault/') || r.path === '/api/trading/start' ? 90_000 : 30_000;
      const timer = setTimeout(abort, timeout);
      try {
        if (r.signal.aborted) throw new Error('Request cancelled');
        return await request(r.path, r.body, r.context.address, r.key, { signal: controller.signal, network: { id: r.context.network } });
      } catch (e) {
        if ([401, 403].includes((e as { status?: number }).status ?? 0) && generationRef.current === r.context.generation && addressRef.current === r.context.address) clearRef.current();
        throw e;
      } finally { clearTimeout(timer); r.signal.removeEventListener('abort', abort); }
    };
    const s = new AccountService(transport);
    s.setSession({ address: wallet.address, network: 'dogeos', generation });
    return s;
    // The existing wallet hook fences reads through its stable address/generation refs.
  }, [wallet.authenticated, wallet.address, generation, addressRef, generationRef]);
  // Restore the same context before child read effects, including StrictMode replay.
  useLayoutEffect(() => {
    service?.setSession({ address: wallet.address, network: 'dogeos', generation });
    return () => { service?.setSession(null); };
  }, [service, wallet.address, generation]);
  const provider = wallet.provider.current;
  const writesEnabled = (testnetRelease || INTEGRATION.writesEnabled) && runtime.writesEnabled;
  const native = useMemo(() => service && provider ? new NativeVaultWorkflow({
    service, provider, storage: localStorage,
    writesEnabled,
    withLock: async (name, fn) => {
      if (!navigator.locks) throw new Error('This browser cannot coordinate wallet transactions safely');
      return navigator.locks.request(name, fn);
    },
  }) : null, [service, provider, writesEnabled]);
  return <ModeNoticeContext.Provider value={true}><DashboardFrame banner={<div className="fixture-stripe py-2 text-center text-xs">DogeOS testnet · paper trading · demo USD</div>} wallet={<div className="flex gap-2">
    {wallet.authenticated ? <Button variant="outline" onClick={() => wallet.logout()}>{wallet.address.slice(0, 6)}… Disconnect</Button> : <Button disabled={!config || wallet.connecting} onClick={() => wallet.connect(metamaskProvider(), 'injected')}>{wallet.retryAvailable ? 'Retry connection' : 'Connect wallet'}</Button>}
    {wallet.connecting && <Button variant="outline" onClick={wallet.cancelConnect}>Cancel connection</Button>}
  </div>} footer="Every account retains its stricter existing stop limit or a $2 fee-inclusive stop-trigger budget. Slippage and funding can produce greater realized losses. Live protection is unverified.">
    {wallet.connecting && <p role="status">Waiting for wallet approval · timeout after 45 seconds.</p>}
    {error && <p role="alert" className="text-destructive">{error}</p>}{notice && <p role="status">{notice}</p>}
    {runtime.error && <p role="alert" className="text-destructive">{runtime.error}</p>}
    {!service && <p className="text-sm text-muted-foreground">Connect your wallet and approve the Vela sign-in message to load your account.</p>}
    <AccountDashboard key={`${wallet.address}-${generation}`} service={service} native={native} refreshConfig={configStore.refresh} />
  </DashboardFrame></ModeNoticeContext.Provider>;
}

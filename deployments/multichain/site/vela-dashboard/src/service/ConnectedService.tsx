import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useWalletSession } from '../../../src/wallet-session.jsx';
import { request, selectWalletNetwork } from '../../../src/wallet.js';
import { metamaskProvider } from '../../../src/wallet-discovery.js';
import { AccountService, type ApiRequest } from '@/lib/vela/accountService';
import { SimpleDashboard } from './simple/SimpleDashboard';
import { DogeLendingWorkflow } from '@/lib/vela/dogeLending';
import { Button } from '@/components/ui/button';
import { NativeVaultWorkflow } from '@/lib/vela/nativeVault';
import { INTEGRATION } from '@/lib/vela/integrationConfig';
import { ModeNoticeContext } from '@/components/common/SourceTag';
import { RuntimeConfigStore } from '@/lib/vela/runtimeConfig';
import { ProfileProvider, ProfileMenu } from './approved/ProfilePreferences';
import { WalletBalanceStore } from '@/lib/vela/walletBalance';
import { WalletBalanceContext } from './approved/WalletBalance';
import { isMobileBrowser, metamaskDappLink } from '@/lib/vela/mobileWallet';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';

/** Reuses Vela's wallet discovery and auth. The explicit testnet build mounts it;
 * the ordinary preview stays inactive. Backend capability and wallet reviews still gate writes. */
export function ConnectedService({ testnetRelease = false }: { testnetRelease?: boolean }) {
  const configStore = useMemo(() => new RuntimeConfigStore(signal => request('/api/config', undefined, undefined, undefined, {signal,network:{id:'dogeos'}})), []);
  const runtime = useSyncExternalStore(configStore.subscribe,configStore.snapshot,configStore.snapshot);
  const config = runtime.config;
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [walletChoices, setWalletChoices] = useState(false);
  const [dogeSdk, setDogeSdk] = useState<typeof import('../../../src/mydoge.jsx').default | null>(null);
  const [dogeLoading, setDogeLoading] = useState(false);
  const dogeRequested = useRef(false);
  const wallet = useWalletSession(config, { onClear: () => setNotice(''), onError: setError,
    onNotice: (message: string) => setNotice(message.startsWith('Wallet signature verified.') ? 'Wallet connected.' : message) });
  const openDogeWallet = async () => {
    setWalletChoices(false);setError('');dogeRequested.current=true;
    if(wallet.sdk){dogeRequested.current=false;wallet.connectMyDoge();return;}
    setDogeLoading(true);
    try {const module=await import('../../../src/mydoge.jsx');setDogeSdk(()=>module.default);}
    catch {dogeRequested.current=false;setDogeLoading(false);setError('DogeOS wallet could not load. Tap Connect wallet to retry.');}
  };
  useEffect(()=>{
    if(dogeRequested.current && wallet.sdk){dogeRequested.current=false;setDogeLoading(false);wallet.connectMyDoge();}
  },[wallet.sdk]);
  const DogeSdk=dogeSdk;
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
  const balanceStore=useMemo(()=>wallet.authenticated&&provider?new WalletBalanceStore(provider,wallet.address):null,[wallet.authenticated,provider,wallet.address,generation]);
  const emptyBalance=useMemo(()=>({balanceWei:null,loading:false,error:'',at:null}),[]);
  const balance=useSyncExternalStore(balanceStore?.subscribe??(()=>()=>{}),balanceStore?.snapshot??(()=>emptyBalance),()=>emptyBalance);
  const refreshBalance=useMemo(()=>()=>balanceStore?.refresh()??Promise.resolve(),[balanceStore]);
  useEffect(()=>{
    if(!balanceStore)return;
    const refresh=()=>{if(document.visibilityState==='visible')void balanceStore.refresh();};
    refresh();const timer=setInterval(refresh,30000);
    window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);
    return()=>{clearInterval(timer);window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh);balanceStore.stop();};
  },[balanceStore]);
  const writesEnabled = (testnetRelease || INTEGRATION.writesEnabled) && runtime.writesEnabled;
  const native = useMemo(() => service && provider ? new NativeVaultWorkflow({
    service, provider, storage: localStorage,
    writesEnabled,
    withLock: async (name, fn) => {
      if (!navigator.locks) throw new Error('This browser cannot coordinate wallet transactions safely');
      return navigator.locks.request(name, fn);
    },
  }) : null, [service, provider, writesEnabled]);
  const dogeWorkflow=useMemo(()=>service&&provider?new DogeLendingWorkflow({service,provider,storage:localStorage,withLock:async(name,fn)=>{if(!navigator.locks)throw Error('This browser cannot coordinate wallet transactions safely');return navigator.locks.request(name,fn);}}):null,[service,provider]);
  const openMobileWallet = testnetRelease && !wallet.authenticated && !wallet.connecting && isMobileBrowser() && !metamaskProvider();
  return <ProfileProvider key={wallet.address||'guest'} wallet={wallet.address||'guest'} defaultTheme="day"><ModeNoticeContext.Provider value={true}><WalletBalanceContext.Provider value={{...balance,refresh:refreshBalance}}><SimpleDashboard service={service} native={native} dogeWorkflow={dogeWorkflow} refreshConfig={configStore.refresh} basePath={testnetRelease?'/app':''} wallet={<div className="flex items-center gap-2">
    {wallet.authenticated ? <Button title={wallet.address} variant="outline" onClick={() => wallet.logout(true)}>{wallet.address.slice(0, 6)}…{wallet.address.slice(-4)} · Disconnect</Button> : <Button disabled={!config || wallet.connecting} onClick={() => setWalletChoices(true)}>{wallet.retryAvailable ? 'Retry connection' : 'Connect wallet'}</Button>}
    {wallet.connecting && <Button variant="outline" onClick={wallet.cancelConnect}>Cancel connection</Button>}
    <ProfileMenu />
  </div>}>
    {config && DogeSdk && <DogeSdk config={config} onChange={wallet.onSdk} />}
    <Dialog open={walletChoices} onOpenChange={setWalletChoices}><DialogContent className="max-w-sm"><DialogHeader><DialogTitle>Connect wallet</DialogTitle><DialogDescription>Choose your wallet to continue on DogeOS.</DialogDescription></DialogHeader><div className="grid gap-3">
      {openMobileWallet ? <Button asChild><a href={metamaskDappLink(location.pathname)} onClick={event=>{const injected=metamaskProvider();if(injected){event.preventDefault();setWalletChoices(false);void wallet.connect(injected,'injected');}}}>MetaMask · Open app</a></Button> : <Button onClick={()=>{setWalletChoices(false);void wallet.connect(metamaskProvider(),'injected');}}>MetaMask</Button>}
      <Button variant="outline" disabled={dogeLoading} onClick={()=>void openDogeWallet()}>DogeOS Wallet / MyDoge</Button>
      {openMobileWallet && <p className="text-sm text-muted-foreground">MetaMask opens Vela inside its app. DogeOS Wallet works here with email or your MyDoge wallet.</p>}
    </div></DialogContent></Dialog>
    {dogeLoading && <p role="status">Opening DogeOS wallet… <Button variant="outline" onClick={()=>{dogeRequested.current=false;setDogeLoading(false);}}>Cancel</Button></p>}
    {wallet.connecting && <p role="status">Waiting for wallet approval · timeout after 45 seconds.</p>}
    {error && <p role="alert" className="text-destructive">{error}</p>}{notice && <p role="status">{notice}</p>}
    {runtime.error && <p role="alert" className="text-destructive">{runtime.error}</p>}
    {!service && <p className="text-sm text-muted-foreground">Connect MetaMask or DogeOS Wallet and approve the Vela sign-in message to load your account.</p>}
  </SimpleDashboard></WalletBalanceContext.Provider></ModeNoticeContext.Provider></ProfileProvider>;
}

import {getWallets} from '@wallet-standard/app';

const announcements=new Map(),adapters=new WeakMap(),listeners=new Set();
let initialized=false;
const notify=()=>listeners.forEach(fn=>fn());
export function initializeWalletDiscovery(){
 if(initialized||typeof window==='undefined')return;
 initialized=true;
 window.addEventListener('eip6963:announceProvider',({detail})=>{
  if(detail?.provider?.request&&detail.info?.uuid){announcements.set(detail.info.uuid,detail);notify();}
 });
 window.addEventListener('ethereum#initialized',notify);
 const wallets=getWallets();wallets.on('register',notify);wallets.on('unregister',notify);
 window.dispatchEvent(new Event('eip6963:requestProvider'));
}
export function watchWallets(listener){initializeWalletDiscovery();listeners.add(listener);return()=>listeners.delete(listener);}
export function metamaskProvider(host=globalThis.window){
 initializeWalletDiscovery();
 const announced=[...announcements.values()].find(x=>x.info.rdns==='io.metamask'&&typeof x.provider.request==='function');
 return announced?.provider||host?.ethereum?.providers?.find(p=>p.isMetaMask&&!p.isPhantom)||(host?.ethereum?.isMetaMask&&!host.ethereum.isPhantom?host.ethereum:null);
}
export function standardSolanaAdapter(wallet){
 if(adapters.has(wallet))return adapters.get(wallet);
 const compatible=account=>account?.chains?.some(c=>c.startsWith('solana:'))&&account.features?.includes('solana:signMessage');
 const account=()=>wallet.accounts.find(compatible);
 const subscriptions=new Map();
 const adapter={
  get publicKey(){const a=account();return a?{toString:()=>a.address}:null;},
  get isConnected(){return Boolean(account());},
  async connect(options){await wallet.features['standard:connect'].connect(options?.onlyIfTrusted?{silent:true}:undefined);if(!account())throw Error('Choose a Solana account with message signing in your wallet.');return {publicKey:this.publicKey};},
  async signMessage(message){const a=account();if(!a)throw Error('Solana wallet disconnected');const [signed]=await wallet.features['solana:signMessage'].signMessage({account:a,message});if(!signed?.signature)throw Error('Wallet did not return a message signature');return {signature:signed.signature};},
  async disconnect(){await wallet.features['standard:disconnect']?.disconnect();},
  on(event,fn){if(!['accountChanged','disconnect'].includes(event))return;const off=wallet.features['standard:events']?.on('change',change=>{if('accounts' in change){if(event==='accountChanged')fn(this.publicKey);else if(!account())fn();}});if(off){const keys=subscriptions.get(fn)||new Map();keys.set(event,off);subscriptions.set(fn,keys);}},
  removeListener(event,fn){const keys=subscriptions.get(fn);keys?.get(event)?.();keys?.delete(event);if(!keys?.size)subscriptions.delete(fn);},
 };
 adapters.set(wallet,adapter);return adapter;
}
export function phantomProvider(host=globalThis.window){
 initializeWalletDiscovery();
 const injected=host?.phantom?.solana||host?.solana;
 if(injected?.isPhantom&&typeof injected.connect==='function'&&typeof injected.signMessage==='function')return injected;
 const wallet=getWallets().get().find(w=>/^phantom$/i.test(w.name)&&w.chains?.some(c=>c.startsWith('solana:'))&&w.features?.['standard:connect']&&w.features?.['solana:signMessage']);
 return wallet?standardSolanaAdapter(wallet):null;
}
export function walletError(error){
 const code=Number(error?.code??error?.data?.originalError?.code);
 if(code===4001||/reject|declin|cancel/i.test(error?.message||''))return 'Wallet request declined. You remain disconnected. Connect again when you are ready.';
 if(code===-32002)return 'A wallet request is already open. Open your wallet and approve or reject that request before trying again.';
 if(code===4902)return 'This network is not available in your wallet. Approve adding the selected network, then try again.';
 if(code===4200||code===4901)return 'Your wallet does not support this network request. Select the network in your wallet, then reconnect.';
 return error?.message||'Wallet connection could not be completed. Unlock your wallet and try again.';
}

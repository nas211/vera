import {useState,useEffect,useRef,useCallback} from 'react';
import {request,authenticate,prepareWallet,restoreSession,sdkMatches,CHAIN_ID,walletNetwork} from './wallet.js';
import {metamaskProvider,phantomProvider,watchWallets,walletError} from './wallet-discovery.js';
import {walletAttempt} from './wallet-attempt.js';

const storageKey='autodefi.wallet.connector';
function selectedWallet(){try{return localStorage.getItem(storageKey);}catch{return null;}}
function remember(kind){try{kind?localStorage.setItem(storageKey,kind):localStorage.removeItem(storageKey);}catch{}}

export function useWalletSession(config,{onClear,onError,onNotice}){
 const [phase,setPhase]=useState('DISCONNECTED'),[address,setAddress]=useState(''),[kind,setKind]=useState(''),[sdk,setSdk]=useState(null);
 const provider=useRef(null),generation=useRef(0),addressRef=useRef(''),sdkRef=useRef(null);
 const selected=useRef(selectedWallet()),restored=useRef(false),pending=useRef(false),modalWasOpen=useRef(false),connecting=useRef(false);
 const callbacks=useRef();callbacks.current={onClear,onError,onNotice};
 const [discovery,setDiscovery]=useState(0);
 const attempt=useRef(null),restoreAbort=useRef(null);
 const [retryAvailable,setRetryAvailable]=useState(false);
 useEffect(()=>()=>{attempt.current?.cancel();restoreAbort.current?.abort();},[]);
 useEffect(()=>{const update=()=>setDiscovery(n=>n+1);const off=watchWallets(update);const timer=phase==='DISCONNECTED'?setInterval(update,1500):null;return()=>{off();clearInterval(timer);};},[phase]);
 const onSdk=useCallback(value=>{sdkRef.current=value;setSdk(value);},[]);
 const authenticated=phase==='DASHBOARD'&&(kind!=='mydoge'||sdkMatches(sdk,address,provider.current));
 function clear(){
  attempt.current?.cancel();attempt.current=null;connecting.current=false;
  restoreAbort.current?.abort();restoreAbort.current=null;
  generation.current++;addressRef.current='';provider.current=null;pending.current=false;
  selected.current=null;remember(null);setAddress('');setKind('');setPhase('DISCONNECTED');callbacks.current.onClear();
 }
 function cancelConnect(){
  clear();setRetryAvailable(true);
  callbacks.current.onError('Connection cancelled. Dismiss any pending prompt in your wallet before Retry, or choose another network.');
 }
 async function logout(disconnectWallet=false){
  const disconnect=kind==='mydoge'?sdkRef.current?.disconnect:null;
  clear();
  try{await request('/api/logout',{});}catch{callbacks.current.onError('Private view cleared; session revocation could not be confirmed. Retry disconnect.');}
  if(disconnectWallet&&disconnect)try{await disconnect();}catch{callbacks.current.onError('Session closed. Disconnect the wallet from its account menu.');}
 }
 async function connect(p,walletKind='injected'){
  if(connecting.current||!config)return;
  if(walletNetwork().family==='solana'?!p?.signMessage:!p?.request){callbacks.current.onError(walletNetwork().family==='solana'?'No compatible Solana wallet found. Open Phantom or a compatible wallet browser.':'No EVM wallet provider found. Open a compatible wallet browser.');return;}
  restored.current=true;pending.current=false;clear();connecting.current=true;setRetryAvailable(false);
  const gen=generation.current;provider.current=p;setKind(walletKind);setPhase('CONNECTING');callbacks.current.onError('');
  const run=walletAttempt(()=>{if(gen===generation.current){clear();setRetryAvailable(true);callbacks.current.onError('Wallet connection timed out after 45 seconds. Dismiss any pending wallet prompt, then Retry. You can also switch networks.');}});
  attempt.current=run;
  const network=walletNetwork(),currentAttempt=()=>gen===generation.current&&attempt.current===run&&run.current();
  const api=(path,body,address,key)=>{
   if(!currentAttempt())throw Error('Connection cancelled');
   return request(path,body,address,key,{signal:run.signal,network});
  };
  try{
   await api('/api/logout',{});
   if(!currentAttempt())throw Error('Wallet changed');
   if(walletKind==='mydoge'&&Number(sdkRef.current?.chainId)!==CHAIN_ID){
    await sdkRef.current.switchNetwork();
    // SDK hooks may publish the new provider after switchChain resolves.
    for(let i=0;i<60&&Number(sdkRef.current?.chainId)!==CHAIN_ID;i++)await new Promise(r=>setTimeout(r,50));
    p=sdkRef.current?.provider;
   }
   if(!currentAttempt())throw Error('Wallet changed');
   const expected=await prepareWallet(p,config.network,{alreadyConnected:walletKind==='mydoge',isCurrent:currentAttempt});
   if(!currentAttempt())throw Error('Wallet changed');
   provider.current=p;
   const isCurrent=()=>currentAttempt()&&(walletKind!=='mydoge'||sdkMatches(sdkRef.current,expected,p));
   const wallet=await authenticate(p,api,s=>{if(currentAttempt())setPhase(s);},
    {isCurrent,alreadyConnected:true,...(walletKind==='mydoge'?{signMessage:sdkRef.current.signMessage}:{})});
   if(!isCurrent())throw Error('Wallet changed');
   selected.current=walletKind;remember(walletKind);setAddress(wallet);addressRef.current=wallet;setPhase('DASHBOARD');
   callbacks.current.onNotice('Wallet signature verified. Testnet collateral and simulated funds are shown separately.');
  }catch(e){if(currentAttempt()){clear();setRetryAvailable(true);callbacks.current.onError(walletError(e));}}
  finally{run.finish();if(attempt.current===run){attempt.current=null;connecting.current=false;}}
 }
 function connectMyDoge(){
  if(connecting.current||!sdk)return;
  if(sdk.isConnected&&sdk.provider&&sdk.chainType==='evm')connect(sdk.provider,'mydoge');
  else{pending.current=true;modalWasOpen.current=false;sdk.open();}
 }
 useEffect(()=>{
  if(sdk?.error){pending.current=false;callbacks.current.onError(String(sdk.error));}
  if(pending.current&&sdk?.isConnected&&sdk.provider&&sdk.chainType==='evm'){
   pending.current=false;connect(sdk.provider,'mydoge');
  }else if(pending.current&&modalWasOpen.current&&!sdk?.isOpenModal&&!sdk?.isConnecting)pending.current=false;
  modalWasOpen.current=Boolean(sdk?.isOpenModal);
  if(phase==='DASHBOARD'&&kind==='mydoge'&&!sdkMatches(sdk,address,provider.current)){
   logout();callbacks.current.onNotice('MyDoge account or network changed. Sign in again.');
  }
 },[sdk]);
 useEffect(()=>{
  if(!config||restored.current||connecting.current||phase!=='DISCONNECTED')return;
  const walletKind=selected.current;
  const p=walletKind==='solana'?phantomProvider():walletKind==='injected'?metamaskProvider():null;
  if(!p)return;
  restored.current=true;const gen=generation.current;
  const controller=new AbortController(),network=walletNetwork();restoreAbort.current=controller;
  const isCurrent=()=>gen===generation.current&&(walletKind!=='mydoge'||sdkMatches(sdkRef.current,sdk?.address?.toLowerCase(),p));
  const api=async(path,body)=>{
   if(!isCurrent())throw Error('Wallet changed');
   const result=await request(path,body,undefined,undefined,{signal:controller.signal,network});
   if(!isCurrent())throw Error('Wallet changed');return result;
  };
  (async()=>{
   try{
    // A trusted reconnect never establishes authentication by itself. Both the
    // existing server session and the current wallet account must still match.
    if(walletKind==='solana'){
     const session=await api('/api/session');
     if(!session.authenticated)throw Error('Sign in again');
     if(!p.publicKey||p.isConnected===false)await p.connect({onlyIfTrusted:true});
    }
    const owner=await restoreSession(p,api,isCurrent);
    if(!isCurrent())return;
    provider.current=p;setKind(walletKind);setAddress(owner);addressRef.current=owner;setPhase('DASHBOARD');
   }catch(e){if(gen===generation.current){clear();if(e.status!==401)callbacks.current.onNotice('Session could not be restored. Connect and sign in again.');}}
  })();
 },[config,sdk,phase,discovery]);
 useEffect(()=>{
  if(!['SIGNING','AUTHENTICATED','DASHBOARD'].includes(phase))return;
  const p=provider.current,gen=generation.current;
  const invalidate=()=>{if(gen===generation.current){logout();callbacks.current.onNotice('Wallet changed. Reconnect and sign in again.');}};
  for(const event of ['accountsChanged','accountChanged','chainChanged','disconnect'])p?.on?.(event,invalidate);
  return()=>{for(const event of ['accountsChanged','accountChanged','chainChanged','disconnect'])p?.removeListener?.(event,invalidate);};
 },[phase,address]);
 return {phase,address,authenticated,provider,generation,addressRef,sdk,onSdk,connect,connectMyDoge,clear,logout,cancelConnect,retryAvailable,
  connecting:['CONNECTING','SIGNING','AUTHENTICATED'].includes(phase)};
}

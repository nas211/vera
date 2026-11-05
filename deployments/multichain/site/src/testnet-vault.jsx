import React,{useEffect,useRef,useState} from 'react';
import {request,formatDoge} from './wallet.js';
import {FAUCET,EXPLORER,pendingTransaction,prepareTransaction,submitTransaction,reconcileTransaction,attachPendingHash,validateObservation} from './testnet-vault.js';

export function TestnetVault({address,provider,config,onTrade}){
 const [state,setState]=useState(null),[pending,setPending]=useState(null),[quote,setQuote]=useState(null),[amount,setAmount]=useState(''),[hash,setHash]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const active=useRef(true),working=useRef(false);
 const storage=()=>window.localStorage;
 const readState=tx=>request('/api/dogeos/account'+(tx?'?tx='+encodeURIComponent(tx):''),undefined,address);
 async function refresh(){
  const p=pendingTransaction(storage(),address);if(active.current)setPending(p);
  if(p?.hash){
   const result=await reconcileTransaction({owner:address,pending:p,readState,storage:storage()});
   if(!active.current)return;
   setState(result.state);
   if(!result.pending){setPending(null);setQuote(null);setNotice(result.reverted?'Transaction reverted. No deposit or withdrawal was credited. Network fees may still apply.':`Confirmed ${p.action==='createVault'?'vault creation':p.action} on DogeOS. Updated balances are from the confirmed contract state.`);}
  }else{
   const s=validateObservation(await readState(),address);if(active.current)setState(s);
  }
 }
 async function run(fn){
  if(working.current)return;working.current=true;setBusy(true);setError('');
  try{await fn();}catch(e){if(active.current){setError(e.message);setQuote(null);try{setPending(pendingTransaction(storage(),address));}catch{}}}
  finally{working.current=false;if(active.current)setBusy(false);}
 }
 useEffect(()=>{
  active.current=true;
  const tick=()=>{if(working.current)return;working.current=true;refresh().catch(e=>{if(active.current){setError(e.message);setState(null);}}).finally(()=>{working.current=false;});};
  tick();const timer=setInterval(tick,6000);return()=>{active.current=false;clearInterval(timer);};
 },[address]);
 const enabled=config.dogeosWallet?.enabled===true,v=state?.vault;
 const review=action=>run(async()=>{setNotice('');const latest=validateObservation(await readState(),address);setState(latest);setQuote(await prepareTransaction({provider,owner:address,config,state:latest,action,amount}));});
 const send=()=>run(async()=>{
  const locks=globalThis.navigator?.locks;
  const p=await submitTransaction({provider,owner:address,config,quote,storage:storage(),readState,
   withLock:locks?((key,fn)=>locks.request(key,fn)):null});
  if(!active.current)return;setPending(p);setQuote(null);setNotice('Submitted. Waiting for three confirmations and verified vault state.');await refresh();
 });
 return <article className="panel simple-card" aria-label="Native test DOGE vault">
  <p className="eyebrow">DOGEOS CHIKYŪ TESTNET · 6281971</p><h2>Your test DOGE vault</h2>
  <p className="simple-help">Deposits move native test DOGE from your wallet into your own vault. They do not create USDC, fund paper trades, or earn yield.</p>
  <div className="simple-stats"><div><small>In your wallet</small><strong>{state?formatDoge(state.walletBalanceWei)+' test DOGE':'Checking…'}</strong></div><div><small>In your vault</small><strong>{state?formatDoge(v?.collateralWei||'0')+' test DOGE':'Checking…'}</strong></div><div><small>Unlocked for withdrawal</small><strong>{state?formatDoge(v?.withdrawableWei||'0')+' test DOGE':'Checking…'}</strong></div></div>
  {state&&<p className="small">Confirmed block {state.block}. {v?<a className="address" href={`${EXPLORER}/address/${v.address}`} target="_blank" rel="noopener noreferrer">Your registered vault ↗</a>:'No vault registered for this wallet.'}</p>}
  {!enabled&&<p className="notice">Wallet deposits and withdrawals are not enabled on this deployment. On-chain balances are read only.</p>}
  {pending?<div role="status" className="notice"><strong>Pending {pending.action==='createVault'?'vault creation':pending.action}</strong><p>Check this transaction before sending another. Closing this page does not cancel a submitted transaction.</p>{pending.hash?<a className="address" href={`${EXPLORER}/tx/${pending.hash}`} target="_blank" rel="noopener noreferrer">View transaction ↗</a>:<><p>The wallet has not returned a transaction hash. Check its activity history.</p><label>Transaction hash<input value={hash} onChange={e=>setHash(e.target.value.trim())} placeholder="0x…"/></label><button disabled={busy} onClick={()=>run(async()=>{attachPendingHash(storage(),address,hash);await refresh();})}>Check wallet transaction</button></>}<button disabled={busy} onClick={()=>run(refresh)}>Check confirmation</button></div>:<>
   {!v?<button className="primary simple-submit" disabled={busy||!enabled||!state} onClick={()=>review('createVault')}>Create my testnet vault</button>:<>
    <label className="simple-question" htmlFor="native-doge-amount">Test DOGE amount</label><div className="simple-input"><input id="native-doge-amount" inputMode="decimal" autoComplete="off" value={amount} onChange={e=>{setAmount(e.target.value.trim());setQuote(null);}} placeholder="0"/><span>test DOGE</span></div>
    <div className="button-row"><button className="primary" disabled={busy||!enabled||v.paused||!amount} onClick={()=>review('deposit')}>Review deposit</button><button disabled={busy||!enabled||BigInt(v.withdrawableWei||'0')<=0n||!amount} onClick={()=>review('withdraw')}>Review withdrawal</button></div>
    {v.paused&&<p className="notice">This vault is paused. Deposits and withdrawals are unavailable.</p>}
   </>}
  </>}
  {quote&&!pending&&<section className="notice" aria-label="Review testnet transaction"><h3>Review {quote.action==='createVault'?'vault creation':quote.action}</h3><p>{quote.action==='createVault'?'Create a vault owned by your connected wallet.':`${quote.amount} test DOGE ${quote.action==='deposit'?'from your wallet to your vault':'from your vault to your wallet'}.`}</p><p>Fee reserve: approximately {formatDoge(quote.feeReserveWei)} test DOGE, including execution and data/finality fees with a buffer. Actual fees may change; your wallet asks for approval.</p><button className="primary" disabled={busy} onClick={send}>Approve in wallet</button><button disabled={busy} onClick={()=>setQuote(null)}>Cancel</button></section>}
  {error&&<p className="alert" role="alert">{error}</p>}{notice&&<p className="status-note" role="status">{notice}</p>}
  <div className="button-row"><a href={FAUCET} target="_blank" rel="noopener noreferrer">Get test DOGE from the official faucet ↗</a><button disabled={busy} onClick={()=>run(refresh)}>Refresh on-chain balances</button></div>
  <p className="small">Paper trading uses separate, non-redeemable demo USD. DogeOS USDC borrowing and loan settlement are unavailable.</p><button className="text-button" onClick={onTrade}>Open paper trading →</button>
 </article>;
}

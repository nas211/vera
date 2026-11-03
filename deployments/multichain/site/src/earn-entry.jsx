import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {loadWalletNetwork,request,shortAddress} from './wallet.js';
import {useWalletSession} from './wallet-session.jsx';
import {metamaskProvider,phantomProvider} from './wallet-discovery.js';

const amountText=value=>Number(value||0).toLocaleString('en-US',{maximumFractionDigits:6});
function EarnAccount(){
 const [config,setConfig]=useState(null),[account,setAccount]=useState(null),[stake,setStake]=useState(null);
 const [amount,setAmount]=useState('100'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const operation=useRef({});
 const wallet=useWalletSession(config,{onClear:()=>{setAccount(null);setStake(null);},onError:setError,onNotice:setNotice});
 const {address,authenticated,connecting,generation}=wallet;
 useEffect(()=>{let stopped=false,id='dogeos';try{const saved=localStorage.getItem('vella:network');if(['dogeos','solana','base','ethereum','bnb','arbitrum','robinhood'].includes(saved))id=saved;}catch{}loadWalletNetwork(id).then(c=>{if(!stopped)setConfig(c);}).catch(e=>setError(e.message));return()=>{stopped=true;};},[]);
 async function refresh(){
  const gen=generation.current;
  const [a,s]=await Promise.all([request('/api/account',undefined,address),request('/api/staking',undefined,address)]);
  if(gen===generation.current){setAccount(a);setStake(s);}
 }
 useEffect(()=>{
  if(!authenticated)return;
  let ended=false,running=false;
  const tick=async()=>{if(running||ended)return;running=true;try{await refresh();}catch(e){if(!ended){setError(e.message);if(e.status===401||e.status===403)wallet.clear();}}finally{running=false;}};
  tick();const timer=setInterval(tick,5000);return()=>{ended=true;clearInterval(timer);};
 },[authenticated,address,config]);
 async function choose(id){
  setBusy(true);setError('');
  try{await wallet.logout();setConfig(await loadWalletNetwork(id));try{localStorage.setItem('vella:network',id);}catch{}setNotice('Sign in to the selected network’s separate demo account.');}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 function connect(){
  const solana=config?.chain?.family==='solana',provider=solana?phantomProvider():metamaskProvider();
  if(!provider){setError(solana?'Open this page in Phantom’s browser or install its extension.':'Open this page in MetaMask’s browser or install its extension.');return;}
  wallet.connect(provider,solana?'solana':'injected');
 }
 async function act(action){
  if(busy||connecting)return;
  const body={confirm:true,...(action==='claim'?{}:{amount})},signature=JSON.stringify([action,body,address,config.chain.id]);
  if(operation.current.signature!==signature)operation.current={signature,key:crypto.randomUUID()};
  const gen=generation.current;setBusy(true);setError('');
  try{
   const path=action==='allocate'?'/api/trading/allocate':'/api/staking/'+action;
   await request(path,body,address,operation.current.key);
   if(gen===generation.current){operation.current={};await refresh();setNotice({allocate:'Demo funds added. No real deposit was made.',deposit:'Demo USDC staked. No tokens left your wallet.',withdraw:'Demo principal returned to your available balance.',claim:'Demo reward credited to your available balance.'}[action]);}
  }catch(e){if(gen===generation.current){setError(e.message);if(e.status===401||e.status===403)wallet.clear();}}
  finally{setBusy(false);}
 }
 const disabled=busy||connecting;
 return <>
  <div className="staking-token" aria-hidden="true">Ð</div><h2>DogeOS first.<br/>Demo staking.</h2>
  <p>Explore the staking lifecycle with simulated USDC. DogeOS is featured; other networks keep their own demo accounts.</p>
  <label className="earn-account-field">Network<select aria-label="Staking network" value={config?.chain?.id||'dogeos'} disabled={disabled||!config} onChange={e=>choose(e.target.value)}>{(config?.chains||[]).map(c=><option key={c.id} value={c.id}>{c.id==='dogeos'?'Featured · ':''}{c.name}</option>)}</select></label>
  {!authenticated?<><button className="button button-gold" disabled={disabled||!config} onClick={connect}>{connecting?'Approve wallet sign-in…':config?.chain?.family==='solana'?'Connect Phantom':'Connect MetaMask'}</button><small>Sign-in only. No token transfer or spending approval.</small></>:<>
   <p className="earn-wallet-id">{shortAddress(address)} <button className="text-link" disabled={disabled} onClick={()=>wallet.logout()}>Disconnect</button></p>
   <div className="staking-facts"><div><span>Available demo USDC</span><strong data-testid="earn-balance">{amountText(account?.paper?.balance)}</strong></div><div><span>Staked demo USDC</span><strong data-testid="earn-principal">{amountText(stake?.principal)}</strong></div><div><span>Claimable demo reward</span><strong data-testid="earn-reward">{amountText(stake?.claimable)}</strong></div><div><span>Assumed simple annual rate</span><strong>{stake?Number(stake.illustrativeAnnualRate)*100+'%':'—'}</strong></div></div>
   <label className="earn-account-field">Demo USDC amount<input aria-label="Demo staking amount" inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)} disabled={disabled}/></label>
   <div className="earn-wallet-actions"><button className="button button-outline" disabled={disabled} onClick={()=>act('allocate')}>Add demo funds</button><button className="button button-gold" disabled={disabled||!stake||stake.depositsPaused} onClick={()=>act('deposit')}>Stake demo USDC</button><button className="button button-outline" disabled={disabled||!stake||Number(stake.principal)<=0} onClick={()=>act('withdraw')}>Withdraw principal</button><button className="button button-outline" disabled={disabled||!stake||Number(stake.claimable)<=0} onClick={()=>act('claim')}>Claim demo reward</button></div>
   <small>No lockup. The shared demo reward pool limits accrual. Stop AI entries and close positions before staking trading capital.</small>
  </>}
  {error&&<p className="earn-account-error" role="alert">{error}</p>}{notice&&<p className="earn-account-notice" role="status">{notice}</p>}
  <small>Simulated accounting only. No real staking, funded rewards, token transfers or guaranteed returns.</small>
  <a className="text-link" href="/paper">Open the paper desk ↗</a>
 </>;
}
createRoot(document.getElementById('earn-account')).render(<EarnAccount/>);

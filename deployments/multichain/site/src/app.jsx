import React,{useState,useEffect,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {encodeFunctionData} from 'viem';
import {request,current,walletBalance,units,shortAddress,loadWalletNetwork,formatDoge} from './wallet.js';
import {NetworkSelector} from './network-selector.jsx';
import {useWalletSession} from './wallet-session.jsx';
import './style.css';
import Landing,{HomeNav,AppSectionLinks} from './landing.jsx';
import {GuidedDashboard} from './guided-app.jsx';
import {WalletWelcome,DepositConfirmation,DogeOSVault} from './product-ui.jsx';
import HowItWorks from './how-it-works.jsx';
import {ROUTES,tabForPath,navigate} from './navigation.js';
import {validTab} from './guided-state.js';
import {amountProblem,spendableInput,accountReadFence} from './flow-validation.js';
import {quickPlan} from './module-controls.jsx';
const dollars=x=>x===null||x===undefined?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(Number(x));
const num=x=>new Intl.NumberFormat('en-US',{maximumFractionDigits:3}).format(Number(x||0));
export {WalletWelcome as Welcome};
export function App({page=typeof window!=='undefined'?window.location.pathname:'/'}={}){
 const [route,setRoute]=useState(page),[receipt,setReceipt]=useState(null);
 const isPortal=Boolean(tabForPath(route)),isHow=route==='/how-it-works';
 useEffect(()=>{
  const update=()=>{const legacy=window.location.pathname==='/vault'&&validTab(window.location.hash.slice(1));const path=legacy?ROUTES[legacy]:window.location.pathname;if(legacy)window.history.replaceState(null,'',path);setRoute(path);window.scrollTo({top:0,behavior:'instant'});};
  const click=e=>{const link=e.target.closest?.('a');if(!link||e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||link.target||link.hasAttribute('download'))return;const url=new URL(link.href);if(url.origin!==location.origin||(!tabForPath(url.pathname)&&!['/','/how-it-works'].includes(url.pathname))||(url.hash&&url.pathname===location.pathname))return;e.preventDefault();navigate(url.pathname+url.hash);};
  update();document.addEventListener('click',click);const events=['popstate','hashchange','vela:navigate'];events.forEach(e=>window.addEventListener(e,update));return()=>{document.removeEventListener('click',click);events.forEach(e=>window.removeEventListener(e,update));};
 },[]);
 const [config,setConfig]=useState(null),[data,setData]=useState(null),[markets,setMarkets]=useState([]),[nativeBalance,setNativeBalance]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[actionBusy,setBusy]=useState(false),[modal,setModal]=useState(null),[amount,setAmount]=useState(''),[autoReduce,setAutoReduce]=useState(false),[asset,setAsset]=useState('');
 const wallet=useWalletSession(isPortal?config:null,{onClear:()=>{setReceipt(null);setData(null);setMarkets([]);setNativeBalance(null);setModal(null);setNotice('');setAutoReduce(false);setAmount('');setAsset('');},onError:setError,onNotice:setNotice});
 const {phase,address,authenticated,provider,generation,addressRef,clear,connect,sdk,onSdk,connectMyDoge}=wallet;
 const busy=actionBusy||wallet.connecting;
 const operation=useRef({}),reads=useRef(accountReadFence()),mutating=useRef(false),actionNotice=useRef('');
 function announce(message){actionNotice.current=message;}
 async function refresh(owner=addressRef.current){const gen=generation.current;if(!owner)return;const latest=reads.current.begin();try{const result=await request('/api/account',undefined,owner);if(latest()&&gen===generation.current&&owner===addressRef.current){setData(result);return result;}}catch(e){if(latest()&&gen===generation.current&&(e.status===401||e.status===403))clear();throw e;}}
 useEffect(()=>{let id='dogeos';{try{const saved=localStorage.getItem('vella:network');if(['dogeos','solana','base','ethereum','bnb','arbitrum','robinhood'].includes(saved))id=saved;}catch{}}loadWalletNetwork(id).then(c=>{setConfig(c);if(/^#[0-9a-f]{6}$/i.test(c.brand.accent))document.documentElement.style.setProperty('--accent',c.brand.accent);}).catch(e=>setError(e.message));},[]);
 useEffect(()=>{
  if(!authenticated||!isPortal)return;
  let stopped=false,running=false;const p=provider.current,gen=generation.current;
  const active=()=>!stopped&&gen===generation.current;
  const tick=async()=>{
   if(running||mutating.current)return;running=true;
   try{
    try{if(await current(p)!==address)throw Error('Wallet changed');}
    catch(e){if(active()){wallet.logout();setNotice('Wallet unavailable or changed. Sign in again.');}return;}
    await refresh(address);
    const m=await request('/api/markets');if(active())setMarkets(m.markets);
    try{const balance=await walletBalance(p,address);if(active())setNativeBalance(balance);}
    catch{if(active())setNativeBalance(null);}
   }catch(e){if(active())setError(e.message);}finally{running=false;}
  };
  tick();const timer=setInterval(tick,3000);
  return()=>{stopped=true;clearInterval(timer);};
 },[authenticated,address,isPortal]);
 async function action(fn){if(mutating.current)return;const gen=generation.current;mutating.current=true;reads.current.invalidate();actionNotice.current='';setBusy(true);setNotice('');setError('');try{await fn();if(gen===generation.current){await refresh();if(gen===generation.current){setNotice(actionNotice.current);setModal(null);}}}catch(e){if(gen===generation.current){if(e.status===401||e.status===403)clear();setError(e.message);}}finally{mutating.current=false;setBusy(false);}}
 async function switchNetwork(id){
  if(busy)return;setBusy(true);setError('');
  try{await wallet.logout();setData(null);const c=await loadWalletNetwork(id);setConfig(c);try{localStorage.setItem('vella:network',id);}catch{}setNotice('Network changed. Sign in to its separate account.');}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 const simulated=config?.chain?.capabilities.simulatedVault;
 const financialBody=body=>({...body,...(simulated?{vaultId:data?.vault?.address||null,loanId:data?.health?.loan?.id||null}:{})});
 async function simVault(name,body={},key=crypto.randomUUID()){return request('/api/simulated-vault/'+name,financialBody({...body,confirm:true}),address,key);}
 async function write(name,args=[],value=0n,to=data?.vault?.address){
  if(config?.dogeosReadOnly?.customerTransactionsEnabled===false)throw Error('Customer contract transactions are disabled. Use demo funds instead.');
  if(simulated)throw Error('Simulated vaults cannot send wallet transactions');
  const p=provider.current,owner=addressRef.current,gen=generation.current;if(await current(p)!==owner)throw Error('Wallet changed');
  if(!config.factory)throw Error('This project’s testnet factory is not deployed');
  const abi=to===config.factory?config.abis.VaultFactory:config.abis.DogeCollateralVault;
  const tx=await p.request({method:'eth_sendTransaction',params:[{from:owner,to,data:encodeFunctionData({abi,functionName:name,args}),value:'0x'+value.toString(16)}]});
  setNotice('Transaction submitted. Waiting for three confirmations…');
  for(let i=0;i<90;i++){
   if(gen!==generation.current||await current(p)!==owner)throw Error('Wallet changed; verify the transaction in your wallet');
   const r=await p.request({method:'eth_getTransactionReceipt',params:[tx]});
   if(r){if(BigInt(r.status)!==1n)throw Error('Testnet transaction reverted');const head=BigInt(await p.request({method:'eth_blockNumber'}));if(head-BigInt(r.blockNumber)>=2n){await request('/api/vault/sync',{tx},owner);setNotice('Testnet transaction confirmed.');return tx;}}
   await new Promise(r=>setTimeout(r,2000));
  }throw Error('Confirmation timed out. Use Refresh vault; do not repeat the transaction blindly.');
 }
 async function certificate(){const c=await request('/api/certificate',{},address);if(c.action===1)await write('acknowledgeDemoLoan',[BigInt(c.deadline),c.signature]);else await write('settleDemoLoan',[BigInt(c.a),BigInt(c.b),BigInt(c.c),BigInt(c.deadline),c.signature]);}
 async function perform(){const problem=amountProblem(modal,amount,data,config);if(problem)throw Error(problem);const gen=generation.current;const body=JSON.stringify([modal,amount,autoReduce,asset]);if(operation.current.body!==body)operation.current={body,key:crypto.randomUUID()};const id=operation.current.key;
  if(modal==='addCollateral'){let vault=data.vault;if(!vault)vault=(await simVault('create',{asset:asset||config.chain.collateral},id+':create')).vault;
   const result=await request('/api/simulated-vault/deposit',{amount,confirm:true,vaultId:vault.address,loanId:data?.health?.loan?.id||null},address,id);if(gen!==generation.current)return;
   setReceipt({kind:'collateral',amount,asset:result.vault.asset,network:config.chain.name,balance:formatDoge(result.vault.collateral),reference:id});return;}
  if(simulated&&['create','deposit','withdraw','borrow','authorize'].includes(modal)){const result=await simVault(modal,{amount,allocation:amount,autoReduce,asset:asset||config.chain.collateral},id);if(gen!==generation.current)return;if(modal==='deposit')setReceipt({kind:'collateral',amount,asset:result.vault.asset,network:config.chain.name,balance:formatDoge(result.vault.collateral),reference:id});announce('Simulated '+modal+' recorded. No real funds moved.');return;}
  if(modal==='deposit')await write('deposit',[],units(amount));
  if(modal==='borrow'){const loanId='0x'+Array.from(crypto.getRandomValues(new Uint8Array(32)),n=>n.toString(16).padStart(2,'0')).join('');await write('requestDemoBorrow',[loanId,units(amount,6)]);await request('/api/borrow',{},address,id);await certificate();}
  if(modal==='authorize'){const allocation=units(amount,6);await write('authorizeAgent',[data.vault.settlementSigner,{allocation,positionSize:allocation<200000000n?allocation:200000000n,expiresAt:BigInt(Math.floor(Date.now()/1000)+86400),maxLoss:2000000,leverage:1,markets:7,autoReduce}]);}
  if(modal==='withdraw')await write('withdraw',[units(amount)]);
  if(modal==='settle')await request(config.chain?.id==='dogeos'?'/api/trading/settle':'/api/settle',config.chain?.id==='dogeos'?{confirm:true}:financialBody({confirm:true}),address,id);
  if(modal==='liquidate')await request('/api/liquidate',financialBody({confirm:true}),address,id);
  if(modal==='confirmSettlement')await certificate();
  if(modal==='repay'||modal==='repayFull'){const receipt=await request('/api/repay',financialBody({amount,full:modal==='repayFull',confirm:true}),address,id);announce(`Simulated repayment of ${dollars(receipt.amount)} recorded. Remaining debt: ${dollars(receipt.health.debt)}.`);}
  if(modal==='allocate'){const result=await request('/api/trading/allocate',{amount,confirm:true},address,id);const account=await refresh();if(account&&gen===generation.current)setReceipt({kind:'trading',amount:result.amount,asset:'USD',network:config.chain.name,balance:account.paper.balance,reference:id});}
  if(modal==='withdrawDemo')await request('/api/trading/withdraw',{amount,confirm:true},address,id);
  if(modal==='quickStart'&&simulated&&l&&!['REPAID','CANCELLED'].includes(l.state)){await simVault('authorize',{amount,allocation:amount,positionSize:String(Math.min(100,Number(amount))),maxLoss:'2',leverage:'1',autoReduce:false},id+':authorize');await request('/api/trading/start',{confirm:true},address);announce(`AI trading started with your borrowed ${dollars(amount)}. Fee-inclusive stop-trigger budget: $2 or your stricter limit per position.`);return;}
  if(modal==='quickStart'){const plan=quickPlan(data,amount);if(plan.topUp>0)await request('/api/trading/allocate',{amount:plan.topUp.toFixed(2),confirm:true},address,id);await request('/api/trading/permission',{confirm:true,allocation:amount,positionSize:String(plan.positionSize),maxLoss:String(plan.maxLoss),leverage:'1'},address,id+':limits');await request('/api/trading/start',{confirm:true},address);announce(`AI trading started with ${dollars(amount)} demo money. Fee-inclusive stop-trigger budget: ${dollars(plan.maxLoss)} per position.`);}
  if(modal==='start'){if(!simulated&&l&&!['REPAID','CANCELLED'].includes(l.state)&&v&&!v.trading)await write('startTrading');await request('/api/trading/start',{confirm:true},address);}

 }
 function open(name,initial='',extra={}){if(['deposit','allocate','addCollateral'].includes(name))setReceipt(null);if(extra.asset)setAsset(extra.asset);operation.current={};setAmount(name==='withdrawDemo'?spendableInput(initial):String(initial));setModal(name);setError('');}
 const h=data?.health,p=data?.paper,l=h?.loan,v=data?.vault;
 const validation=modal&&data?amountProblem(modal,amount,data,config):'';
 return <div className={isPortal?"shell portal-shell":"shell homepage-shell"}>{!isPortal?<HomeNav/>:<header><a className="wordmark" href="/" aria-label="Home"><span className="brand-mark">V</span><span>{config?.brand?.shortName||'VELA'}<small>AUTONOMOUS FINANCE</small></span></a><nav><a href="/how-it-works">How It Works</a>{!isPortal&&<a className="nav-launch" href="/vault">Launch VELA ↗</a>}<NetworkSelector config={config} busy={busy} onChange={switchNetwork}/>{authenticated&&<button className="text-button" onClick={()=>wallet.logout(true)}>{shortAddress(address)} · Disconnect</button>}</nav></header>}{isPortal&&(!authenticated||!data)&&<AppSectionLinks className="portal-section-links"/>}<main>{isPortal&&!authenticated&&<div className="product-title">VELA / YOUR ACCOUNT</div>}{error&&<div role="alert" className="alert">{error}<button onClick={()=>setError('')} aria-label="Dismiss error">×</button></div>}{notice&&<div role="status" className="status-note">{notice}</div>}
 {receipt&&authenticated&&isPortal&&<DepositConfirmation receipt={receipt} onClose={()=>setReceipt(null)}/>}
 {!isPortal?(isHow?<HowItWorks/>:<Landing/>):!authenticated?<><WalletWelcome config={config} connect={connect} busy={busy} connecting={wallet.connecting} onCancel={wallet.cancelConnect} retryAvailable={wallet.retryAvailable} product={tabForPath(route)}/>{config?.chain?.id==='dogeos'&&tabForPath(route)==='vault'&&<DogeOSVault address=""/>}{phase!=='DISCONNECTED'&&<p role="status">{phase==='SIGNING'?'Sign the authentication message in your wallet.':'Connecting wallet…'}</p>}</>:!data?<div className="panel">Loading your authenticated account…</div>:<GuidedDashboard initialTab={tabForPath(route)} key={`${config?.chain?.id||'dogeos'}:${address}`} {...{config,data,markets,nativeBalance,address,busy}} provider={provider.current} actions={{
  open,
  create:chosen=>{if(simulated){setAsset(typeof chosen==='string'?chosen:config.chain.collateral);open('create');}else action(()=>write('createVault',[],0n,config.factory));},
  refresh:()=>action(()=>simulated?request('/api/quote',{drop:data?.health?.quote?.drop||'0'},address):request('/api/vault/sync',{},address)),
  permission:body=>action(()=>request('/api/trading/permission',body,address,crypto.randomUUID())),
  policy:body=>action(()=>request('/api/repayment/policy',financialBody(body),address,crypto.randomUUID())),
  stop:()=>action(async()=>{await request('/api/trading/stop',{},address);announce('AI stopped. No new trades will open; protective exits remain active for existing positions. Slippage and funding can cause larger realized losses.');}),
  resume:()=>action(async()=>{await request('/api/borrow',{},address,crypto.randomUUID());await certificate();}),
  cancel:()=>action(async()=>{const c=await request('/api/borrow/cancel',{},address);await write('cancelUnissuedLoan',[BigInt(c.deadline),c.signature]);}),
  revoke:()=>action(()=>simulated?simVault('revoke'):write('revokeAgent')),
  pause:()=>action(()=>simulated?simVault('pause'):write(v.paused?'unpause':'emergencyPause')),
  quote:drop=>action(()=>request('/api/quote',{drop:String(drop)},address)),
 }}/>}
 </main>{isPortal&&<footer><span>{config?.brand?.name||'VELA'}</span><p>Autonomous DeFi Trading Protocol · Built for DogeOS</p></footer>}
 {modal&&authenticated&&<div className="modal-backdrop" onClick={()=>!busy&&setModal(null)}><section className="modal panel" role="dialog" aria-modal="true" aria-labelledby="modal-title" onClick={e=>e.stopPropagation()}><button className="close" disabled={busy} onClick={()=>setModal(null)} aria-label="Close dialog">×</button><div className="eyebrow">YOU CONTROL THE NEXT STEP</div><h2 id="modal-title">{simulated&&['create','deposit','withdraw'].includes(modal)?({create:'Create simulated vault',deposit:'Allocate demo '+(v?.asset||config.chain.collateral),withdraw:'Withdraw demo '+(v?.asset||config.chain.collateral)}[modal]):{deposit:'Deposit test DOGE',borrow:'Borrow simulated USDC',authorize:'Bound your AI agent',withdraw:'Withdraw unlocked DOGE',settle:'Stop & settle',liquidate:'Simulate collateral liquidation',confirmSettlement:'Confirm vault settlement',repay:'Repay simulated USDC',repayFull:'Repay full simulated debt',allocate:'Allocate demo trading capital',withdrawDemo:'Withdraw demo equity',start:'Start paper AI trading',quickStart:'Start AI trading',addCollateral:'Add to your vault'}[modal]}</h2>{['deposit','borrow','authorize','withdraw','repay','repayFull','allocate','withdrawDemo'].includes(modal)&&<label>{['deposit','withdraw'].includes(modal)?(simulated?'Demo '+(v?.asset||config.chain.collateral)+' amount':'Test DOGE amount'):modal==='repayFull'?'Maximum authorized USDC repayment':'USDC amount'}<input autoFocus inputMode="decimal" aria-invalid={Boolean(validation)} aria-describedby={validation?'amount-error':undefined} value={amount} onChange={e=>setAmount(e.target.value)}/></label>}{validation&&<p className="alert" id="amount-error" role="alert">{validation}</p>}{modal==='repayFull'&&<p className="small">Current debt (principal plus accrued interest): {dollars(data.repayment?.debt||h?.debt)}. The maximum rounds debt up to the next cent, then adds $0.01 for interest while you confirm. Only the actual debt is debited, up to your maximum; unused allowance stays in your account. This is not an extra fee.</p>}{modal==='withdrawDemo'&&<p className="small">The suggested amount is rounded down to cents. Any fractional-cent balance remains in the demo account; you can enter up to six decimal places.</p>}{modal==='borrow'&&<p className="small">Selected LTV: {h?.collateralValue?(Number(amount||0)/Number(h.collateralValue)*100).toFixed(1):'0'}% · maximum {Number(config.risk.maxLtv)*100}%</p>}{modal==='authorize'&&<label className="checkbox"><input type="checkbox" checked={autoReduce} onChange={e=>setAutoReduce(e.target.checked)}/> Allow position closure and settlement when loan health falls below {config.risk.blockHealthBelow}.</label>}{simulated&&modal==='create'&&<label>Collateral asset<select aria-label="Collateral asset" value={asset||config.chain.collateral} onChange={e=>setAsset(e.target.value)}>{config.chain.assets.map(a=><option key={a.symbol} value={a.symbol}>{a.symbol} · {a.kind}</option>)}</select></label>}<p className="muted">{simulated&&['create','deposit','withdraw','borrow','authorize','repayFull','liquidate'].includes(modal)?'Simulated vault and loan accounting only. No token transfer, contract approval or blockchain receipt. Demo funds are not redeemable. Debt and risk limits remain specific to this wallet and network.':{addCollateral:`Add ${amount} demo ${data?.vault?.asset||asset||config.chain?.collateral} to your vault. Demo funds only: nothing real leaves your wallet.`,quickStart:data&&simulated&&data.health?.loan&&!['REPAID','CANCELLED'].includes(data.health.loan.state)?`The AI will trade your borrowed ${dollars(amount)}. Max $100 per trade, 1× leverage and a fee-inclusive stop-trigger budget of $2 or your stricter limit per position, for 24 hours. Paper trading only. Slippage, price gaps and funding can cause larger realized losses. Trading continues after closing or disconnecting this browser until permission expires, changes, or you press Stop.`:data&&(plan=>`The AI will trade with ${dollars(amount)} of demo money${plan.topUp>0?` (${dollars(plan.topUp)} non-redeemable demo USD is added to your account)`:''}. Max ${dollars(plan.positionSize)} per trade, 1× leverage and a fee-inclusive stop-trigger budget of ${dollars(plan.maxLoss)} per position, for 24 hours. Paper trading only: no real orders or funds. Slippage, price gaps and funding can cause larger realized losses. Trading continues after closing or disconnecting this browser until permission expires, changes, or you press Stop.`)(quickPlan(data,amount)),start:'Authorize paper execution using live market data and simulated funds. No real exchange orders. Trading continues after closing or disconnecting this browser until permission expires, changes, or you press Stop. Risk checks apply to every decision.',repay:'Repay only the selected amount from free simulated equity. Reserved position margin is protected. Interest is paid first. No real stablecoin is transferred.',repayFull:'Repay current principal and accrued interest up to the displayed maximum. Reserved margin is protected. A separate owner-signed contract settlement is required to unlock eligible test DOGE.',allocate:'Add non-transferable simulated capital to your trading account. No real deposit is made. A lifetime limit of 10,000 demo USDC applies.',withdrawDemo:'Retire available demo trading equity into the simulated surplus record. This is not a real stablecoin withdrawal; nothing is transferred to your wallet.',deposit:'This signs a real testnet transaction. Use faucet DOGE only.',borrow:'A testnet collateral lock is followed by a simulated USDC database credit. No stablecoin is transferred.',authorize:'24-hour permission. BTC, ETH and SOL only; 1× leverage; up to $200 per position and a $2 fee-inclusive stop-trigger budget. Off-chain checks enforce trading limits.',withdraw:'Only collateral unlocked by the confirmed contract state can be withdrawn.',settle:config.chain?.id==='dogeos'?'This stops new entries and closes paper positions at available market depth. It does not move test DOGE or repay a DogeOS loan.':'This stops new entries and closes all open simulated positions at available market depth. Repayment may be insufficient. Shortfalls keep collateral locked.',liquidate:`A simulated buyer purchases collateral at the illustrative price minus ${Number(config.risk.liquidationDiscount)*100}%. Any test DOGE transfer requires a separate owner-signed settlement transaction.`,confirmSettlement:`This signs a real testnet transaction. ${num(Number(l?.seized||0)/1e18)} test DOGE will be transferred to the configured demo treasury, if nonzero. Residual bad debt: ${dollars(l?.bad_debt||0)} (simulated).`}[modal]}</p>{error&&<p className="alert">{error}</p>}<button className="primary" disabled={busy||Boolean(validation)} onClick={()=>action(perform)}>{busy?'Waiting for confirmation…':'Confirm'}</button></section></div>}
 </div>;
}
if(typeof document!=='undefined'&&document.getElementById('root'))createRoot(document.getElementById('root')).render(<App/>);

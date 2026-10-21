import React, {Suspense, lazy, useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {api, authenticate, chainId, mobile, short, switchNetwork} from './portal-wallet.js';
import {chain} from './network.js';

const MyDoge=lazy(()=>import('./portal-mydoge.jsx'));
const usd=(v,d=2)=>v===null||v===undefined?'—':Number(v).toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:d,maximumFractionDigits:d});
const num=(v,d=4)=>Number(v||0).toLocaleString('en-US',{maximumFractionDigits:d});
const tone=v=>Number(v)>0?'positive':Number(v)<0?'negative':'';
const id=()=>crypto.randomUUID();
const tabs=[['dashboard','◫','Dashboard'],['trades','⇄','Trades'],['history','◷','History'],['account','◎','Account']];
const remember=kind=>{try{localStorage.setItem('vela.portal.wallet',kind);}catch{}};
const currentTab=()=>{
  const view=new URLSearchParams(location.search).get('view');
  return tabs.some(([key])=>key===view)?view:'dashboard';
};

function App(){
  const [provider,setProvider]=useState(null),[wallet,setWallet]=useState(null),[account,setAccount]=useState(null);
  const [market,setMarket]=useState(null),[online,setOnline]=useState(false),[now,setNow]=useState(Date.now());
  const [tab,updateTab]=useState(currentTab),[modal,setModal]=useState('wallet'),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
  const setTab=useCallback(next=>{
    if(next!==currentTab()){
      const url=new URL(location.href);
      if(next==='dashboard')url.searchParams.delete('view');else url.searchParams.set('view',next);
      history.pushState(null,'',url.pathname+url.search+url.hash);
    }
    updateTab(next);
    window.scrollTo({top:0,behavior:'instant'});
  },[]);
  useEffect(()=>{
    const restore=()=>{updateTab(currentTab());setModal(null);setMessage('');window.scrollTo({top:0,behavior:'instant'});};
    window.addEventListener('popstate',restore);
    return()=>window.removeEventListener('popstate',restore);
  },[]);
  const [mydoge,setMydoge]=useState(false),[dogeConnection,setDogeConnection]=useState(null),[balance,setBalance]=useState(null);
  const version=useRef(0),restored=useRef(false),received=useRef(0),locked=useRef(false),mounted=useRef(true);
  const isMobile=mobile();
  const walletRef=useRef(wallet);walletRef.current=wallet;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;version.current++;};},[]);

  const invalidate=useCallback((reason='Wallet disconnected. New AI entries are paused.')=>{
    version.current++;setAccount(null);setWallet(null);setBalance(null);setModal('wallet');setMessage(typeof reason==='string'?reason:'Wallet disconnected. Reconnect to continue.');
    api('auth/logout',{}).catch(()=>{setMessage('Connection interrupted. Reconnect to confirm that AI entries are paused. Existing risk controls remain active.');});
    try{localStorage.removeItem('vela.portal.wallet');}catch{}
  },[]);
  const onDoge=useCallback(value=>{setDogeConnection(old=>old?.provider===value.provider&&old?.address===value.address&&old?.chainId===value.chainId?old:value);if(!walletRef.current)setModal('wallet');},[]);
  const onDogeStart=useCallback(()=>setModal(null),[]);
  const onDogeFailure=useCallback(()=>setModal('wallet'),[]);
  useEffect(()=>{if(wallet?.kind==='mydoge'&&dogeConnection&&(dogeConnection.address?.toLowerCase()!==wallet.address.toLowerCase()||chainId(dogeConnection.chainId)!==chain.id))invalidate('MyDoge account or network changed. Reconnect to continue.');},[wallet,dogeConnection,invalidate]);
  const onDogeDisconnect=useCallback(()=>invalidate(),[invalidate]);

  useEffect(()=>{
    const found=e=>{if(e.detail?.info?.rdns==='io.metamask'&&e.detail.provider?.request)setProvider(e.detail.provider);};
    window.addEventListener('eip6963:announceProvider',found);window.dispatchEvent(new Event('eip6963:requestProvider'));
    const fallback=window.ethereum?.providers?.find(p=>p.isMetaMask)||(window.ethereum?.isMetaMask?window.ethereum:null);
    if(fallback?.request)setProvider(p=>p||fallback);
    return()=>window.removeEventListener('eip6963:announceProvider',found);
  },[]);

  useEffect(()=>{
    if(!wallet)return;
    const changed=accounts=>{if(accounts?.[0]?.toLowerCase()!==wallet.address.toLowerCase())invalidate('Wallet account changed. Sign in again to open that account.');};
    const network=value=>{if(chainId(value)!==chain.id)invalidate('Network changed. Reconnect on DogeOS Chikyū Testnet.');};
    const gone=()=>invalidate();
    wallet.provider.on?.('accountsChanged',changed);wallet.provider.on?.('chainChanged',network);wallet.provider.on?.('disconnect',gone);
    return()=>{wallet.provider.removeListener?.('accountsChanged',changed);wallet.provider.removeListener?.('chainChanged',network);wallet.provider.removeListener?.('disconnect',gone);};
  },[wallet,invalidate]);

  useEffect(()=>{
    if(!provider||restored.current)return;
    restored.current=true;
    let kind;try{kind=localStorage.getItem('vela.portal.wallet');}catch{}
    if(kind!=='metamask')return;
    const v=version.current;
    (async()=>{try{
      const accounts=await provider.request({method:'eth_accounts'}), network=chainId(await provider.request({method:'eth_chainId'}));
      const a=await api('account');
      if(v!==version.current)return;
      if(network!==chain.id||accounts?.[0]?.toLowerCase()!==a.wallet.toLowerCase()){invalidate('Reconnect your wallet to continue.');return;}
      setWallet({provider,address:accounts[0],kind:'metamask'});setAccount(a);setModal(null);
    }catch{/* A missing/expired session needs a fresh user-approved signature. */}})();
  },[provider,invalidate]);

  useEffect(()=>{
    if(!wallet||!account)return;
    let stop=false,timer;const v=version.current;
    async function refresh(){try{
      const a=await api('account');
      if(!stop&&v===version.current){setAccount(a);setOnline(true);}
    }catch(e){if(!stop){setOnline(false);if(e.status===401)invalidate('Session expired. Reconnect your wallet.');}}
      if(!stop)timer=setTimeout(refresh,1000);
    }
    refresh();return()=>{stop=true;clearTimeout(timer);};
  },[wallet,Boolean(account),invalidate]);

  useEffect(()=>{
    if(!wallet)return;let cancelled=false;
    async function read(){try{
      const network=chainId(await wallet.provider.request({method:'eth_chainId'}));
      const raw=await wallet.provider.request({method:'eth_getBalance',params:[wallet.address,'latest']});
      const after=chainId(await wallet.provider.request({method:'eth_chainId'}));
      if(!cancelled&&network===chain.id&&after===chain.id)setBalance(`${num(Number(BigInt(raw))/1e18,4)} test DOGE`);
    }catch{if(!cancelled)setBalance('Unavailable');}}
    read();const timer=setInterval(read,20000);return()=>{cancelled=true;clearInterval(timer);setBalance(null);};
  },[wallet]);

  useEffect(()=>{
    let stop=false,socket,retry,attempt=0;
    const open=()=>{if(stop)return;socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/paper/stream`);
      socket.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.mode!=='PAPER'||m.liveEnabled)throw Error();setMarket(m);received.current=Date.now();attempt=0;}catch{setMarket(null);socket.close();}};
      socket.onerror=()=>socket.close();socket.onclose=()=>{if(!stop){received.current=0;retry=setTimeout(open,Math.min(15000,1000*2**attempt++));}};};
    open();const timer=setInterval(()=>setNow(Date.now()),1000);
    return()=>{stop=true;clearTimeout(retry);clearInterval(timer);socket?.close();};
  },[]);

  async function connect(selected,kind,address){
    if(locked.current)return;locked.current=true;setBusy(true);setMessage('');const v=++version.current;
    try{
      if(!selected?.request)throw new Error('Open Vela in MetaMask’s browser or install the official MetaMask extension.');
      const accounts=address?[address]:await selected.request({method:'eth_requestAccounts'});
      await switchNetwork(selected);
      if(v!==version.current)return;
      setWallet({provider:selected,address:accounts[0],kind});
      const a=await authenticate(selected,accounts[0],()=>v===version.current&&mounted.current);
      if(v===version.current){setAccount(a);setOnline(true);setModal(null);remember(kind);setTab('dashboard');}
    }catch(e){if(v===version.current){setWallet(null);setMessage(Number(e.code)===4001?'Cancelled in your wallet. You can try again.':e.message);}}
    finally{locked.current=false;if(mounted.current)setBusy(false);}
  }
  async function act(path,body={},close=false){
    if(locked.current)return false;locked.current=true;setBusy(true);setMessage('');const v=version.current;
    try{
      await api(path,body);const a=await api('account');
      if(v===version.current){setAccount(a);setOnline(true);if(close)setModal(null);}return true;
    }catch(e){if(v===version.current){setMessage(e.message);if(e.status===401)invalidate('Session expired. Reconnect your wallet.');}return false;}
    finally{locked.current=false;if(mounted.current)setBusy(false);}
  }
  async function disconnect(){
    if(locked.current)return;
    locked.current=true;setBusy(true);setMessage('');
    const selected=wallet;
    try {
      await api('auth/logout',{});
      version.current++;setAccount(null);setWallet(null);setBalance(null);setModal('wallet');
      try{localStorage.removeItem('vela.portal.wallet');}catch{}
      if(selected?.kind==='mydoge')try{await dogeConnection?.disconnect?.();}catch{}
      else try{await selected?.provider.request({method:'wallet_revokePermissions',params:[{eth_accounts:{}}]});}catch{}
    } catch(e){setMessage('Could not disconnect the server session. Please retry.');}
    finally{locked.current=false;setBusy(false);}
  }

  const active=Boolean(account?.preferences?.autonomous);
  const fresh=Boolean(now-received.current<4000&&market?.markets?.length===3&&market.markets.every(m=>m.fresh)&&!market.error&&market.serverTime-market.engineAt<3);
  const canStart=online&&fresh&&Number(account?.available)>=10&&account?.model?.configured&&account?.riskReady;
  const pending=account?.orders?.filter(o=>o.status==='pending')||[];
  const decision=account?.decision;
  const explanation=decision?.answer?.reason||decision?.answer?.summary||decision?.answer?.action||decision?.answer?.verdict;
  const openPositions=account?.positions||[];

  return <><header className="portal-header"><a className="brand" href="/" aria-label="Vela home">vela<span className="brand-period">.</span></a><span className="portal-badge"><i/>DEMO MODE</span><button className="portal-wallet-button" onClick={()=>account?setTab('account'):setModal('wallet')}><span className={account?'connected-dot':''}/>{short(account?.wallet)}<span>↗</span></button></header>
    <div className="portal-layout"><nav className="portal-nav" aria-label="Trading navigation">{tabs.map(([key,icon,label])=><button key={key} aria-label={label} className={tab===key?'selected':''} aria-current={tab===key?'page':undefined} onClick={()=>{setTab(key);window.scrollTo({top:0,behavior:'instant'});}}><span>{icon}</span>{label}</button>)}<div className="portal-nav-note"><span>YOUR WALLET.<br/>YOUR AI TEAM.</span><p>More perspective.<br/>Less friction.</p></div></nav>
    <main className="portal-main" id="main"><div className="portal-page-title"><div><p className="eyebrow">YOUR NEXT CHAPTER</p><h1>{tab==='dashboard'?'Your trading home.':tabs.find(t=>t[0]===tab)?.[2]+'.'}</h1></div><span className={`portal-feed ${fresh?'positive':''}`}><i/>{fresh?'Markets live':'Markets reconnecting'}</span></div>
    {!account?<section className="portal-welcome"><span className="portal-star">✳</span><h2>A little instinct.<br/><em>A team of intelligence.</em></h2><p>Connect your wallet. Add demo funds.<br/>Let Vela look for your next move.</p><button className="button button-gold" onClick={()=>setModal('wallet')}>Connect wallet <span>↗</span></button><small>Real markets. Simulated funds. No deposit required.</small></section>:<>
      {!online&&<div className="portal-notice" role="status">Account connection interrupted. Reconnecting…</div>}
      {account.fundingIncomplete&&<div className="portal-notice">Some funding intervals were not observed; P&amp;L excludes those intervals.</div>}
      {tab==='dashboard'&&<>
        <section className="portal-equity"><div className="equity-top"><div><span className="eyebrow">TOTAL DEMO EQUITY</span><strong data-testid="equity">{usd(account.equity)}</strong><span className="muted">Simulated USDC · {short(account.wallet)}</span></div><button className="button button-gold" onClick={()=>setModal('deposit')}>+ Deposit</button></div>
          <EquityChart rows={account.history}/><div className="portal-stats"><Stat label="Available balance" value={usd(account.available)}/><Stat label="Today’s P&L · closed fills, UTC" value={usd(account.today.realized,4)} color={tone(account.today.realized)}/><Stat label="All-time P&L · includes open" value={usd(account.pnl,4)} color={tone(account.pnl)}/></div>
        </section>
        <section className="portal-ai"><div className="portal-ai-heading"><span className="portal-star">✳</span><div><h2>AI Managed</h2><p>{active?'AI Trading Active':openPositions.length?'Entries paused · managing open positions':'Your team is ready when you are.'}</p></div><span className={`portal-badge ${active?'positive':''}`}>{active?'ACTIVE':'PAUSED'}</span></div>
          <div className="portal-ai-story"><span className="eyebrow">{decision?`${decision.phase.toUpperCase()} · ${new Date(decision.at*1000).toLocaleTimeString()}`:'RESEARCH → REVIEW → DIRECTION'}</span><p data-testid="decision">{explanation||(active?'Analyzing live markets. Vela opens a position only when a signal passes review and risk checks.':account.model.configured?'Start your AI team to evaluate BTC, ETH and SOL. A hold decision is a valid outcome.':'AI is currently disconnected. Your demo account remains available.')}</p></div>
          <div className="portal-ai-controls"><button className={`button ${active?'button-outline':'button-gold'}`} disabled={busy||(!active&&!canStart)} onClick={()=>act(active?'stop':'start')}>{busy?'Working…':active?'Stop Trading':'Start Trading'} <span>{active?'Ⅱ':'↗'}</span></button><button className="portal-subtle" disabled={busy} onClick={()=>setModal('withdraw')}>Withdraw ↗</button></div>
          <p className="portal-help">AI: {account.model.status}. {Number(account.available)<10?'Add at least 10 demo USDC to start. ':!fresh?'Waiting for fresh market data. ':''}{openPositions.length?`${openPositions.length} open position${openPositions.length===1?'':'s'} · stop controls remain active when you pause.`:'Stop Trading pauses new entries. Closing positions is a separate action.'}</p>
        </section>
        <div className="portal-markets">{(market?.markets||[]).map(m=><div key={m.symbol}><span className={`market-coin ${m.symbol.split('/')[0].toLowerCase()}`}>{m.symbol[0]}</span><div><strong>{m.symbol.split('/')[0]}</strong><small>PERPETUAL</small></div><b>{usd(m.price)}</b><span className={m.fresh?'positive':'muted'}>{m.fresh?'●':'○'}</span></div>)}</div>
        <PositionList positions={openPositions} busy={busy} act={act} onCloseAll={()=>setModal('close-all')}/>
        <section className="portal-card"><CardTitle title="Recently closed" action="View history ↗" onAction={()=>setTab('history')}/><TradeList rows={account.trades.slice(0,3)}/></section>
      </>}
      {tab==='trades'&&<><PositionList positions={openPositions} busy={busy} act={act} onCloseAll={()=>setModal('close-all')}/><section className="portal-card"><CardTitle title="Pending orders"/><OrderList rows={pending} act={act} busy={busy}/></section><ManualOrder busy={busy} ready={fresh&&online} act={act} markets={market?.markets||[]}/><MarketDetails market={market}/><section className="portal-card"><CardTitle title="Agent activity"/>{account.activity.length?<ol className="portal-activity">{account.activity.slice(0,15).map((a,i)=><li key={i}><time>{new Date(a.ts*1000).toLocaleTimeString()}</time><div><b>{a.kind.replaceAll('_',' ')}</b><p>{a.detail}</p></div></li>)}</ol>:<Empty title="Your team’s work appears here." text="Actual decisions and simulated execution events, as they happen."/>}</section></>}
      {tab==='history'&&<><section className="portal-card"><CardTitle title="Performance"/><div className="portal-table-wrap"><table><thead><tr><th>Closed fills</th><th>Today · UTC</th><th>All time</th></tr></thead><tbody>{[['trades','Count'],['wins','Wins'],['losses','Losses'],['realized','Net realized P&L']].map(([key,label])=><tr key={key}><td>{label}</td><td>{key==='realized'?usd(account.today[key],4):account.today[key]}</td><td>{key==='realized'?usd(account.totals[key],4):account.totals[key]}</td></tr>)}</tbody></table></div><p className="portal-help">Each partial exit counts as a closed fill. P&amp;L includes allocated fees and funding. Open P&amp;L is separate.</p></section><section className="portal-card"><CardTitle title="Trade history"/><TradeList rows={account.trades}/></section><details className="portal-card portal-advanced"><summary>All-time execution costs <span>＋</span></summary><div className="portal-costs">{[['tradingFee','Trading fees'],['spread','Spread'],['depthImpact','Depth impact'],['slippage','Slippage'],['funding','Funding ±']].map(([key,label])=><Stat key={key} label={label} value={usd(account.costs[key]||0,4)}/>)}</div><p className="portal-help">Spread and slippage are included in fill prices. Funding is a prorated estimate.</p></details></>}
      {tab==='account'&&<><section className="portal-card"><CardTitle title="Your wallet"/><div className="portal-account-address">{account.wallet}</div><dl className="portal-details"><Row label="Network" value="DogeOS Chikyū Testnet"/><Row label="Wallet balance" value={balance||'Loading…'}/><Row label="Trading mode" value="PAPER TRADING · DEMO"/><Row label="Demo trading balance" value={usd(account.balance)}/></dl><div className="portal-action-row"><button className="button button-gold" onClick={()=>setModal('deposit')}>Deposit</button><button className="button button-outline" onClick={()=>setModal('withdraw')}>Withdraw</button></div><a className="portal-subtle" href={`${chain.blockExplorers.default.url}/address/${account.wallet}`} target="_blank" rel="noopener noreferrer">View testnet wallet ↗</a></section><details className="portal-card portal-advanced"><summary>Advanced preferences <span>＋</span></summary><dl className="portal-details"><Row label="Strategy" value="AI Managed"/><Row label="Stop-trigger budget per position" value={`≤ ${usd(market?.config?.maxLossBudget||2)} including fees`}/></dl><label className="portal-field">Leverage<select aria-label="Leverage" disabled={busy} value={account.preferences.leverage} onChange={e=>act('preferences',{leverage:Number(e.target.value)})}>{[1,2,3].map(n=><option key={n} value={n}>{n}×</option>)}</select></label><p className="portal-help">Stop budgets can be exceeded by slippage, gaps, funding and data outages.</p></details><section className="portal-card"><CardTitle title="Transaction history"/>{account.transactions.length?<div className="portal-transfers">{account.transactions.map(t=><div key={t.id}><span><b>{t.kind==='demo_deposit'?'Demo deposit':t.kind==='demo_withdrawal'?'Demo withdrawal':'Account reset'}</b><small>{new Date(t.created*1000).toLocaleString()} · simulated</small></span><strong>{usd(t.amount)}</strong></div>)}</div>:<Empty title="No demo transfers yet."/>}</section><div className="portal-account-controls"><button className="portal-subtle" onClick={()=>setModal('reset')}>Reset demo account</button><button className="button button-outline" disabled={busy} onClick={disconnect}>Disconnect wallet ↗</button></div></>}
    </>}
    {message&&!modal&&<div className="portal-notice" role="alert">{message}<button aria-label="Dismiss message" onClick={()=>setMessage('')}>×</button></div>}
    <footer className="portal-footer"><span>Vela · Real markets. Simulated funds.</span><a href="https://lighter.xyz" target="_blank" rel="noopener noreferrer">Powered by Lighter</a></footer></main></div>
    {<Modal open={modal==='wallet'} title="Your wallet. Your Vela." onClose={()=>setModal(null)}><p className="portal-modal-intro">Connect to explore AI trading. Sign one message to verify ownership. No funds move.</p><div className="portal-wallet-options"><button disabled={busy} onClick={()=>connect(provider,'metamask')}><span className="wallet-glyph">◈</span><span><strong>MetaMask</strong><small>Desktop &amp; mobile</small></span><span>↗</span></button><button disabled={!isMobile||busy} onClick={()=>{setMydoge(true);setMessage('');}}><span className="wallet-glyph doge">Ð</span><span><strong>DogeOS / MyDoge</strong><small>Coming Soon — Mobile Only</small></span><span>{isMobile?'↗':'◷'}</span></button></div>
      {!provider&&<a className="portal-subtle" href={isMobile?`https://link.metamask.io/dapp/${location.host}/trade`:'https://metamask.io/download/'} target="_blank" rel="noopener noreferrer">{isMobile?'Open in MetaMask':'Get MetaMask'} ↗</a>}
      {mydoge&&isMobile&&<Suspense fallback={<p>Loading MyDoge…</p>}><MyDoge onProvider={onDoge} onDisconnect={onDogeDisconnect} onStart={onDogeStart} onFailure={onDogeFailure}/>{dogeConnection&&<button className="button button-gold" disabled={busy} onClick={()=>connect(dogeConnection.provider,'mydoge',dogeConnection.address)}>Verify wallet &amp; continue</button>}</Suspense>}
      {busy&&<p role="status">Approve the request in your wallet…</p>}{message&&<p role="alert" className="portal-error">{message}</p>}<p className="portal-help">DogeOS Chikyū Testnet · Demo funds only</p>{isMobile&&<p className="portal-help">MyDoge mobile approval is not yet verified. Use MetaMask for the competition demo.</p>}</Modal>}
    {account&&['deposit','withdraw'].includes(modal)&&<Funding key={modal} kind={modal} account={account} busy={busy} message={message} act={act} onClose={()=>{setModal(null);setMessage('');}}/>}
    {account&&['reset','close-all'].includes(modal)&&<Modal title={modal==='reset'?'Reset your demo account?':'Close all positions?'} onClose={()=>setModal(null)}><p className="portal-modal-intro">{modal==='reset'?'This resets your simulated balance and performance. Stop trading and close all positions first. Your transfer audit remains available.':'Pause new entries and submit simulated market exits for every open position. Prices may change before the orders fill.'}</p><ConfirmButton kind={modal} busy={busy} act={act}/>{message&&<p className="portal-error" role="alert">{message}</p>}</Modal>}
  </>;
}

function Modal({title,onClose,children,open=true}){const ref=useRef(null);useEffect(()=>{const d=ref.current,previous=document.activeElement;if(open)d.showModal();else d.close();return()=>{d.close();if(open)previous?.focus?.();};},[open]);return <dialog className="portal-modal" aria-label={title} ref={ref} onCancel={e=>{e.preventDefault();onClose();}} onClick={e=>{if(e.target===ref.current){const r=ref.current.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)onClose();}}}><button className="portal-modal-close" aria-label="Close dialog" onClick={onClose}>×</button><span className="eyebrow">VELA / DEMO</span><h2>{title}</h2>{children}</dialog>;}
function Funding({kind,account,busy,message,act,onClose}){
  const [amount,setAmount]=useState(kind==='deposit'?'1000':String(Math.max(0,Math.floor(Number(account.available)||0))));
  const [clientId,setClientId]=useState(id);
  const deposit=kind==='deposit';
  const blocked=!deposit&&(account.preferences.autonomous||account.positions.length||account.orders.some(o=>o.status==='pending'));
  return <Modal title={deposit?'Make your first move.':'Withdraw demo funds.'} onClose={onClose}>
    <p className="portal-modal-intro">{deposit?'Add simulated USDC to your trading balance.':'Remove simulated USDC from your balance.'} No real funds move.</p>
    <div className="portal-assets" role="group" aria-label="Demo asset"><div className="selected"><b>Demo USDC</b><small>Simulated funds only</small></div></div>
    <form onSubmit={e=>{e.preventDefault();act(kind,{clientId,amount,asset:'USDC'},true);}}>
      <label className="portal-field">Amount · demo USDC<input aria-label="Demo amount" autoFocus type="number" min="1" max={deposit?'100000':String(Math.max(0,Number(account.balance)))} step="0.000001" required value={amount} onChange={e=>{setAmount(e.target.value);setClientId(id());}}/></label>
      {deposit&&<div className="portal-presets">{[100,1000,10000].map(n=><button type="button" key={n} onClick={()=>{setAmount(String(n));setClientId(id());}}>{usd(n,0)}</button>)}</div>}
      <dl className="portal-details">
        <Row label="Connected wallet" value={short(account.wallet)}/>
        <Row label="Current demo balance" value={usd(account.balance)}/>
        <Row label={deposit?'Added to demo balance':'Removed from demo balance'} value={usd(Number(amount)||0)}/>
      </dl>
      {blocked&&<p className="portal-error">Stop trading and close all positions before withdrawing demo funds.</p>}
      {message&&<p role="alert" className="portal-error">{message}</p>}
      <button className="button button-gold portal-full" disabled={busy||blocked}>{busy?'Processing…':deposit?'Add demo funds':'Confirm demo withdrawal'} <span>↗</span></button>
      <p className="portal-help">Demo balances have no cash value. No wallet transfer, conversion or bridge is performed.</p>
    </form>
  </Modal>;
}
function ConfirmButton({kind,busy,act}){const [clientId]=useState(id);return <button className="button button-gold portal-full" disabled={busy} onClick={()=>act(kind,{clientId,confirm:true},true)}>{busy?'Processing…':kind==='reset'?'Confirm reset':'Pause & close all positions'}</button>;}
function Stat({label,value,color=''}){return <div className="portal-stat"><span>{label}</span><strong className={color}>{value}</strong></div>;}
function Row({label,value}){return <div><dt>{label}</dt><dd>{value}</dd></div>;}
function CardTitle({title,action,onAction}){return <div className="portal-card-title"><h2>{title}</h2>{action&&<button className="portal-subtle" onClick={onAction}>{action}</button>}</div>;}
function Empty({title,text}){return <div className="portal-empty"><span>◇</span><strong>{title}</strong>{text&&<p>{text}</p>}</div>;}
function EquityChart({rows=[]}){if(rows.length<2)return <div className="portal-chart-empty"><span>YOUR STORY STARTS HERE</span><p>Your observed equity appears as you trade.</p></div>;const values=rows.map(r=>Number(r.equity)),lo=Math.min(...values),hi=Math.max(...values),range=Math.max(hi-lo,.01),points=values.map((v,i)=>`${i/(values.length-1)*900},${120-(v-lo)/range*95}`),d='M'+points.join(' L');return <div><svg className="portal-chart" viewBox="0 0 900 150" role="img" aria-label={`Recent observed demo equity: ${usd(values[0])} to ${usd(values.at(-1))}`}><defs><linearGradient id="portal-fill" x2="0" y2="1"><stop stopColor="#dfc18b" stopOpacity=".18"/><stop offset="1" stopColor="#dfc18b" stopOpacity="0"/></linearGradient></defs><path d={`${d} L900,150 L0,150 Z`} fill="url(#portal-fill)"/><path d={d} stroke="#dfc18b" fill="none" strokeWidth="2" vectorEffect="non-scaling-stroke"/></svg><p className="portal-chart-caption">Recent observed equity · {usd(values[0],4)} → {usd(values.at(-1),4)} · auto-scaled</p></div>;}
function PositionList({positions,busy,act,onCloseAll}){return <section className="portal-card"><CardTitle title={`Open positions${positions.length?` · ${positions.length}`:''}`} action={positions.length?'Close all ↗':undefined} onAction={onCloseAll}/>{positions.length?<div className="portal-positions">{positions.map(p=><article key={p.id}><div className="position-title"><strong>{p.symbol.split('/')[0]}<small className={p.side==='long'?'positive':'negative'}>{p.side.toUpperCase()} · {p.leverage}×</small></strong><b className={tone(p.unrealized)}>{usd(p.unrealized,4)}</b></div><dl className="portal-position-values"><Row label="Entry / mark" value={`${usd(p.entry,3)} / ${usd(p.mark,3)}${p.fresh?'':' · stale'}`}/><Row label="Size" value={num(p.qty,8)}/><Row label="Stop / take profit" value={`${usd(p.stop,3)} / ${usd(p.target,3)}`}/><Row label="Stop budget · incl. fees" value={`≤ ${usd(p.riskBudget)}`}/></dl><div className="position-actions"><button disabled={busy||!p.fresh} onClick={()=>act(`positions/${p.id}/close`,{clientId:id(),fraction:'0.5'})}>Close 50%</button><button disabled={busy||!p.fresh} onClick={()=>act(`positions/${p.id}/close`,{clientId:id(),fraction:'1'})}>Close position ↗</button></div></article>)}</div>:<Empty title="Room for your next move." text="Approved AI trades will appear here. No position is opened just for the demo."/>}<p className="portal-help">Fee-inclusive stop triggers stay at $2 or less per position. Slippage, gaps, funding and outages can exceed that budget.</p></section>;}
function TradeList({rows}){return rows.length?<div className="portal-table-wrap"><table><thead><tr><th>Market / closed</th><th>Side / size</th><th>Entry → exit</th><th>Net P&amp;L</th><th>Reason</th></tr></thead><tbody>{rows.map(t=><tr key={t.id}><td><b>{t.symbol.split('/')[0]}</b><small>{new Date(t.closed*1000).toLocaleString()}</small></td><td>{t.side}<small>{num(t.qty,8)}</small></td><td>{usd(t.entry,3)} → {usd(t.exit,3)}</td><td className={tone(t.pnl)}>{usd(t.pnl,4)}</td><td>{t.reason}</td></tr>)}</tbody></table><p className="portal-help">Latest closed fills, including partial exits. Net of trading fees and allocated funding.</p></div>:<Empty title="A clean slate." text="Your history begins with a completed simulated trade."/>;}
function OrderList({rows,act,busy}){return rows.length?<div className="portal-transfers">{rows.map(o=><div key={o.id}><span><b>{o.payload.symbol} · {o.payload.reduceOnly?'Close':o.payload.side}</b><small>{o.payload.type} · {o.error||'Waiting to fill'}</small></span>{!o.payload.reason&&<button className="portal-subtle" disabled={busy} onClick={()=>act(`orders/${o.id}/cancel`)}>Cancel</button>}</div>)}</div>:<Empty title="No orders waiting."/>;}
function MarketDetails({market}){const cfg=market?.config;const pct=v=>`${(Number(v||0)*100).toFixed(3)}%`;return <details className="portal-card portal-advanced"><summary>Market data &amp; execution assumptions <span>＋</span></summary><dl className="portal-details"><Row label="Maker / taker / Vela fee" value={`${pct(cfg?.makerFee)} / ${pct(cfg?.takerFee)} / ${pct(cfg?.platformFee)}`}/><Row label="Simulated latency" value={`${cfg?.latencyMs??300} ms`}/><Row label="Adverse slippage / extra half-spread" value={`${pct(cfg?.slippage)} / ${pct(cfg?.additionalHalfSpread)}`}/></dl><div className="portal-table-wrap"><table><thead><tr><th>Market</th><th>Best bid / size</th><th>Best ask / size</th><th>Hourly funding estimate</th></tr></thead><tbody>{(market?.markets||[]).map(m=><tr key={m.symbol}><td>{m.symbol}{!m.fresh?' · stale':''}</td><td>{usd(m.bids?.[0]?.[0],3)} / {num(m.bids?.[0]?.[1],5)}</td><td>{usd(m.asks?.[0]?.[0],3)} / {num(m.asks?.[0]?.[1],5)}</td><td>{pct(m.fundingRate)}</td></tr>)}</tbody></table></div><p className="portal-help">Simulation assumptions, not future live fee promises. Fills walk observed depth; maker queue priority and liquidation are approximations. Funding is prorated while data is fresh. No real trading or funding is enabled.</p></details>;}
function ManualOrder({busy,ready,act,markets}){const [symbol,setSymbol]=useState('BTC/USD'),[side,setSide]=useState('long'),[type,setType]=useState('market'),[notional,setNotional]=useState('100'),[limit,setLimit]=useState(''),[leverage,setLeverage]=useState('2');return <details className="portal-card portal-advanced"><summary>Optional manual paper order <span>＋</span></summary><form onSubmit={e=>{e.preventDefault();act('orders',{clientId:id(),symbol,side,type,notional,leverage,stopBps:'50',targetBps:'75',...(type==='limit'?{limitPrice:limit}:{})});}}><div className="portal-form-grid"><label className="portal-field">Market<select aria-label="Market" value={symbol} onChange={e=>setSymbol(e.target.value)}>{['BTC','ETH','SOL'].map(s=><option key={s} value={s+'/USD'}>{s}</option>)}</select></label><label className="portal-field">Direction<select aria-label="Direction" value={side} onChange={e=>setSide(e.target.value)}><option value="long">Long</option><option value="short">Short</option></select></label><label className="portal-field">Order<select aria-label="Order" value={type} onChange={e=>setType(e.target.value)}><option value="market">Market</option><option value="limit">Limit</option></select></label><label className="portal-field">Notional · USD<input type="number" min="10" max="100000" value={notional} onChange={e=>setNotional(e.target.value)} required/></label><label className="portal-field">Leverage<select value={leverage} onChange={e=>setLeverage(e.target.value)}>{[1,2,3].map(n=><option key={n} value={n}>{n}×</option>)}</select></label>{type==='limit'&&<label className="portal-field">Limit price<input type="number" min="0.001" step={markets.find(m=>m.symbol===symbol)?.tick||'.001'} required value={limit} onChange={e=>setLimit(e.target.value)}/></label>}</div><p className="portal-help">0.5% stop / 0.75% target. Size may reduce to meet the fee-inclusive $2 risk ceiling.</p><button className="button button-outline" disabled={busy||!ready}>Place paper order ↗</button></form></details>;}
class Boundary extends React.Component{state={error:false};static getDerivedStateFromError(){return {error:true};}render(){return this.state.error?<main className="portal-main"><h1>Let’s reconnect.</h1><p>Vela could not open this view.</p><a href="/trade">Reload Vela</a></main>:this.props.children;}}
createRoot(document.getElementById('portal-root')).render(<Boundary><App/></Boundary>);

import React,{useState} from 'react';

// One question and one button per tab. Detailed controls stay under "Advanced";
// every amount is still validated again by the server when confirmed.
const usd=x=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(x||0));
const qty=x=>new Intl.NumberFormat('en-US',{maximumFractionDigits:4}).format(Number(x||0));
const valid=x=>/^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/.test(x)&&Number(x)>0;

function AmountForm({label,id,unit,value,setValue,max,help,error,button,busy,disabled,onSubmit}){
 const bad=value!==''&&!valid(value);
 return <form className="simple-form" onSubmit={e=>{e.preventDefault();if(!disabled&&valid(value))onSubmit(value);}}>
  <label htmlFor={id} className="simple-question">{label}</label>
  <div className="simple-input"><input id={id} inputMode="decimal" autoComplete="off" placeholder="0" value={value} onChange={e=>setValue(e.target.value.trim())}/><span>{unit}</span>{max!=null&&<button type="button" className="text-button" disabled={busy||Number(max)<=0} onClick={()=>setValue(String(Math.floor(Number(max)*100)/100))}>Max</button>}</div>
  {help&&<p className="simple-help">{help}</p>}
  {(bad||error)&&<p className="simple-error" role="status">{bad?'Enter a number above zero.':error}</p>}
  <button className="primary simple-submit" disabled={busy||disabled||!valid(value)||Boolean(error)}>{button}</button>
 </form>;
}

export function SimpleOverview({data,asset,next,aiRunning,choose}){
 const h=data.health,p=data.paper;
 return <article className="panel simple-card">
  <div className="simple-stats">
   <div><small>In your vault</small><strong>{h?`${qty(h.collateral)} ${asset}`:'Empty'}</strong>{h&&<span>{usd(h.collateralValue)}</span>}</div>
   <div><small>Borrowed</small><strong>{usd(h?.debt||0)}</strong></div>
   <div><small>AI trading</small><strong>{aiRunning?'Running':'Stopped'}</strong></div>
   <div><small>Profit / loss (all time)</small><strong>{usd(p.pnl)}</strong></div>
  </div>
  {next&&<button className="primary simple-submit" onClick={()=>choose(next.tab)}>{next.label} →</button>}
 </article>;
}

export function SimpleVault({data,config,asset,busy,actions,dogeos}){
 const h=data.health,v=data.vault,[value,setValue]=useState('');
 const [chosen,setChosen]=useState(config.chain?.collateral||asset);
 if(dogeos)return <article className="panel simple-card">
  <div className="simple-balance"><small>Your demo trading money</small><strong>{usd(data.paper.equity)}</strong></div>
  <AmountForm id="simple-vault" label="How much demo money do you want to add?" unit="USDC" value={value} setValue={setValue} busy={busy}
   help="Demo money only. Nothing real is sent." button="Add demo money" onSubmit={amount=>actions.open('allocate',amount)}/>
 </article>;
 const assets=config.chain?.assets||[],shown=v?.asset||chosen;
 return <article className="panel simple-card">
  <div className="simple-balance"><small>In your vault</small><strong>{qty(h?.collateral||0)} {shown}</strong>{h&&<span>{usd(h.collateralValue)}</span>}</div>
  {!v&&assets.length>1&&<label className="simple-select">What do you want to add?<select aria-label="Vault asset" value={chosen} onChange={e=>setChosen(e.target.value)}>{assets.map(a=><option key={a.symbol} value={a.symbol}>{a.symbol}</option>)}</select></label>}
  <AmountForm id="simple-vault" label="How much do you want to add?" unit={shown} value={value} setValue={setValue} busy={busy} disabled={v?.paused}
   error={v?.paused?'Your vault is paused. Open Advanced to unpause it.':''}
   help="Demo funds only. Nothing real leaves your wallet." button="Add to vault" onSubmit={amount=>actions.open('addCollateral',amount,{asset:shown})}/>
  {Number(h?.withdrawableDoge||0)>0&&<button className="text-button simple-link" disabled={busy||v?.paused} onClick={()=>actions.open('withdraw',h.withdrawableDoge)}>Take out {qty(h.withdrawableDoge)} {shown}</button>}
 </article>;
}

export function SimpleBorrow({data,asset,busy,actions,choose}){
 const h=data.health,v=data.vault,l=h?.loan,[value,setValue]=useState('');
 const capacity=Number(h?.borrowCapacity||0),active=l&&!['REPAID','CANCELLED'].includes(l.state);
 if(!v||Number(h?.collateral||0)<=0)return <article className="panel simple-card">
  <p className="simple-question">To borrow, first add something to your vault.</p>
  <button className="primary simple-submit" onClick={()=>choose('vault')}>Go to Vault →</button>
 </article>;
 if(active)return <article className="panel simple-card">
  <div className="simple-balance"><small>You owe</small><strong>{usd(h.debt)}</strong><span>Borrowed {usd(h.principal)} · interest {usd(h.interest)}</span></div>
  <p className="simple-help">Repay this loan before borrowing again.</p>
  <div className="simple-actions"><button className="primary simple-submit" onClick={()=>choose('trading')}>Trade with it →</button><button className="simple-submit" onClick={()=>choose('agent')}>Repay →</button></div>
 </article>;
 return <article className="panel simple-card">
  <div className="simple-balance"><small>You can borrow up to</small><strong>{usd(capacity)}</strong><span>Backed by {qty(h.collateral)} {asset} in your vault</span></div>
  <AmountForm id="simple-borrow" label="How much do you want to borrow?" unit="USDC" value={value} setValue={setValue} max={capacity} busy={busy}
   error={Number(value)>capacity?`You can borrow up to ${usd(capacity)}.`:''}
   help="Demo loan only. No real USDC is issued." button="Borrow" onSubmit={amount=>actions.open('borrow',amount)}/>
 </article>;
}

export function SimpleRepay({data,asset,busy,actions,choose,canSettle}){
 const h=data.health,v=data.vault,l=h?.loan,[value,setValue]=useState('');
 const debt=Number(h?.debt||0),free=Number(data.repayment?.maxRepayable||0),back=Number(h?.withdrawableDoge||0);
 return <article className="panel simple-card">
  {debt>0?<>
   <div className="simple-balance"><small>You owe</small><strong>{usd(debt)}</strong><span>You can repay up to {usd(Math.min(free,debt))} right now</span></div>
   <AmountForm id="simple-repay" label="How much do you want to repay?" unit="USDC" value={value} setValue={setValue} max={Math.min(free,debt)} busy={busy}
    error={Number(value)>Math.min(free,debt)?`You can repay up to ${usd(Math.min(free,debt))} right now.`:''}
    button="Repay" onSubmit={amount=>actions.open('repay',amount)}/>
   <button className="simple-submit" disabled={busy||free<debt} onClick={()=>actions.open('repayFull',(Math.ceil(debt*100)/100+.01).toFixed(2))}>Repay everything ({usd(debt)})</button>
   {free<debt&&canSettle&&<p className="simple-help">Money is still in open trades. <button className="text-button" disabled={busy} onClick={()=>actions.open('settle')}>Stop AI & close trades</button> to free it up.</p>}
  </>:<div className="simple-balance"><small>You owe</small><strong>{usd(0)}</strong><span>No loan to repay</span></div>}
  {v&&<div className="simple-divider">
   <div className="simple-balance"><small>Ready to take back</small><strong>{qty(back)} {asset}</strong>{back<=0&&Number(h?.collateral||0)>0&&<span>Your {asset} unlocks after the loan is repaid</span>}</div>
   <button className="primary simple-submit" disabled={busy||back<=0||v.paused} onClick={()=>actions.open('withdraw',h.withdrawableDoge)}>Take back my {asset}</button>
  </div>}
  {!v&&debt<=0&&<button className="primary simple-submit" onClick={()=>choose('vault')}>Go to Vault →</button>}
 </article>;
}

import React,{useState} from 'react';

const money=x=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:6}).format(Number(x||0));

export function RepaymentPanel({data,busy,actions}){
 const [amount,setAmount]=useState('');
 const r=data.repayment||{},debt=Number(r.debt||data.health?.debt||0),maximum=Number(r.maxRepayable||0);
 return <article className="panel repayment-panel"><p className="eyebrow">REPAY AT YOUR PACE</p><h3>Partial or full repayment</h3>
  <div className="repayment-metrics"><span>Outstanding debt<strong>{money(debt)}</strong></span><span>Maximum repayable now<strong>{money(maximum)}</strong></span></div>
  <p className="small">Use free trading equity, including original capital. Profits are optional. Position margin, pending orders and costs stay reserved. Balances are non-transferable.</p>
  <label>Repayment amount · USDC<input type="number" min="0" step="0.000001" max={maximum} value={amount} onChange={e=>setAmount(e.target.value)} placeholder="Choose an amount"/></label>
  <div className="button-row"><button className="primary" disabled={busy||!Number(amount)||Number(amount)>maximum} onClick={()=>actions.open('repay',amount)}>Review partial repayment</button><button disabled={busy||debt<=0||maximum<debt} onClick={()=>actions.open('repayFull',(Math.ceil(debt*100)/100+.01).toFixed(2))}>Repay full debt</button></div>
  {r.restriction&&<p role="status" className="notice">{r.restriction}</p>}
  {debt>maximum&&<p className="small">Available funds cover {money(maximum)}. Make a partial repayment now, or review position closure to release margin. Losses leave outstanding debt.</p>}
  <button disabled={busy||(!debt&&!data.paper.positions.length)} onClick={()=>actions.open('settle')}>Review Stop & Settle</button>
  <details className="guided-details"><summary>Repayment history & receipts</summary>{data.events.filter(e=>/REPAYMENT|DEMO_REPAYMENT/.test(e.kind)).slice(0,15).map(e=><div className="activity" key={e.id}><small>{new Date(e.at*1000).toISOString().replace('T',' ').slice(0,19)} UTC · {e.kind.replaceAll('_',' ')}</small><p>{money(e.amount)} · simulated repayment</p><p className="small">{e.detail}</p></div>)}{!data.events.some(e=>/REPAYMENT/.test(e.kind))&&<p className="muted">No repayments yet.</p>}</details>
 </article>;
}

export function AutomationPanel({data,busy,actions}){
 const policy=data.repayment?.policy;
 const skipped=data.events.find(e=>e.kind==='SCHEDULED_REPAYMENT_SKIPPED');
 const [percent,setPercent]=useState(policy?.sweepPercent||'0'),[frequency,setFrequency]=useState(policy?.frequency||'off'),[maximum,setMaximum]=useState(policy?.maximum||'50'),[minimum,setMinimum]=useState(policy?.minimum||'100'),[days,setDays]=useState('90'),[confirm,setConfirm]=useState(false);
 return <article className="panel automation-panel"><p className="eyebrow">BOUNDED. REVOCABLE. YOUR CHOICE.</p><h3>Automated repayment</h3>{skipped&&<p className="notice" role="status">Last skipped repayment · {new Date(skipped.at*1000).toLocaleString()}: {skipped.detail}</p>}
  <p className="muted">{policy?.enabled?'Permission active':'Manual repayment only'}{policy?.enabled&&` · expires ${new Date(policy.expiresAt*1000).toLocaleDateString()}`}</p>
  <label>Profit Sweep percentage<select value={['0','25','50','75','100'].includes(percent)?percent:'custom'} onChange={e=>setPercent(e.target.value==='custom'?'10':e.target.value)}><option value="0">Off</option>{[25,50,75,100].map(n=><option key={n} value={n}>{n}%</option>)}<option value="custom">Custom percentage</option></select></label>
  <label>Custom percentage<input type="number" min="0" max="100" step="0.1" value={percent} onChange={e=>setPercent(e.target.value)}/></label>
  <p className="small">Only new realized net profits after fees and funding qualify. Unrealized gains are excluded. Previously allocated profits are never reused. Trading may not generate enough profit to repay the loan.</p>
  <div className="form-grid"><label>Repayment frequency<select value={frequency} onChange={e=>setFrequency(e.target.value)}><option value="off">Off</option><option value="daily">Daily · UTC</option><option value="weekly">Weekly · UTC</option><option value="monthly">Monthly · UTC</option></select></label><label>Maximum per scheduled repayment<input type="number" min="0" step="0.000001" value={maximum} onChange={e=>setMaximum(e.target.value)}/></label><label>Funding source<select defaultValue="trading"><option value="trading">Free trading equity</option></select></label><label>Minimum remaining free balance<input type="number" min="0" step="0.000001" value={minimum} onChange={e=>setMinimum(e.target.value)}/></label><label>Permission duration · days<input type="number" min="1" max="365" value={days} onChange={e=>setDays(e.target.value)}/></label></div>
  <p className="small">Scheduled repayments skip and record a notification if funds are insufficient. No position is reduced automatically. Both methods stop when debt reaches zero. Updating permission starts a new profit baseline.</p>
  <label className="checkbox"><input type="checkbox" checked={confirm} onChange={e=>setConfirm(e.target.checked)}/> I authorize simulated repayments within these limits until expiry. I can revoke at any time.</label>
  <div className="button-row"><button className="primary" disabled={busy||!confirm||data.health?.loan?.state!=='ACTIVE'} onClick={()=>{actions.policy({confirm:true,enabled:true,sweepPercent:percent,frequency,maximum,minimum,source:'trading',days});setConfirm(false);}}>Authorize repayment rules</button><button disabled={busy||!policy?.enabled} onClick={()=>actions.policy({confirm:true,enabled:false})}>Revoke automation</button></div>
  {policy?.nextDue&&policy.enabled&&<p className="small">Next scheduled check: {new Date(policy.nextDue*1000).toISOString()}.</p>}
  <details className="guided-details"><summary>Permission audit & skipped payments</summary>{data.events.filter(e=>/REPAYMENT_(AUTHORIZED|DISABLED|SKIPPED)/.test(e.kind)).slice(0,12).map(e=><div className="activity" key={e.id}><small>{new Date(e.at*1000).toISOString()} · {e.kind.replaceAll('_',' ')}</small><p className="small">{e.detail}</p></div>)}</details>
 </article>;
}

// One-step AI start: the confirmation dialog tops up demo capital, saves 24-hour
// limits and starts the agent in that order. The server still enforces every limit.
const usd=x=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(x||0));
export const QUICK_AMOUNTS=['100','500','1000'];
export function quickPlan(data,amount){
 const equity=Number(data.paper.equity||0),remaining=Math.max(0,10000-Number(data.directAllocation||0));
 const old=data.tradingPermission||{},oldPosition=Number(old.positionSize||0)/1e6,oldLoss=Number(old.maxLoss||0)/1e6;
 const topUp=Math.max(0,Math.ceil((Number(amount)-equity)*100)/100);
 return {amount,topUp,reachable:topUp<=remaining,positionSize:Math.min(100,Number(amount),oldPosition>0?oldPosition:100),maxLoss:Math.min(2,oldLoss>0?oldLoss:2)};
}
export function QuickStartAI({data,config,busy,actions,clock,marketsFresh,financed,canStart,permission={}}){
 const p=data.paper,simulated=Boolean(config.chain?.capabilities?.simulatedVault),active=Number(permission.expiresAt||0)*1000>clock;
 const running=p.preferences.autonomous&&active;
 const [amount,setAmount]=useState(()=>[...QUICK_AMOUNTS].reverse().find(a=>Number(p.equity||0)>=Number(a))||'500');
 const plan=quickPlan(data,amount),open=p.positions[0];
 const blocker=!config.modelConfigured?'AI is disconnected. Trading stays stopped until the service reconnects.':!marketsFresh?'Waiting for live market prices. This usually takes a few seconds.':financed&&!simulated&&!canStart?'You have an active demo loan. Authorize AI from your Vault first, then start here.':financed&&Number(p.balance||0)<=0?'Your borrowed money is not in your trading account yet.':!financed&&!plan.reachable?'This amount exceeds your remaining demo allowance (10,000 USDC per wallet). Choose a smaller amount.':'';
 const until=active?new Date(Number(permission.expiresAt)*1000).toISOString().slice(11,16)+' UTC':'';
 if(running)return <article className="panel quick-ai is-running" aria-label="AI trading status">
  <div className="quick-ai-head"><div><p className="eyebrow">PAPER TRADING · DEMO MONEY</p><h3><span className="live-dot" aria-hidden="true"></span>AI is running</h3></div><button disabled={busy} onClick={actions.stop}>Stop AI Trading</button></div>
  <p className="muted">Watching BTC, ETH and SOL. It only trades when it finds a good setup, so waiting is normal.</p>
  <div className="quick-ai-trade">{open?<><strong>Open trade: {open.symbol} {open.side}</strong><span>Entry {money(open.entry)} · Stop {money(open.stop)}{open.target?` · Target ${money(open.target)}`:''}</span><span>Profit / loss now: {usd(open.unrealized)}</span></>:<strong>No open trade right now</strong>}</div>
  <dl className="quick-ai-facts"><div><dt>Today (UTC)</dt><dd>{usd(p.today.realized)} · {p.today.trades} trades ({p.today.wins} won / {p.today.losses} lost)</dd></div><div><dt>Your limits until {until}</dt><dd>Max {money(Number(permission.positionSize)/1e6)} per trade · {permission.leverage}× · max loss {money(Number(permission.maxLoss)/1e6)} per trade</dd></div>{p.decision?.answer?.reason&&<div><dt>Latest AI note</dt><dd>{p.decision.answer.reason}</dd></div>}</dl>
  <p className="small">Stopping prevents new trades. An open trade stays protected by its stop.</p>
 </article>;
 return <article className="panel quick-ai" aria-label="Start AI trading">
  <p className="eyebrow">PAPER TRADING · DEMO MONEY</p>
  {financed&&<p className="quick-ai-label">The AI will trade your borrowed {usd(p.balance)}.</p>}{!financed&&<><p className="quick-ai-label" id="quick-amount-label">How much do you want to trade?</p><div className="quick-ai-amounts" role="radiogroup" aria-labelledby="quick-amount-label">{QUICK_AMOUNTS.map(a=><button key={a} role="radio" aria-checked={amount===a} className={amount===a?'selected':''} disabled={busy} onClick={()=>setAmount(a)}>${Number(a).toLocaleString('en-US')}</button>)}</div></>}
  <ul className="quick-ai-checks"><li>Max {money(financed?Math.min(100,Number(p.balance||0)):plan.positionSize)} per trade · 1× leverage</li><li>Max loss {money(plan.maxLoss)} per trade, fees included</li><li>Runs for 24 hours · demo money only, no real orders</li></ul>
  {blocker&&<p className="notice" role="status">{blocker}</p>}
  <button className="primary quick-ai-start" disabled={busy||Boolean(blocker)} onClick={()=>financed&&!simulated?actions.open('start'):actions.open('quickStart',financed?String(Math.floor(Number(p.balance)*100)/100):amount)}>Start AI Trading</button>
  {p.preferences.autonomous&&!active&&<p className="small">Your previous 24-hour limits expired. Start again to renew them.</p>}
  <p className="small">Slippage and funding can make a loss slightly larger than the stop limit.</p>
 </article>;
}

export function TradingCapitalPanel({data,busy,actions}){
 const permission=data.tradingPermission||{};
 const [allocation,setAllocation]=useState(String(Number(permission.allocation||0)/1e6||500)),[position,setPosition]=useState(String(Number(permission.positionSize||0)/1e6||100)),[loss,setLoss]=useState(String(Number(permission.maxLoss||0)/1e6||2)),[leverage,setLeverage]=useState(permission.leverage||'1'),[confirm,setConfirm]=useState(false);
 const loan=data.health?.loan,hasDebt=loan&&!['REPAID','CANCELLED'].includes(loan.state);
 return <article className="panel"><p className="eyebrow">START WITH YOUR OWN ALLOCATION</p><h3>Trading capital & risk</h3><p className="muted">Use this product independently. A vault and loan are optional.</p>
  <div className="button-row"><button disabled={busy||hasDebt||Number(data.directAllocation||0)>=10000} onClick={()=>actions.open('allocate','500')}>Allocate demo capital</button><button disabled={busy||hasDebt||Number(data.repayment?.freeEquity||0)<=0} onClick={()=>actions.open('withdrawDemo',data.repayment.freeEquity)}>Withdraw available demo equity</button></div>
  <p className="small">Demo allocations are limited to 10,000 USDC per wallet over its lifetime. Withdrawals retire simulated trading equity; they do not send redeemable funds to a wallet. Outstanding loans must settle first.</p>
  <details className="guided-details"><summary>Trading limits & authorization</summary><div className="form-grid"><label>Maximum trading allocation<input type="number" min="1" max="10000" value={allocation} onChange={e=>setAllocation(e.target.value)}/></label><label>Maximum per position<input type="number" min="1" max="200" value={position} onChange={e=>setPosition(e.target.value)}/></label><label>Stop-trigger budget · includes fees<input type="number" min="0.01" max="2" step="0.01" value={loss} onChange={e=>setLoss(e.target.value)}/></label><label>Maximum leverage<select value={leverage} onChange={e=>setLeverage(e.target.value)}>{[1,2,3].map(n=><option key={n} value={n}>{n}×</option>)}</select></label></div>
  <label className="checkbox"><input type="checkbox" checked={confirm} onChange={e=>setConfirm(e.target.checked)}/> Authorize paper execution within these limits for 24 hours. No real orders.</label>
  <button disabled={busy||!confirm} onClick={()=>{actions.permission({confirm:true,allocation,positionSize:position,maxLoss:loss,leverage});setConfirm(false);}}>Save trading limits</button>
  <p className="small">Saving stops new entries for review. Existing stricter limits remain. Slippage and funding can cause losses beyond a stop-trigger budget.</p>
  </details>
  {hasDebt&&<button disabled={busy} onClick={()=>actions.open('authorize',data.paper.balance)}>Review vault agent permission</button>}
 </article>;
}

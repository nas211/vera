import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, NavLink, Route, Routes, useNavigate } from 'react-router-dom';
import { ArrowRight, LockKeyhole, Plus, RefreshCw, TrendingUp } from 'lucide-react';
import { Panel } from '@/components/common/Panel';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { formatFixed, formatUnits, parseDecimal } from '@/lib/units';
import type { AccountService } from '@/lib/vela/accountService';
import type { NativeVaultWorkflow } from '@/lib/vela/nativeVault';
import { DogeLendingWorkflow, type DogeAction, type DogeConfig, type DogePending, type DogeReview, type DogeState } from '@/lib/vela/dogeLending';
import { DashboardController } from '@/lib/vela/dashboardController';
import { AccountProvider } from '../approved/AccountProvider';
import { useAccountDashboard } from '../approved/context';
import { AccountReview, PendingTransaction } from '../approved/ReviewDialog';
import { AccountTradingStatus } from '../approved/AccountPanels';
import { AccountOpenTrades, AccountActivity, AccountRepaymentHistory } from '../approved/ActivityPanels';
import { AccountClaim, AccountDepositWithdraw, AccountRewardTerms } from '../approved/VaultPanels';
import { AccountRepay } from '../approved/LendingPanels';
import { AccountSettings } from '../approved/ProfilePreferences';
import { useWalletBalance } from '../approved/WalletBalance';
import './simple.css';
import './light-theme.css';

const titles:Record<DogeAction,string>={deposit:'Add collateral',borrow:'Borrow DOGE',withdrawProceeds:'Withdraw to wallet',withdrawCollateral:'Withdraw collateral',repay:'Repay from wallet',repayAvailable:'Repay available DOGE',repayCollateral:'Repay using collateral'};
const doge=(value:string|undefined,dp=2)=>value===undefined?'—':BigInt(value)>0n&&BigInt(value)<10n**BigInt(18-dp)?'<'+formatFixed(10n**BigInt(18-dp),18,dp):formatFixed(BigInt(value),18,dp,'down');
const exact=(value:bigint)=>formatUnits(value,18);
const ltv=(debt:bigint,collateral:bigint)=>collateral>0n?Number(debt*10000n/collateral)/100:0;
const min=(a:bigint,b:bigint)=>a<b?a:b;

export function SimpleDashboard({service,native,dogeWorkflow,wallet,children,basePath='/app',refreshConfig}:{service:AccountService|null;native:NativeVaultWorkflow|null;dogeWorkflow:DogeLendingWorkflow|null;wallet:ReactNode;children?:ReactNode;basePath?:string;refreshConfig:()=>Promise<void>}) {
  const controller=useMemo(()=>new DashboardController(service,native,undefined,refreshConfig),[service,native,refreshConfig]);
  const navigate=useNavigate();
  useEffect(()=>{const redirect=()=>{if(location.hash==='#trade'&&(location.pathname===basePath||location.pathname===basePath+'/'))navigate(basePath+'/trading'+location.search,{replace:true});};redirect();window.addEventListener('hashchange',redirect);return()=>window.removeEventListener('hashchange',redirect);},[basePath,navigate]);
  return <div className="simple-app"><header className="simple-header"><div className="simple-nav"><a className="simple-logo" href="/" aria-label="Vela home"><svg viewBox="0 0 32 34" aria-hidden><path d="M3 6h8l5 14L24 2h6L17 32h-5Z" fill="#d5b257" /></svg>vela</a><nav className="simple-dashboard-tabs" aria-label="Dashboards"><NavLink end to={basePath+'/'} className={({isActive})=>'simple-navlink'+(isActive?' active':'')}>Lending Dashboard</NavLink><NavLink to={basePath+'/trading'} className={({isActive})=>'simple-navlink'+(isActive?' active':'')}>Trading Dashboard</NavLink></nav><div className="simple-wallet">{wallet}</div></div></header><AccountProvider controller={controller}><main className="simple-main">{children}<Routes><Route path="/settings" element={<><Link className="simple-back" to={basePath+'/'}>← Back to dashboard</Link><AccountSettings /></>} /><Route path={basePath+'/trading'} element={<TradingDashboard />} /><Route path="*" element={<LendingDashboard workflow={dogeWorkflow} tradingPath={basePath+'/trading'} />} /></Routes><AccountReview /></main></AccountProvider></div>;
}

function LendingDashboard({workflow,tradingPath}:{workflow:DogeLendingWorkflow|null;tradingPath:string}) {
  const {account,native,error:accountError,notice:accountNotice,controller}=useAccountDashboard();
  const wallet=useWalletBalance();
  const [config,setConfig]=useState<DogeConfig|null>(null),[state,setState]=useState<DogeState|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [pending,setPending]=useState<DogePending|null>(null),[recovery,setRecovery]=useState('');
  const [tab,setTab]=useState<'borrow'|'repay'>('borrow'),[source,setSource]=useState<'available'|'wallet'>('available'),[amount,setAmount]=useState('');
  const [repayAll,setRepayAll]=useState(false);
  const [modal,setModal]=useState<{action:DogeAction;amount:string;fullRepayment?:boolean}|null>(null),[review,setReview]=useState<DogeReview|null>(null),[reviewing,setReviewing]=useState(false),[sending,setSending]=useState(false);
  const epoch=useRef(0),readEpoch=useRef(0),reviewEpoch=useRef(0),inFlight=useRef<Promise<void>|null>(null);
  const refresh=useCallback(()=>{
    if(!workflow)return Promise.resolve();
    if(inFlight.current)return inFlight.current;
    const e=epoch.current,r=++readEpoch.current;setLoading(true);
    const task=(async()=>{
      try{const c=await workflow.config();if(e!==epoch.current||r!==readEpoch.current)return;setConfig(c);setPending(workflow.pending());if(!c.enabled){setState(null);setError('');return;}const p=workflow.pending(),s=p?.hash?await workflow.reconcile():await workflow.read();if(e!==epoch.current||r!==readEpoch.current)return;const nextPending=workflow.pending();setState(s);setPending(nextPending);setError('');
        if(p?.hash&&!nextPending&&s.transaction?.status==='CONFIRMED')setNotice(`${titles[p.action]} confirmed. Balances updated.`);
        if(p?.hash&&!nextPending&&s.transaction?.status==='REVERTED'){setNotice('');setError('Transaction reverted on DogeOS. Check the transaction details before trying again.');}
      }
      catch(err){if(e===epoch.current&&r===readEpoch.current){setState(null);setError((err as Error).message);}}
      finally{if(e===epoch.current&&r===readEpoch.current)setLoading(false);}
    })();
    inFlight.current=task;void task.finally(()=>{if(inFlight.current===task)inFlight.current=null;});return task;
  },[workflow]);
  useEffect(()=>{const refs={epoch,readEpoch,reviewEpoch,inFlight};refs.epoch.current++;refs.inFlight.current=null;setState(null);setConfig(null);setPending(null);setModal(null);setReview(null);setNotice('');const visible=()=>{if(document.visibilityState==='visible')void refresh();};void refresh();const timer=setInterval(visible,30000);window.addEventListener('focus',visible);document.addEventListener('visibilitychange',visible);return()=>{refs.epoch.current++;refs.readEpoch.current++;refs.reviewEpoch.current++;refs.inFlight.current=null;clearInterval(timer);window.removeEventListener('focus',visible);document.removeEventListener('visibilitychange',visible);};},[refresh]);
  useEffect(()=>{if(!pending?.hash)return;const t=setInterval(()=>void refresh(),4000);return()=>clearInterval(t);},[pending?.hash,refresh]);
  const collateral=BigInt(state?.position.collateral??'0'),debt=BigInt(state?.debtWei??'0'),available=BigInt(state?.position.available??'0');
  const balance=BigInt(wallet.balanceWei??state?.walletBalanceWei??'0');
  const interestBuffer=BigInt(state?.position.principal??'0')*500n*120n/(10000n*365n*86400n)+1n;
  const rawCapacity=BigInt(state?.availableToBorrowWei??'0'),capacity=debt>0n?(rawCapacity>interestBuffer?rawCapacity-interestBuffer:0n):rawCapacity;
  const limit=tab==='borrow'?capacity:source==='available'?min(debt,available):min(debt+interestBuffer,balance);
  const parsed=parseDecimal(amount,18),valid=parsed.ok&&parsed.value>0n&&parsed.value<=limit;
  const ready=!!workflow&&config?.enabled&&!!state&&!pending;
  const nextDebt=parsed.ok?(tab==='borrow'?debt+parsed.value:debt>parsed.value?debt-parsed.value:0n):debt;
  const open=(action:DogeAction,value='',fullRepayment=false)=>{reviewEpoch.current++;setReview(null);setModal({action,amount:value,fullRepayment});setError('');setNotice('');};
  const prepare=async()=>{if(!workflow||!modal)return;const r=++reviewEpoch.current;setReviewing(true);setError('');try{const q=await workflow.review(modal.action,modal.amount,modal.fullRepayment);if(r===reviewEpoch.current)setReview(q);}catch(e){if(r===reviewEpoch.current)setError((e as Error).message);}finally{if(r===reviewEpoch.current)setReviewing(false);}};
  const confirm=async()=>{if(!workflow||!review)return;const e=epoch.current;setSending(true);setError('');try{await workflow.submit(review,true);if(e===epoch.current){setNotice('Transaction sent. Waiting for confirmation.');setModal(null);setReview(null);}}catch(err){if(e===epoch.current)setError((err as Error).message);}finally{if(e===epoch.current){setSending(false);setPending(workflow.pending());void refresh();void wallet.refresh();}}};
  const switchTab=(value:'borrow'|'repay')=>{setTab(value);setAmount('');setRepayAll(false);};
  const hasLegacy=!!native?.vault&&(BigInt(native.vault.collateral)>0n||BigInt(native.vault.rewards?.claimableWei??'0')>0n)||!!account&&Number(account.repayment.debt)>0;
  return <>
    <section className="simple-intro"><div><p className="simple-eyebrow">Lending on DogeOS</p><h1>Borrow against your DOGE.</h1><p>Deposit DOGE. Borrow DOGE. Repay when you’re ready.</p></div><div className="simple-steps"><b>1</b> Deposit <ArrowRight size={14}/><b>2</b> Borrow <ArrowRight size={14}/><b>3</b> Repay</div></section>
    {!workflow&&<p className="simple-notice">Connect your wallet to see your balance and borrowing limit.</p>}
    {workflow&&config&&!config.enabled&&<p className="simple-notice">DOGE borrowing is awaiting the new lending pool. Your existing account is preserved below.</p>}
    {(error||accountError)&&<p role="alert" className="simple-error">{error||accountError}</p>}{(notice||accountNotice)&&<p role="status" className="simple-notice">{notice||accountNotice}</p>}
    {pending&&<div className="simple-notice" role="status"><strong>{titles[pending.action]}</strong> · {pending.hash?'Waiting for chain confirmation':'Check the request in your wallet'}{pending.hash?<a className="simple-hash" href={'https://dogeos-testnet.l2scan.co/tx/'+pending.hash} target="_blank" rel="noreferrer">View transaction ↗</a>:<><p>Check wallet history before trying again. A submitted transaction still needs confirmation.</p><input className="simple-input" placeholder="Paste transaction hash" aria-label="Recovery transaction hash" value={recovery} onChange={e=>setRecovery(e.target.value)}/><Button size="sm" onClick={()=>{void workflow?.attachHash(recovery).then(refresh).catch(e=>setError(e.message));}}>Recover transaction</Button></>}<button onClick={()=>void refresh()}>Check confirmation</button></div>}
    <div className="simple-grid">
      <Panel className="simple-position"><div className="simple-card-head"><h2>Your position</h2><span className="simple-health">{state?debt===0n?'Debt free':ltv(debt,collateral)>=80?'Repayment needed':ltv(debt,collateral)>=70?'At borrowing limit':'Healthy':'—'}</span></div><div className="simple-balances"><div><p>Your collateral</p><strong>{doge(state?.position.collateral)}<small>DOGE</small></strong><span>Locked while you have a loan.</span></div><div><p>Your loan</p><strong title={state?exact(debt)+' DOGE':undefined}>{doge(state?.debtWei)}<small>DOGE</small></strong><span>Amount to repay, including interest.</span></div></div>
        <div className="simple-meter-label"><span>Loan-to-value</span><strong>{state?ltv(debt,collateral).toFixed(2)+'%':'—'}</strong></div><div className="simple-meter"><span style={{width:Math.min(100,ltv(debt,collateral)/.8)+'%'}} /></div><div className="simple-meter-foot"><span>0%</span><span>70% borrowing limit</span></div>
        <div className="simple-row"><span>Available to borrow</span><strong>{state?doge(capacity.toString())+' DOGE':'—'}</strong></div><div className="simple-row"><span>Borrow rate</span><strong>{config?'5.00% APR':'—'}</strong></div>
        <div className="simple-position-bottom"><span>Wallet: <strong>{doge(wallet.balanceWei??state?.walletBalanceWei)} DOGE</strong></span><button className="simple-secondary" disabled={!ready} onClick={()=>open('deposit')}><Plus size={14}/>Add collateral</button></div>
        {state&&debt>0n&&debt<10n**16n&&<p className="simple-notice">A small loan balance remains. Use Repay in full to unlock your collateral.</p>}<details className="simple-details"><summary>Manage collateral</summary><p>You can withdraw collateral after the loan is fully repaid. Repayment using collateral spends the amount selected.</p><div className="simple-button-row">{debt>0n&&<Button disabled={!ready} onClick={()=>open('repay',exact(debt+interestBuffer),true)}>Repay in full</Button>}<Button variant="outline" disabled={!ready||debt>0n||collateral===0n} onClick={()=>open('withdrawCollateral',exact(collateral))}>Withdraw collateral</Button><Button variant="outline" disabled={!ready||debt===0n||collateral===0n} onClick={()=>open('repayCollateral',exact(min(debt+interestBuffer,collateral)))}>Repay using collateral</Button></div></details>
      </Panel>
      <Panel className="simple-action"><div className="simple-tabs" role="tablist"><button role="tab" aria-selected={tab==='borrow'} className={tab==='borrow'?'selected':''} onClick={()=>switchTab('borrow')}>Borrow</button><button role="tab" aria-selected={tab==='repay'} className={tab==='repay'?'selected':''} onClick={()=>switchTab('repay')}>Repay</button></div><form onSubmit={e=>{e.preventDefault();if(valid&&ready)open(tab==='borrow'?'borrow':source==='available'?'repayAvailable':'repay',amount,repayAll&&source==='wallet');}}><label className="simple-label" htmlFor="doge-amount">How much would you like to {tab}?</label>{tab==='repay'&&<select className="simple-source" aria-label="Repayment source" value={source} onChange={e=>{setSource(e.target.value as 'available'|'wallet');setRepayAll(false);setAmount('');}}><option value="available">Use available DOGE</option><option value="wallet">Use wallet DOGE</option></select>}<div className="simple-amount"><div><input id="doge-amount" inputMode="decimal" autoComplete="off" placeholder="0.00" value={amount} onChange={e=>{setAmount(e.target.value);setRepayAll(false);}}/><span>Ð DOGE</span></div><div><span>Available: {state?doge(limit.toString())+' DOGE':'—'}</span><button type="button" disabled={!ready} onClick={()=>{setAmount(exact(limit));setRepayAll(tab==='repay'&&source==='wallet');}}>MAX</button></div></div><div className="simple-loan-preview"><div className="simple-row"><span>Your loan after {tab==='borrow'?'borrowing':'repayment'}</span><strong>{state?doge(nextDebt.toString())+' DOGE':'—'}</strong></div><div className="simple-row"><span>Loan-to-value after</span><strong>{state?ltv(nextDebt,collateral).toFixed(2)+'%':'—'}</strong></div></div>{amount&&!valid&&<p className="simple-error">{!parsed.ok?parsed.error:'Enter an amount within the available DOGE balance.'}</p>}<button type="submit" className="simple-primary" disabled={!ready||!valid}>{tab==='borrow'?'Borrow DOGE':'Repay DOGE'}<ArrowRight size={16}/></button><p className="simple-footnote"><LockKeyhole size={12}/>{tab==='borrow'?'Your collateral stays locked.':'Only confirmed repayment reduces your loan.'}</p></form></Panel>
    </div>
    <Panel className="simple-proceeds"><div><p>Available borrowed funds</p><strong>{doge(state?.position.available)} <small>DOGE</small></strong><span>Ready to withdraw. Withdrawing does not reduce your loan.</span></div><button className="simple-primary" disabled={!ready||available===0n} onClick={()=>open('withdrawProceeds',exact(available))}>Withdraw to wallet <ArrowRight size={16}/></button></Panel>
    <Panel className="simple-trading"><div className="simple-trade-top"><span className="simple-trade-icon"><TrendingUp size={24}/></span><div><h2>Trade to repay <span>Optional</span></h2><p>Trade with live prices. Convert net demo profits into test-DOGE loan repayments.</p></div><Link className="simple-secondary" to={tradingPath}>Open trading dashboard<ArrowRight size={15}/></Link></div></Panel>
    {hasLegacy&&<details className="simple-details simple-legacy"><summary>Previous vault, rewards and demo loan</summary><p>Your previous balances remain separate. Demo USD is not DOGE and cannot be withdrawn to your wallet.</p><PendingTransaction/><div className="simple-grid"><div><AccountRewardTerms/><AccountClaim/></div><AccountDepositWithdraw/>{!!account&&Number(account.repayment.debt)>0&&<AccountRepay/>}</div></details>}
    <details className="simple-details simple-history"><summary>Repayment history & activity</summary><p>Confirmed DOGE transactions saved in this browser</p><ul>{workflow?.receipts().map(r=><li key={r.hash}><a href={'https://dogeos-testnet.l2scan.co/tx/'+r.hash} target="_blank" rel="noreferrer">{titles[r.action]} · {r.status} ↗</a></li>)}</ul>{workflow?.receipts().length===0&&<p>No confirmed DOGE actions in this browser yet.</p>}{account?.demoDogeRepayment?.receipts.map(r=><p key={r.hash}><a href={'https://dogeos-testnet.l2scan.co/tx/'+r.hash} target="_blank" rel="noreferrer">Trading profit repayment · {doge(r.paidWei,6)} DOGE · Confirmed ↗</a></p>)}<details><summary>Previous demo USD history</summary><div className="simple-grid"><AccountRepaymentHistory/><AccountActivity/></div></details></details>
    <footer className="simple-footer"><span>DogeOS testnet · Test funds only</span><button disabled={!workflow||loading} onClick={()=>{void refresh();void wallet.refresh();void controller.refresh();}}><RefreshCw size={12}/>{loading?'Updating…':'Refresh balances'}</button></footer>
    <Dialog open={!!modal} onOpenChange={value=>{if(!value&&!sending){reviewEpoch.current++;setModal(null);setReview(null);setReviewing(false);}}}><DialogContent className="simple-dialog"><DialogHeader><DialogTitle>{modal?titles[modal.action]:''}</DialogTitle><DialogDescription>Review the amount before confirming in your wallet.</DialogDescription></DialogHeader>{modal&&!review&&<><label>DOGE amount<input autoFocus readOnly={!!modal.fullRepayment} className="simple-input" inputMode="decimal" value={modal.amount} onChange={e=>setModal({...modal,amount:e.target.value})}/></label>{modal.fullRepayment&&<p>The review refreshes your full balance, including interest. Any excess DOGE is refunded.</p>}{modal.action==='withdrawProceeds'&&<p>Your loan remains due after this withdrawal.</p>}{modal.action==='repayCollateral'&&<p>This spends collateral to reduce your loan.</p>}<Button disabled={reviewing||!modal.amount} onClick={()=>void prepare()}>{reviewing?'Checking amount and fees…':'Review transaction'}</Button></>}{review&&<><div className="simple-row"><span>Amount</span><strong>{exact(BigInt(review.amountWei))} DOGE</strong></div><div className="simple-row"><span>Fee reserve</span><strong>{exact(BigInt(review.feeWei))} DOGE</strong></div>{review.action==='repay'&&<p>Any amount above the confirmed debt is returned to your wallet.</p>}<Button disabled={sending} onClick={()=>void confirm()}>{sending?'Confirm in your wallet…':'Confirm in wallet'}</Button></>}{error&&<p role="alert" className="simple-error">{error}</p>}</DialogContent></Dialog>
  </>;
}

function TradingDashboard(){
 const {account,controller}=useAccountDashboard();const status=account?.demoDogeRepayment;
 const [saving,setSaving]=useState(false),[error,setError]=useState('');
 const change=async(enabled:boolean)=>{setSaving(true);setError('');try{if(!controller.service||!status)throw Error('Connect your wallet first.');await controller.service.setDemoDogeRepayment(enabled,status.revision);await controller.refresh();}catch(e){setError((e as Error).message);}finally{setSaving(false);}};
 return <><section className="simple-intro"><div><p className="simple-eyebrow">Trade to repay</p><h1>Trading Dashboard</h1><p>Choose your strategy and manage your trades.</p></div></section>
 <p className="simple-notice">Live market prices, simulated trades and swap. Net profit converts at 1 demo USDC = 1 test DOGE. A separate project reserve sends DOGE to repay your loan.</p>
 <div className="simple-grid"><AccountTradingStatus/><AccountOpenTrades/></div>
 <label className="simple-auto"><span>Use profits to repay my DOGE loan</span><input type="checkbox" aria-label="Use profits to repay my DOGE loan" disabled={saving||!status?.configured||!controller.service} checked={status?.enabled??false} onChange={e=>void change(e.target.checked)}/></label>
 <p className="simple-footnote">{saving?'Saving…':status?.pending?'Repayment is confirming on DogeOS.':status?.enabled?(status.ready?'Enabled. Repayment follows closed trades after fees, funding and losses.':status.reason):'Enable for 24 hours. Up to 10 test DOGE per wallet. Pending payments finish if you switch this off.'}</p>
 {error&&<p role="alert" className="simple-error">{error}</p>}
 {status&&<Panel title="Loan repayments from trading"><p>{doge(status.paidWei,6)} DOGE repaid</p>{status.receipts.length?<ul>{status.receipts.map(r=><li key={r.hash}><a href={'https://dogeos-testnet.l2scan.co/tx/'+r.hash} target="_blank" rel="noreferrer">{doge(r.paidWei,6)} DOGE · Confirmed ↗</a></li>)}</ul>:<p>No confirmed repayments yet.</p>}</Panel>}
 </>;
}

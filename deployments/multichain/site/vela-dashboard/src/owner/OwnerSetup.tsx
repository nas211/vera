import { useState } from 'react';
import { metamaskProvider } from '../../../src/wallet-discovery.js';
import { Panel } from '@/components/common/Panel';
import { Row } from '@/components/common/Metric';
import { Details } from '@/components/common/Details';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { formatFixed } from '@/lib/units';
import { deployment, readRPC, RewardDeployment, type DeploymentQuote, type DeploymentPending, type DeploymentReceipt } from './deployment';

const doge = (value:string) => formatFixed(BigInt(value),18,6,'up')+' test DOGE';
const explorer = 'https://dogeos-testnet.l2scan.co/tx/';
export function OwnerSetup() {
  const [workflow,setWorkflow]=useState<RewardDeployment|null>(null);
  const [quote,setQuote]=useState<DeploymentQuote|null>(null), [pending,setPending]=useState<DeploymentPending|null>(null);
  const [receipt,setReceipt]=useState<DeploymentReceipt|null>(null), [hash,setHash]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const run=async(fn:()=>Promise<void>)=>{if(busy)return;setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'Request failed. Check wallet history before retrying.');}finally{setBusy(false);}};
  const connect=()=>run(async()=>{
    const provider=metamaskProvider();if(!provider)throw Error('Open this page in a browser with your owner wallet extension.');
    await provider.request({method:'eth_requestAccounts'});
    const w=new RewardDeployment(provider,readRPC,localStorage,async fn=>{
      if(!navigator.locks)throw Error('This browser cannot coordinate deployment. Use a current desktop browser.');
      return navigator.locks.request('vela:reward-factory-deployment',fn);
    });
    setWorkflow(w);const saved=w.pending();setPending(saved);
    if(!saved){setNotice('Reading current nonce, gas and data fees…');setQuote(await w.quote());setNotice('Review these details, then approve the deployment in your wallet.');}
    else setNotice('A deployment is already saved in this browser. Check its confirmation below.');
  });
  const submit=()=>run(async()=>{
    if(!workflow||!quote)return;
    setNotice('Checking the reviewed transaction, then waiting for your wallet…');
    try {setPending(await workflow.submit(quote));setQuote(null);setNotice('Transaction submitted. Check confirmation below.');}
    finally {setPending(workflow.pending());}
  });
  const check=()=>run(async()=>{
    if(!workflow)return;
    const result=await workflow.receipt();setReceipt(result);setPending(workflow.pending());
    setNotice(result.status==='CONFIRMED'?'Factory and pool confirmed. Share the transaction hash here so backend activation can finish.':result.status==='REVERTED'?'Deployment reverted. Keep this receipt for a fresh deployment review.':'Waiting for three confirmations. Check again shortly.');
  });
  return <main className="mx-auto max-w-2xl space-y-5 px-5 py-10">
    <a href="./" className="text-sm text-primary">← Vela app</a>
    <div><h1 className="font-display text-2xl font-semibold">Activate the shared reward pool</h1><p className="mt-2 text-sm text-muted-foreground">Owner setup · DogeOS testnet. This deploys the factory and pool needed for borrowing and rewards.</p></div>
    <Panel title="Deployment review"><Row k="Owner & reward manager" v={deployment.owner} /><Row k="Starting rate" v="5% simple APR · paid in test DOGE" />
      <p className="mt-3 text-sm text-muted-foreground">Deployment fees come from the owner wallet. Pool funding is a separate step. Your existing vault stays registered.</p>
      {quote && <div className="mt-4 space-y-1"><Row k="Fee reserve, including data fees" v={doge(quote.reserveWei)} /><Row k="Wallet balance" v={doge(quote.balanceWei)} /><Details label="Addresses & transaction details"><Row k="Factory" v={quote.factory} /><Row k="Shared pool" v={quote.pool} /><Row k="Dedicated attestor" v={deployment.attestor} /><Row k="Nonce" v={BigInt(quote.nonce).toString()} /><Row k="Calldata hash" v={deployment.initHash} /></Details></div>}
      {!pending && <div className="mt-5 flex gap-3"><Button disabled={busy} onClick={connect}>{quote?'Refresh quote':'Connect owner wallet & review'}</Button>{quote&&<Button disabled={busy} onClick={submit}>Deploy factory & pool</Button>}</div>}
    </Panel>
    {pending && <Panel title="Deployment transaction"><p className="text-sm">{pending.phase.replaceAll('_',' ')}</p>{pending.hash ? <><a className="mt-3 block break-all text-sm text-primary" href={explorer+pending.hash} target="_blank" rel="noreferrer">{pending.hash}</a><Button className="mt-4" disabled={busy} onClick={check}>Check confirmation</Button></> : <div className="mt-3 space-y-3"><p className="text-sm text-muted-foreground">Check your wallet history before continuing. If submitted, paste its hash to recover the receipt.</p><Input aria-label="Deployment transaction hash" value={hash} onChange={e=>setHash(e.target.value)} /><Button disabled={busy} onClick={()=>void run(async()=>{await workflow!.recover(hash.trim());setPending(workflow!.pending());})}>Recover transaction</Button></div>}
      {receipt?.status==='CONFIRMED'&&<Details label="Confirmed contracts"><Row k="Factory" v={receipt.factory??''} /><Row k="Pool" v={receipt.pool??''} /><p className="mt-3 text-sm text-muted-foreground">Backend activation and pool funding are next.</p></Details>}
    </Panel>}
    {notice&&<p role="status" className="text-sm text-muted-foreground">{notice}</p>}{error&&<p role="alert" className="text-sm text-destructive">{error}</p>}
  </main>;
}

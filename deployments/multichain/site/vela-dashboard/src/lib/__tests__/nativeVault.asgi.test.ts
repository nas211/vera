/** Native workflow → actual ASGI routes → existing isolated USD ledger.
 * Wallet/RPC and native transitions are deterministic test doubles; Foundry covers the EVM. */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';
import { toHex } from 'viem';
import { AccountService, type ServiceAction } from '../vela/accountService';
import { DashboardController } from '../vela/dashboardController';
import { NativeVaultWorkflow, NATIVE_CHAIN, type NativeAction } from '../vela/nativeVault';

it('create → deposit → borrow → locked claim → settle → withdraw → cancel next loan',async()=>{
  const root=fileURLToPath(new URL('../../../../../',import.meta.url));
  const child=spawn(resolve(root,'.venv/bin/python'),[resolve(root,'verification/noah/vela-native-asgi-driver.py')],{cwd:root,stdio:['pipe','pipe','pipe']});
  const timeout=setTimeout(()=>child.kill(),45_000),lines=createInterface({input:child.stdout}),iterator=lines[Symbol.asyncIterator]();
  let stderr='';child.stderr.on('data',c=>{stderr+=String(c);});
  const next=async()=>{const line=await iterator.next();if(line.done)throw Error(`ASGI stopped: ${stderr}`);return JSON.parse(line.value);};
  const send=async(payload:unknown)=>{child.stdin.write(JSON.stringify(payload)+'\n');return next();};
  const storageMap=new Map<string,string>();
  try {
    const ready=await next();expect(ready.ready).toBe(true);
    const context={address:ready.address as string,network:'dogeos' as const,generation:1};
    const service=new AccountService(async request=>{
      const {signal,...payload}=request;if(signal.aborted)throw Error('Aborted');
      const r=await send(payload);if(r.status!==200)throw Error(`${r.status}: ${JSON.stringify(r.body)}`);return r.body;
    });service.setSession(context);
    const provider={request:async({method,params}:{method:string;params?:unknown[]})=>{
      switch(method){
        case 'eth_chainId':return toHex(NATIVE_CHAIN);case 'eth_accounts':return [context.address];
        case 'eth_getBlockByNumber':return {hash:ready.blockHash};case 'eth_getCode':return ready.code;
        case 'eth_estimateGas':return '0x5208';case 'eth_gasPrice':return '0x1';case 'eth_getTransactionCount':return '0x0';case 'eth_call':return '0x64';
        case 'eth_getBalance':return toHex(100000n*10n**18n);
        case 'eth_sendTransaction':{const result=await send({control:'broadcast',tx:params![0]});expect(result.status).toBe(200);return result.body;}
        default:throw Error(method);
      }
    }};
    const workflow=new NativeVaultWorkflow({service,provider,storage:{getItem:k=>storageMap.get(k)??null,setItem:(k,v)=>{storageMap.set(k,v);},removeItem:k=>{storageMap.delete(k);}},writesEnabled:true,now:()=>ready.now,withLock:async(_,fn)=>fn()});
    let counter=0;
    const dashboard=new DashboardController(service,workflow,()=>`native-journey-${++counter}`);
    const act=async(action:NativeAction)=>{
      await dashboard.refresh(); await dashboard.reviewNative(action);
      expect(dashboard.snapshot().error).toBe(''); expect(dashboard.snapshot().review?.kind).toBe('native');
      await dashboard.confirm(); expect(dashboard.snapshot().error).toBe('');
      const p=dashboard.snapshot().pending; expect(p?.hash).toBeTruthy();
      await dashboard.refresh(); expect(dashboard.snapshot().pending?.hash).toBe(p!.hash);
      expect(dashboard.snapshot().receipts.some(r=>r.hash===p!.hash)).toBe(false);
      expect((await send({control:'mine',hash:p!.hash})).status).toBe(200);
      await dashboard.refresh(); expect(dashboard.snapshot().error).toBe(''); expect(dashboard.snapshot().nativeError).toBe('');
      expect(dashboard.snapshot().pending).toBeNull();
      const receipt=dashboard.snapshot().receipts.find(r=>r.hash===p!.hash); expect(receipt?.status).toBe('CONFIRMED');
      return {receipt,state:dashboard.snapshot().native};
    };
    const ledger=async(action:ServiceAction)=>{
      await dashboard.reviewLedger(action); const review=dashboard.snapshot().review;
      if(review?.kind!=='ledger')throw Error(dashboard.snapshot().error);
      await dashboard.confirm(); if(dashboard.snapshot().error)throw Error(dashboard.snapshot().error);
      return review.request;
    };
    expect((await service.refresh()).paper.balance).toBe('0');
    await act({kind:'fundReserve',amount:'1'}); // Project can fund once before having a personal vault.
    await act({kind:'create'});await act({kind:'deposit',amount:'10000'});await act({kind:'setRewardRate',aprBps:'500'});
    expect((await service.refresh()).paper.balance).toBe('0');
    await act({kind:'requestBorrow',amount:'100',loanId:toHex(1n,{size:32})});
    await expect(workflow.review({kind:'withdraw',amount:'1'})).rejects.toThrow('locked');
    const bad=await send({path:'/api/borrow',body:{vaultId:'0x'+'00'.repeat(20),loanId:toHex(1n,{size:32})},context,key:'wrong-native-context'});expect(bad.status).toBe(400);
    const issued=await ledger({kind:'issueBorrow'});await service.execute(issued,true);
    const credited=await service.refresh();expect(credited.paper.balance).toBe('100');expect(credited.events.filter(e=>e.kind==='DEMO_LOAN_ISSUED')).toHaveLength(1);
    await act({kind:'acknowledge'});await send({control:'rewards'});
    const claim=await act({kind:'claim'});expect(claim.receipt?.rewardPaidWei).toBe('10000000000000000');
    const afterClaim=await service.refresh();expect(afterClaim.paper.balance).toBe('100');expect(afterClaim.repayment.debt).toBe(credited.repayment.debt);
    expect((await workflow.read()).vault?.withdrawable).toBe('0');
    await ledger({kind:'settle'});await send({control:'tick'});
    const paid=await service.refresh();expect(Number(paid.repayment.debt)).toBe(0);expect(paid.health?.loan?.state).toBe('AWAITING_CHAIN');
    await expect(workflow.review({kind:'withdraw',amount:'1'})).rejects.toThrow('locked');
    await act({kind:'settle'});expect((await service.refresh()).health?.loan?.state).toBe('REPAID');
    await act({kind:'withdraw',amount:'5000'});expect((await workflow.read()).vault?.collateral).toBe('5000000000000000000000');
    await act({kind:'requestBorrow',amount:'10',loanId:toHex(2n,{size:32})});await ledger({kind:'cancelBorrow'});
    await expect(ledger({kind:'issueBorrow'})).rejects.toThrow('cancelled');await act({kind:'cancel'});
    expect((await workflow.read()).vault?.withdrawable).toBe('5000000000000000000000');
    expect((await service.refresh()).paper.balance).toBe('0.000000');
    const foreign=await send({path:'/api/vault/state',context:{...context,address:'0x'+'ff'.repeat(20)}});expect(foreign.status).toBe(403);
    const config=await send({path:'/api/config',context});
    expect(config.body).toMatchObject({nativeVaultActionsEnabled:true,nativeWorkflowGatewayEnabled:true,mode:'PAPER',lending:'DEMO',liveEnabled:false,realFunding:false});
  } finally {
    child.stdin.end();await new Promise<void>(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',()=>resolve());});clearTimeout(timeout);lines.close();
  }
},50_000);

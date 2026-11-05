import {encodeFunctionData,serializeTransaction} from 'viem';
import {units,formatDoge,CHAIN_ID} from './wallet.js';

export const FAUCET='https://faucet.testnet.dogeos.com/';
export const EXPLORER='https://dogeos-testnet.l2scan.co';
const FACTORY='0xD6644a0C4233257b091D669142A7A8db3F2B7C3a';
const ORACLE='0x5300000000000000000000000000000000000002';
const abi=[{type:'function',name:'createVault',stateMutability:'nonpayable',inputs:[],outputs:[{type:'address'}]},
 {type:'function',name:'deposit',stateMutability:'payable',inputs:[],outputs:[]},
 {type:'function',name:'withdraw',stateMutability:'nonpayable',inputs:[{type:'uint256',name:'amount'}],outputs:[]}];
const oracleAbi=[{type:'function',name:'getL1Fee',stateMutability:'view',inputs:[{type:'bytes',name:'data'}],outputs:[{type:'uint256'}]}];
const hex=n=>'0x'+BigInt(n).toString(16);
export const pendingKey=owner=>`vela:testnet-vault:${CHAIN_ID}:${owner.toLowerCase()}`;
export function pendingTransaction(storage,owner){
 const raw=storage.getItem(pendingKey(owner));if(!raw)return null;
 const p=JSON.parse(raw);
 if(p.version!==1||p.owner!==owner.toLowerCase()||p.chainId!==CHAIN_ID||!['createVault','deposit','withdraw'].includes(p.action))throw Error('Invalid saved transaction. Review your wallet history before continuing.');
 return p;
}
function save(storage,owner,value){storage.setItem(pendingKey(owner),JSON.stringify(value));}
export async function checkWallet(provider,owner){
 if(!provider?.request)throw Error('Connect your DogeOS wallet first.');
 const chain=Number(await provider.request({method:'eth_chainId'}));
 const accounts=await provider.request({method:'eth_accounts'});
 if(chain!==CHAIN_ID||accounts?.[0]?.toLowerCase()!==owner.toLowerCase())throw Error('Wallet or network changed. Select the original wallet on DogeOS Chikyū Testnet.');
}
export function validateObservation(state,owner){
 if(state?.status!=='VERIFIED'||state.chainId!==CHAIN_ID||state.wallet?.toLowerCase()!==owner.toLowerCase()||state.factory?.toLowerCase()!==FACTORY.toLowerCase()||!Number.isInteger(state.block)||state.confirmations<3)throw Error('Confirmed DogeOS wallet state is unavailable.');
 if(state.registered!==Boolean(state.vault))throw Error('Vault registration is inconsistent.');
 const v=state.vault;
 if(v&&(!v.ownershipVerified||!v.registrationVerified||!v.bytecodeVerified||v.owner?.toLowerCase()!==owner.toLowerCase()||!/^0x[0-9a-f]{40}$/i.test(v.address)))throw Error('Vault ownership and code must be verified.');
 return state;
}
function capability(config,action){
 const c=config?.dogeosWallet;
 if(config?.chain?.id!=='dogeos'||c?.enabled!==true||c.chainId!==CHAIN_ID||c.factory?.toLowerCase()!==FACTORY.toLowerCase()||!c.actions?.includes(action))throw Error('Testnet wallet actions are not enabled on this deployment.');
}
export async function prepareTransaction({provider,owner,config,state,action,amount='0'}){
 owner=owner.toLowerCase();capability(config,action);validateObservation(state,owner);await checkWallet(provider,owner);
 const v=state.vault,quantity=action==='createVault'?0n:units(amount);
 if(action!=='createVault'&&quantity<=0n)throw Error('Enter a test DOGE amount above zero.');
 if(action==='createVault'&&v)throw Error('This wallet already has a registered vault.');
 if(action!=='createVault'&&(!v||v.paused))throw Error('An unpaused verified vault is required.');
 if(action==='deposit'&&v.loanState===4)throw Error('This vault cannot accept deposits.');
 if(action==='withdraw'&&quantity>BigInt(v.withdrawableWei))throw Error('Amount exceeds confirmed unlocked test DOGE.');
 const value=action==='deposit'?quantity:0n;
 const tx={from:owner,to:action==='createVault'?FACTORY:v.address,
  data:encodeFunctionData({abi,functionName:action,args:action==='withdraw'?[quantity]:[]}),value:hex(value)};
 const balance=BigInt(await provider.request({method:'eth_getBalance',params:[owner,'pending']}));
 if(balance<=value)throw Error('Insufficient test DOGE for this amount plus network fees. Get faucet funds or use a smaller amount.');
 let gas,gasPrice,nonce,l1;
 try{
  gas=(BigInt(await provider.request({method:'eth_estimateGas',params:[tx]}))*125n+99n)/100n;
  gasPrice=BigInt(await provider.request({method:'eth_gasPrice'}))*2n;
  nonce=Number(BigInt(await provider.request({method:'eth_getTransactionCount',params:[owner,'pending']})));
  // DogeOS L1GasPriceOracle accounts for the unsigned RLP signature padding.
  // See docs.dogeos.com/en/developers/transaction-fees-on-dogeos.
  const raw=serializeTransaction({type:'legacy',chainId:CHAIN_ID,nonce,gas,gasPrice,to:tx.to,data:tx.data,value});
  const data=encodeFunctionData({abi:oracleAbi,functionName:'getL1Fee',args:[raw]});
  l1=BigInt(await provider.request({method:'eth_call',params:[{to:ORACLE,data},'latest']}));
 }catch(e){throw Error(/insufficient funds/i.test(e.message)?'Insufficient test DOGE for network fees. Get faucet funds or use a smaller amount.':'Could not estimate execution and data/finality fees. Refresh and retry before approving.');}
 if(gas<=0n||gasPrice<=0n||l1<0n||!Number.isSafeInteger(nonce))throw Error('Invalid fee estimate.');
 const reserve=gas*gasPrice+2n*l1;
 if(balance<value+reserve)throw Error(`Insufficient test DOGE. Keep approximately ${formatDoge(reserve)} test DOGE for execution and data/finality fees, or get faucet funds.`);
 await checkWallet(provider,owner);
 return {version:1,owner,chainId:CHAIN_ID,action,amount:String(amount),tx:{...tx,gas:hex(gas),gasPrice:hex(gasPrice)},nonce,
  feeReserveWei:String(reserve),dataFeeEstimateWei:String(l1),createdAt:Date.now()};
}
export async function submitTransaction({provider,owner,config,quote,storage,readState,withLock}){
 if(!withLock)throw Error('This browser cannot safely coordinate wallet transactions. Use a current supported wallet browser.');
 return withLock(pendingKey(owner),async()=>{
  if(pendingTransaction(storage,owner))throw Error('A vault transaction is already pending. Check it before starting another.');
  const state=await readState();
  const fresh=await prepareTransaction({provider,owner,config,state,action:quote.action,amount:quote.amount});
  if(fresh.tx.to.toLowerCase()!==quote.tx.to.toLowerCase()||fresh.tx.data!==quote.tx.data||BigInt(fresh.feeReserveWei)>BigInt(quote.feeReserveWei))throw Error('Vault or fees changed. Review the transaction again.');
  const pending={...fresh,hash:null,phase:'AWAITING_WALLET'};
  // Fail before requesting approval if durable storage cannot be written.
  save(storage,owner,pending);
  try{
   await checkWallet(provider,owner);
   const hash=await provider.request({method:'eth_sendTransaction',params:[{...fresh.tx,chainId:hex(CHAIN_ID)}]});
   if(!/^0x[0-9a-f]{64}$/i.test(hash))throw Error('Wallet returned no valid transaction hash. Check wallet history.');
   pending.hash=hash;pending.phase='SUBMITTED';save(storage,owner,pending);return pending;
  }catch(e){
   // Other errors can occur after broadcast. Keep the context until reconciled.
   if(Number(e.code??e.data?.originalError?.code)===4001)storage.removeItem(pendingKey(owner));
   throw e;
  }
 });
}
export async function reconcileTransaction({owner,pending,readState,storage}){
 if(!pending?.hash||!/^0x[0-9a-f]{64}$/i.test(pending.hash))throw Error('Find the transaction hash in your wallet history to check its status. Do not send another deposit.');
 const state=validateObservation(await readState(pending.hash),owner),tx=state.transaction;
 if(!tx||tx.hash.toLowerCase()!==pending.hash.toLowerCase()||tx.status==='PENDING')return {pending:true,state};
 if(tx.sender?.toLowerCase()!==owner.toLowerCase()||tx.to?.toLowerCase()!==pending.tx.to.toLowerCase()||tx.data?.toLowerCase()!==pending.tx.data.toLowerCase()||BigInt(tx.valueWei)!==BigInt(pending.tx.value)||tx.block>state.block)throw Error('Confirmed transaction does not match this wallet action.');
 if(!['CONFIRMED','REVERTED'].includes(tx.status))throw Error('Unknown transaction status.');
 if(tx.status==='CONFIRMED'&&!state.vault)throw Error('Confirmed vault state is not available yet.');
 storage.removeItem(pendingKey(owner));
 return {pending:false,reverted:tx.status==='REVERTED',state,hash:pending.hash};
}
export function attachPendingHash(storage,owner,hash){
 if(!/^0x[0-9a-f]{64}$/i.test(hash))throw Error('Enter the transaction hash from your wallet.');
 const p=pendingTransaction(storage,owner);if(!p)throw Error('No pending wallet action.');
 if(p.hash)throw Error('A transaction hash is already recorded.');
 const updated={...p,hash,phase:'SUBMITTED'};save(storage,owner,updated);return updated;
}

export const CHAIN_ID=6281971;
let activeNetwork={id:'dogeos',family:'evm',chainId:CHAIN_ID};
export function selectWalletNetwork(network){activeNetwork=network;}
export const walletNetwork=()=>activeNetwork;
// A failed configuration fetch must leave the previous selector and wallet
// adapter usable. Persist the preference only after this succeeds.
export async function loadWalletNetwork(id,api=request){
  const previous=activeNetwork;
  selectWalletNetwork({id});
  try{
    const config=await api('/api/config');
    const chain=config.chain||{id:'dogeos',family:'evm',chainId:config.network.chainId};
    if(chain.id!==id)throw Error('Network configuration mismatch');
    selectWalletNetwork(chain);return config;
  }catch(error){selectWalletNetwork(previous);throw error;}
}
export const shortAddress=a=>a?`${a.slice(0,6)}…${a.slice(-4)}`:'';
export function units(text,decimals=18){
  if(!/^(0|[1-9]\d*)(\.\d+)?$/.test(String(text)))throw Error('Enter a positive decimal amount');
  const [a,b='']=String(text).split('.');if(b.length>decimals)throw Error('Too many decimal places');
  return BigInt(a)*10n**BigInt(decimals)+BigInt(b.padEnd(decimals,'0')||'0');
}
export async function request(path,body,address,key,options={}){
  const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',
    signal:options.signal,
    headers:{'Content-Type':'application/json','X-ADP-Client':'web','X-Vella-Network':(options.network||activeNetwork).id,...(address?{'X-Wallet-Address':address}:{}),...(key?{'Idempotency-Key':key}:{})},
    body:body===undefined?undefined:JSON.stringify(body)});
  const result=await response.json();if(!response.ok){const error=Error(result.error||result.detail||'Request failed');error.status=response.status;throw error;}return result;
}
export async function current(provider){
  if(activeNetwork.family==='solana'){
    const key=provider?.publicKey?.toString();
    if(!key||provider.isConnected===false)throw Error('Solana wallet disconnected');
    return key;
  }
  const accounts=await provider.request({method:'eth_accounts'});const chain=Number(await provider.request({method:'eth_chainId'}));
  if(!accounts?.[0])throw Error('Wallet disconnected');if(chain!==activeNetwork.chainId)throw Error('Switch to '+(activeNetwork.name||'DogeOS Chikyū Testnet'));
  return accounts[0].toLowerCase();
}
export async function prepareWallet(provider,network,{alreadyConnected=false,isCurrent=()=>true,selectAccount=false}={}){
  const check=()=>{if(!isCurrent())throw Error('Wallet changed');};
  check();
  if(activeNetwork.family==='solana'){
    if(!provider?.connect||!provider?.signMessage)throw Error('Compatible Solana wallet with message signing required');
    await provider.connect();check();return current(provider);
  }
  // eth_accounts is empty until the user grants access on a first connection.
  // Native MyDoge has already completed its SDK approval and must not prompt twice.
  let approved;
  if(!alreadyConnected){
    if(selectAccount){
      try{await provider.request({method:'wallet_requestPermissions',params:[{eth_accounts:{}}]});}
      catch(error){if(![-32601,4200].includes(Number(error?.code)))throw error;}
      check();
    }
    const accounts=await provider.request({method:'eth_requestAccounts'});check();
    if(!Array.isArray(accounts)||!/^0x[0-9a-f]{40}$/i.test(accounts[0]))throw Error('Wallet approval did not return an account. Reconnect.');
    approved=accounts[0].toLowerCase();
  }
  check();await ensureNetwork(provider,network);check();
  const owner=await current(provider);check();
  if(approved&&owner!==approved)throw Error('Wallet changed after approval. Select the same account in your wallet and reconnect.');
  return owner;
}
export async function authenticate(provider,api=request,onState=()=>{},options={}){
  const check=()=>{if(options.isCurrent&&!options.isCurrent())throw Error('Wallet changed');};
  onState('CONNECTING');check();
  const network=activeNetwork;
  let expected=options.expectedAddress;
  if(!options.alreadyConnected){if(network.family==='solana')await provider.connect();else {const approved=await provider.request({method:'eth_requestAccounts'});if(!Array.isArray(approved)||!/^0x[0-9a-f]{40}$/i.test(approved[0]))throw Error('Wallet approval did not return an account. Reconnect.');expected=approved[0].toLowerCase();}}
  const owner=await current(provider);
  if(expected&&owner!==expected)throw Error('Wallet changed after approval. Reconnect the account you selected.');
  const challenge=await api('/api/auth/nonce',{address:owner,chainId:network.chainId});
  check();if(await current(provider)!==owner)throw Error('Wallet changed');
  onState('SIGNING');
  const hex='0x'+Array.from(new TextEncoder().encode(challenge.message),b=>b.toString(16).padStart(2,'0')).join('');
  const signed=network.family==='solana'?(await provider.signMessage(new TextEncoder().encode(challenge.message),'utf8')).signature:options.signMessage?await options.signMessage({message:challenge.message}):await provider.request({method:'personal_sign',params:[hex,owner]});
  const signature=signed instanceof Uint8Array?(network.family==='solana'?'':'0x')+Array.from(signed,b=>b.toString(16).padStart(2,'0')).join(''):signed;
  if(activeNetwork!==network)throw Error('Network changed during authentication');
  check();if(await current(provider)!==owner)throw Error('Wallet changed');
  const session=await api('/api/auth/verify',{address:owner,nonce:challenge.nonce,signature});
  try{check();if(await current(provider)!==owner||session.authenticated!==true||session.address!==owner)throw Error('Wallet changed');}
  catch(error){await api('/api/logout',{});throw error;}
  onState('AUTHENTICATED');return owner;
}
export async function restoreSession(provider,api=request,isCurrent=()=>true){
  const session=await api('/api/session');
  try{
    const owner=await current(provider);
    if(!isCurrent()||session.authenticated!==true||session.address!==owner||session.network&&session.network!==activeNetwork.id)throw Error('Wallet changed');
    return owner;
  }catch(error){await api('/api/logout',{});throw error;}
}
export function sdkMatches(sdk,owner,provider){
  return Boolean(sdk?.isConnected&&sdk.chainType==='evm'&&Number(sdk.chainId)===CHAIN_ID&&
    sdk.address?.toLowerCase()===owner&&sdk.provider===provider);
}
export function formatDoge(wei){
  const n=BigInt(wei);if(n<0n)throw Error('Invalid wallet balance');
  const fraction=(n%10n**18n).toString().padStart(18,'0').replace(/0+$/,'');
  return (n/10n**18n).toString()+(fraction?'.'+fraction:'');
}
export async function walletBalance(provider,owner){
  if(activeNetwork.family==='solana')return null; // Demo collateral is never inferred from real wallet funds.
  if(await current(provider)!==owner)throw Error('Wallet changed');
  const hex=await provider.request({method:'eth_getBalance',params:[owner,'latest']});
  if(!/^0x[0-9a-f]+$/i.test(hex))throw Error('Invalid wallet balance');
  if(await current(provider)!==owner)throw Error('Wallet changed');
  return formatDoge(BigInt(hex));
}
export async function ensureNetwork(provider,network){
  const chainId=activeNetwork.chainId;
  if(Number(await provider.request({method:'eth_chainId'}))===chainId)return;
  try{await provider.request({method:'wallet_switchEthereumChain',params:[{chainId:'0x'+chainId.toString(16)}]});}
  catch(e){if(Number(e.code??e.data?.originalError?.code)!==4902)throw e;if(!network.rpcUrls?.length)throw Error('Add the official network in your wallet first');await provider.request({method:'wallet_addEthereumChain',params:[{chainId:'0x'+chainId.toString(16),chainName:chainId===CHAIN_ID?'DogeOS Chikyū Testnet':network.chainName,nativeCurrency:network.nativeCurrency,rpcUrls:network.rpcUrls,blockExplorerUrls:network.blockExplorerUrls}]});
   if(Number(await provider.request({method:'eth_chainId'}))!==chainId)await provider.request({method:'wallet_switchEthereumChain',params:[{chainId:'0x'+chainId.toString(16)}]});}
  if(Number(await provider.request({method:'eth_chainId'}))!==chainId)throw Error(activeNetwork.id==='dogeos'?'DogeOS network required':'Selected network required');
}
export function calculator(doge,price='.20',ltv='.40'){
  const collateral=Number(doge)*Number(price);const borrowed=collateral*Number(ltv);
  return {collateral,borrowed,scenarios:[0,.1,.25,.5].map(drop=>({drop,value:collateral*(1-drop),health:borrowed?collateral*(1-drop)*.65/borrowed:null}))};
}

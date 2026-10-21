import {chain, networkConfig} from './network.js';

export const mobile = () => /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
export const short = address => address ? `${address.slice(0,6)}…${address.slice(-4)}` : 'Connect wallet';
export const chainId = value => { try { return Number(BigInt(value)); } catch { return null; } };
export const validAddress = value => /^0x[0-9a-f]{40}$/i.test(value || '');

export function assertAccount(account,address) {
  if (!validAddress(address) || !validAddress(account?.wallet) || account.wallet.toLowerCase()!==address.toLowerCase()) {
    const error=new Error('Wallet session changed. Reconnect your wallet.');error.status=403;throw error;
  }
  if(account.mode!=='PAPER' || account.demo!==true || account.realFundingEnabled!==false || account.liveEnabled)
    throw new Error('Demo trading is unavailable.');
  return account;
}

export async function api(path, body, signal, address) {
  const response = await fetch(`/api/portal/${path}`, {
    method: body === undefined ? 'GET' : 'POST', credentials:'same-origin', cache:'no-store', signal,
    headers:{'Content-Type':'application/json','X-Vela-Portal':'1',...(address?{'X-Vela-Wallet':address}:{})},
    body:body === undefined ? undefined : JSON.stringify(body)
  });
  const data = await response.json().catch(()=>({detail:'Vela could not respond. Please retry.'}));
  if (!response.ok) {const e=new Error(typeof data.detail==='string'?data.detail:'Please check your entry and try again.');e.status=response.status;throw e;}
  if (data.mode && (data.mode!=='PAPER' || data.realFundingEnabled)) throw new Error('Demo trading is unavailable.');
  return data;
}

export async function switchNetwork(provider) {
  const id=`0x${chain.id.toString(16)}`;
  if(chainId(await provider.request({method:'eth_chainId'}))===chain.id)return;
  try {await provider.request({method:'wallet_switchEthereumChain',params:[{chainId:id}]});}
  catch(e){
    if(Number(e.code)!==4902)throw e;
    await provider.request({method:'wallet_addEthereumChain',params:[{chainId:id,chainName:chain.name,nativeCurrency:chain.nativeCurrency,rpcUrls:networkConfig.rpcUrls,blockExplorerUrls:networkConfig.blockExplorerUrls}]});
    await provider.request({method:'wallet_switchEthereumChain',params:[{chainId:id}]});
  }
  if(chainId(await provider.request({method:'eth_chainId'}))!==chain.id)throw new Error('Select DogeOS Chikyū Testnet in your wallet.');
}

export async function authenticate(provider, address, current, phase=()=>{}) {
  if(!validAddress(address))throw new Error('Choose an EVM account in your wallet.');
  const check=async()=>{
    const accounts=await provider.request({method:'eth_accounts'});
    const network=chainId(await provider.request({method:'eth_chainId'}));
    if(!current() || accounts?.[0]?.toLowerCase()!==address.toLowerCase() || network!==chain.id)
      throw new Error('Your wallet changed. Please connect again.');
  };
  await check();
  const challenge=await api('auth/challenge',{address,chainId:chain.id});
  const origin=location.origin;
  if(!/^[a-f0-9]{64}$/.test(challenge.challengeId) || typeof challenge.message!=='string' ||
      !challenge.message.startsWith(`${location.host} wants you to sign in with your Ethereum account:\n`) ||
      challenge.message.split('\n')[1]?.toLowerCase()!==address.toLowerCase() ||
      !challenge.message.includes(`\nURI: ${origin}/trade\nVersion: 1\nChain ID: ${chain.id}\nNonce: `))
    throw new Error('Vela returned an invalid sign-in message. Please reconnect.');
  await check();phase('SIGNING');
  // EIP-191 personal_sign. The server binds the complete message to a one-use
  // nonce, wallet, chain, configured origin and five-minute expiry.
  const message='0x'+Array.from(new TextEncoder().encode(challenge.message),b=>b.toString(16).padStart(2,'0')).join('');
  const signature=await provider.request({method:'personal_sign',params:[message,address]});
  await check();
  const result=await api('auth/verify',{challengeId:challenge.challengeId,signature});
  try {assertAccount(result,address);await check();} catch(e){await api('auth/logout',{},undefined,address).catch(()=>{});throw e;}
  return result;
}

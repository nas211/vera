import test from 'node:test';
import assert from 'node:assert/strict';
import {getWallets} from '@wallet-standard/app';
import {metamaskProvider,phantomProvider,standardSolanaAdapter,walletError} from '../src/wallet-discovery.js';
import {prepareWallet,selectWalletNetwork,ensureNetwork} from '../src/wallet.js';

test('Phantom namespace and legacy injection require connect and message signing',()=>{
 const phantom={isPhantom:true,connect:async()=>{},signMessage:async()=>{}};
 assert.equal(phantomProvider({phantom:{solana:phantom},solana:{isPhantom:false}}),phantom);
 assert.equal(phantomProvider({solana:phantom}),phantom);
 assert.equal(phantomProvider({solana:{...phantom,signMessage:undefined}}),null);
});
test('MetaMask is found among competing injected providers',()=>{
 const mm={isMetaMask:true,request:async()=>{}},other={isPhantom:true};
 assert.equal(metamaskProvider({ethereum:{providers:[other,mm]}}),mm);
 assert.equal(metamaskProvider({ethereum:mm}),mm);
 assert.equal(metamaskProvider({ethereum:other}),null);
});
test('Wallet Standard Phantom connects, signs exact bytes and tracks account changes',async()=>{
 const account={address:'SolanaCaseSensitiveAddress',chains:['solana:mainnet'],features:['solana:signMessage']},calls=[],handlers=new Set();
 const wallet={name:'Phantom',version:'1.0.0',chains:['solana:mainnet'],accounts:[],features:{
  'standard:connect':{connect:async options=>{calls.push(options);wallet.accounts=[account];return {accounts:wallet.accounts};}},
  'standard:events':{on:(_event,callback)=>{handlers.add(callback);return()=>handlers.delete(callback);}},
  'solana:signMessage':{signMessage:async input=>{assert.equal(input.account,account);assert.deepEqual([...input.message],[1,2,3]);return [{signature:new Uint8Array(64).fill(5)}];}},
  'standard:disconnect':{disconnect:async()=>{wallet.accounts=[];handlers.forEach(fn=>fn({accounts:[]}));}},
 }};
 const unregister=getWallets().register(wallet);
 try{const adapter=phantomProvider({});assert.equal(adapter,standardSolanaAdapter(wallet));assert.equal(adapter.isConnected,false);await adapter.connect({onlyIfTrusted:true});assert.deepEqual(calls,[{silent:true}]);assert.equal(adapter.publicKey.toString(),account.address);assert.equal((await adapter.signMessage(new Uint8Array([1,2,3]))).signature.length,64);let changed=false;const callback=()=>changed=true;adapter.on('accountChanged',callback);await adapter.disconnect();assert.equal(changed,true);assert.equal(adapter.isConnected,false);adapter.removeListener('accountChanged',callback);assert.equal(handlers.size,0);}finally{unregister();}
});
test('EIP-6963 announcements select MetaMask even when window.ethereum is another wallet',()=>{
 const previous=globalThis.window;globalThis.window=new EventTarget();
 try{const mm={request:async()=>{}};window.addEventListener('eip6963:requestProvider',()=>window.dispatchEvent(new CustomEvent('eip6963:announceProvider',{detail:{info:{uuid:'fixture-mm',rdns:'io.metamask'},provider:mm}})));assert.equal(metamaskProvider(),mm);}finally{globalThis.window=previous;}
});
test('adding Chikyu asks for a switch if the wallet did not switch automatically',async()=>{
 selectWalletNetwork({id:'dogeos',family:'evm',chainId:6281971});let chain=1,added=false;const calls=[];
 const provider={request:async q=>{calls.push(q);if(q.method==='eth_chainId')return '0x'+chain.toString(16);if(q.method==='wallet_switchEthereumChain'){if(!added)throw {code:4902};chain=Number(q.params[0].chainId);}if(q.method==='wallet_addEthereumChain')added=true;}};
 await ensureNetwork(provider,{chainName:'DogeOS',rpcUrls:['https://rpc.testnet.dogeos.com/'],blockExplorerUrls:['https://dogeos-testnet.l2scan.co'],nativeCurrency:{name:'DOGE',symbol:'DOGE',decimals:18}});
 assert.equal(chain,6281971);assert.equal(calls.filter(c=>c.method==='wallet_switchEthereumChain').length,2);assert.equal(calls.find(c=>c.method==='wallet_addEthereumChain').params[0].chainName,'DogeOS Chikyū Testnet');
});
test('declined or pending requests are clear and never claim authentication',()=>{
 assert.match(walletError({code:4001}),/declined.*disconnected/);assert.match(walletError({code:-32002}),/already open/);assert.match(walletError({code:4200}),/does not support/);
});

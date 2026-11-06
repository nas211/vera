import React,{useEffect,useLayoutEffect,useMemo,useRef} from 'react';
import {WalletConnectProvider,useWalletConnect,useAccount,ChainTypeEnum} from '@dogeos/dogeos-sdk';
import '@dogeos/dogeos-sdk/style.css';
// Stay mounted through onboarding and dashboard transitions. Native MyDoge
// readiness comes from the active account/provider, not embedded readiness.
function Bridge({network,onChange}){
 const connection=useWalletConnect(),account=useAccount();
 const latest=useRef();latest.current={connection,account};
 const actions=useMemo(()=>({
  open:()=>latest.current.connection.openModal(),
  disconnect:()=>latest.current.connection.disconnect(),
  signMessage:args=>{
   const sign=latest.current.account.signMessage;
   if(!sign)throw Error('This wallet does not support authentication signatures');
   return sign(args);
  },
  switchNetwork:()=>latest.current.account.switchChain({chainType:ChainTypeEnum.EVM,chainInfo:network}),
 }),[network]);
 useLayoutEffect(()=>{onChange({...actions,provider:account.currentProvider,address:account.address,
  chainType:account.chainType,chainId:account.chainId,isConnected:connection.isConnected,
  isConnecting:connection.isConnecting,isOpenModal:connection.isOpenModal,error:connection.error});
 },[onChange,actions,account.currentProvider,account.address,account.chainType,account.chainId,
  connection.isConnected,connection.isConnecting,connection.isOpenModal,connection.error]);
 useEffect(()=>()=>onChange(null),[onChange]);
 return null;
}
export default function MyDoge({config,onChange}){
 const network=useMemo(()=>({id:config.network.chainId,name:config.network.chainName,
  nativeCurrency:config.network.nativeCurrency,rpcUrls:{default:{http:config.network.rpcUrls}},
  blockExplorers:{default:{name:'DogeOS L2Scan',url:config.network.blockExplorerUrls[0]}},testnet:true}),[config.network]);
 const options=useMemo(()=>({clientId:config.myDogeClientId,defaultConnectChain:ChainTypeEnum.EVM,
  chains:{evm:[network]},
  login:{basicLogins:['email','externalWallets'],socialLogins:[]},
  theme:{defaultTheme:'dark',themes:{dark:{colors:{primary:{DEFAULT:config.brand.accent},background:'#101710'}}}},
  ...(config.walletConnectProjectId?{walletConnectProjectId:config.walletConnectProjectId}:{}),
  metadata:{name:config.brand.name,description:'DogeOS testnet collateral and simulated lending',url:location.origin,icons:[]}
 }),[config.myDogeClientId,config.walletConnectProjectId,config.brand.name,config.brand.accent,network]);
 return <WalletConnectProvider config={options}><Bridge network={network} onChange={onChange}/></WalletConnectProvider>;
}

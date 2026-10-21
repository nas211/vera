import React, {useEffect, useMemo, useRef, useState} from 'react';
import {WalletConnectProvider,useWalletConnect,useAccount,ChainTypeEnum} from '@dogeos/dogeos-sdk';
import '@dogeos/dogeos-sdk/style.css';
import {chain} from './network.js';

export default function MyDoge({onProvider,onDisconnect,onStart,onFailure}) {
  const clientId=window.VELA_WALLET_CONFIG?.dogeosClientId;
  const config=useMemo(()=>({clientId,login:{basicLogins:['externalWallets'],socialLogins:[]},
    defaultConnectChain:ChainTypeEnum.EVM,chains:{evm:[chain]},
    theme:{defaultTheme:'dark'},metadata:{name:'Vela',description:'Vela Demo',url:location.origin,icons:[`${location.origin}/favicon.svg`]}}),[clientId]);
  if(!clientId)return <p role="status">MyDoge mobile connection is awaiting activation.</p>;
  return <WalletConnectProvider config={config}><Bridge onProvider={onProvider} onDisconnect={onDisconnect} onStart={onStart} onFailure={onFailure}/></WalletConnectProvider>;
}

function Bridge({onProvider,onDisconnect,onStart,onFailure}) {
  const {isConnected,isConnecting,openModal,disconnect,error}=useWalletConnect();
  const {address,currentProvider,chainType,chainId}=useAccount();
  const previous=useRef(false),[message,setMessage]=useState('');
  useEffect(()=>{
    if(isConnected&&chainType==='evm'&&currentProvider?.request) {
      previous.current=true;onProvider({provider:currentProvider,address,chainId,disconnect});
    } else if(previous.current) {previous.current=false;onDisconnect();}
  },[isConnected,address,chainType,chainId,currentProvider,onProvider,onDisconnect,disconnect]);
  useEffect(()=>{if(error)onFailure();},[error,onFailure]);
  return <div className="mydoge-native">
    <p>Mobile connection preview. Actual MyDoge approval is not yet verified. Open this page in MyDoge’s mobile dApp browser to try it.</p>
    <button className="button button-outline" disabled={isConnecting} onClick={async()=>{try{setMessage('');onStart();await openModal();}catch{onFailure();setMessage('Connection cancelled. Please try again in MyDoge.');}}}>{isConnecting?'Connecting…':isConnected?'View MyDoge wallet':'Connect in MyDoge'}</button>
    {(message||error)&&<p role="status">{message||'MyDoge could not connect. Please retry in its mobile app.'}</p>}
  </div>;
}

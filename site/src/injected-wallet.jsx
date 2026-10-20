import React, {useEffect, useRef, useState} from "react";
import {chain, networkConfig} from "./network.js";
import {PracticePanel} from "./practice-panel.jsx";
import {formatEther} from "viem";

// Direct injected signing is independent of the hosted email-wallet service.
export function InjectedWallet() {
  const [provider,setProvider]=useState(null), [address,setAddress]=useState(""),
    [chainId,setChainId]=useState(null), [balance,setBalance]=useState("—"),
    [error,setError]=useState(""), [busy,setBusy]=useState(false), [refresh,setRefresh]=useState(0);
  const identity=useRef(null);
  identity.current=`${address}:${chainId}`;
  const onDogeOS=!!address && chainId===chain.id;
  useEffect(()=>{
    const found=e=>{if(e.detail?.info?.rdns==="io.metamask" && e.detail?.provider?.request)setProvider(e.detail.provider);};
    window.addEventListener("eip6963:announceProvider",found);
    window.dispatchEvent(new Event("eip6963:requestProvider"));
    const fallback=window.ethereum?.providers?.find(p=>p.isMetaMask) || (window.ethereum?.isMetaMask?window.ethereum:null);
    if(fallback?.request)setProvider(p=>p||fallback);
    return()=>window.removeEventListener("eip6963:announceProvider",found);
  },[]);
  useEffect(()=>{
    if(!provider)return;
    const accounts=a=>{setAddress(a?.[0]||"");setBalance("—");};
    const network=id=>{try{setChainId(Number(BigInt(id)));}catch{setChainId(null);}setBalance("—");};
    const disconnected=()=>{accounts([]);network(null);};
    provider.on?.("accountsChanged",accounts);provider.on?.("chainChanged",network);provider.on?.("disconnect",disconnected);
    return()=>{provider.removeListener?.("accountsChanged",accounts);provider.removeListener?.("chainChanged",network);provider.removeListener?.("disconnect",disconnected);};
  },[provider]);
  useEffect(()=>{
    setBalance("—");if(!onDogeOS)return;
    let cancelled=false;const selected=identity.current;
    (async()=>{try{
      const id=Number(BigInt(await provider.request({method:"eth_chainId"})));
      const value=await provider.request({method:"eth_getBalance",params:[address,"latest"]});
      const confirmedId=Number(BigInt(await provider.request({method:"eth_chainId"})));
      if(!cancelled && selected===identity.current && id===chain.id && confirmedId===chain.id)setBalance(`${Number(formatEther(BigInt(value))).toFixed(4)} DOGE`);
    }catch{if(!cancelled)setError("Could not read your wallet balance. Refresh to retry.");}})();
    return()=>{cancelled=true;};
  },[provider,address,onDogeOS,chainId,refresh]);
  async function connect(){
    if(!provider){setError("Open this page in MetaMask’s browser, or install its browser extension.");return;}
    setBusy(true);setError("");
    try{
      const accounts=await provider.request({method:"eth_requestAccounts"});
      setAddress(accounts[0]||"");setChainId(Number(BigInt(await provider.request({method:"eth_chainId"}))));
    }catch(e){setError(Number(e.code)===4001?"Connection cancelled in your wallet.":"MetaMask could not connect. Please retry.");}
    finally{setBusy(false);}
  }
  async function switchNetwork(){
    setBusy(true);setError("");
    try{
      try{await provider.request({method:"wallet_switchEthereumChain",params:[{chainId:`0x${chain.id.toString(16)}`}]});}
      catch(e){if(Number(e.code)!==4902)throw e;
        await provider.request({method:"wallet_addEthereumChain",params:[{chainId:`0x${chain.id.toString(16)}`,chainName:chain.name,nativeCurrency:chain.nativeCurrency,rpcUrls:networkConfig.rpcUrls,blockExplorerUrls:networkConfig.blockExplorerUrls}]});
        await provider.request({method:"wallet_switchEthereumChain",params:[{chainId:`0x${chain.id.toString(16)}`}]});
      }
      setChainId(Number(BigInt(await provider.request({method:"eth_chainId"}))));
    }catch(e){setError(Number(e.code)===4001?"Network change cancelled.":"Could not switch networks. Select DogeOS Chikyū in MetaMask.");}
    finally{setBusy(false);}
  }
  return <><section className="wallet-stage">
    <span className="stage-mark">Ð</span><p className="stage-eyebrow">YOUR WALLET. YOUR PORTFOLIO.</p>
    <h1>Make your<br/><em>next move.</em></h1>
    {address?<div className="stage-account"><span className="stage-connected">CONNECTED · METAMASK</span><strong title={address}>{address.slice(0,8)}…{address.slice(-6)}</strong>
      <dl><div><dt>Network</dt><dd>{onDogeOS?"DogeOS Chikyū":"Different network"}</dd></div><div><dt>DOGE balance</dt><dd>{balance}</dd></div></dl>
      {!onDogeOS && <button id="injected-switch" className="stage-primary" disabled={busy} onClick={switchNetwork}>Switch to DogeOS ↗</button>}
      <div className="stage-actions"><button onClick={()=>setRefresh(n=>n+1)}>Refresh balance</button><button onClick={()=>{setAddress("");setBalance("—");}}>Disconnect</button></div>
    </div>:<button id="injected-connect" className="stage-primary" disabled={busy} onClick={connect}>Connect MetaMask ↗</button>}
    {error&&<p role="alert" className="stage-message">{error}</p>}
    {!provider&&<a className="stage-secondary" href={`https://link.metamask.io/dapp/${location.host}/mydoge.html?wallet=metamask`}>Open MetaMask mobile ↗</a>}
    <a className="stage-secondary" href="/mydoge.html">Use MyDoge / email login ↗</a>
    <a className="stage-secondary" href={networkConfig.faucetUrl} target="_blank" rel="noopener noreferrer">Get DOGE from the faucet ↗</a>
    <a className="stage-secondary" href="#ai-portfolio">Open AI portfolio ↓</a>
    <p className="stage-footnote">The practice portfolio uses a virtual balance. No wallet deposit or gas is required.</p>
  </section><PracticePanel/></>;
}

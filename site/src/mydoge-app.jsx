import React, { useEffect, useMemo, useRef, useState } from "react";
import { WalletConnectProvider, useWalletConnect, useAccount, useDogeOSAuth, ChainTypeEnum } from "@dogeos/dogeos-sdk";
import "@dogeos/dogeos-sdk/style.css";
import { chain, networkConfig } from "./network.js";
import { TradingPanel } from "./trading-panel.jsx";
import { InjectedWallet } from "./injected-wallet.jsx";

const validAddress = value => typeof value === "string" && /^0x[0-9a-f]{40}$/i.test(value);
const normalizeId = value => {
  try { return value === null || value === undefined ? null : BigInt(value); } catch { return null; }
};
const formatBalance = hex => {
  if (typeof hex !== "string" || !/^0x[0-9a-f]+$/i.test(hex)) throw new Error("Invalid balance");
  const n = BigInt(hex), unit = 10n ** 18n;
  return `${(n / unit).toLocaleString("en-US")}.${(n % unit).toString().padStart(18, "0").slice(0, 4)} DOGE`;
};

export function MyDogeApp({ clientId }) {
  const config = useMemo(() => ({
    clientId,
    login: { basicLogins: ["email", "externalWallets"], socialLogins: [{type:"google"}] },
    defaultConnectChain: ChainTypeEnum.EVM,
    chains: { evm: [chain] },
    theme: { defaultTheme: "dark", themes: { dark: { colors: { primary: { DEFAULT: "#dfc18b" }, background: "#10120f" } } } },
    metadata: { name: "Vela", description: "Vela AI Agents", url: location.origin, icons: [`${location.origin}/favicon.svg`] }
  }), [clientId]);
  if (new URLSearchParams(location.search).get("wallet") === "metamask") return <InjectedWallet />;
  return <WalletConnectProvider config={config}><AccountPanel /></WalletConnectProvider>;
}

function AccountPanel() {
  const { isConnected, isConnecting, openModal, closeModal, disconnect, error } = useWalletConnect();
  const auth = useDogeOSAuth();
  const { address, chainType, chainId, currentProvider, currentWallet, switchChain } = useAccount();
  const [balance, setBalance] = useState(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [locallyDisconnected, setLocallyDisconnected] = useState(false);
  const generation = useRef(0);
  const identity = useRef(null);
  const connected = !locallyDisconnected && isConnected && validAddress(address) && chainType === "evm" && typeof currentProvider?.request === "function";
  const deliveryRejected = !connected && auth.state.kind === "error" && auth.state.error?.code === "invalid_delivery";
  const onDogeOS = connected && normalizeId(chainId) === BigInt(chain.id);
  const key = connected ? `${address.toLowerCase()}:${chainType}:${chainId}` : "";
  // Replaced during render so late reads cannot win a race with an account change.
  const currentIdentity = useMemo(() => ({ key, provider: currentProvider }), [key, currentProvider]);
  identity.current = currentIdentity;
  const displayedBalance = onDogeOS && balance?.identity === currentIdentity ? balance.text : onDogeOS ? "Loading…" : "—";
  useEffect(() => {
    // Surface a recoverable action instead of the SDK's generic retry loop.
    // Do not alter the SDK's delivery origin or bypass its authorization checks.
    if (deliveryRejected) closeModal();
  }, [deliveryRejected, closeModal]);

  useEffect(() => {
    const request = ++generation.current;
    setBalance(null); setMessage(""); setBusy(false);
    if (!onDogeOS) return;
    let cancelled = false;
    const stillCurrent = () => !cancelled && request === generation.current && identity.current === currentIdentity;
    (async () => {
      try {
        const observed = await currentProvider.request({ method: "eth_chainId" });
        if (!stillCurrent()) return;
        if (normalizeId(observed) !== BigInt(chain.id)) throw new Error("Network changed");
        const raw = await currentProvider.request({ method: "eth_getBalance", params: [address, "latest"] });
        if (!stillCurrent()) return;
        // Recheck the provider's network after the asynchronous read.
        const verified = await currentProvider.request({ method: "eth_chainId" });
        if (!stillCurrent()) return;
        if (normalizeId(verified) !== BigInt(chain.id)) throw new Error("Network changed");
        setBalance({ identity: currentIdentity, text: formatBalance(raw) });
      } catch {
        if (stillCurrent()) {
          setBalance({ identity: currentIdentity, text: "Unavailable" });
          setMessage("Could not read your balance. Check your wallet and refresh.");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [onDogeOS, currentIdentity, currentProvider, address, refresh]);

  async function act(action, task) {
    const selected = identity.current;
    setBusy(true); setMessage("");
    try { await task(); }
    catch (err) {
      if (identity.current !== selected) return;
      setMessage(Number(err?.code) === 4001 ? `${action} cancelled in your wallet.` : `${action} could not be completed. Please try again.`);
    } finally { if (identity.current === selected) setBusy(false); }
  }
  async function connect() {
    setLocallyDisconnected(false);
    await act("Connection", () => openModal());
  }
  async function leave() {
    generation.current++;
    setLocallyDisconnected(true); setBalance(null);
    try { await disconnect(); }
    catch { setMessage("Disconnected from this view. You can also remove Vela’s connection in your wallet."); }
  }
  async function resetLogin() {
    setBusy(true); setMessage("");
    try {
      await auth.logout();
      location.reload();
    } catch {
      setMessage("MyDoge could not reset this login. Open Vela in a new browser tab or connect MetaMask.");
      setBusy(false);
    }
  }
  return <><section className="wallet-stage" aria-labelledby="mydoge-title">
    <span className="stage-mark" aria-hidden="true">Ð</span>
    <p className="stage-eyebrow">MYDOGE WALLET · DOGEOS</p>
    <h1 id="mydoge-title">Your wallet.<br /><em>Your possibilities.</em></h1>
    <p>Connect your wallet, get faucet DOGE and start your own AI trading portfolio.</p>
    {connected ? <div className="stage-account">
      <span className="stage-connected">CONNECTED · {currentWallet?.info?.name || "DogeOS Wallet"}</span>
      <strong id="mydoge-address" title={address}>{address.slice(0, 6)}…{address.slice(-4)}</strong>
      <dl>
        <div><dt>Network</dt><dd id="mydoge-network">{onDogeOS ? "DogeOS Chikyū" : "Different network"}</dd></div>
        <div><dt>DOGE balance</dt><dd id="mydoge-balance">{displayedBalance}</dd></div>
      </dl>
      {!onDogeOS && <button id="mydoge-switch" className="stage-primary" disabled={busy || isConnecting} onClick={() => act("Network change", () => switchChain({ chainType: ChainTypeEnum.EVM, chainInfo: chain }))}>Switch to DogeOS ↗</button>}
      {onDogeOS && <div className="stage-actions"><button id="mydoge-refresh" onClick={() => setRefresh(n => n + 1)}>Refresh balance</button><a href={`${chain.blockExplorers.default.url}/address/${address}`} target="_blank" rel="noopener noreferrer">View on explorer ↗</a></div>}
      <button id="mydoge-disconnect" className="stage-secondary" disabled={busy} onClick={leave}>Disconnect</button>
    </div> : <button id="mydoge-open" className="stage-primary" disabled={busy || isConnecting} onClick={connect}>{isConnecting ? "Connecting…" : "Continue with MyDoge"} <span>↗</span></button>}
    {isConnected && !locallyDisconnected && !connected && <p role="status">Choose an EVM account for DogeOS Chikyū in the wallet connection.</p>}
    {(message || error) && <p id="mydoge-message" className="stage-message" role="status">{message || "The wallet connection needs attention. Open your wallet and try again."}</p>}
    {deliveryRejected && <div className="stage-notice" role="alert">
      <p>MyDoge rejected this email sign-in request. Reset and start a fresh request.</p>
      <button id="mydoge-reset-login" className="stage-primary" disabled={busy} onClick={resetLogin}>Reset sign-in ↗</button>
      <a className="stage-secondary" href="https://defivela.xyz/mydoge" target="_blank" rel="noopener noreferrer">Open Vela in a new tab ↗</a>
      <small>If it repeats, MyDoge needs to check this app’s email-login configuration. MyDoge mobile and MetaMask connect separately.</small>
    </div>}
    <a className="stage-secondary" href="/mydoge.html?wallet=metamask">Use MetaMask <span>↗</span></a>
    <a className="stage-secondary" href="/">Back to Vela <span>↗</span></a>
    <a className="stage-secondary" href={networkConfig.faucetUrl} target="_blank" rel="noopener noreferrer">Get DOGE from the faucet ↗</a>
    <a className="stage-secondary" href="#ai-portfolio">Open AI portfolio ↓</a>
    <p className="stage-footnote">Your faucet DOGE funds your portfolio. One approval to start, one to stop and receive DOGE back.</p>
  </section>{connected ? <TradingPanel address={address} provider={currentProvider} onDogeOS={onDogeOS}
    embedded={currentWallet?.isEmbeddedWallet === true}
    onSettled={() => setRefresh(n => n + 1)}/> :
    <section id="ai-portfolio" className="trading-workspace"><p className="stage-eyebrow">YOUR DOGE. ONE AGENT TEAM.</p>
      <h2>Faucet to <em>portfolio.</em></h2><p className="trading-intro">Connect → fund your wallet from the faucet → choose an amount → START TRADING. The vault wraps your DOGE and credits TUSDC for the AI agents. Stop to close your positions and receive DOGE back.</p>
      <button className="stage-primary" disabled={busy || isConnecting} onClick={connect}>Connect to start ↗</button>
    </section>}</>;
}

import React, { useEffect, useMemo, useRef, useState } from "react";
import { WalletConnectProvider, useWalletConnect, useAccount, ChainTypeEnum } from "@dogeos/dogeos-sdk";
import "@dogeos/dogeos-sdk/style.css";
import { chain, networkConfig } from "./network.js";
import { PracticePanel } from "./practice-panel.jsx";
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
  const { isConnected, isConnecting, openModal, disconnect, error } = useWalletConnect();
  const { address, chainType, chainId, currentProvider, currentWallet, switchChain } = useAccount();
  const [balance, setBalance] = useState(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [locallyDisconnected, setLocallyDisconnected] = useState(false);
  const generation = useRef(0);
  const identity = useRef(null);
  const connected = !locallyDisconnected && isConnected && validAddress(address) && chainType === "evm" && typeof currentProvider?.request === "function";
  const onDogeOS = connected && normalizeId(chainId) === BigInt(chain.id);
  const key = connected ? `${address.toLowerCase()}:${chainType}:${chainId}` : "";
  // Replaced during render so late reads cannot win a race with an account change.
  const currentIdentity = useMemo(() => ({ key, provider: currentProvider }), [key, currentProvider]);
  identity.current = currentIdentity;
  const displayedBalance = onDogeOS && balance?.identity === currentIdentity ? balance.text : onDogeOS ? "Loading…" : "—";

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
  return <><section className="wallet-stage" aria-labelledby="mydoge-title">
    <span className="stage-mark" aria-hidden="true">Ð</span>
    <p className="stage-eyebrow">MYDOGE WALLET · DOGEOS</p>
    <h1 id="mydoge-title">Your wallet.<br /><em>Your possibilities.</em></h1>
    <p>Open Vela in MyDoge to use your wallet in the app. On the web, continue through DogeOS’s wallet connection.</p>
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
    <a className="stage-secondary" href="/mydoge.html?wallet=metamask">Use MetaMask <span>↗</span></a>
    <a className="stage-secondary" href="/">Back to Vela <span>↗</span></a>
    <a className="stage-secondary" href={networkConfig.faucetUrl} target="_blank" rel="noopener noreferrer">Get DOGE from the faucet ↗</a>
    <a className="stage-secondary" href="#ai-portfolio">Open AI portfolio ↓</a>
    <p className="stage-footnote">The practice portfolio below uses a virtual balance. No wallet deposit or gas is required.</p>
  </section><PracticePanel/></>;
}

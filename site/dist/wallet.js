(() => {
  "use strict";
  // Official DogeOS Chikyū Testnet configuration, verified 2026-10-20:
  // https://docs.dogeos.com/en/developers/developer-quickstart
  // DOGE has 18 native decimals on DogeOS, unlike Dogecoin L1's 8:
  // https://docs.dogeos.com/en/developers/native-doge-erc20
  const NETWORK = Object.freeze({
    chainId: "0x5fdaf3",
    chainName: "DogeOS Chikyū Testnet",
    nativeCurrency: { name: "Dogecoin", symbol: "DOGE", decimals: 18 },
    rpcUrls: ["https://rpc.testnet.dogeos.com/"],
    blockExplorerUrls: ["https://dogeos-testnet.l2scan.co"]
  });
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const discovered = new Map();
  let provider = null;
  let account = null;
  let chain = null;
  let busy = false;
  let epoch = 0;
  let refreshId = 0;
  let handlers = null;
  const isAddress = (value) => typeof value === "string" && /^0x[0-9a-f]{40}$/i.test(value);
  const normalizeChain = (value) => typeof value === "string" && /^0x[0-9a-f]+$/i.test(value) ? `0x${BigInt(value).toString(16)}` : null;
  const shorten = (value) => `${value.slice(0, 6)}…${value.slice(-4)}`;
  function message(text, error = false) {
    $("#wallet-message").textContent = text;
    $("#wallet-message").classList.toggle("error", error);
  }
  function availableProvider() {
    for (const item of discovered.values()) if (item.info.rdns === "io.metamask") return item.provider;
    const candidates = window.ethereum?.providers || (window.ethereum ? [window.ethereum] : []);
    return candidates.find((candidate) => candidate.isMetaMask && !candidate.isBraveWallet && !candidate.isCoinbaseWallet && typeof candidate.request === "function") || null;
  }
  function detect() {
    const available = !!availableProvider();
    $("#metamask-availability").textContent = available ? "Detected · connect your account" : "Use the extension or MetaMask’s in-app browser";
    $("#wallet-install").hidden = available;
  }
  window.addEventListener("eip6963:announceProvider", (event) => {
    const detail = event.detail;
    if (detail?.info?.uuid && detail.info.rdns === "io.metamask" && typeof detail.provider?.request === "function") {
      discovered.set(detail.info.uuid, detail);
      detect();
    }
  });
  window.dispatchEvent(new Event("eip6963:requestProvider"));
  window.addEventListener("ethereum#initialized", detect, { once: true });
  detect();
  const dappHost = location.protocol === "https:" ? `${location.host}${location.pathname}` : "defivela.uuuiiaaaa.chatgpt.site";
  $("#metamask-mobile").href = `https://link.metamask.io/dapp/${dappHost}`;

  function render() {
    const connected = !!account;
    const onDogeOS = chain === NETWORK.chainId;
    $("#wallet-disconnected").hidden = connected;
    $("#wallet-connected").hidden = !connected;
    $$(".wallet-button-label").forEach((label) => { label.textContent = connected ? shorten(account) : "Connect wallet"; });
    $(".app-wallet-status").textContent = connected ? `${shorten(account)} · WALLET ↗` : "CONNECT WALLET ↗";
    $("#wallet-address").textContent = connected ? shorten(account) : "";
    $("#wallet-address").title = account || "";
    $("#wallet-network").textContent = onDogeOS ? "DogeOS Chikyū · Testnet" : chain ? `Different network · ${BigInt(chain).toString()}` : "Checking…";
    $("#wallet-switch").hidden = !connected || onDogeOS;
    $("#wallet-refresh").hidden = !connected || !onDogeOS;
    $("#wallet-explorer").hidden = !connected || !onDogeOS;
    if (connected && onDogeOS) $("#wallet-explorer").href = `${NETWORK.blockExplorerUrls[0]}/address/${account}`;
    else $("#wallet-explorer").removeAttribute("href");
    $("#metamask-connect").disabled = busy;
    $("#wallet-switch").disabled = busy;
    $("#wallet-refresh").disabled = busy;
  }
  function detach() {
    if (provider && handlers && typeof provider.removeListener === "function") {
      for (const [event, handler] of Object.entries(handlers)) provider.removeListener(event, handler);
    }
    handlers = null;
  }
  function reset() {
    epoch++; refreshId++;
    detach(); provider = null; account = null; chain = null; busy = false;
    $("#wallet-balance").textContent = "—";
    render();
  }
  function formatDoge(hex) {
    if (typeof hex !== "string" || !/^0x[0-9a-f]+$/i.test(hex)) throw new Error("Invalid balance");
    const value = BigInt(hex);
    const whole = value / 10n ** 18n;
    const decimal = (value % 10n ** 18n).toString().padStart(18, "0").slice(0, 4);
    return `${whole.toLocaleString("en-US")}.${decimal} DOGE`;
  }
  async function refresh() {
    if (!provider || !account) return;
    const currentProvider = provider, currentAccount = account, currentEpoch = epoch, request = ++refreshId;
    $("#wallet-balance").textContent = "—";
    try {
      const observed = normalizeChain(await currentProvider.request({ method: "eth_chainId" }));
      if (currentEpoch !== epoch || request !== refreshId || currentAccount !== account) return;
      if (!observed) throw new Error("Invalid chain");
      chain = observed; render();
      if (chain !== NETWORK.chainId) { message("Connected. Switch to DogeOS Chikyū Testnet to view your testnet DOGE."); return; }
      $("#wallet-balance").textContent = "Loading…";
      const balance = await currentProvider.request({ method: "eth_getBalance", params: [currentAccount, "latest"] });
      if (currentEpoch !== epoch || request !== refreshId || currentAccount !== account || chain !== observed) return;
      $("#wallet-balance").textContent = formatDoge(balance);
      message("Connected to DogeOS testnet. Trading and rewards remain in preview.");
    } catch {
      if (currentEpoch !== epoch || request !== refreshId) return;
      $("#wallet-balance").textContent = "Unavailable";
      message("Could not read the network or balance. Check MetaMask, then refresh.", true);
    }
  }
  function attach() {
    const attached = provider;
    handlers = {
      accountsChanged(accounts) {
        if (provider !== attached) return;
        if (!Array.isArray(accounts) || !isAddress(accounts[0])) { reset(); message("Wallet disconnected or locked."); return; }
        account = accounts[0]; refreshId++;
        render(); refresh();
      },
      chainChanged(value) {
        if (provider !== attached) return;
        chain = normalizeChain(value); refreshId++;
        $("#wallet-balance").textContent = "—"; render(); refresh();
      },
      disconnect() { if (provider === attached) { reset(); message("MetaMask disconnected. Reopen your wallet and reconnect.", true); } }
    };
    if (typeof provider.on === "function") for (const [event, handler] of Object.entries(handlers)) provider.on(event, handler);
  }
  function explain(error, action) {
    if (Number(error?.code) === 4001) return `${action} cancelled in MetaMask.`;
    if (Number(error?.code) === -32002) return "A request is already open in MetaMask. Please respond there first.";
    return `${action} could not be completed. Open MetaMask, unlock it, and try again.`;
  }
  $("#metamask-connect").addEventListener("click", async () => {
    if (busy) return;
    const selected = availableProvider();
    if (!selected) { detect(); message("Open this site in MetaMask’s mobile browser, or install the browser extension."); return; }
    busy = true; const operation = ++epoch; render();
    message("Approve the account connection in MetaMask.");
    try {
      const accounts = await selected.request({ method: "eth_requestAccounts" });
      if (operation !== epoch) return;
      if (!Array.isArray(accounts) || !isAddress(accounts[0])) throw new Error("No account selected");
      detach(); provider = selected; account = accounts[0]; chain = null;
      attach(); render(); await refresh();
    } catch (error) {
      if (operation === epoch) { reset(); message(explain(error, "Connection"), true); }
    } finally {
      if (operation === epoch || !account) { busy = false; render(); }
    }
  });
  $("#wallet-switch").addEventListener("click", async () => {
    if (busy || !provider || !account) return;
    busy = true; const currentProvider = provider; const operation = epoch; render();
    message("Approve the DogeOS testnet network change in MetaMask.");
    try {
      try {
        await currentProvider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: NETWORK.chainId }] });
      } catch (error) {
        if (Number(error?.code) !== 4902) throw error;
        await currentProvider.request({ method: "wallet_addEthereumChain", params: [NETWORK] });
        if (provider !== currentProvider || operation !== epoch) return;
        await currentProvider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: NETWORK.chainId }] });
      }
      if (provider === currentProvider && operation === epoch) await refresh();
    } catch (error) {
      if (provider === currentProvider && operation === epoch) message(explain(error, "Network change"), true);
    } finally { if (provider === currentProvider) { busy = false; render(); } }
  });
  $("#wallet-disconnect").addEventListener("click", async () => {
    const previous = provider;
    reset(); message("Disconnected from Vela.");
    if (previous) {
      try { await previous.request({ method: "wallet_revokePermissions", params: [{ eth_accounts: {} }] }); }
      catch { if (!account) message("Disconnected from Vela. You can also remove this site’s permission in MetaMask."); }
    }
  });
  $("#wallet-refresh").addEventListener("click", () => refresh());
  $("#wallet-copy").addEventListener("click", async () => {
    if (!account) return;
    try { await navigator.clipboard.writeText(account); message("Wallet address copied."); }
    catch { message(`Your address: ${account}`); }
  });
  $$(".wallet-open").forEach((button) => button.addEventListener("click", () => {
    detect(); render(); $("#wallet-dialog").showModal();
  }));
  render();
  // Returning from the MyDoge screen may open the chooser, never request access.
  if (new URLSearchParams(location.search).get("wallet") === "metamask") {
    $("#wallet-dialog").showModal();
    $("#metamask-connect").focus();
  }
})();

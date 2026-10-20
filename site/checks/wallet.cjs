const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const artifacts = process.env.CHECK_ARTIFACTS_DIR || "/tmp/vela-checks";
fs.mkdirSync(artifacts, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || undefined,
    headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"]
  });
  const origin = process.env.PREVIEW_URL || "http://127.0.0.1:8769";
  const errors = [], requests = [], cases = [];
  async function setup(options = {}) {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => requests.push(request.url()));
    await page.addInitScript((options) => {
      const listeners = {};
      const mock = window.mock = {
        chain: options.chain || "0x1",
        accounts: ["0x1111111111111111111111111111111111111111"],
        balance: "0x6b14e9f812f366c35",
        calls: [], error: options.error, switchMissing: options.switchMissing,
        hold: false, resolve: null, rejectRevoke: false,
        emit(event, data) { (listeners[event] || []).slice().forEach(fn => fn(data)); }
      };
      const provider = {
        isMetaMask: true,
        on(event, fn) { (listeners[event] ||= []).push(fn); },
        removeListener(event, fn) { listeners[event] = (listeners[event] || []).filter(item => item !== fn); },
        async request(args) {
          mock.calls.push(args);
          switch (args.method) {
            case "eth_requestAccounts":
              if (mock.error) throw { code: mock.error };
              return mock.accounts;
            case "eth_chainId": return mock.chain;
            case "eth_getBalance":
              if (mock.hold) return new Promise(resolve => { mock.resolve = resolve; });
              return mock.balance;
            case "wallet_switchEthereumChain":
              if (mock.switchError) throw { code: mock.switchError };
              if (mock.switchMissing) { mock.switchMissing = false; throw { code: 4902 }; }
              mock.chain = args.params[0].chainId;
              mock.emit("chainChanged", mock.chain);
              return null;
            case "wallet_addEthereumChain": return null;
            case "wallet_revokePermissions":
              if (mock.rejectRevoke) throw { code: 4200 };
              return null;
            default: throw new Error(`Unexpected wallet method: ${args.method}`);
          }
        }
      };
      if (options.fallback) window.ethereum = provider;
      else if (!options.absent) {
        // An unrelated injected wallet must not override EIP-6963 MetaMask.
        window.ethereum = { isMetaMask: true, isCoinbaseWallet: true, request() { throw new Error("Wrong provider"); } };
        window.addEventListener("eip6963:requestProvider", () => {
          window.dispatchEvent(new CustomEvent("eip6963:announceProvider", {
            detail: { info: { uuid: "mock-metamask", rdns: "io.metamask", name: "MetaMask", icon: "" }, provider }
          }));
        });
      }
    }, options);
    await page.goto(origin, { waitUntil: "networkidle" });
    assert.deepEqual(await page.evaluate(() => mock.calls), [], "Wallet should not request anything before a click");
    await page.locator(".wallet-open").first().click();
    await page.waitForSelector("#wallet-dialog[open]");
    return page;
  }
  async function connect(page) {
    await page.locator("#metamask-connect").click();
    await page.waitForFunction(() => !document.querySelector("#metamask-connect").disabled);
  }
  async function waitBalance(page, text) {
    await page.waitForFunction(text => document.querySelector("#wallet-balance").textContent === text, text);
  }
  let page = await setup({ absent: true });
  await connect(page);
  assert.equal(await page.locator("#wallet-install").isVisible(), true);
  assert.match(await page.locator("#metamask-mobile").getAttribute("href"), /^https:\/\/link\.metamask\.io\/dapp\//);
  assert.deepEqual(await page.evaluate(() => mock.calls), []);
  cases.push("absent MetaMask and mobile deep link"); await page.close();

  for (const error of [4001, -32002]) {
    page = await setup({ error }); await connect(page);
    assert.equal(await page.locator("#wallet-connected").isVisible(), false);
    assert.match(await page.locator("#wallet-message").textContent(), error === 4001 ? /cancelled/ : /already open/);
    await page.evaluate(() => { mock.error = null; });
    await connect(page);
    assert.equal(await page.locator("#wallet-connected").isVisible(), true);
    cases.push(`connection error ${error} and retry`); await page.close();
  }

  page = await setup({ switchMissing: true }); await connect(page);
  assert.match(await page.locator("#wallet-network").textContent(), /Different network/);
  assert.equal(await page.locator("#wallet-balance").textContent(), "—");
  assert.equal(await page.evaluate(() => mock.calls.some(c => c.method === "eth_getBalance")), false);
  await page.evaluate(() => { mock.switchError = 4001; });
  await page.locator("#wallet-switch").click();
  await page.waitForFunction(() => document.querySelector("#wallet-message").textContent.includes("cancelled"));
  assert.match(await page.locator("#wallet-network").textContent(), /Different network/);
  await page.evaluate(() => { mock.switchError = null; });
  await page.locator("#wallet-switch").click();
  const expectedBalance = await page.evaluate(() => {
    const n = BigInt(mock.balance);
    return `${(n / 10n ** 18n).toLocaleString("en-US")}.${(n % 10n ** 18n).toString().padStart(18, "0").slice(0,4)} DOGE`;
  });
  await waitBalance(page, expectedBalance);
  const added = await page.evaluate(() => mock.calls.find(c => c.method === "wallet_addEthereumChain").params[0]);
  assert.deepEqual(added, {
    chainId: "0x5fdaf3", chainName: "DogeOS Chikyū Testnet",
    nativeCurrency: { name: "DOGE", symbol: "DOGE", decimals: 18 },
    rpcUrls: ["https://rpc.testnet.dogeos.com/"],
    blockExplorerUrls: ["https://dogeos-testnet.l2scan.co"]
  });
  assert.match(await page.locator("#wallet-explorer").getAttribute("href"), /dogeos-testnet\.l2scan\.co\/address\/0x111/);
  cases.push("EIP-6963 provider selection; rejected switch; add and switch DogeOS testnet; 18-decimal balance");
  await page.evaluate(() => {
    mock.accounts = ["0x2222222222222222222222222222222222222222"];
    mock.emit("accountsChanged", mock.accounts);
  });
  await page.waitForFunction(() => document.querySelector("#wallet-address").title.startsWith("0x222"));
  await waitBalance(page, expectedBalance);
  assert.equal(await page.evaluate(() => mock.calls.filter(c => c.method === "eth_getBalance").at(-1).params[0]), "0x2222222222222222222222222222222222222222");
  await page.evaluate(() => { mock.hold = true; });
  await page.locator("#wallet-refresh").click();
  await page.waitForFunction(() => mock.resolve !== null);
  await page.evaluate(() => {
    mock.chain = "0x1"; mock.emit("chainChanged", mock.chain);
    mock.resolve("0xffffffffffffffffffff");
  });
  await page.waitForFunction(() => document.querySelector("#wallet-network").textContent.includes("Different network"));
  assert.equal(await page.locator("#wallet-balance").textContent(), "—");
  assert.equal(await page.locator("#wallet-explorer").getAttribute("href"), null);
  cases.push("account updates; stale balance cannot overwrite network change");
  await page.evaluate(() => { mock.rejectRevoke = true; });
  await page.locator("#wallet-disconnect").click();
  await page.waitForFunction(() => document.querySelector("#wallet-message").textContent.includes("remove this site"));
  assert.equal(await page.locator("#wallet-connected").isVisible(), false);
  assert.equal(await page.locator(".wallet-button-label").first().textContent(), "Connect wallet");
  cases.push("local disconnect when revoke is unsupported");
  await page.close();

  page = await setup({ fallback: true, chain: "0x5fdaf3" }); await connect(page);
  await waitBalance(page, expectedBalance);
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    assert.equal(await page.locator("#wallet-dialog").evaluate(el => el.scrollWidth > el.clientWidth + 1), false, `Wallet overflow at ${width}`);
  }
  await page.screenshot({ path: path.join(artifacts, "vela-wallet.png") });
  await page.evaluate(() => mock.emit("accountsChanged", []));
  assert.equal(await page.locator("#wallet-connected").isVisible(), false);
  await connect(page);
  await page.evaluate(() => mock.emit("disconnect", { code: 4900 }));
  assert.equal(await page.locator("#wallet-connected").isVisible(), false);
  await connect(page);
  await page.locator("#wallet-disconnect").click();
  assert.equal(await page.evaluate(() => mock.calls.at(-1).method), "wallet_revokePermissions");
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#wallet-dialog").getAttribute("open"), null);
  const allowed = new Set(["eth_requestAccounts", "eth_chainId", "eth_getBalance", "wallet_switchEthereumChain", "wallet_addEthereumChain", "wallet_revokePermissions"]);
  assert.equal(await page.evaluate(() => mock.calls.map(c => c.method)).then(methods => methods.every(m => allowed.has(m))), true);
  cases.push("legacy injected provider; wallet responsive at 4 widths; lock/provider disconnect/reconnect; explicit revoke; Escape");
  assert.deepEqual(errors, []);
  assert.deepEqual(requests.filter(url => /whytradingbot|bybit|supabase|weex|rpc\.testnet\.dogeos/.test(url)), []);
  await browser.close();
  console.log(JSON.stringify({ result: "passed", cases, errors, liveWalletRequests: 0 }, null, 2));
})().catch(error => { console.error(error); process.exit(1); });

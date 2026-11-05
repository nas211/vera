// Additive, build-free view over the existing wallet/session and paper APIs.
// No wallet provider calls, signing methods or blockchain writes exist here.
const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const set = (node, value) => { if (node.textContent !== String(value)) node.textContent = String(value); };
const money = value => new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'}).format(Number(value || 0));
const doge = value => new Intl.NumberFormat('en-US', {maximumFractionDigits:6}).format(Number(value || 0)) + ' test DOGE';
let generation = 0, stateKey = '', busy = false, lastCheck = 0, authenticated = false;
let currentWallet = '', loading = false;
const panel = el('section', undefined, 'panel dogeos-contract-panel');
panel.id = 'dogeos-contract-panel';
panel.setAttribute('aria-label', 'DogeOS testnet contracts and demo trading');
panel.innerHTML = `<p class="eyebrow">DOGEOS × VELA</p>
  <div class="panel-title"><h2>DogeOS Chikyū Testnet</h2><span class="tag">CHAIN 6281971 · READ ONLY</span></div>
  <p class="small dogeos-disclosure">Live testnet contract data. Demo deposits and paper trading use a separate simulated ledger. No DOGE moves, and Lighter trades do not settle on DogeOS.</p>
  <p class="dogeos-read-status" role="status">Checking confirmed on-chain data…</p>
  <div class="dogeos-contract-grid"><dl class="dogeos-facts"></dl><div class="dogeos-demo">
    <p class="eyebrow">SIMULATED TRADING FUNDS</p><h3 class="dogeos-equity">Connect to view demo equity</h3>
    <p class="small dogeos-pnl">Paper trading uses live Lighter market data.</p>
    <div class="button-row"><button class="primary dogeos-fund" type="button">Deposit Demo Funds</button><button class="dogeos-trade" type="button">Open AI Trading →</button></div>
    <p class="small">Demo funds are not redeemable. Contract deposits, withdrawals, borrowing, liquidation and settlement are disabled.</p>
  </div></div><details class="guided-details dogeos-contract-details"><summary>Verified contracts & explorer receipts</summary><div class="dogeos-links"></div></details>`;
const status = panel.querySelector('.dogeos-read-status');
const facts = panel.querySelector('.dogeos-facts');
const rows = {};
for (const label of ['Wallet', 'Wallet balance', 'Vault', 'Vault balance', 'Vault status']) {
  const row = el('div');
  row.append(el('dt', label), rows[label] = el('dd', '—'));
  facts.append(row);
}
const links = panel.querySelector('.dogeos-links');
const linkRows = {};
for (const label of ['Factory', 'Reference vault', 'Factory deployment', 'Vault creation']) {
  const row = el('p', undefined, 'small');
  const link = el('a', '—', 'address tx-link');
  link.target = '_blank'; link.rel = 'noopener noreferrer';
  row.append(el('span', label), link); links.append(row); linkRows[label] = link;
}
const referenceNote = el('p', '', 'small'); links.append(referenceNote);
const fund = panel.querySelector('.dogeos-fund');
const trade = panel.querySelector('.dogeos-trade');
function clearPrivate() {
  currentWallet = '';
  for (const node of Object.values(rows)) set(node, '—');
  set(panel.querySelector('.dogeos-equity'), 'Connect to view demo equity');
  set(panel.querySelector('.dogeos-pnl'), 'Paper trading uses live Lighter market data.');
  fund.disabled = trade.disabled = true;
  dialog.close();
}
async function api(path, body, key) {
  const response = await fetch(path, {credentials:'same-origin', method:body === undefined ? 'GET' : 'POST',
    headers:{'Content-Type':'application/json','X-ADP-Client':'web','X-Vella-Network':'dogeos',
      ...(currentWallet ? {'X-Wallet-Address':currentWallet} : {}), ...(key ? {'Idempotency-Key':key} : {})},
    body:body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) { const error = new Error(result.error || 'Request unavailable'); error.status = response.status; throw error; }
  return result;
}
function paintContract(data, privateView) {
  set(status, `Verified from DogeOS RPC · confirmed block ${data.block.toLocaleString()} · ${new Date(data.observedAt * 1000).toLocaleTimeString()}`);
  const vault = data.vault;
  set(rows.Wallet, privateView ? data.wallet : 'Connect your wallet');
  set(rows['Wallet balance'], privateView ? doge(data.walletBalanceTestDOGE) : 'Shown after authentication');
  set(rows.Vault, privateView ? (vault?.address || 'No registered vault for this wallet') : data.referenceVault);
  set(rows['Vault balance'], vault ? doge(vault.balanceTestDOGE) : '—');
  set(rows['Vault status'], vault ? `${privateView ? 'Your vault' : 'Reference vault'} · ${vault.paused ? 'PAUSED' : vault.status} · registered` : 'Read-only demo available without a personal vault');
  set(referenceNote, `Reference vault owner: ${data.referenceOwner}. ${privateView && !vault ? 'The deployed reference vault belongs to this owner, not your connected wallet. ' : ''}Treasury and settlement attestor verified against the deployment record.`);
  for (const [label, path] of [['Factory', 'address/' + data.factory], ['Reference vault', 'address/' + data.referenceVault],
    ['Factory deployment', 'tx/' + data.deploymentTransactions.VaultFactory], ['Vault creation', 'tx/' + data.deploymentTransactions.DogeCollateralVault]]) {
    linkRows[label].href = data.explorer + '/' + path;
    set(linkRows[label], path.split('/')[1] + ' ↗');
  }
}
async function refresh() {
  if (busy || loading || !document.documentElement.classList.contains('dogeos-readonly')) return;
  loading = true;
  const token = generation, privateView = authenticated;
  try {
    if (privateView) {
      const session = await api('/api/session');
      if (token !== generation) return;
      currentWallet = session.address;
    }
    const [data, account] = await Promise.all([api(privateView ? '/api/dogeos/account' : '/api/dogeos/status'),
      privateView ? api('/api/account') : Promise.resolve(null)]);
    if (token !== generation) return;
    paintContract(data, privateView);
    if (account) {
      set(panel.querySelector('.dogeos-equity'), money(account.paper.equity) + ' demo equity');
      set(panel.querySelector('.dogeos-pnl'), `${account.paper.preferences.autonomous ? 'AI entries enabled' : 'AI stopped'} · ${account.paper.positions.length} open positions · ${money(account.paper.pnl)} total P&L`);
      fund.disabled = Number(account.directAllocation) >= 10000;
      trade.disabled = false;
    }
    lastCheck = Date.now();
  } catch (error) {
    if (token !== generation) return;
    if (error.status === 401 || error.status === 403) clearPrivate();
    set(status, 'Contract data unavailable. Refresh to verify current state.');
    for (const label of ['Wallet balance','Vault balance','Vault status']) set(rows[label], 'Unavailable');
  } finally { loading = false; }
}
const refreshButton = el('button', 'Refresh on-chain data');
refreshButton.type = 'button'; refreshButton.addEventListener('click', refresh);
panel.querySelector('.dogeos-contract-details').append(refreshButton);
trade.addEventListener('click', () => document.getElementById('tab-trading')?.click());
const demonstration = el('details', undefined, 'guided-details dogeos-recorded-demo');
demonstration.append(el('summary', 'Recorded paper-trading test demonstration'));
const demonstrationBody = el('div'); demonstration.append(demonstrationBody); panel.append(demonstration);
demonstration.addEventListener('toggle', async () => {
  if (!demonstration.open || demonstrationBody.childElementCount) return;
  try {
    const data = await api('/api/dogeos/demonstration');
    demonstrationBody.replaceChildren(el('p', data.label, 'small'));
    for (const item of data.cases) {
      demonstrationBody.append(el('p', `${item.scenario}: ${money(item.unrealizedPnl)} unrealized → ${money(item.realizedPnl)} realized. Protective ${item.protectiveExit} exit remained active after Stop Trading.`, 'small'));
    }
    demonstrationBody.append(el('p', data.scope, 'small'));
  } catch { demonstrationBody.replaceChildren(el('p', 'Recorded test evidence unavailable.', 'small')); }
});

// The existing authenticated demo-allocation API owns limits and idempotency.
const dialog = document.createElement('dialog');
dialog.className = 'panel dogeos-demo-dialog';
dialog.setAttribute('aria-labelledby', 'dogeos-demo-title');
dialog.innerHTML = `<form><p class="eyebrow">SIMULATION ONLY</p><h2 id="dogeos-demo-title">Deposit Demo Funds</h2>
  <p class="muted">Add non-redeemable demo USDC to your existing paper-trading ledger. No wallet transaction or DOGE deposit occurs.</p>
  <label>Demo USDC amount<input name="amount" inputmode="decimal" type="number" min="0.000001" max="10000" step="0.000001" value="500" required></label>
  <p class="small">The existing lifetime limit is 10,000 demo USDC per wallet. Trading risk limits are unchanged.</p>
  <p class="dogeos-demo-error" role="alert"></p><div class="button-row"><button class="primary" type="submit">Confirm demo deposit</button><button type="button" class="dogeos-cancel">Cancel</button></div></form>`;
document.body.append(dialog);
let requestKey = '', requestAmount = '';
fund.addEventListener('click', () => {
  if (!authenticated || !currentWallet) return;
  set(dialog.querySelector('.dogeos-demo-error'), '');
  requestKey = ''; requestAmount = ''; dialog.showModal();
});
dialog.querySelector('.dogeos-cancel').addEventListener('click', () => { if (!busy) dialog.close(); });
dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
dialog.querySelector('form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy || !authenticated || !currentWallet) return;
  const amount = dialog.querySelector('input').value, token = generation;
  if (!requestKey || amount !== requestAmount) {requestKey = crypto.randomUUID(); requestAmount = amount;}
  busy = true;
  dialog.querySelectorAll('button').forEach(button => button.disabled = true);
  try {
    await api('/api/trading/allocate', {amount,confirm:true}, requestKey);
    if (token === generation) dialog.close();
  } catch (error) {
    if (token === generation) set(dialog.querySelector('.dogeos-demo-error'), error.message);
  } finally {
    busy = false; dialog.querySelectorAll('button').forEach(button => button.disabled = false);
    if (token === generation) { lastCheck = 0; refresh(); }
  }
});

// React retains its existing dashboard, connectors and trading controls. This
// sibling panel mounts only in the DogeOS portal and never touches the homepage.
function reconcile() {
  const selector = document.querySelector('select[aria-label="Network"]');
  const dogeos = selector?.value === 'dogeos';
  const signedIn = dogeos && Boolean(document.querySelector('header .text-button'));
  const identity = dogeos ? (document.querySelector('header .text-button')?.textContent || 'anonymous') : 'other';
  if (identity !== stateKey) {
    stateKey = identity; generation++; authenticated = signedIn; lastCheck = 0; clearPrivate();
  }
  document.documentElement.classList.toggle('dogeos-readonly', dogeos);
  if (!dogeos) { panel.remove(); return; }
  const dashboard = document.querySelector('.guided-app');
  const tab = document.querySelector('.guided-tabs [aria-selected="true"]')?.id.replace('tab-', '');
  panel.classList.toggle('is-trading', tab === 'trading');
  const anchor = dashboard?.querySelector('.guided-heading') || document.querySelector('.portal-welcome');
  if (anchor && panel.previousElementSibling !== anchor) anchor.after(panel);
  const unsupported = ['borrow','agent'].includes(tab);
  if (unsupported) document.getElementById('tab-vault')?.click();
  if (Date.now() - lastCheck > 10000) refresh();
}
let queued = false;
const observer = new MutationObserver(() => {
  if (!queued) { queued = true; requestAnimationFrame(() => {queued = false; reconcile();}); }
});
observer.observe(document.getElementById('root'), {childList:true,subtree:true,attributes:true,attributeFilter:['aria-selected']});
setInterval(reconcile, 3000);
reconcile();

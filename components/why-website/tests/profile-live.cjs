// Run against a local Next server. Every account/auth request is mocked; no
// exchange credentials, customer accounts, or trading endpoints are used.
// PROFILE_TEST_URL=http://127.0.0.1:3012 node --test tests/profile-live.cjs
// Playwright and an installed browser are required. Existing installations may
// be supplied through PLAYWRIGHT_MODULE_PATH and CHROME_PATH.
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const baseURL = process.env.PROFILE_TEST_URL || "http://127.0.0.1:3012";
assert.ok(["127.0.0.1", "localhost"].includes(new URL(baseURL).hostname), "Use a local test server");
let browser;
before(async () => { browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined, args: ["--no-sandbox"] }); });
after(async () => { await browser?.close(); });

const uid = "00000000-0000-4000-8000-000000000001";
const user = { id: uid, email: "live-flow@example.test", aud: "authenticated", role: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2026-10-03T00:00:00Z" };
const settings = { strategy: "why-1", allocation_pct: 25, position_size_pct: 2, leverage: 5, risk_preset: "conservative" };
function yellow(state = "DISCONNECTED", equity = null, available = null) {
  return {
    account: { id: "yellow-test", state, state_reason: null, api_key_masked: state === "DISCONNECTED" ? null : "test••••key",
      verification_state: state === "PENDING_YELLOW_VERIFICATION" ? "PENDING" : state === "DISCONNECTED" ? "NONE" : "VERIFIED",
      verification_reason: state === "PENDING_YELLOW_VERIFICATION" ? "permission_metadata_unavailable" : null,
      permission_source: null, verified_at: null, app_session_id: null, balance_equity: equity, balance_available: available,
      balance_at: equity == null ? null : "2026-10-03T00:00:00Z", connected_at: null },
    config: { position_size_usd: 50, pending_position_size_usd: null, leverage: 10, max_open_positions: 8, entries_paused: state !== "ACTIVE", activated_at: null, sizes_usd: [20, 50, 100, 200, 500] },
    runtime: { open_positions: 0, ambiguous_orders: 0, venue: null, loaded: false, positions: [] }, events: [],
  };
}
function weex(state = "DISCONNECTED", equity = null, available = null) {
  return { id: "weex-test", state, state_reason: null, api_key_masked: state === "DISCONNECTED" ? null : "test••••weex",
    balance_equity: equity, balance_available: available, connected_at: null, status: "live", settings, runtime: { open_positions: 0, positions: [] } };
}
async function setup(t, options = {}) {
  const context = await browser.newContext({ viewport: options.desktop ? { width: 1280, height: 900 } : { width: 390, height: 844 } });
  t.after(() => context.close());
  const page = await context.newPage();
  const faults = [];
  page.on("pageerror", (e) => faults.push(e.message));
  const requests = [];
  let me = options.me || yellow();
  let wx = options.weex || weex();
  const expires = Math.floor(Date.now() / 1000) + 3600;
  const token = [Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url"),
    Buffer.from(JSON.stringify({ sub: uid, aud: "authenticated", role: "authenticated", exp: expires })).toString("base64url"), "test-signature"].join(".");
  await context.addInitScript(({ user, expires, token }) => {
    localStorage.setItem("sb-profile-test-auth-token", JSON.stringify({ access_token: token, refresh_token: "test-refresh", token_type: "bearer", expires_in: 3600, expires_at: expires, user }));
    localStorage.setItem(`why_tutorial_v1_${user.id}`, "complete");
  }, { user, expires, token });
  await context.route("https://profile-test.supabase.co/**", (route) => route.fulfill({ json: user }));
  await context.route("**/yellow/api/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/yellow/api", "");
    requests.push(`${req.method()} ${path}`);
    let data;
    if (path === "/public-config") data = { configured: true, supabase_url: "https://profile-test.supabase.co", supabase_anon_key: "test-anon-key", sizes_usd: [20, 50, 100, 200, 500], leverage: 10, leverages: [5, 10], position_sizes_pct: [2, 5], max_open_positions: 8, weex_status: "live", billing_configured: false };
    else if (path === "/me") data = me;
    else if (path === "/me/subscription") data = { subscription: { plan: "free", plan_name: "WHY Free", status: "active", live_allowed: true, exchange_limit: 1, paper_starting_balance: 25000 }, plans: [] };
    else if (path === "/me/paper") data = { paper_enabled: true, running: false, paper_starting_balance: 25000, paper_equity: 25000, paper_available_balance: 25000, paper_realized_pnl: 0, paper_total_fees: 0, paper_return_pct: 0, paper_max_drawdown: 0, paper_open_positions: [], paper_trade_history: [], settings };
    else if (path === "/me/weex") data = wx;
    else if (path === "/me/trades") data = { account_id: "yellow-test", mode: "live", summary: { closed_trades: 0, wins: 0, losses: 0, realized_pnl: 0, fees: 0, volume: 0, open_positions: 0, last_trade_at: null }, trades: [] };
    else if (path === "/me/yellow/connect") data = me = options.connectResult || yellow("VERIFIED", 1234.56, 1000);
    else if (path === "/me/weex/connect") data = wx = weex("VERIFIED", 987.65, 800);
    else if (path === "/me/bot/activate") {
      if (options.startFailure) return route.fulfill({ status: 409, json: { error: "activation_not_allowed_from_state" } });
      data = me = { ...me, account: { ...me.account, state: "ACTIVE" } };
    }
    else if (path === "/me/bot/pause") data = me = { ...me, account: { ...me.account, state: "PAUSED" } };
    else if (path === "/me/weex/start") data = wx = { ...wx, state: "ACTIVE" };
    else if (path === "/me/weex/stop") data = wx = { ...wx, state: "PAUSED" };
    else throw new Error(`Unmocked API request: ${req.method()} ${path}`);
    return route.fulfill({ json: data });
  });
  await page.goto(baseURL + "/profile");
  await page.getByText(user.email, { exact: true }).first().waitFor();
  await page.getByRole("button", { name: "Live", exact: true }).waitFor();
  t.after(() => assert.deepEqual(faults, [], "No browser exceptions"));
  return { page, requests, panel: (name = "Yellow Pro") => page.locator(`[aria-label="${name} live account"]`) };
}
async function connectYellow(page) {
  await page.locator(".pf-xcard").filter({ hasText: "Yellow Pro" }).click();
  await page.locator("#pf-key").fill("test-api-key-12345678");
  await page.locator("#pf-secret").fill("test-secret-12345678");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
}
async function waitText(locator, text) { await locator.getByText(text, { exact: true }).first().waitFor(); }
async function noOverflow(page) { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false, "No horizontal overflow"); }
async function screenshot(page, name) {
  if (!process.env.PROFILE_TEST_SCREENSHOT_DIR) return;
  await page.locator(".pf-toast").waitFor({ state: "detached" });
  await page.evaluate(() => { document.documentElement.style.scrollBehavior = "auto"; window.scrollTo(0, 0); });
  await page.screenshot({ path: process.env.PROFILE_TEST_SCREENSHOT_DIR + "/" + name + ".png", fullPage: true });
}

test("phone: accepted Yellow key immediately shows balances, then start and stop use confirmed actions", async (t) => {
  const { page, panel, requests } = await setup(t);
  await connectYellow(page);
  await waitText(panel(), "$1,234.56");
  await waitText(panel(), "$1,000.00");
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(await panel().getByRole("button", { name: "Start Trading", exact: true }).isEnabled(), true);
  assert.equal(requests.includes("POST /me/bot/activate"), false, "Connecting never starts trading");
  await panel().getByRole("button", { name: "Start Trading", exact: true }).click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.equal(requests.includes("POST /me/bot/activate"), false, "Cancelling never activates");
  await panel().getByRole("button", { name: "Start Trading", exact: true }).click();
  await page.getByRole("button", { name: "Start Trading", exact: true }).last().click();
  await panel().getByRole("button", { name: "Stop Trading", exact: true }).waitFor();
  await waitText(panel(), "Running");
  await panel().getByRole("button", { name: "Stop Trading", exact: true }).click();
  await waitText(panel(), "Stopped");
  assert.equal(await panel().getByRole("button", { name: "Start Trading", exact: true }).isEnabled(), true);
  assert.equal(requests.filter((r) => r === "POST /me/bot/activate").length, 1);
  assert.equal(requests.filter((r) => r === "POST /me/bot/pause").length, 1);
  await noOverflow(page);
  await screenshot(page, "live-mobile");
});

test("pending verification displays a real zero balance and explains the disabled start button", async (t) => {
  const { page, panel, requests } = await setup(t, { connectResult: yellow("PENDING_YELLOW_VERIFICATION", 0, 0) });
  await connectYellow(page);
  await waitText(panel(), "$0.00");
  assert.equal(await panel().getByRole("button", { name: "Start Trading", exact: true }).isDisabled(), true);
  assert.match(await panel().innerText(), /Yellow does not expose its permission scopes/);
  assert.equal(requests.includes("POST /me/bot/activate"), false);
});

test("a rejected key stays in the connection dialog without a success message", async (t) => {
  const rejected = yellow(); rejected.account.state_reason = "invalid_credential";
  const { page } = await setup(t, { connectResult: rejected });
  await connectYellow(page);
  await page.getByText(/Yellow rejected the last credential/).first().waitFor();
  assert.equal(await page.getByRole("dialog").count(), 1);
  assert.equal(await page.getByText("Yellow Pro connected and verified.", { exact: true }).count(), 0);
});

test("disconnected and errored accounts cannot start even if verification is recorded", async (t) => {
  const me = yellow("ERROR", null, null);
  const { page, panel } = await setup(t, { me });
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await panel().waitFor();
  assert.equal(await panel().getByRole("button", { name: "Start Trading", exact: true }).isDisabled(), true);
  assert.match(await panel().innerText(), /connection needs attention/);
  assert.equal(await panel("WEEX").getByRole("button", { name: "Start Trading", exact: true }).isDisabled(), true);
});

test("WEEX connection switches from Paper to Live and controls only the WEEX account", async (t) => {
  const { page, panel, requests } = await setup(t);
  await page.locator(".pf-xcard").filter({ hasText: "WEEX" }).click();
  await page.locator("#pf-weex-key").fill("test-weex-key-12345678");
  await page.locator("#pf-weex-secret").fill("test-weex-secret-12345678");
  await page.locator("#pf-weex-passphrase").fill("testpassphrase");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await waitText(panel("WEEX"), "$987.65");
  await waitText(panel("WEEX"), "$800.00");
  assert.equal(requests.includes("POST /me/weex/start"), false);
  await panel("WEEX").getByRole("button", { name: "Start Trading", exact: true }).click();
  await page.getByRole("button", { name: "Start Trading", exact: true }).last().click();
  await waitText(panel("WEEX"), "Running");
  await panel("WEEX").getByRole("button", { name: "Stop Trading", exact: true }).click();
  await waitText(panel("WEEX"), "Stopped");
  assert.equal(requests.filter((r) => r === "POST /me/weex/start").length, 1);
  assert.equal(requests.includes("POST /me/bot/activate"), false);
  assert.equal(await page.getByText("Yellow Pro · Live results", { exact: true }).count(), 0);
  await noOverflow(page);
});

test("failed activation reports the backend error and keeps the account stopped", async (t) => {
  const { page, panel } = await setup(t, { me: yellow("VERIFIED", 1234.56, 1000), startFailure: true });
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await panel().getByRole("button", { name: "Start Trading", exact: true }).click();
  await page.getByRole("button", { name: "Start Trading", exact: true }).last().click();
  await page.getByText("Trading cannot be started from the current state.", { exact: true }).waitFor();
  await waitText(panel(), "Stopped");
  assert.equal(await panel().getByRole("button", { name: "Stop Trading", exact: true }).count(), 0);
});

test("desktop: both exchange balances render independently and unknown balances stay unavailable", async (t) => {
  const { page, panel } = await setup(t, { desktop: true, me: yellow("PAUSED", 1234.56, null), weex: weex("VERIFIED", 987.65, 800) });
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await waitText(panel(), "$1,234.56");
  await waitText(panel("WEEX"), "$987.65");
  assert.match(await panel().innerText(), /has not returned a complete balance/);
  assert.match(await panel().innerText(), /—/);
  await noOverflow(page);
  await screenshot(page, "live-desktop");
});

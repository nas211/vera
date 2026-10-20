const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const baseURL = process.env.HELPDESK_TEST_URL || "http://127.0.0.1:3014";
assert.ok(["127.0.0.1", "localhost"].includes(new URL(baseURL).hostname));
let browser;
before(async () => { browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH, args: ["--no-sandbox"] }); });
after(async () => browser?.close());
async function setup(t, size = { width: 390, height: 844 }) {
  const context = await browser.newContext({ viewport: size }); t.after(() => context.close());
  const page = await context.newPage(); const faults = [];
  page.on("pageerror", error => faults.push(error.message));
  t.after(() => assert.deepEqual(faults, []));
  await page.goto(baseURL + "/help");
  await page.getByRole("button", { name: "Help desk", exact: true }).click();
  const chat = page.getByRole("dialog", { name: "WHY help desk" }); await chat.waitFor();
  return { page, chat };
}

test("phone chat sends questions and follow-ups, shows credit, closes and retains chat", async t => {
  const { page, chat } = await setup(t); const calls = [];
  await page.route("**/api/helpdesk", async route => { calls.push(route.request().postDataJSON()); await route.fulfill({ json: { reply: calls.length === 1 ? "Open Dashboard > Live, then press Start Live Trading and confirm." : "Yes. Save the settings before starting." } }); });
  await chat.getByRole("button", { name: "How do I start live trading?" }).click();
  await chat.getByText("Open Dashboard > Live, then press Start Live Trading and confirm.", { exact: true }).waitFor();
  await chat.getByRole("textbox", { name: "Your question" }).fill("Should I save settings first?");
  await chat.getByRole("button", { name: "Send question" }).click();
  await chat.getByText("Yes. Save the settings before starting.", { exact: true }).waitFor();
  assert.equal(calls[1].history.length, 2);
  assert.equal(await chat.getByRole("link", { name: "Powered by Cybella AI" }).getAttribute("href"), "https://www.cybella.io");
  assert.equal(await chat.getByRole("button", { name: /Need a person\? Chat with Nad/ }).count(), 1);
  const box = await chat.boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  if (process.env.HELPDESK_SCREENSHOT_DIR) await page.screenshot({ path: process.env.HELPDESK_SCREENSHOT_DIR + "/helpdesk-phone.png" });
  await page.keyboard.press("Escape"); await chat.waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "Ask the AI help desk" }).click();
  await chat.getByText("Yes. Save the settings before starting.", { exact: true }).waitFor();
  await chat.getByRole("button", { name: "New chat" }).click();
  await chat.getByRole("button", { name: "How do I start live trading?" }).waitFor();
});

test("unavailable AI offers retry without duplicating the question", async t => {
  const { page, chat } = await setup(t); let calls = 0;
  await page.route("**/api/helpdesk", route => { calls++; return route.fulfill(calls === 1 ? { status: 503, json: { error: "The AI help desk is temporarily unavailable." } } : { json: { reply: "Read access is needed for balances." } }); });
  await chat.getByRole("button", { name: "Where is my account balance?" }).click();
  await chat.getByRole("button", { name: "Retry reply" }).click();
  await chat.getByText("Read access is needed for balances.").waitFor();
  assert.equal(await chat.getByText("Where is my account balance?", { exact: true }).count(), 1);
  assert.equal(calls, 2);
});

test("pasted credentials are blocked before network transmission; replies render as text", async t => {
  const { page, chat } = await setup(t); let calls = 0;
  await page.route("**/api/helpdesk", route => { calls++; return route.fulfill({ json: { reply: '<img src=x onerror="window.helpdeskInjected=true">' } }); });
  const input = chat.getByRole("textbox", { name: "Your question" });
  await input.fill("api_key=fixture1234567890"); await chat.getByRole("button", { name: "Send question" }).click();
  await chat.getByRole("alert").waitFor(); assert.equal(calls, 0);
  await input.fill("What is Paper mode?"); await input.press("Enter");
  await chat.getByText('<img src=x onerror="window.helpdeskInjected=true">', { exact: true }).waitFor();
  assert.equal(await chat.locator("img").count(), 0);
  assert.equal(await page.evaluate(() => window.helpdeskInjected), undefined);
});

test("desktop and small-phone dialog fit the screen and guide links work", async t => {
  for (const viewport of [{ width: 1280, height: 900 }, { width: 320, height: 568 }]) {
    const { page, chat } = await setup(t, viewport);
    const box = await chat.boundingBox(); assert.ok(box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width && box.y + box.height <= viewport.height);
    await chat.getByText("Help guides & sources").click();
    assert.equal(await chat.getByRole("link", { name: "Yellow API guide" }).getAttribute("href"), "https://docs.yellow.pro/api-and-programmatic-access/api-key-management");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (viewport.width === 1280 && process.env.HELPDESK_SCREENSHOT_DIR) await page.screenshot({ path: process.env.HELPDESK_SCREENSHOT_DIR + "/helpdesk-desktop.png" });
    await chat.getByRole("button", { name: "Close help desk" }).click(); await chat.waitFor({ state: "hidden" });
  }
});

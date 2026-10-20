const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const base = process.env.HELPDESK_TEST_URL || "http://127.0.0.1:3014";
assert.ok(["localhost", "127.0.0.1"].includes(new URL(base).hostname));
let browser;
before(async () => { browser = await chromium.launch({ executablePath: process.env.CHROME_PATH, headless: true, args: ["--no-sandbox"] }); });
after(async () => browser?.close());
async function setup(t, online = true) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } }); t.after(() => context.close());
  const page = await context.newPage(); const errors = [];
  page.on("pageerror", e => errors.push(e.message)); t.after(() => assert.deepEqual(errors, []));
  let messages = [], polls = 0; const sends = [];
  await page.route("**/api/helpdesk/human", async route => {
    const request = route.request(); const body = request.method() === "POST" ? request.postDataJSON() : null;
    if (body?.action === "send") {
      sends.push(body);
      if (!messages.some(m => m.client_id === body.clientId)) messages.push({ id: 1, role: "visitor", text: body.text, state: "sent", client_id: body.clientId });
    }
    if (request.method() === "GET") {
      polls++;
      if (messages.length === 1) messages.push({ id: 2, role: "nad", text: "Hi! Open Live and check the connection status.", state: "sent", client_id: "telegram-fixture" });
    }
    await route.fulfill({ json: { ready: true, online, receiving: true, messages } });
  });
  await page.goto(base + "/help");
  await page.getByRole("button", { name: "Help desk", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "WHY help desk" });
  return { page, dialog, sends, getPolls: () => polls };
}

test("Need a person opens in-site chat; message and Nad's reply appear in the same box", async t => {
  const { page, dialog, sends } = await setup(t);
  const original = page.url();
  await dialog.getByRole("button", { name: /Need a person\?/ }).click();
  await dialog.getByText("Nad is online", { exact: true }).waitFor();
  assert.equal(page.url(), original);
  await dialog.getByRole("textbox", { name: "Message Nad" }).fill("Can you help me connect Yellow?");
  await dialog.getByRole("button", { name: "Send message to Nad" }).click();
  await dialog.getByText("Delivered to Nad’s Telegram", { exact: true }).waitFor();
  await dialog.getByText("Hi! Open Live and check the connection status.", { exact: true }).waitFor();
  assert.equal(sends.length, 1); assert.equal(sends[0].text, "Can you help me connect Yellow?");
  if (process.env.HELPDESK_SCREENSHOT_DIR) await page.screenshot({ path: process.env.HELPDESK_SCREENSHOT_DIR + "/human-chat-phone.png" });
  await dialog.getByRole("button", { name: "Back to AI" }).click();
  await dialog.getByRole("button", { name: "How do I start live trading?" }).waitFor();
  await dialog.getByRole("button", { name: /Need a person\?/ }).click();
  await dialog.getByText("Hi! Open Live and check the connection status.", { exact: true }).waitFor();
});

test("away status allows a message and direct Chat with Nad button opens the chat", async t => {
  const { page, dialog } = await setup(t, false);
  await dialog.getByRole("button", { name: "Close help desk" }).click();
  await page.getByRole("button", { name: "Chat with Nad", exact: true }).click();
  await dialog.getByText("Nad is away · leave a message", { exact: true }).waitFor();
  await dialog.getByRole("textbox", { name: "Message Nad" }).fill("I will check back later.");
  await dialog.getByRole("button", { name: "Send message to Nad" }).click();
  await dialog.getByText("I will check back later.", { exact: true }).waitFor();
});

test("credential blocking prevents transmission, and retries reuse the same message id", async t => {
  const { page, dialog } = await setup(t); const sends = [];
  await page.unroute("**/api/helpdesk/human");
  await page.route("**/api/helpdesk/human", async route => {
    const body = route.request().method() === "POST" ? route.request().postDataJSON() : null;
    if (body?.action === "send") {
      sends.push(body);
      if (sends.length === 1) return route.fulfill({ status: 503, json: { error: "Please retry this message." } });
    }
    return route.fulfill({ json: { ready: true, online: true, receiving: true, messages: sends.length > 1 ? [{ id: 1, role: "visitor", text: body.text, client_id: body.clientId, state: "sent" }] : [] } });
  });
  await dialog.getByRole("button", { name: /Need a person\?/ }).click();
  await dialog.getByText("Nad is online", { exact: true }).waitFor();
  const input = dialog.getByRole("textbox", { name: "Message Nad" });
  await input.fill("api_key=fixture1234567890"); await dialog.getByRole("button", { name: "Send message to Nad" }).click();
  await dialog.getByRole("alert").waitFor(); assert.equal(sends.length, 0);
  await input.fill("Hello Nad"); await dialog.getByRole("button", { name: "Send message to Nad" }).click();
  await dialog.getByText("Please retry this message.", { exact: true }).waitFor();
  assert.equal(await input.inputValue(), "Hello Nad");
  await dialog.getByRole("button", { name: "Send message to Nad" }).click();
  await dialog.getByText("Delivered to Nad’s Telegram", { exact: true }).waitFor();
  assert.equal(sends[0].clientId, sends[1].clientId);
});

test("closing the chat stops polling and human support fits a small phone", async t => {
  const { page, dialog, getPolls } = await setup(t);
  await page.setViewportSize({ width: 320, height: 568 });
  await dialog.getByRole("button", { name: /Need a person\?/ }).click();
  await dialog.getByText("Nad is online", { exact: true }).waitFor();
  const box = await dialog.boundingBox(); assert.ok(box.x >= 0 && box.y >= 0 && box.x + box.width <= 320 && box.y + box.height <= 568);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await dialog.getByRole("button", { name: "Close help desk" }).click();
  await dialog.waitFor({ state: "hidden" }); const count = getPolls();
  await page.waitForTimeout(1800); assert.equal(getPolls(), count);
});

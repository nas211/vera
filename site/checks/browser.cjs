const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const artifacts = process.env.CHECK_ARTIFACTS_DIR || "/tmp/vela-checks";
fs.mkdirSync(artifacts, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || undefined,
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"]
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const errors = [];
  const forbiddenRequests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (/whytradingbot|api\.yellow|bybit|supabase|weex|api\.openai/i.test(request.url())) forbiddenRequests.push(request.url());
  });
  const origin = process.env.PREVIEW_URL || "http://127.0.0.1:8769";
  await page.goto(origin, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => document.querySelectorAll(".reveal").forEach((el) => el.classList.add("visible")));
  await page.waitForTimeout(850);
  await page.screenshot({ path: path.join(artifacts, "vela-desktop.png"), fullPage: true });
  assert.match(await page.title(), /Vela/);
  assert.equal(await page.locator("body").innerText().then((text) => /WHY Trading/.test(text)), false);

  await page.locator('[data-range="1W"]').click();
  assert.match(await page.locator("#market-chart").getAttribute("aria-label"), /1W/);
  await page.locator("#chart-reset").click();
  assert.match(await page.locator("#market-chart").getAttribute("aria-label"), /1D/);
  for (const side of ["long", "short"]) {
    await page.locator(`[data-side="${side}"]`).click();
    for (const amount of ["5", "100", "2000"]) {
      await page.locator("#trade-amount").fill(amount);
      await page.locator(".trade-submit").click();
      await page.waitForSelector("#trade-dialog[open]");
      const rows = await page.locator("#trade-summary .summary-row strong").allTextContents();
      const entry = Number(rows[2].replace("$", ""));
      const qty = Number(amount) / entry;
      const stop = Number(rows[4].replace("$", ""));
      const fee = .0004;
      const net = (side === "long" ? stop - entry : entry - stop) * qty - Number(amount) * fee - stop * qty * fee;
      assert.ok(net >= -2 - 1e-9, `Fee-inclusive ${side} loss budget breached for ${amount}: ${net}`);
      assert.equal(rows[0].includes(side === "long" ? "Long" : "Short"), true);
      await page.locator("#trade-dialog .dialog-done").click();
    }
  }
  await page.locator('[data-tab="earn"]').click();
  await page.locator("#earn-deposit").fill("10000");
  await page.locator("#earn-rate").fill("10");
  await page.locator("#earn-months").selectOption("12");
  assert.equal(await page.locator("#earn-output").textContent(), "1,000");
  await page.locator("#earn-rate").fill("0");
  assert.equal(await page.locator("#earn-output").textContent(), "0");
  await page.locator("#earn-rate").fill("31");
  assert.equal(await page.locator("#earn-output").textContent(), "—");
  await page.locator("#earn-rate").fill("8");
  await page.locator('[data-agent="2"]').click();
  assert.match(await page.locator("#agent-step").textContent(), /DIRECTION/);
  await page.locator("#sequence-toggle").click();
  await page.waitForTimeout(2800);
  assert.match(await page.locator("#agent-step").textContent(), /PAPER/);
  await page.locator("#sequence-toggle").click();
  await page.locator('[data-tab="bonuses"]').click();
  assert.equal(await page.locator("#mission-count").textContent(), "3");
  assert.equal(await page.locator("#progress-percent").textContent(), "100%");
  await page.locator('[data-mission="trade"]').click();
  assert.equal(await page.locator("#panel-trade").isVisible(), true);
  await page.locator(".faq-list details").first().locator("summary").click();
  assert.equal(await page.locator(".faq-list details").first().getAttribute("open"), "");
  await page.locator("#preview-info").click();
  await page.waitForSelector("#info-dialog[open]");
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#info-dialog").getAttribute("open"), null);

  for (const width of [320, 375, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(origin, { waitUntil: "networkidle" });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    assert.equal(overflow, false, `Horizontal overflow at ${width}px`);
    for (const name of ["trade", "earn", "bonuses"]) {
      await page.locator(`[data-tab="${name}"]`).click();
      assert.equal(await page.locator(`#panel-${name}`).isVisible(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1), false, `${name} panel overflow at ${width}px`);
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(origin, { waitUntil: "networkidle" });
  await page.locator(".menu-toggle").click();
  assert.equal(await page.locator(".menu-toggle").getAttribute("aria-expanded"), "true");
  await page.locator('#nav-links a[href="#dogeos"]').click();
  assert.equal(await page.locator(".menu-toggle").getAttribute("aria-expanded"), "false");
  await page.evaluate(() => {
    document.querySelectorAll(".reveal").forEach((el) => el.classList.add("visible"));
    scrollTo(0, 0);
  });
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(artifacts, "vela-mobile.png"), fullPage: true });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload({ waitUntil: "networkidle" });
  assert.equal(await page.locator(".coin-scene").evaluate((el) => getComputedStyle(el).animationName), "none");
  assert.deepEqual(errors, []);
  assert.deepEqual(forbiddenRequests, []);
  console.log(JSON.stringify({
    result: "passed",
    checked: ["6 responsive widths", "3 feature tabs", "chart ranges/reset", "long/short fee-inclusive $2 preview stops at 3 sizes each", "yield scenario math and invalid values", "agent flow play/pause", "preview mission tracking", "FAQ", "dialogs and Escape", "mobile menu", "reduced motion", "zero production trading requests", "zero JavaScript errors"]
  }, null, 2));
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });

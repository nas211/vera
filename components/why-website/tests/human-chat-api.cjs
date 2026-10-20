const { test, after } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const Module = require("node:module");
const ts = require("typescript");
require.extensions[".ts"] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, filename);
const { GET, POST } = require("../app/api/helpdesk/human/route.ts");
const original = global.fetch; after(() => { global.fetch = original; });
process.env.WHY_HUMAN_INTERNAL_SECRET = "fixture-internal-support-secret";
function request(body, headers = {}) {
  return new Request("https://whytradingbot.com/api/helpdesk/human", { method: body === undefined ? "GET" : "POST", headers: { Origin: "https://whytradingbot.com", "Content-Type": "application/json", ...headers }, body });
}
test("cross-origin writes and reads are rejected before touching the relay", async () => {
  let called = false; global.fetch = async () => { called = true; throw new Error("unexpected"); };
  assert.equal((await POST(request('{"action":"open"}', { Origin: "https://foreign.example" }))).status, 403);
  assert.equal((await GET(request(undefined, { "sec-fetch-site": "cross-site" }))).status, 403);
  assert.equal(called, false);
});
test("only the support cookie reaches the backend and its secure cookie returns", async () => {
  let sent;
  global.fetch = async (url, options) => { sent = { url, options }; return Response.json({ ready: true, online: true, messages: [] }, { headers: { "Set-Cookie": "why_human_session=fixture; HttpOnly; Secure; SameSite=Strict" } }); };
  const response = await POST(request('{"action":"open"}', { Cookie: "account_token=private-fixture; why_human_session=chat-fixture; unrelated=private" }));
  assert.equal(response.status, 200);
  assert.equal(sent.url, "http://127.0.0.1:8111/chat");
  assert.equal(sent.options.headers.Cookie, "why_human_session=chat-fixture");
  assert.equal(sent.options.headers["X-Why-Human-Secret"], "fixture-internal-support-secret");
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.match(response.headers.get("Set-Cookie"), /HttpOnly; Secure/);
});
test("relay downtime returns a safe retry message", async () => {
  global.fetch = async () => { throw new Error("private details fixture-internal-support-secret"); };
  const response = await GET(request()); assert.equal(response.status, 503);
  assert.doesNotMatch(await response.text(), /fixture-internal-support-secret|private details/);
});
test("malformed and oversized requests are rejected without a backend call", async () => {
  let called = false; global.fetch = async () => { called = true; throw new Error("unexpected"); };
  assert.equal((await POST(request("broken json"))).status, 400);
  assert.equal((await POST(request("x".repeat(16385)))).status, 413);
  assert.equal((await POST(request("{}", { "Content-Type": "text/plain" }))).status, 415);
  assert.equal(called, false);
});

import { createHash } from "node:crypto";
import { supportReply } from "@/lib/helpdesk/agent";
import { containsCredentials, MAX_QUESTION, type SupportTurn } from "@/lib/helpdesk/shared";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const DAY = 86_400_000;
type Meter = { day: number; total: number; recent: number[]; active: boolean };
const visitors = new Map<string, Meter>();
let active = 0;
let daily = { day: 0, total: 0 };
const headers = { "Cache-Control": "no-store" };
function error(message: string, status: number, retryAfter?: number) {
  return Response.json({ error: message }, { status, headers: { ...headers, ...(retryAfter ? { "Retry-After": String(retryAfter) } : {}) } });
}

export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  const allowed = process.env.WHY_SUPPORT_ORIGIN?.split(",") || ["https://whytradingbot.com", "https://www.whytradingbot.com"];
  if (!origin || !allowed.includes(origin)) return error("Open the help desk on WHY Trading Bot to ask a question.", 403);
  if (!request.headers.get("content-type")?.startsWith("application/json")) return error("Send a JSON question.", 415);
  if (Number(request.headers.get("content-length")) > 16_384) return error("Your message is too long. Please shorten it.", 413);
  let payload: Record<string, unknown>;
  try {
    // Bounded stream also enforces the limit when Content-Length is absent/incorrect.
    const reader = request.body?.getReader();
    if (!reader) return error("Please enter a question.", 400);
    const chunks: Uint8Array[] = [];
    let bytes = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 16_384) { await reader.cancel(); return error("Your message is too long. Please shorten it.", 413); }
      chunks.push(value);
    }
    payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("invalid");
  } catch { return error("Please enter a valid question.", 400); }
  const question = typeof payload.question === "string" ? payload.question.trim() : "";
  if (!question || question.length > MAX_QUESTION) return error(`Enter a question of up to ${MAX_QUESTION} characters.`, 400);
  if (payload.history !== undefined && !Array.isArray(payload.history)) return error("Please start a new chat.", 400);
  const history: SupportTurn[] = [];
  for (const turn of (payload.history as unknown[] || []).slice(-8)) {
    if (!turn || typeof turn !== "object") return error("Please start a new chat.", 400);
    const item = turn as Record<string, unknown>;
    if ((item.role !== "user" && item.role !== "assistant") || typeof item.content !== "string" || item.content.length > 5000) {
      return error("Please start a new chat.", 400);
    }
    history.push({ role: item.role, content: item.content.trim() });
  }
  if ([question, ...history.map(t => t.content)].some(containsCredentials)) {
    return error("Please remove any API keys, secrets, passwords or recovery phrases. Enter exchange credentials only in the dashboard connection form.", 400);
  }
  const now = Date.now(), day = Math.floor(now / DAY);
  // Nginx overwrites X-Real-IP with the actual client address. Do not trust X-Forwarded-For.
  const ip = request.headers.get("x-real-ip") || "unknown";
  const id = createHash("sha256").update(ip).digest("hex");
  for (const [key, value] of visitors) if (value.day !== day && !value.active) visitors.delete(key);
  if (visitors.size >= 10_000 && !visitors.has(id)) return error("The help desk is busy. Please try again later or contact human support.", 503, 60);
  const meter = visitors.get(id) || { day, total: 0, recent: [], active: false };
  if (meter.day !== day) { meter.day = day; meter.total = 0; meter.recent = []; }
  meter.recent = meter.recent.filter(t => now - t < 60_000);
  if (meter.active || meter.recent.length >= 5) return error("Please wait a minute before asking another question.", 429, 60);
  if (meter.total >= 40) return error("You’ve reached today’s help-desk limit. Please contact human support or return tomorrow.", 429, 3600);
  if (daily.day !== day) daily = { day, total: 0 };
  if (active >= 4 || daily.total >= 1000) return error("The help desk is busy. Please try again later or contact human support.", 503, 60);
  meter.total++; meter.recent.push(now); meter.active = true;
  visitors.set(id, meter); active++; daily.total++;
  try {
    const reply = await supportReply(question, history);
    return Response.json({ reply }, { headers });
  } catch {
    return error("The AI help desk is temporarily unavailable. Please retry, or contact WHY support on Telegram.", 503, 30);
  } finally { meter.active = false; active--; }
}

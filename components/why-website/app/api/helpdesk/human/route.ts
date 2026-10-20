export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store" };
const unavailable = () => Response.json({ error: "Nad’s chat is temporarily unavailable. Please retry in a moment." }, { status: 503, headers });

async function forward(request: Request, body?: string) {
  const secret = process.env.WHY_HUMAN_INTERNAL_SECRET;
  if (!secret) return unavailable();
  try {
    const response = await fetch("http://127.0.0.1:8111/chat", {
      method: body === undefined ? "GET" : "POST",
      headers: {
        "Content-Type": "application/json", "X-Why-Human-Secret": secret,
        "X-Real-IP": request.headers.get("x-real-ip") || "unknown",
        Cookie: request.headers.get("cookie")?.split(";").map(part => part.trim()).find(part => part.startsWith("why_human_session=")) || "",
      },
      body, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(12_000),
    });
    const data = await response.json();
    const outgoing = new Headers(headers);
    const cookie = response.headers.get("set-cookie");
    if (cookie) outgoing.set("Set-Cookie", cookie);
    return Response.json(data, { status: response.status, headers: outgoing });
  } catch { return unavailable(); }
}

export async function GET(request: Request) {
  // Strict SameSite cookie protects the saved chat; no CORS headers are returned.
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") return Response.json({ error: "Open your chat on WHY Trading Bot." }, { status: 403, headers });
  return forward(request);
}

export async function POST(request: Request) {
  const origins = process.env.WHY_SUPPORT_ORIGIN?.split(",") || ["https://whytradingbot.com", "https://www.whytradingbot.com"];
  if (!origins.includes(request.headers.get("origin") || "")) return Response.json({ error: "Open your chat on WHY Trading Bot." }, { status: 403, headers });
  if (!request.headers.get("content-type")?.startsWith("application/json")) return Response.json({ error: "Send a JSON message." }, { status: 415, headers });
  try {
    const reader = request.body?.getReader();
    if (!reader) throw new Error("empty");
    const chunks: Uint8Array[] = []; let bytes = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 16_384) { await reader.cancel(); return Response.json({ error: "Please shorten your message." }, { status: 413, headers }); }
      chunks.push(value);
    }
    const body = Buffer.concat(chunks).toString("utf8");
    JSON.parse(body);
    return forward(request, body);
  } catch { return Response.json({ error: "Please enter a valid message." }, { status: 400, headers }); }
}

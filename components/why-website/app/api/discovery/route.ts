import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest) {
  const action = request.nextUrl.searchParams.get("action") || "state";
  const allowed = request.method === "GET" ? ["state", "archive"] : ["start", "pause", "close"];
  if (!allowed.includes(action)) return Response.json({ error: "Unknown paper action" }, { status: 400 });
  const offset = request.nextUrl.searchParams.get("offset") || "0";
  if (action === "archive" && (!/^\d{1,7}$/.test(offset) || Number(offset) > 1000000)) return Response.json({ error: "Invalid archive offset" }, { status: 400 });
  const authorization = request.headers.get("authorization") || "";
  if (!authorization.startsWith("Bearer ") || authorization.length > 8200) return Response.json({ error: "Sign in to use AI Discovery" }, { status: 401 });
  if (request.method === "POST" && request.headers.get("sec-fetch-site") === "cross-site") return Response.json({ error: "Cross-site request refused" }, { status: 403 });
  let body: string | undefined;
  if (request.method === "POST") {
    if (Number(request.headers.get("content-length") || 0) > 2048) return Response.json({ error: "Request too large" }, { status: 413 });
    // Read at most 2KB, including requests without Content-Length.
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = []; let bytes = 0;
    if (reader) {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.length;
        if (bytes > 2048) { await reader.cancel(); return Response.json({ error: "Request too large" }, { status: 413 }); }
        chunks.push(value);
      }
    }
    body = Buffer.concat(chunks).toString("utf8");
  }
  try {
    const response = await fetch(`http://127.0.0.1:8114/${action}${action === "archive" ? `?offset=${offset}` : ""}`, {
      method: request.method, headers: { Authorization: authorization, "Content-Type": "application/json" },
      body, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(12000),
    });
    return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" } });
  } catch {
    return Response.json({ error: "AI Discovery paper service is not available yet." }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;

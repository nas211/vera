import { NextResponse } from "next/server";
import { issue } from "../pow";

// Handing out a challenge is deliberately free and unthrottled: it costs this
// server one HMAC, and rate-limiting it would only punish real visitors who
// reload. The expense sits on the SOLVING side, which is the point — see
// ../pow.ts. The write endpoint keeps its own per-IP throttle.
export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(issue(), { headers: { "Cache-Control": "no-store" } });
}

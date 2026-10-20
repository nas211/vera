import { appendFile, readFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { NextResponse } from "next/server";
import { send, wrap } from "@/lib/mail";
import { verify } from "./pow";

// Signups are the product here, so the durable store is a plain append-only
// JSONL file on this box — it works with no mail provider configured. The
// two emails below are best-effort on top of that, never the record.
const FILE  = process.env.WAITLIST_FILE ?? "/var/lib/why-website/waitlist.jsonl";

// ── the queue people are actually shown ────────────────────────────────────
// Every one of these is a promise printed on a stranger's screen, so they are
// settings rather than constants: the drop date has to be a date invitations
// really go out on, and the offset exists because someone will ask for it —
// it defaults to 0, which is the truth.
const BATCH      = Number(process.env.WAITLIST_BATCH ?? 100);
const ACT_HOURS  = Number(process.env.WAITLIST_ACTIVATION_HOURS ?? 24);
const PRIORITY_N = Number(process.env.WAITLIST_PRIORITY_INVITES ?? 3);
const OFFSET     = Number(process.env.WAITLIST_POSITION_OFFSET ?? 0);
const EVERY_DAYS = Number(process.env.WAITLIST_DROP_EVERY_DAYS ?? 7);
const REF_API    = process.env.REFERRAL_API ?? "";
const REF_TOKEN  = process.env.REFERRAL_TOKEN ?? "";

/** The next drop, rolled forward so a passed date never shows as a stale or
 *  negative countdown. */
function nextDrop(): string | null {
  const raw = process.env.WAITLIST_NEXT_DROP;
  if (!raw) return null;
  const t = Date.parse(raw);
  if (Number.isNaN(t)) return null;
  const step = Math.max(1, EVERY_DAYS) * 86_400_000;
  let when = t;
  while (when <= Date.now()) when += step;
  return new Date(when).toISOString();
}

const queue = (position: number) => ({
  position: position + OFFSET,
  batch: BATCH,
  next_drop: nextDrop(),
  activation_hours: ACT_HOURS,
  priority_invites: PRIORITY_N,
});

/** A stable id for the referral ledger that is NOT the email address. The
 *  ledger is a different system with a different lifetime; it has no business
 *  holding anyone's address. */
async function subjectFor(email: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("why-waitlist:" + email));
  return Array.from(new Uint8Array(buf)).slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Register the signup with the $Y engine. Best-effort by design: the waitlist
 *  entry is already on disk, and a rewards outage must never fail a signup. */
async function joinRewards(email: string, telegram: string, ref: string, ip: string) {
  if (!REF_API || !REF_TOKEN) return null;
  try {
    const display = telegram ? "@" + telegram : email.split("@")[0].slice(0, 3) + "…";
    const r = await fetch(REF_API + "/api/referral/waitlist", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + REF_TOKEN },
      body: JSON.stringify({ subject: await subjectFor(email), display, ref: ref || null, ip }),
      signal: AbortSignal.timeout(4000),
    });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}
const NOTIFY = process.env.NOTIFY_EMAIL?.trim();

// Only shown publicly once the list is big enough to be worth showing.
const COUNT_FLOOR = Number(process.env.WAITLIST_COUNT_FLOOR ?? 50);

export const dynamic = "force-dynamic";

type Entry = { email: string; telegram: string; ts: string; ip: string; ua: string };

/** What the visitor is told, and what the confirmation email repeats back.
 *  The referral fields arrive from the $Y engine and may be absent. */
type Place = ReturnType<typeof queue> & { code?: string; invited?: number; verified?: number };

const EMAIL_RE = /^[^\s@,;]+@[^\s@,;]+\.[a-z]{2,}$/i;

// ── crude per-IP throttle: 5 writes / 10 min, single process, resets on deploy ──
const hits = new Map<string, number[]>();
function throttled(ip: string) {
  const now = Date.now();
  const recent = (hits.get(ip) ?? []).filter((t) => now - t < 600_000);
  recent.push(now);
  hits.set(ip, recent);
  if (hits.size > 5000) hits.clear();
  return recent.length > 5;
}

async function readEntries(): Promise<Entry[]> {
  try {
    const raw = await readFile(FILE, "utf8");
    return raw.split("\n").filter(Boolean).flatMap((l) => {
      try { return [JSON.parse(l) as Entry]; } catch { return []; }
    });
  } catch {
    return [];   // no file yet == empty list
  }
}

export async function GET(req: Request) {
  const entries = await readEntries();
  const n = entries.length;
  const code = new URL(req.url).searchParams.get("code");

  // A returning visitor holding their code asks how many friends have joined
  // on it, so the priority-queue progress is right when they come back.
  let invites = null;
  if (code && REF_API) {
    try {
      const r = await fetch(REF_API + "/api/referral/waitlist/" + encodeURIComponent(code),
                            { signal: AbortSignal.timeout(3000) });
      if (r.ok) invites = await r.json();
    } catch { /* the count is a nicety, not the record */ }
  }

  return NextResponse.json(
    { count: n >= COUNT_FLOOR ? n : null, queue: queue(n), invites },
    { headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST(req: Request) {
  const ip =
    req.headers.get("x-real-ip") ??
    req.headers.get("x-forwarded-for")?.split(",")[0].trim() ??
    "unknown";

  if (throttled(ip)) {
    return NextResponse.json({ error: "Too many attempts — try again later." }, { status: 429 });
  }

  let body: Record<string, unknown>;
  try { body = await req.json(); } catch { return NextResponse.json({ error: "Bad request." }, { status: 400 }); }

  // ── the honeypot ──────────────────────────────────────────────────────────
  // A field real people never see and never fill. Checked before anything
  // expensive, and answered with a plain `ok` rather than an error: a bot that
  // is told it failed will retry differently, one that is told it succeeded
  // will not. Nothing is written.
  if (String(body.website ?? "").trim() !== "") {
    return NextResponse.json({ ok: true, already: false });
  }

  // ── the forge ─────────────────────────────────────────────────────────────
  // Proof of work over a challenge this server signed. See ./pow.ts for why
  // this and not a keyed third-party captcha.
  const verdict = verify(body.pow);
  if (!verdict.ok) {
    return NextResponse.json({ error: verdict.reason, retry: verdict.retry }, { status: 400 });
  }

  const email = String(body.email ?? "").trim().toLowerCase();
  const telegram = String(body.telegram ?? "").trim().replace(/^@/, "").slice(0, 64);

  if (!EMAIL_RE.test(email) || email.length > 254) {
    return NextResponse.json({ error: "That email doesn't look right." }, { status: 400 });
  }

  const ref = String(body.ref ?? "").trim().slice(0, 120);
  const entries = await readEntries();

  const seen = entries.findIndex((e) => e.email === email);
  if (seen >= 0) {
    // Already on the list — say so as success, so the form never leaks
    // "this address is registered" as a failure and never double-writes.
    // They still get their place and their code back: someone who signs up
    // twice is someone who lost the tab, not someone to turn away.
    const again = await joinRewards(email, telegram, "", ip);
    const place: Place = { ...queue(seen + 1), ...(again ?? {}) };
    // Re-send the confirmation. Someone signing up twice is usually someone
    // who never got the first one, and telling them "you are already on the
    // list" in the browser while sending nothing is how this looked broken.
    void confirm({ ...entries[seen], telegram }, place);
    return NextResponse.json({ ok: true, already: true, ...place });
  }

  const entry: Entry = {
    email,
    telegram,
    ts: new Date().toISOString(),
    ip,
    ua: (req.headers.get("user-agent") ?? "").slice(0, 200),
  };

  try {
    await mkdir(dirname(FILE), { recursive: true });
    await appendFile(FILE, JSON.stringify(entry) + "\n", "utf8");
  } catch {
    return NextResponse.json({ error: "Could not save — try again." }, { status: 500 });
  }

  // The referral code is awaited before the email goes out: the invite link is
  // the most useful line in it, and it is not worth sending without one.
  const position = entries.length + 1;
  const rewards = await joinRewards(email, telegram, ref, ip);
  const place: Place = { ...queue(position), ...(rewards ?? {}) };
  void confirm(entry, place);
  void notify(entry, position);
  return NextResponse.json({ ok: true, already: false, ...place });
}

// ── the two emails ─────────────────────────────────────────────────────────
// Both are best-effort: the entry is on disk before either runs, so a mail
// outage costs a notification, never a signup. Failures are logged by
// lib/mail.ts rather than swallowed.

const SITE = process.env.SITE_ORIGIN ?? "https://whytradingbot.com";

/** To the person who just joined. This is the whole point of asking for an
 *  address, and until now it did not exist: the only mail this route could
 *  send went to the operator. */
async function confirm(entry: Entry, place: Place) {
  const when = place.next_drop
    ? new Date(place.next_drop).toUTCString().replace(/ GMT$/, " UTC")
    : null;
  const invite = place.code ? `${SITE}/?r=${place.code}` : null;

  const lines = [
    `You are #${place.position} on the WHY TRADE waitlist.`,
    ``,
    `Invitations go out in batches of ${place.batch}${when ? `, and the next batch is ${when}` : ""}.`,
    `An invite stays open for ${place.activation_hours} hours once it lands, so watch this inbox.`,
  ];
  if (invite) {
    lines.push(
      ``,
      `Your invite link: ${invite}`,
      `Bring ${place.priority_invites} people through it and you move to the front of the queue.`,
    );
  }
  lines.push(
    ``,
    `While you wait, the book is already public:`,
    `  Live trades   ${SITE}/trades`,
    `  WHY Predict   ${SITE}/predict`,
    ``,
    `— WHY TRADE`,
  );

  const p = (s: string) => `<p style="font-size:14.5px;line-height:1.65;color:#c8cdd8;margin:0 0 14px">${s}</p>`;
  const html = wrap(`You are #${place.position} in the queue`, [
    p(`Invitations go out in batches of <strong style="color:#f2f4f8">${place.batch}</strong>${when ? `. The next batch is <strong style="color:#f2f4f8">${when}</strong>` : ""}. An invite stays open for ${place.activation_hours} hours once it lands, so keep an eye on this inbox.`),
    invite
      ? p(`Your invite link:<br><a href="${invite}" style="color:#8ab4ff;word-break:break-all">${invite}</a><br><span style="color:#6b7280">Bring ${place.priority_invites} people through it and you move to the front of the queue.</span>`)
      : "",
    p(`While you wait, the book is already public — <a href="${SITE}/trades" style="color:#8ab4ff">live trades</a> and <a href="${SITE}/predict" style="color:#8ab4ff">WHY Predict</a>.`),
  ].join(""));

  await send({
    to: entry.email,
    subject: `You are #${place.position} on the WHY TRADE waitlist`,
    text: lines.join("\n"),
    html,
  });
}

/** To the operator. Optional — unset NOTIFY_EMAIL and it simply does not run. */
async function notify(entry: Entry, position: number) {
  if (!NOTIFY) return;
  await send({
    to: NOTIFY,
    subject: `Waitlist #${position} — ${entry.email}`,
    text: [
      `Email:    ${entry.email}`,
      `Telegram: ${entry.telegram || "—"}`,
      `Position: #${position}`,
      `When:     ${entry.ts}`,
      `IP:       ${entry.ip}`,
    ].join("\n"),
  });
}

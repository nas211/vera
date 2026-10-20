import { createHmac, createHash, randomBytes, randomInt, timingSafeEqual } from "node:crypto";

// ─────────────────────────────────────────────────────────────────────────────
// THE FORGE — proof-of-work gate for the waitlist.
//
// WHY THIS AND NOT A THIRD-PARTY CAPTCHA. Turnstile/hCaptcha both need an
// account and a key pair before they protect anything, and every other keyed
// integration on this box (Resend, Groq, X, Gemini) is currently sitting
// unconfigured. A gate that only works once someone signs up for something is
// not a gate. This one needs no account, no key, and makes no outbound request.
//
// WHAT IT ACTUALLY STOPS. The cost of a signup goes from "one HTTP POST" to
// "one HTTP POST plus ~RANGE/2 SHA-256 hashes, against a challenge this server
// signed, that expires in ten minutes and can only be spent once". A human
// pays that once. A script pays it on every single attempt — and unlike a
// honeypot, it cannot be skipped by reading the page source.
//
// WHAT IT DOES NOT STOP. Someone who specifically wants THIS list can still
// burn CPU. If that ever happens the answer is Turnstile, not a bigger RANGE:
// past a point it is only real phones that suffer.
//
// WHY A BOUNDED SEARCH RATHER THAN "N LEADING ZERO BITS". The classic
// leading-zeros form has an exponential tail — MEASURED on this box at 2^19,
// the median solve was 935ms but p90 was 2165ms and p99 3130ms, which on a
// mid-range phone is a median of 3.7s and a p90 of 8.7s. Same work, wildly
// different waits, and the unlucky visitor is the one who gives up.
//
// So the server picks a secret integer k in [0, RANGE) and publishes only the
// digest of it. The client scans 0..RANGE-1 to find k. Expected work is
// RANGE/2 and the WORST case is RANGE — bounded, so the slowest visitor waits
// twice the typical wait, never ten times it. The progress bar also becomes
// honest: position in the scan IS the fraction of the work done.
// ─────────────────────────────────────────────────────────────────────────────

// RE-MEASURED end to end at ~394k hashes/sec in V8 on this box, not the ~590k
// a bare micro-benchmark suggested. At a 400k window that put a mid-range
// phone at a 2.0s median and a 2.9s p90 — too long to hold a button. 250k
// costs an attacker 125k hashes per attempt, still thousands of times the
// price of a bare POST, while a phone sits at ~1.3s typical, ~2.5s worst.
export const RANGE = Number(process.env.WAITLIST_POW_RANGE ?? 250_000);

// Long enough that someone can fill the form unhurried, short enough that a
// harvested challenge is worthless.
const TTL_MS = 10 * 60_000;

// WAITLIST_POW_SECRET should be set (it is, in /etc/why-website/env) so that
// challenges issued before a deploy still verify after it. Without it the
// fallback is a per-process secret: correct, but every in-flight challenge
// dies at restart and those visitors have to hold the button a second time.
//
// Deliberately NOT persisted to a file here. Doing filesystem work inside a
// route module makes Turbopack trace the whole project ("Encountered unexpected
// file in NFT list") — and a signing secret belongs in the service's
// environment anyway, not next to the data it protects.
const SECRET: Buffer = process.env.WAITLIST_POW_SECRET?.trim()
  ? Buffer.from(process.env.WAITLIST_POW_SECRET.trim(), "utf8")
  : randomBytes(32);

/** salt, search range, target digest prefix, expiry, signature over all four. */
export type Challenge = { s: string; r: number; t: string; e: number; sig: string };

function sign(s: string, r: number, t: string, e: number): string {
  return createHmac("sha256", SECRET).update(`${s}.${r}.${t}.${e}`).digest("hex");
}

/**
 * First 32 bits of SHA-256, as 8 hex characters.
 *
 * THE TARGET IS A PREFIX, NOT THE WHOLE DIGEST, and the client checks exactly
 * the same 32 bits. Matching on the full digest would be strictly worse here:
 * the browser solver only computes the first word for speed, so a full-digest
 * server check could reject a client that had legitimately found a 32-bit
 * collision instead of k. Since the answer must also lie inside [0, RANGE),
 * finding a colliding preimage in that window costs exactly what finding k
 * costs — the work is identical, and client and server now agree perfectly.
 */
function word(input: string): string {
  return createHash("sha256").update(input).digest("hex").slice(0, 8);
}

export function issue(): Challenge {
  const s = randomBytes(16).toString("hex");
  const r = RANGE;
  const k = randomInt(0, r);
  const e = Date.now() + TTL_MS;
  const t = word(`${s}.${k}`);
  return { s, r, t, e, sig: sign(s, r, t, e) };
}

// Spent challenges, so one solved salt cannot be replayed across a burst of
// signups. Entries are dropped once past their own expiry, so this cannot grow
// without bound.
const spent = new Map<string, number>();
function burn(s: string, e: number): boolean {
  const now = Date.now();
  if (spent.size > 10_000) {
    for (const [k, exp] of spent) if (exp < now) spent.delete(k);
  }
  if (spent.has(s)) return false;
  spent.set(s, e);
  return true;
}

export type Verdict = { ok: true } | { ok: false; reason: string; retry: boolean };

export function verify(input: unknown): Verdict {
  const c = input as Partial<Challenge> & { c?: unknown };
  const s = typeof c?.s === "string" ? c.s : "";
  const t = typeof c?.t === "string" ? c.t : "";
  const sig = typeof c?.sig === "string" ? c.sig : "";
  const r = Number(c?.r);
  const e = Number(c?.e);
  const answer = Number(c?.c);

  if (!s || !t || !sig || !Number.isFinite(r) || !Number.isFinite(e)) {
    return { ok: false, reason: "Verification missing — reload and try again.", retry: true };
  }

  // The signature covers r, so a caller cannot shrink the search space: a
  // forged r fails the HMAC below. This only rejects challenges issued before
  // WAITLIST_POW_RANGE was lowered, which are no longer worth what they cost.
  if (r < RANGE) {
    return { ok: false, reason: "Verification expired — try again.", retry: true };
  }

  const expect = Buffer.from(sign(s, r, t, e), "hex");
  const got = Buffer.from(sig, "hex");
  if (expect.length !== got.length || !timingSafeEqual(expect, got)) {
    return { ok: false, reason: "Verification failed — reload and try again.", retry: true };
  }

  if (Date.now() > e) {
    return { ok: false, reason: "Verification expired — try again.", retry: true };
  }

  // The answer must be inside the window that was actually searched. Without
  // this the scheme degrades to "find any preimage anywhere", which is a much
  // larger haystack to hide a cheap answer in.
  if (!Number.isInteger(answer) || answer < 0 || answer >= r) {
    return { ok: false, reason: "Verification failed — try again.", retry: true };
  }

  if (word(`${s}.${answer}`) !== t) {
    return { ok: false, reason: "Verification failed — try again.", retry: true };
  }

  // Burn LAST, so a request that was going to fail anyway does not consume a
  // challenge the visitor could still legitimately spend.
  if (!burn(s, e)) {
    return { ok: false, reason: "Verification already used — try again.", retry: true };
  }

  return { ok: true };
}

export const __test = { word, sign };

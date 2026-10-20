"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { solve, type Challenge } from "./forge";

type State = "idle" | "forging" | "sending" | "done" | "error";

/** What the server tells us about someone's place in the queue. */
type Place = {
  position: number;
  batch: number;
  next_drop: string | null;
  activation_hours: number;
  priority_invites: number;
  code?: string;
  invited?: number;
  verified?: number;
  already?: boolean;
};

const STORE = "why.waitlist.place";

/** The countdown, split for display. Recomputed from the target on every tick
 *  rather than decremented — a tab that sleeps for an hour must wake up with
 *  the right number, not an hour behind. */
function untilParts(iso: string | null) {
  if (!iso) return null;
  const ms = Date.parse(iso) - Date.now();
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const m = Math.floor(ms / 60_000);
  return { d: Math.floor(m / 1440), h: Math.floor((m % 1440) / 60), m: m % 60 };
}
const pad = (n: number) => String(n).padStart(2, "0");

// The bar never completes faster than this, however quick the machine is. A
// desktop solves 2^19 in well under a second, and a progress bar that flashes
// past reads as "nothing happened" rather than "something was done".
const MIN_FORGE_MS = 900;

const COLD = 620;
const HOT = 1480;

export function Waitlist() {
  const [state, setState] = useState<State>("idle");
  const [msg, setMsg] = useState("");
  const [count, setCount] = useState<number | null>(null);
  const [place, setPlace] = useState<Place | null>(null);
  const [copied, setCopied] = useState(false);
  const [, tick] = useState(0);              // drives the countdown re-render
  const [heat, setHeat] = useState(0);          // 0..1, drives the whole animation
  const emailRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  // Forge state lives in refs, not state: the solver ticks thousands of times a
  // second and the rAF loop reads it once a frame. Routing that through React
  // would re-render the section on every slice.
  const ch = useRef<Challenge | null>(null);
  const tried = useRef(0);
  const solved = useRef<string | null>(null);
  const startedAt = useRef(0);
  const holding = useRef(false);
  const raf = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    // Someone who already signed up on this device gets their card back
    // instead of an empty form they would fill in a second time.
    let saved: Place | null = null;
    try { saved = JSON.parse(localStorage.getItem(STORE) ?? "null"); } catch { saved = null; }
    if (saved?.position) { setPlace(saved); setState("done"); }

    const q = saved?.code ? "?code=" + encodeURIComponent(saved.code) : "";
    fetch("/api/waitlist" + q)
      .then((r) => r.json())
      .then((d) => {
        if (!alive) return;
        if (typeof d.count === "number") setCount(d.count);
        // The drop date and the friend count are the server's to know; refresh
        // both so a card restored from this device is never out of date.
        setPlace((cur) => (cur ? { ...cur, ...(d.queue ?? {}), ...(d.invites ?? {}) } : cur));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // One second is the resolution the card shows; minutes tick over honestly.
  useEffect(() => {
    if (state !== "done" || !place?.next_drop) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [state, place?.next_drop]);

  // Fetch the challenge up front so the hold starts hashing immediately rather
  // than waiting on a round trip with the button already pressed.
  const getChallenge = useCallback(async (force = false) => {
    if (!force && ch.current && ch.current.e > Date.now() + 30_000) return ch.current;
    try {
      const r = await fetch("/api/waitlist/challenge", { cache: "no-store" });
      ch.current = (await r.json()) as Challenge;
    } catch {
      ch.current = null;
    }
    return ch.current;
  }, []);

  useEffect(() => { void getChallenge(); }, [getChallenge]);

  const stopLoop = () => {
    if (raf.current !== null) cancelAnimationFrame(raf.current);
    raf.current = null;
  };

  // Cools back down when the visitor lets go early. Nothing is submitted and
  // the solved counter is kept — releasing is not a punishment, just a pause.
  const release = useCallback(() => {
    holding.current = false;
    if (state !== "forging") return;
    stopLoop();
    setState("idle");
    setHeat(0);
    setMsg("Cooled off — hold it until it casts.");
  }, [state]);

  const send = useCallback(async () => {
    const form = formRef.current;
    if (!form || !ch.current || !solved.current) return;
    const fd = new FormData(form);
    setState("sending");
    setMsg("");
    try {
      const res = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: fd.get("email"),
          telegram: fd.get("telegram"),
          website: fd.get("website"),          // honeypot — always empty for a human
          // Who sent them. Read off the URL the friend shared, so the person
          // who did the inviting gets the credit.
          ref: new URLSearchParams(window.location.search).get("r") ?? "",
          pow: { ...ch.current, c: solved.current },
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        // A spent or expired challenge is not the visitor's fault: get a fresh
        // one so the next hold can succeed instead of failing the same way.
        if (data.retry) { solved.current = null; void getChallenge(true); }
        throw new Error(data.error ?? "Something went wrong.");
      }
      setState("done");
      if (data.position) {
        const p = data as Place;
        setPlace(p);
        try { localStorage.setItem(STORE, JSON.stringify(p)); } catch { /* private mode */ }
      }
      setMsg(data.already ? "You were already on the list — this is your place." : "");
    } catch (err) {
      setState("error");
      setHeat(0);
      setMsg(err instanceof Error ? err.message : "Something went wrong.");
      emailRef.current?.focus();
    }
  }, [getChallenge]);

  // `auto` skips the hold requirement for keyboard and assistive tech, where
  // "press and hold" is not a gesture that exists. The work is identical; only
  // the gate on it differs.
  const inviteUrl = place?.code
    ? `${typeof window === "undefined" ? "" : window.location.origin}/?r=${place.code}`
    : "";

  const copyInvite = useCallback(async () => {
    if (!inviteUrl) return;
    try {
      await navigator.clipboard.writeText(inviteUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2400);
    } catch {
      // Clipboard is blocked in some embedded browsers; showing the link is
      // still a way to share it.
      window.prompt("Copy your invite link:", inviteUrl);
    }
  }, [inviteUrl]);

  const startForge = useCallback(async (auto: boolean) => {
    if (state === "forging" || state === "sending") return;
    const email = (formRef.current?.elements.namedItem("email") as HTMLInputElement | null)?.value ?? "";
    if (!email.trim()) { emailRef.current?.focus(); setMsg("Email first."); setState("error"); return; }

    holding.current = true;
    setState("forging");
    setMsg("");
    tried.current = 0;
    startedAt.current = performance.now();

    const c = await getChallenge();
    if (!c) { setState("error"); setMsg("Could not reach the forge — try again."); return; }
    if (!holding.current && !auto) return;

    const total = c.r;   // the search window IS the total work — bounded, so this cannot overrun
    let done = false;

    void solve(
      c,
      (t) => { tried.current = t; },
      () => !auto && !holding.current,
    ).then((counter) => {
      done = true;
      if (counter) solved.current = counter;
    });

    const tick = () => {
      if (!auto && !holding.current) return;            // released; `release` cleans up
      const byWork = done ? 1 : Math.min(0.97, tried.current / total);
      const byTime = (performance.now() - startedAt.current) / MIN_FORGE_MS;
      // The slower of the two, so the bar is never ahead of the real work AND
      // never faster than the eye can follow.
      const h = Math.max(0, Math.min(1, Math.min(byWork, byTime)));
      setHeat(h);
      if (h >= 1 && done && solved.current) { stopLoop(); void send(); return; }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }, [state, getChallenge, send]);

  useEffect(() => stopLoop, []);

  const field: React.CSSProperties = {
    width: "100%",
    background: "var(--carbon-2)",
    border: "1px solid var(--steel)",
    borderRadius: 3,
    padding: "13px 14px",
    color: "var(--platinum)",
    fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
    fontSize: 13.5,
    outline: "none",
  };

  const forging = state === "forging";
  const busy = forging || state === "sending";
  const temp = Math.round(COLD + (HOT - COLD) * heat);

  return (
    <section
      id="waitlist"
      style={{
        position: "relative",
        borderBottom: "1px solid var(--steel-soft)",
        background: "linear-gradient(180deg, rgba(240,133,42,.05), transparent 60%)",
        padding: "56px 0 60px",
      }}
    >
      <div className="wrap">
        <div className="wl-grid" style={{
          display: "grid",
          gridTemplateColumns: "1.05fr .95fr",
          gap: 48,
          alignItems: "center",
        }}>
          {/* LEFT — pitch */}
          <div>
            <div className="eyebrow" style={{ color: "var(--gold)" }}>
              Early access · closed beta
            </div>
            <h2 style={{
              fontFamily: 'var(--font-display, "Bricolage Grotesque"), sans-serif',
              fontSize: "clamp(30px, 4.4vw, 46px)",
              fontWeight: 800,
              lineHeight: 1.02,
              letterSpacing: "-.032em",
              margin: "14px 0 12px",
            }}>
              Join the <span className="molten-text">waitlist</span>.
            </h2>
            <p style={{ color: "var(--ash)", fontSize: 16, maxWidth: "44ch", lineHeight: 1.55 }}>
              Seats open in small batches so the engine keeps its edge. Leave your
              email and you get the invite before it goes public — plus the live
              results while you wait.
            </p>

            <div style={{
              marginTop: 22,
              display: "flex",
              gap: 18,
              flexWrap: "wrap",
              fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
              fontSize: 12,
              color: "var(--ash-dim)",
            }}>
              <span>no card</span>
              <span>no spam</span>
              <span>leave any time</span>
              {count !== null && (
                <span>
                  <strong style={{ color: "var(--gold)", fontWeight: 500 }}>
                    {count.toLocaleString("en-US")}
                  </strong>{" "}
                  in line
                </span>
              )}
            </div>
          </div>

          {/* RIGHT — form */}
          <div style={{
            background: "linear-gradient(180deg, var(--carbon), var(--carbon-2))",
            border: "1px solid var(--steel)",
            borderRadius: 6,
            padding: "26px 24px",
            boxShadow: "0 40px 90px -55px rgba(194,56,26,.5), 0 24px 60px -50px #000",
          }}>
            {state === "done" ? (
              <Reserved place={place} note={msg} copied={copied} onCopy={copyInvite} />
            ) : (
              <form
                ref={formRef}
                noValidate
                onSubmit={(e) => { e.preventDefault(); void startForge(true); }}
              >
                <label htmlFor="wl-email" className="eyebrow" style={{ display: "block", marginBottom: 8 }}>
                  Email
                </label>
                <input
                  id="wl-email"
                  ref={emailRef}
                  name="email"
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="you@domain.com"
                  style={field}
                  onFocus={(e) => (e.currentTarget.style.borderColor = "var(--gold-dim)")}
                  onBlur={(e) => (e.currentTarget.style.borderColor = "var(--steel)")}
                />

                <label htmlFor="wl-tg" className="eyebrow" style={{ display: "block", margin: "18px 0 8px" }}>
                  Telegram <span style={{ color: "var(--ash-dim)" }}>· optional</span>
                </label>
                <input
                  id="wl-tg"
                  name="telegram"
                  type="text"
                  autoComplete="off"
                  placeholder="@handle"
                  style={field}
                  onFocus={(e) => (e.currentTarget.style.borderColor = "var(--gold-dim)")}
                  onBlur={(e) => (e.currentTarget.style.borderColor = "var(--steel)")}
                />

                {/* Honeypot. Hidden from sight AND from the accessibility tree,
                    so no real visitor — sighted or using a screen reader — is
                    ever offered it, but a form-filling script sees a plain
                    text input called "website" and cannot resist. */}
                <div aria-hidden="true" style={{
                  position: "absolute", width: 1, height: 1, overflow: "hidden",
                  clip: "rect(0 0 0 0)", whiteSpace: "nowrap",
                }}>
                  <input name="website" type="text" tabIndex={-1} autoComplete="off" defaultValue="" />
                </div>

                {/* ── THE FORGE ─────────────────────────────────────────────
                    Hold to fill. The bar is not a timer: it tracks the real
                    proof-of-work the browser is doing to earn the seat. */}
                <button
                  type="button"
                  className="btn"
                  disabled={state === "sending"}
                  aria-label="Hold to forge your seat"
                  onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); void startForge(false); }}
                  onPointerUp={release}
                  onPointerCancel={release}
                  onPointerLeave={release}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); void startForge(true); }
                  }}
                  style={{
                    position: "relative",
                    overflow: "hidden",
                    width: "100%",
                    marginTop: 22,
                    padding: "16px 20px",
                    fontSize: 13.5,
                    // COLD IRON, deliberately overriding .btn's molten gradient.
                    // The site's primary button is already fully hot, so a
                    // molten fill drawn on top of it is invisible — the bar
                    // only reads as filling if the billet starts cold.
                    background: "linear-gradient(180deg, #232130, #16151E)",
                    border: "1px solid var(--gold-dim)",
                    color: "var(--gold)",
                    cursor: state === "sending" ? "wait" : "pointer",
                    touchAction: "none",
                    userSelect: "none",
                    transform: forging ? `translateY(${Math.sin(heat * 40) * 0.6}px)` : "none",
                    boxShadow: heat > 0
                      ? `0 0 ${18 + heat * 46}px rgba(240,133,42,${0.18 + heat * 0.5})`
                      : undefined,
                    transition: "box-shadow .12s linear",
                  }}
                >
                  {/* molten fill */}
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute", inset: 0,
                      transformOrigin: "left center",
                      transform: `scaleX(${heat})`,
                      background: "var(--molten)",
                      opacity: 0.92,
                      transition: heat === 0 ? "transform .45s ease-out" : "none",
                    }}
                  />
                  {/* white-hot leading edge */}
                  {heat > 0 && heat < 1 && (
                    <span aria-hidden="true" style={{
                      position: "absolute", top: 0, bottom: 0,
                      left: `calc(${heat * 100}% - 12px)`, width: 24,
                      background: "radial-gradient(closest-side, var(--whitehot), transparent)",
                      filter: "blur(3px)",
                    }} />
                  )}
                  {/* While the fill sweeps, the label straddles cold iron on
                      one side and white-hot metal on the other, so no single
                      colour is readable across it. `difference` against white
                      resolves per pixel: dark over the molten, bright over the
                      iron. Only while there IS a boundary — at rest the label
                      is plain gold. */}
                  <span style={{
                    position: "relative",
                    color: heat > 0 ? "#fff" : undefined,
                    mixBlendMode: heat > 0 ? "difference" : "normal",
                    letterSpacing: forging ? ".08em" : undefined,
                  }}>
                    {state === "sending"
                      ? "Casting…"
                      : forging
                        ? (heat >= 1 ? "✦ CAST" : `FORGING · ${temp}°C`)
                        : "Hold to forge my seat"}
                  </span>
                </button>

                <p aria-live="polite" style={{
                  minHeight: 18,
                  marginTop: 12,
                  fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
                  fontSize: 11.5,
                  color: state === "error" ? "var(--red)" : "var(--ash-dim)",
                  textAlign: "center",
                }}>
                  {state === "error"
                    ? msg
                    : forging
                      ? `${tried.current.toLocaleString("en-US")} hammer blows`
                      : msg || "Press and hold — your browser earns the seat."}
                </p>
              </form>
            )}
          </div>
        </div>
      </div>

      <style>{`
        @media(max-width:920px){ .wl-grid{ grid-template-columns:1fr!important; gap:30px!important } }
        #waitlist input::placeholder{ color: var(--ash-dim) }
        @media (prefers-reduced-motion: reduce){
          #waitlist .btn{ transform:none!important }
        }
      `}</style>
    </section>
  );
}


/** The reserved-place card. Everything on it is a real number: the position is
 *  the line as it stands, and the clock counts to a date invitations actually
 *  go out on (WAITLIST_NEXT_DROP). Urgency you have to honour is the only kind
 *  worth printing. */
function Reserved({ place, note, copied, onCopy }: {
  place: Place | null; note: string; copied: boolean; onCopy: () => void;
}) {
  const left = untilParts(place?.next_drop ?? null);
  const need = place?.priority_invites ?? 3;
  const got = place?.verified ?? 0;
  const inPriority = got >= need;

  const cell = {
    fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
    fontSize: 26, fontWeight: 700, lineHeight: 1,
    color: "var(--whitehot)",
  } as const;
  const unit = {
    fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
    fontSize: 9.5, letterSpacing: ".18em", textTransform: "uppercase",
    color: "var(--ash-dim)", marginTop: 6, display: "block",
  } as const;

  return (
    <div style={{ padding: "6px 2px" }}>
      <div style={{
        fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
        fontSize: 11, letterSpacing: ".2em", textTransform: "uppercase",
        color: "var(--green)", textAlign: "center",
      }}>
        ✓ your place is reserved
      </div>

      <p style={{ textAlign: "center", fontSize: 19, marginTop: 14, lineHeight: 1.35 }}>
        You&rsquo;re{" "}
        <b style={{
          fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
          fontSize: 26, background: "var(--molten)", WebkitBackgroundClip: "text",
          backgroundClip: "text", color: "transparent",
        }}>#{place?.position ?? "—"}</b>{" "}
        on the WHY Trade waitlist.
      </p>
      {note && (
        <p style={{ textAlign: "center", color: "var(--ash-dim)", fontSize: 12.5, marginTop: 6 }}>{note}</p>
      )}

      {left && (
        <div style={{ marginTop: 22, textAlign: "center" }}>
          <span className="eyebrow" style={{ color: "var(--ash)" }}>
            The next {place?.batch ?? 100} invitations go out in
          </span>
          <div style={{
            display: "grid", gridTemplateColumns: "1fr auto 1fr auto 1fr",
            alignItems: "start", gap: 8, marginTop: 12,
            border: "1px solid var(--steel)", borderRadius: 5, padding: "16px 10px",
            background: "rgba(240,133,42,.05)",
          }}>
            <div><span style={cell}>{pad(left.d)}</span><span style={unit}>days</span></div>
            <span style={{ ...cell, color: "var(--gold-dim)" }}>·</span>
            <div><span style={cell}>{pad(left.h)}</span><span style={unit}>hours</span></div>
            <span style={{ ...cell, color: "var(--gold-dim)" }}>·</span>
            <div><span style={cell}>{pad(left.m)}</span><span style={unit}>minutes</span></div>
          </div>
        </div>
      )}

      <p style={{ color: "var(--ash)", fontSize: 13, marginTop: 16, lineHeight: 1.6, textAlign: "center" }}>
        If selected, you&rsquo;ll have <b style={{ color: "var(--platinum)" }}>
        {place?.activation_hours ?? 24} hours</b> to activate your access before the
        invitation passes to the next person.
      </p>

      <a href="/trades" className="btn" style={{ marginTop: 18, width: "100%", textAlign: "center" }}>
        Watch the bot trade live →
      </a>

      <div style={{ borderTop: "1px solid var(--steel)", marginTop: 24, paddingTop: 20 }}>
        <span className="eyebrow" style={{ color: inPriority ? "var(--green)" : "var(--gold)" }}>
          {inPriority ? "You're in the priority queue" : "Want priority access?"}
        </span>
        <p style={{ color: "var(--ash)", fontSize: 13.5, marginTop: 8, lineHeight: 1.6 }}>
          {inPriority
            ? `${got} friends joined on your link. You move up with the next batch.`
            : `Invite ${need} verified friends to move into the Priority Queue.`}
        </p>

        {place?.code && (
          <>
            <button type="button" className="btn btn-ghost" onClick={onCopy}
                    style={{ marginTop: 14, width: "100%" }}>
              {copied ? "Copied ✓" : "Copy my invite link"}
            </button>
            <div style={{ display: "flex", gap: 5, marginTop: 14 }}>
              {Array.from({ length: need }, (_, i) => (
                <span key={i} style={{
                  flex: 1, height: 4, borderRadius: 2,
                  background: i < got ? "var(--molten)" : "var(--steel)",
                }} />
              ))}
            </div>
            <p style={{
              fontFamily: 'var(--font-mono, "JetBrains Mono"), monospace',
              fontSize: 11, color: "var(--ash-dim)", marginTop: 8, textAlign: "center",
            }}>
              {got} of {need} joined{place.invited && place.invited > got
                ? ` · ${place.invited - got} pending` : ""}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

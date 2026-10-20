"use client";

import { useEffect, useRef, useState, useCallback } from "react";

// ── Types ──────────────────────────────────────────────────────────────────────
type CardRow = [label: string, value: string, col: "g" | "rd" | ""];
type ChatMsg =
  | { role: "why";  type: "text"; html: string }
  | { role: "why";  type: "card"; rows: CardRow[] }
  | { role: "user"; type: "text"; text: string };

type PingItem =
  | { w: string; a: number; p: string }
  | { l: string; a: number; p: string; n: string }
  | { i: string };

// ── Hardcoded data ─────────────────────────────────────────────────────────────
const OPENING: ChatMsg[] = [
  { role: "why", type: "text", html: '⚡ <b>03:14 · cut SOL.</b>\nEdge died — PF dropped to 0.71 over the last 20 trades. No hope, no holding. Recycled into a funding play on TON. We move. 🔥' },
  { role: "why", type: "text", html: '💰 Booked: WIF long <span class="pos">+3.2%  ·  +$640</span>. Clean exit. 🟢' },
  { role: "why", type: "text", html: 'Today so far: <span class="pos">+$1,240</span> net · paper. And I\'m still working. Ask me anything 👇' },
];

const ANSWERS: Record<string, ChatMsg[]> = {
  short: [{ role: "why", type: "text", html: 'Short ETH because the data says so, not because I have a feeling.\n\nFunding flipped negative, OI is climbing into resistance, and my short book is running <b>PF 1.4</b> this week. I size what\'s working and flip the second the edge flips. 📉' }],
  week: [
    { role: "why", type: "text", html: "7-day card 👇" },
    { role: "why", type: "card", rows: [["Net", "<b>+6.8%</b>", "g"], ["Profit factor", "<b>1.31</b>", ""], ["Trades", "412", ""], ["Win rate", "58%", ""], ["Best", "funding arb +2.9%", "g"], ["Worst", "breakout fade, cut early", "rd"]] },
    { role: "why", type: "text", html: "Nothing heroic. Just edge, compounded. That's the whole game." },
  ],
  status: [
    { role: "why", type: "text", html: "🟢 <b>Running.</b>" },
    { role: "why", type: "card", rows: [["Strategies live", "340", ""], ["On probation", "11", ""], ["Suspended", "2", ""], ["Open positions", "6 · all bracketed", ""], ["Today (paper)", "<b>+$1,240</b>", "g"], ["Uptime", "99.98%", ""]] },
    { role: "why", type: "text", html: "I don't sleep so you can." },
  ],
  loss: [{ role: "why", type: "text", html: 'Took an L — ate <span class="neg">1.2%  ·  -$190</span> on a long that timed wrong. My miss, not the market\'s.\n\nAlready retired the strategy that fired it. I\'d rather show you the red than hide it — that\'s how you know the green is real.' }],
  kill: [{ role: "why", type: "text", html: 'Kill switch armed. 🛑\nHalting new entries, cancelling pending orders, flattening on your call. You always have the last word — I just do the work.\n\nConfirm with <b>/kill confirm</b>.' }],
  moon: [{ role: "why", type: "text", html: "I don't do moonboy. I do profit factor.\n\nAsk me what I'm trading and why — I'll show you receipts, not hopium. 📊" }],
  def:  [{ role: "why", type: "text", html: "Straight answer: ask me what I'm trading, why I'm in it, how your week looks, or tell me to /kill. I deal in positions and numbers — not vibes." }],
};

const PINGS: PingItem[] = [
  { w: "Closed ARB long",      a: 380, p: "+1.9%" },
  { i: "⚡ Opened short DOGE — funding's paying me to hold." },
  { w: "Scalped ETH momentum", a: 210, p: "+0.6%" },
  { w: "Closed short DOGE",    a: 300, p: "+1.5%" },
  { l: "Cut a SUI long early", a: 120, p: "-0.4%", n: "timing was off — my call. On to the next." },
  { w: "Funding arb on TON",   a: 540, p: "+2.4%" },
  { w: "Booked WIF runner",    a: 720, p: "+3.6%" },
  { i: "⚡ Rotating risk into majors — chop's not worth it right now." },
  { w: "Closed long BTC",      a: 460, p: "+0.9%" },
];

const CHIPS: [label: string, key: string][] = [
  ["why short ETH?", "short"],
  ["how's my week?", "week"],
  ["/status", "status"],
  ["you down bad?", "loss"],
  ["moon soon?", "moon"],
  ["/kill", "kill"],
];

// ── Helpers ───────────────────────────────────────────────────────────────────
const wait    = (ms: number) => new Promise<void>(r => setTimeout(r, ms));
const fmtTime = () => { const d = new Date(); return `${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`; };
const fmtPnl  = (n: number) => `${n < 0 ? "−$" : "+$"}${Math.abs(n).toLocaleString("en-US")}`;
const nlpKey  = (t: string) => {
  const s = t.toLowerCase();
  if (/short|long|why/.test(s)) return "short";
  if (/week|perf|stats?|pnl|profit/.test(s)) return "week";
  if (/status|running|alive|live/.test(s)) return "status";
  if (/loss|down|red|lost/.test(s)) return "loss";
  if (/kill|stop|halt|panic/.test(s)) return "kill";
  if (/moon|pump|lambo|100x/.test(s)) return "moon";
  return "def";
};

// ── Sub-components ─────────────────────────────────────────────────────────────
function CardBubble({ rows }: { rows: CardRow[] }) {
  return (
    <div style={{ fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 12, lineHeight: 1.7, color: "var(--ash)", minWidth: 210 }}>
      {rows.map(([lbl, val, col], i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 14 }}>
          <span>{lbl}</span>
          <span className={col === "g" ? "pos" : col === "rd" ? "neg" : ""}
            dangerouslySetInnerHTML={{ __html: val }} />
        </div>
      ))}
    </div>
  );
}

function Bubble({ msg }: { msg: ChatMsg }) {
  const isWhy = msg.role === "why";
  return (
    <div style={{ display: "flex", justifyContent: isWhy ? "flex-start" : "flex-end", marginBottom: 10, animation: "pop .25s ease" }}>
      <div style={{
        maxWidth: "84%",
        padding: "9px 12px",
        borderRadius: isWhy ? "15px 15px 15px 5px" : "15px 15px 5px 15px",
        background: isWhy ? "#171b23" : "linear-gradient(120deg,#F0852A,#F3C76A)",
        border: isWhy ? "1px solid var(--steel-soft)" : "none",
        fontSize: 13.5,
        lineHeight: 1.5,
        color: isWhy ? "var(--platinum)" : "#1a0d05",
        fontWeight: isWhy ? 400 : 500,
      }}>
        {msg.type === "card" ? (
          <CardBubble rows={msg.rows} />
        ) : msg.role === "why" ? (
          <span style={{ whiteSpace: "pre-wrap" }} dangerouslySetInnerHTML={{ __html: msg.html }} />
        ) : (
          <span>{msg.text}</span>
        )}
        <span style={{
          display: "block",
          fontFamily: "var(--font-mono,'JetBrains Mono'),monospace",
          fontSize: 9.5,
          color: isWhy ? "var(--ash-dim)" : "rgba(26,13,5,.5)",
          marginTop: 5,
        }}>
          {fmtTime()}
        </span>
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <div style={{ display: "flex", alignSelf: "flex-start", gap: 4, padding: "11px 14px", background: "#171b23", border: "1px solid var(--steel-soft)", borderRadius: "15px 15px 15px 5px", marginBottom: 10, animation: "pop .2s ease" }}>
      {[0, 1, 2].map(i => (
        <span key={i} style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--ash)", display: "block", animation: `bln 1.2s ${i * 0.2}s infinite` }} />
      ))}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export function TalkToWhy() {
  const sectionRef = useRef<HTMLElement>(null);
  const chatRef    = useRef<HTMLDivElement>(null);
  const killedRef  = useRef(false);
  const busyRef    = useRef(false);
  const startedRef = useRef(false);
  const pingIdxRef = useRef(0);

  const [msgs,     setMsgs]     = useState<ChatMsg[]>([]);
  const [typing,   setTyping]   = useState(false);
  const [pnl,      setPnl]      = useState(1240);
  const [pnlKey,   setPnlKey]   = useState(0);
  const [inputVal, setInputVal] = useState("");

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, typing]);

  const addMsg = useCallback((msg: ChatMsg) => setMsgs(prev => [...prev, msg]), []);

  const playSeq = useCallback(async (seq: ChatMsg[]) => {
    for (const item of seq) {
      if (killedRef.current) return;
      setTyping(true);
      await wait(560 + Math.random() * 420);
      if (killedRef.current) return;
      setTyping(false);
      addMsg(item);
      await wait(60);
    }
  }, [addMsg]);

  const pingLoop = useCallback(async () => {
    while (!killedRef.current) {
      await wait(11000 + Math.random() * 6000);
      if (killedRef.current) return;
      setTyping(true);
      await wait(900);
      if (killedRef.current) return;
      setTyping(false);

      const p = PINGS[pingIdxRef.current % PINGS.length];
      pingIdxRef.current++;

      if ("i" in p) {
        addMsg({ role: "why", type: "text", html: p.i });
      } else if ("w" in p) {
        addMsg({ role: "why", type: "text", html: `💰 ${p.w} <span class="pos">${p.p}  ·  +$${p.a}</span>. 🟢` });
        setPnl(v => v + p.a);
        setPnlKey(k => k + 1);
      } else if ("l" in p) {
        addMsg({ role: "why", type: "text", html: `🩸 ${p.l} <span class="neg">${p.p}  ·  -$${p.a}</span>. ${p.n}` });
        setPnl(v => v - p.a);
        setPnlKey(k => k + 1);
      }
    }
  }, [addMsg]);

  useEffect(() => {
    const el = sectionRef.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting && !startedRef.current) {
        startedRef.current = true;
        (async () => {
          await wait(500);
          await playSeq(OPENING);
          pingLoop();
        })();
      }
    }, { threshold: 0.2 });
    io.observe(el);
    return () => io.disconnect();
  }, [playSeq, pingLoop]);

  useEffect(() => () => { killedRef.current = true; }, []);

  const handleChip = useCallback(async (label: string, key: string) => {
    if (busyRef.current) return;
    busyRef.current = true;
    addMsg({ role: "user", type: "text", text: label });
    await playSeq(ANSWERS[key] ?? ANSWERS.def);
    busyRef.current = false;
  }, [addMsg, playSeq]);

  const handleSend = useCallback(async () => {
    const v = inputVal.trim();
    if (!v || busyRef.current) return;
    setInputVal("");
    busyRef.current = true;
    addMsg({ role: "user", type: "text", text: v });
    await playSeq(ANSWERS[nlpKey(v)] ?? ANSWERS.def);
    busyRef.current = false;
  }, [inputVal, addMsg, playSeq]);

  return (
    <section ref={sectionRef} style={{
      position: "relative",
      minHeight: "100vh",
      display: "flex",
      alignItems: "center",
      borderTop: "1px solid var(--steel-soft)",
      overflow: "hidden",
      padding: "80px 0",
    }}>
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(700px 420px at 72% 50%, rgba(240,133,42,.10), transparent 62%)", pointerEvents: "none" }} />

      <div className="wrap" style={{ position: "relative", width: "100%" }}>
        <div className="talkwhy-grid" style={{ display: "grid", gridTemplateColumns: "1fr 368px", gap: 54, alignItems: "center" }}>

          {/* ── LEFT ── */}
          <div>
            <div className="eyebrow reveal">The product · live on Telegram</div>
            <h2 className="reveal" style={{
              fontFamily: "var(--font-display,'Bricolage Grotesque'),sans-serif",
              fontWeight: 700,
              fontSize: "clamp(32px,4.6vw,56px)",
              letterSpacing: "-.03em",
              lineHeight: 1,
              margin: "16px 0 14px",
            }}>
              Meet <span className="molten-text">WHY.</span>
            </h2>
            <p className="reveal" style={{ color: "var(--ash)", maxWidth: "44ch", fontSize: 17 }}>
              No dashboard to learn. WHY runs the forge autonomously and talks to you right in Telegram — books the wins, owns the losses, and hands you the kill switch.
            </p>
            <div className="reveal" style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 12, maxWidth: "42ch" }}>
              {[
                ["It prints while you scroll.", "Closed trades land in your chat in real time, with a running P&L up top."],
                ["Brutally honest.", "Shows you the red as fast as the green. Every call comes with a number."],
                ["Always reachable.", '"Why short?" "How\'s my week?" "/kill." Plain language, with receipts.'],
              ].map(([b, rest]) => (
                <div key={b} style={{ display: "flex", gap: 12, alignItems: "flex-start", fontSize: 14.5, color: "var(--ash)" }}>
                  <div style={{ flexShrink: 0, width: 7, height: 7, borderRadius: 2, background: "var(--molten)", marginTop: 7, transform: "rotate(45deg)" }} />
                  <div><b style={{ color: "var(--platinum)", fontWeight: 500 }}>{b}</b> {rest}</div>
                </div>
              ))}
            </div>
            <p className="reveal" style={{ marginTop: 20, fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 11, color: "var(--ash-dim)", maxWidth: "42ch", lineHeight: 1.5 }}>
              Demo — scripted to show WHY&apos;s voice and reporting. The live agent connects to your exchange and trades for real (paper mode first). Numbers shown are illustrative.
            </p>
          </div>

          {/* ── PHONE ── */}
          <div className="talkwhy-phone" style={{
            width: 368,
            height: 720,
            background: "#06070A",
            border: "1px solid var(--steel)",
            borderRadius: 46,
            padding: 11,
            boxShadow: "0 50px 120px -50px rgba(240,133,42,.40), 0 40px 80px -50px #000",
            transform: "perspective(1400px) rotateY(-6deg) rotateX(2deg)",
            flexShrink: 0,
            position: "relative",
          }}>
            {/* Notch */}
            <div style={{ position: "absolute", top: 20, left: "50%", transform: "translateX(-50%)", width: 120, height: 26, background: "#06070A", borderRadius: 14, zIndex: 5 }} />

            {/* Screen */}
            <div style={{ height: "100%", background: "#0c0e13", borderRadius: 36, overflow: "hidden", display: "flex", flexDirection: "column" }}>

              {/* Header */}
              <div style={{ display: "flex", alignItems: "center", gap: 11, padding: "30px 16px 12px", borderBottom: "1px solid var(--steel-soft)", background: "#0e1016", flexShrink: 0 }}>
                <div style={{ width: 38, height: 38, borderRadius: "50%", background: "#11151c", display: "flex", alignItems: "center", justifyContent: "center", position: "relative", flexShrink: 0, border: "1px solid var(--gold-dim)" }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src="/logo-gold.svg" alt="WHY" width={24} height={24} />
                  <div style={{ position: "absolute", right: -1, bottom: -1, width: 11, height: 11, borderRadius: "50%", background: "var(--green)", border: "2px solid #0e1016", animation: "beat 2.5s infinite" }} />
                </div>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 15, letterSpacing: ".01em" }}>WHY</div>
                  <div style={{ fontFamily: "var(--font-mono,'JetBrains Mono'),monospace", fontSize: 11, color: "var(--green)" }}>online · autonomous</div>
                </div>
                <div key={pnlKey} style={{
                  marginLeft: "auto",
                  textAlign: "right",
                  fontFamily: "var(--font-mono,'JetBrains Mono'),monospace",
                  fontWeight: 700,
                  fontSize: 15,
                  color: pnl >= 0 ? "var(--green)" : "var(--red)",
                  lineHeight: 1.05,
                  animation: pnlKey > 0 ? "pf .55s ease" : "none",
                }}>
                  {fmtPnl(pnl)}
                  <small style={{ display: "block", fontSize: 8, color: "var(--ash-dim)", fontWeight: 400, letterSpacing: ".14em", textTransform: "uppercase", marginTop: 2 }}>
                    today · paper
                  </small>
                </div>
              </div>

              {/* Chat body */}
              <div ref={chatRef} style={{ flex: 1, overflowY: "auto", padding: "16px 14px 4px", display: "flex", flexDirection: "column", scrollbarWidth: "none" }}>
                {msgs.map((m, i) => <Bubble key={i} msg={m} />)}
                {typing && <TypingDots />}
              </div>

              {/* Suggestion chips */}
              <div style={{ display: "flex", gap: 8, padding: "10px 12px 4px", overflowX: "auto", borderTop: "1px solid var(--steel-soft)", scrollbarWidth: "none", flexShrink: 0 }}>
                {CHIPS.map(([label, key]) => (
                  <button key={label} onClick={() => handleChip(label, key)}
                    disabled={typing}
                    style={{
                      flexShrink: 0,
                      fontFamily: "var(--font-mono,'JetBrains Mono'),monospace",
                      fontSize: 11.5,
                      padding: "8px 13px",
                      borderRadius: 999,
                      border: "1px solid var(--gold-dim)",
                      color: "var(--gold)",
                      background: "rgba(243,199,106,.06)",
                      cursor: "pointer",
                      whiteSpace: "nowrap",
                      transition: "background .15s",
                      opacity: typing ? 0.45 : 1,
                    }}
                    onMouseEnter={e => { if (!typing) e.currentTarget.style.background = "rgba(243,199,106,.16)"; }}
                    onMouseLeave={e => { e.currentTarget.style.background = "rgba(243,199,106,.06)"; }}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {/* Input */}
              <div style={{ display: "flex", gap: 8, padding: "10px 12px 14px", alignItems: "center", flexShrink: 0 }}>
                <input
                  value={inputVal}
                  onChange={e => setInputVal(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && handleSend()}
                  placeholder="Ask WHY anything…"
                  style={{
                    flex: 1,
                    background: "#10131a",
                    border: "1px solid var(--steel)",
                    borderRadius: 999,
                    padding: "10px 14px",
                    color: "var(--platinum)",
                    fontFamily: "var(--font-body,'Inter'),sans-serif",
                    fontSize: 13,
                    outline: "none",
                  }}
                />
                <button onClick={handleSend} style={{
                  flexShrink: 0,
                  width: 38, height: 38,
                  borderRadius: "50%",
                  border: "none",
                  background: "var(--molten)",
                  color: "#1a0d05",
                  fontSize: 16,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}>↑</button>
              </div>
            </div>
          </div>

        </div>
      </div>

      <style>{`
        @keyframes pop { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:none} }
        @keyframes bln { 0%,60%,100%{opacity:.3;transform:translateY(0)} 30%{opacity:1;transform:translateY(-3px)} }
        @keyframes pf  { 0%{transform:scale(1)} 38%{transform:scale(1.14);filter:brightness(1.4)} 100%{transform:scale(1)} }
        .pos{color:var(--green);font-weight:700}
        .neg{color:var(--red);font-weight:700}
        @media(max-width:900px){
          .talkwhy-grid{grid-template-columns:1fr!important;justify-items:center}
        }
        @media(prefers-reduced-motion:reduce){
          .msg,span{animation:none!important}
        }
      `}</style>
    </section>
  );
}

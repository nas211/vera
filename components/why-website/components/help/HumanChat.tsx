"use client";

import { useEffect, useRef, useState } from "react";
import { containsCredentials } from "@/lib/helpdesk/shared";
import styles from "./HelpDesk.module.css";

type Message = { id: number; role: "visitor" | "nad"; text: string; state: string; client_id: string };
type Snapshot = { ready: boolean; online: boolean; receiving: boolean; messages: Message[] };
const ENDPOINT = "/api/helpdesk/human";

export function HumanChat({ visible }: { visible: boolean }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(true);
  const [pollError, setPollError] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const pending = useRef<{ text: string; clientId: string } | null>(null);
  const sending = useRef(false);
  const mounted = useRef(true);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function read(response: Response): Promise<Snapshot> {
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : "Couldn’t connect to Nad. Please retry.");
    if (!Array.isArray(data.messages) || typeof data.online !== "boolean") throw new Error("Couldn’t refresh your chat. Please retry.");
    return data;
  }
  async function openChat(signal?: AbortSignal) {
    setOpening(true); setError("");
    try {
      const data = await read(await fetch(ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "open" }), signal: signal || AbortSignal.timeout(15_000) }));
      if (mounted.current) { setSnapshot(data); setPollError(false); }
    } catch (e) {
      if (mounted.current && !(e instanceof Error && e.name === "AbortError" && signal?.aborted)) setError(e instanceof Error ? e.message : "Couldn’t open your chat. Please retry.");
    } finally { if (mounted.current) setOpening(false); }
  }

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const poll = async () => {
      if (stopped) return;
      if (document.visibilityState === "visible") {
        try {
          const data = await read(await fetch(ENDPOINT, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]) }));
          if (!stopped) { setSnapshot(data); setPollError(false); }
        } catch { if (!stopped) setPollError(true); }
      }
      if (!stopped) timer = setTimeout(poll, 3000);
    };
    void openChat(controller.signal).then(() => { if (!stopped) timer = setTimeout(poll, 1500); });
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  // Only visibility controls polling; typing never starts a second poll loop.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);
  const lastId = snapshot?.messages.at(-1)?.id;
  useEffect(() => { scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: "smooth" }); }, [lastId, error]);

  async function send() {
    const value = text.trim();
    if (sending.current || !value || !snapshot?.ready) return;
    if (containsCredentials(value)) { setError("Remove API keys, secrets and passwords before sending."); return; }
    if (!pending.current || pending.current.text !== value) pending.current = { text: value, clientId: crypto.randomUUID() };
    sending.current = true; setBusy(true); setError("");
    try {
      const data = await read(await fetch(ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "send", ...pending.current }), signal: AbortSignal.timeout(15_000) }));
      if (mounted.current) { setSnapshot(data); setText(""); setPollError(false); }
      pending.current = null;
    } catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : "Couldn’t send. Your message is still here; retry to send it safely."); }
    finally { sending.current = false; if (mounted.current) setBusy(false); }
  }

  return <>
    <div className={styles.conversation} ref={scroll}>
      <div className={styles.presence} role="status"><span className={snapshot?.online && !pollError ? styles.onlineDot : styles.awayDot} aria-hidden="true" />{opening ? "Connecting to Nad…" : pollError ? "Reconnecting to Nad…" : snapshot?.online ? "Nad is online" : "Nad is away · leave a message"}</div>
      <div className={styles.welcome}><h3>Chat with Nad.</h3><p>Your message goes directly to Nad on Telegram. His replies appear here.</p></div>
      <div role="log" aria-label="Chat with Nad" aria-live="polite" aria-relevant="additions text" className={styles.messages}>
        {snapshot?.messages.map(message => <div key={message.id} className={message.role === "visitor" ? styles.user : styles.assistant}>
          <span className={styles.speaker}>{message.role === "visitor" ? "You" : "Nad"}</span><p>{message.text}</p>
          {message.role === "visitor" && <span className={styles.delivery}>{message.state === "sent" ? "Delivered to Nad’s Telegram" : message.state === "uncertain" ? "Message saved · notification not confirmed" : "Sending notification…"}</span>}
        </div>)}
      </div>
      {error && <div className={styles.error} role="alert"><p>{error}</p>{!snapshot?.ready && <button onClick={() => void openChat()}>Retry connection</button>}</div>}
      {!opening && snapshot && !snapshot.ready && !error && <div className={styles.error}><p>Your chat has expired.</p><button onClick={() => void openChat()}>Open a new chat</button></div>}
    </div>
    <div className={styles.bottom}>
      <form className={styles.form} onSubmit={e => { e.preventDefault(); void send(); }}>
        <label className={styles.srOnly} htmlFor="nad-question">Message Nad</label>
        <textarea id="nad-question" value={text} maxLength={2000} rows={2} placeholder="Write your message to Nad…" disabled={busy} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} />
        <button type="submit" aria-label="Send message to Nad" disabled={busy || opening || !snapshot?.ready || !text.trim()}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m5 12 7-7 7 7M12 5v14" /></svg></button>
      </form>
      <p className={styles.private}>Keep keys and passwords private. Chat messages are saved for 30 days.</p>
      <a className={styles.credit} href="https://www.cybella.io" target="_blank" rel="noopener noreferrer">Powered by Cybella AI ↗</a>
    </div>
  </>;
}

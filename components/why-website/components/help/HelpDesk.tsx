"use client";

import { useEffect, useRef, useState } from "react";
import { containsCredentials, MAX_QUESTION, type SupportTurn } from "@/lib/helpdesk/shared";
import styles from "./HelpDesk.module.css";
import { HumanChat } from "./HumanChat";

const STARTERS = ["How do I connect my exchange?", "Where is my account balance?", "How do I start live trading?", "Why is the Start button disabled?"];
const SOURCES = [
  { label: "Dashboard", href: "/profile" },
  { label: "Getting started", href: "/help" },
  { label: "Pricing", href: "/#pricing" },
  { label: "Yellow API guide", href: "https://docs.yellow.pro/api-and-programmatic-access/api-key-management" },
  { label: "WEEX API guide", href: "https://www.weex.com/api-doc/contract/QuickStart/IntegrationPreparation" },
];

// Render optional model emphasis as React text nodes; never interpret HTML.
function ReplyText({ text }: { text: string }) {
  return <>{text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : part)}</>;
}

export function OpenHelpDesk({ children = "Ask the AI help desk", human = false }: { children?: React.ReactNode; human?: boolean }) {
  return <button className="btn" onClick={() => window.dispatchEvent(new CustomEvent("why:open-help", { detail: { human } }))}>{children}</button>;
}

export function HelpDesk() {
  const [open, setOpen] = useState(false);
  const [human, setHuman] = useState(false);
  const [messages, setMessages] = useState<SupportTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [failed, setFailed] = useState<{ question: string; history: SupportTurn[] } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const request = useRef<AbortController | null>(null);
  const sending = useRef(false);

  useEffect(() => {
    const show = (event: Event) => { const detail = (event as CustomEvent).detail; if (typeof detail?.human === "boolean") setHuman(detail.human); setOpen(true); };
    window.addEventListener("why:open-help", show);
    return () => { window.removeEventListener("why:open-help", show); request.current?.abort(); };
  }, []);
  useEffect(() => {
    if (open && !dialog.current?.open) {
      dialog.current?.showModal();
      if (window.matchMedia("(pointer: fine)").matches) textarea.current?.focus({ preventScroll: true });
    }
    if (!open && dialog.current?.open) dialog.current?.close();
  }, [open]);
  useEffect(() => { scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: "smooth" }); }, [messages, busy, error]);

  async function send(raw: string, retry?: { question: string; history: SupportTurn[] }) {
    const text = raw.trim();
    if (!text || sending.current || text.length > MAX_QUESTION) return;
    if (containsCredentials(text)) {
      setError("Remove any API keys, secrets or passwords. Use the dashboard connection form for exchange credentials.");
      return;
    }
    const history = retry?.history || messages.slice(-8);
    sending.current = true; setBusy(true); setError(""); setFailed(null);
    if (!retry) { setMessages([...messages, { role: "user" as const, content: text }].slice(-24)); setQuestion(""); }
    const controller = new AbortController(); request.current = controller;
    const timeout = setTimeout(() => controller.abort(), 30_000);
    try {
      const response = await fetch("/api/helpdesk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, history }), signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok || typeof data.reply !== "string" || !data.reply.trim()) {
        throw new Error(typeof data.error === "string" ? data.error : "The help desk couldn’t answer. Please retry or contact human support.");
      }
      setMessages(previous => [...previous, { role: "assistant" as const, content: data.reply }].slice(-24));
    } catch (e) {
      setError(e instanceof Error && e.name !== "AbortError" ? e.message : "The reply took too long. Please retry or contact human support.");
      setFailed({ question: text, history });
    } finally {
      clearTimeout(timeout); sending.current = false; setBusy(false); request.current = null;
    }
  }

  return <>
    <button ref={launcher} data-help-launcher="" className={styles.launcher} aria-haspopup="dialog" aria-controls="why-help-desk" aria-expanded={open} onClick={() => setOpen(true)}>
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z" /></svg>
      Help desk
    </button>
    <dialog id="why-help-desk" ref={dialog} className={styles.panel} aria-labelledby="help-title" onClose={() => { setOpen(false); launcher.current?.focus({ preventScroll: true }); }} onCancel={() => setOpen(false)} onKeyDownCapture={e => { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); setOpen(false); } }}>
      <header className={styles.header}>
        <div><h2 id="help-title">WHY help desk</h2><p>{human ? "Human support · Nad" : "AI setup & product support"}</p></div>
        <div className={styles.controls}>
          {human ? <button onClick={() => setHuman(false)}>Back to AI</button> : <button disabled={busy} onClick={() => { setMessages([]); setQuestion(""); setError(""); setFailed(null); textarea.current?.focus(); }}>New chat</button>}
          <button className={styles.close} aria-label="Close help desk" onClick={() => setOpen(false)}>×</button>
        </div>
      </header>
      {human ? <HumanChat visible={open} /> : <>
      <div className={styles.conversation} ref={scroll}>
        <div className={styles.welcome}><span className={styles.badge}>HERE TO HELP</span><h3>Let’s get you set up.</h3><p>Ask about connecting an exchange, your dashboard or trading controls.</p></div>
        {messages.length === 0 && <div className={styles.starters}>{STARTERS.map(text => <button key={text} disabled={busy} onClick={() => void send(text)}>{text}<span aria-hidden="true">↗</span></button>)}</div>}
        <div role="log" aria-label="Help desk conversation" aria-live="polite" aria-relevant="additions text" className={styles.messages}>
          {messages.map((message, index) => <div key={index} className={message.role === "user" ? styles.user : styles.assistant}>
            <span className={styles.speaker}>{message.role === "user" ? "You" : "WHY support · AI"}</span><p>{message.role === "assistant" ? <ReplyText text={message.content} /> : message.content}</p>
          </div>)}
          {busy && <p className={styles.thinking} role="status">Finding the right steps…</p>}
        </div>
        {error && <div className={styles.error} role="alert"><p>{error}</p>{failed && <button disabled={busy} onClick={() => void send(failed.question, failed)}>Retry reply</button>}</div>}
        <details className={styles.sources}><summary>Help guides &amp; sources</summary><div>{SOURCES.map(source => <a key={source.label} href={source.href} {...(source.href.startsWith("https:") ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{source.label} ↗</a>)}</div></details>
      </div>
      <div className={styles.bottom}>
        <button type="button" className={styles.human} onClick={() => setHuman(true)}>Need a person? Chat with Nad →</button>
        <form className={styles.form} onSubmit={e => { e.preventDefault(); void send(question); }}>
          <label className={styles.srOnly} htmlFor="help-question">Your question</label>
          <textarea id="help-question" ref={textarea} value={question} maxLength={MAX_QUESTION} rows={2} placeholder="What do you need help with?" onChange={e => setQuestion(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(question); } }} />
          <button type="submit" aria-label="Send question" disabled={busy || !question.trim()}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m5 12 7-7 7 7M12 5v14" /></svg></button>
        </form>
        <p className={styles.private}>Don’t paste API keys, passwords or private account details.</p>
        <a className={styles.credit} href="https://www.cybella.io" target="_blank" rel="noopener noreferrer">Powered by Cybella AI ↗</a>
      </div>
      </>}
    </dialog>
  </>;
}

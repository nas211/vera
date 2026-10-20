import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { OpenHelpDesk } from "@/components/help/HelpDesk";

export const metadata: Metadata = { title: "Help desk — WHY Trading Bot", description: "Get help connecting an exchange, checking your balance and starting live trading with WHY Trading Bot." };

export default function HelpPage() {
  return <><Nav /><main className="wrap" style={{ padding: "72px 24px 90px", minHeight: "70vh", maxWidth: 960 }}>
    <span className="mono" style={{ color: "var(--ember2)", fontSize: 12, letterSpacing: ".1em" }}>WHY SUPPORT</span>
    <h1 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(36px,6vw,58px)", margin: "14px 0 18px" }}>A little help. A clear next step.</h1>
    <p style={{ color: "var(--ash)", maxWidth: 580, lineHeight: 1.8, marginBottom: 26 }}>Ask our AI help desk about setup, exchange connections and trading controls. It can walk you through the dashboard and help you understand what to check.</p>
    <OpenHelpDesk />
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,250px),1fr))", gap: 20, margin: "46px 0 30px" }}>
      {[
        ["Try Paper first", "Sign in to your dashboard and choose Paper to practice with a simulated $25,000 balance. No exchange key or deposit is needed."],
        ["Connect your exchange", "Use the dashboard connection form for a supported exchange. Enable read and futures trading permissions, keep withdrawals disabled, and enter credentials only in that form."],
        ["Start live trading", "In Live, wait for your exchange to be connected and verified. Check the account balance, save your settings, then press Start Live Trading and confirm."],
      ].map(([title, text], i) => <article key={title} style={{ padding: 24, border: "1px solid var(--steel-soft)", borderRadius: 14, background: "#ffffff03" }}>
        <span style={{ color: "var(--ember2)", fontFamily: "var(--font-mono)", fontSize: 12 }}>0{i + 1}</span><h2 style={{ fontSize: 19, margin: "12px 0" }}>{title}</h2><p style={{ fontSize: 14, color: "var(--ash)", lineHeight: 1.8 }}>{text}</p>
      </article>)}
    </div>
    <p style={{ color: "var(--ash)", fontSize: 14, lineHeight: 1.8, marginBottom: 16 }}>For account-specific problems, billing questions or anything that needs a person, chat with Nad here. Your message goes to his Telegram, and his reply appears in your chat. Send the exchange name and a redacted error message. Keep keys and passwords private.</p>
    <OpenHelpDesk human>Chat with Nad</OpenHelpDesk>
    <a href="/profile" style={{ display: "inline-block", marginTop: 18, color: "var(--platinum)", fontSize: 14 }}>Open your dashboard →</a>
  </main><Footer /></>;
}

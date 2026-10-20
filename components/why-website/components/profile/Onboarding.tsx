"use client";

import { useEffect, useState } from "react";

type Route = "paper" | "yellow" | "weex";

export function EmailVerification({ email, busy, error, info, onResend, onSignIn, onChangeEmail }: {
  email: string; busy: boolean; error: string; info: string;
  onResend: () => Promise<boolean>; onSignIn: () => void; onChangeEmail: () => void;
}) {
  const [remaining, setRemaining] = useState(error ? 0 : 60);
  useEffect(() => {
    if (remaining <= 0) return;
    const timer = setTimeout(() => setRemaining((n) => n - 1), 1000);
    return () => clearTimeout(timer);
  }, [remaining]);
  return <div className="pf-card pf-welcome" aria-labelledby="pf-verify-title">
    <div className="eyebrow pf-gold">Step 1 · Verify your email</div>
    <div className="pf-mail-icon" aria-hidden="true">✉</div>
    <h1 id="pf-verify-title" className="pf-h1">Check your email</h1>
    <p className="pf-guide-copy">Your verification link is in the email sent to:</p>
    <p className="pf-email-address">{email}</p>
    <ol className="pf-guide-list">
      <li><strong>Open your inbox.</strong><span>Look for the WHY account confirmation email. Check Spam or Junk if you don’t see it.</span></li>
      <li><strong>Click the confirmation link.</strong><span>It takes you back to WHY to continue setting up your account.</span></li>
      <li><strong>Continue with the quick tutorial.</strong><span>We’ll show you Paper trading and how to connect your exchange.</span></li>
    </ol>
    {error && <p className="pf-err" role="alert">{error}</p>}
    {info && <p className="pf-ok" role="status">{info}</p>}
    <div className="pf-actions">
      <button type="button" className="btn btn-ghost" disabled={busy || remaining > 0} onClick={async () => { if (await onResend()) setRemaining(60); }}>
        {busy ? "Sending…" : remaining > 0 ? `Resend email in ${remaining}s` : "Resend verification email"}
      </button>
      <button type="button" className="btn" onClick={onSignIn}>I’ve verified — sign in →</button>
    </div>
    <button type="button" className="pf-link" style={{ marginTop: 20 }} onClick={onChangeEmail}>Wrong email address? Change it</button>
  </div>;
}

export function ApiKeyGuide({ exchange, compact = false }: { exchange: "yellow" | "weex"; compact?: boolean }) {
  const weex = exchange === "weex";
  const name = weex ? "WEEX" : "Yellow Pro";
  const instructions = <>
    <p className="pf-guide-copy">An API key lets WHY read your exchange account and place trades. Create it on {name}, then paste it into WHY.</p>
    <ol className="pf-guide-list">
      <li><strong>Open {name} in another tab.</strong><span>{weex ? "Sign in and open Account → API Management." : "Sign in to your Yellow account and look for API keys or API management. If you cannot find this option, use Yellow’s support link below."}</span></li>
      <li><strong>Create a separate key for WHY.</strong><span>Name it “WHY Trading Bot”. {weex && "Choose an 8–31 character passphrase using letters and numbers; you will need it below."}</span></li>
      <li><strong>Choose read + futures trading permissions.</strong><span>{weex ? "Enable Read and Futures/Contract trading." : "Enable futures reading and trading (read:futures and trade:futures)."} Keep withdrawals and transfers disabled.</span></li>
      <li><strong>Copy the credentials, then return here.</strong><span>Paste the API Key and API Secret into their matching fields{weex ? ", plus the passphrase you chose" : ""}. The secret is shown only when the key is created. If you lose it, create a replacement key.</span></li>
    </ol>
    <div className="pf-permissions"><span>✓ Read account</span><span>✓ Futures trading</span><span className="pf-danger">✕ Withdrawals / transfers</span></div>
    <div className="pf-guide-links">
      <a className="btn btn-ghost" href={weex ? "https://www.weex.com/account/newapi" : "https://yellow.pro"} target="_blank" rel="noopener noreferrer">Open {name} ↗</a>
      <a className="pf-a" href={weex ? "https://www.weex.com/api-doc/contract/QuickStart/IntegrationPreparation" : "https://docs.yellow.pro/api-and-programmatic-access/api-key-management"} target="_blank" rel="noopener noreferrer">Official API guide ↗</a>
      {!weex && <a className="pf-a" href="https://docs.yellow.pro/community-and-resources/contact-support" target="_blank" rel="noopener noreferrer">Can’t find API keys? ↗</a>}
    </div>
    <p className="pf-guide-copy" style={{ fontSize: 12 }}>If the exchange asks for an IP allowlist, use the WHY server IP provided by support. Your device’s IP will not work for the bot.</p>
  </>;
  if (compact) return <details className="pf-guide" open>
    <summary>Need API keys? Start here — {name} setup guide</summary>
    {instructions}
  </details>;
  return <div className="pf-guide"><h3>How to get your {name} API keys</h3>{instructions}</div>;
}

export function QuickTutorial({ weexLive, paperBalance, onFinish }: {
  weexLive: boolean; paperBalance: number; onFinish: (route: Route) => void;
}) {
  const [step, setStep] = useState(0);
  const [route, setRoute] = useState<Route>("paper");
  const paper = route === "paper";
  const titles = ["Choose how to get started", paper ? "Try trading with simulated funds" : "Get your exchange API keys", "Know what to do next"];
  return <>
    <nav className="pf-tutorial-progress" aria-label="Tutorial progress">
      {["Choose mode", "Set up", "Start & monitor"].map((label, index) => <button key={label} type="button" aria-current={step === index ? "step" : undefined} disabled={index > step} onClick={() => setStep(index)}><span>{index < step ? "✓" : index + 1}</span>{label}</button>)}
    </nav>
    <div className="pf-tutorial-body" aria-live="polite">
      <div className="eyebrow pf-gold">Step {step + 1} of 3</div>
      <h3 className="pf-tutorial-title">{titles[step]}</h3>
      {step === 0 && <>
        <p className="pf-guide-copy">New here? Paper lets you learn the controls first. Live uses real funds in your own exchange account.</p>
        <div className="pf-grid">
          <button type="button" className="pf-card pf-xcard pf-tutorial-choice" aria-pressed={paper} onClick={() => setRoute("paper")}><strong>Paper · Practice first</strong><span className="pf-guide-copy">${paperBalance.toLocaleString("en-US")} simulated USD. No exchange account or API keys needed.</span><span className="pf-gold">Recommended for your first visit</span></button>
          <button type="button" className="pf-card pf-xcard pf-tutorial-choice" aria-pressed={route === "yellow"} onClick={() => setRoute("yellow")}><strong>Live · Yellow Pro</strong><span className="pf-guide-copy">Connect your Yellow account using an API key and secret. Real trades, real gains and losses.</span><span className="pf-gold">Funds stay on your exchange</span></button>
          {weexLive && <button type="button" className="pf-card pf-xcard pf-tutorial-choice" aria-pressed={route === "weex"} onClick={() => setRoute("weex")}><strong>Live · WEEX</strong><span className="pf-guide-copy">Connect with an API key, secret and passphrase.</span></button>}
        </div>
      </>}
      {step === 1 && (paper ? <ol className="pf-guide-list">
        <li><strong>Your practice account is ready.</strong><span>Paper trades use simulated funds. You do not need to deposit money or connect an exchange.</span></li>
        <li><strong>Review the settings.</strong><span>Strategy chooses the trading approach. Allocation sets how much of the account the bot may use. Position size sets the amount for each trade.</span></li>
        <li><strong>Understand leverage.</strong><span>Leverage increases exposure and can magnify gains and losses. Review it before saving your settings.</span></li>
      </ol> : <ApiKeyGuide exchange={route} />)}
      {step === 2 && <ol className="pf-guide-list">
        <li><strong>{paper ? "Open the Paper panel." : `Connect ${route === "yellow" ? "Yellow Pro" : "WEEX"} and wait for verification.`}</strong><span>{paper ? "You’ll find your balance and settings below the Paper / Live switch." : "Paste your credentials on the next screen. Connecting an exchange does not start trading. If verification is pending, wait and re-check it in the exchange panel."}</span></li>
        <li><strong>Review settings before pressing Start.</strong><span>{paper ? "Save any settings you change, then choose Start Paper trading." : "Open your exchange card, review the trading size and available settings, then choose Start trading when you are ready. Trading can lose real money."}</span></li>
        <li><strong>Watch status, positions and P&amp;L.</strong><span>After Start, the bot waits for a trading signal; a trade may not appear immediately. Use Stop to pause new entries. Open live positions may still need management.</span></li>
      </ol>}
    </div>
    <div className="pf-actions">
      {step > 0 && <button type="button" className="btn btn-ghost" onClick={() => setStep((s) => s - 1)}>← Back</button>}
      <button type="button" className="btn" onClick={() => step < 2 ? setStep((s) => s + 1) : onFinish(route)}>{step < 2 ? "Next →" : paper ? "Go to Paper trading →" : `Continue to connect ${route === "yellow" ? "Yellow Pro" : "WEEX"} →`}</button>
    </div>
  </>;
}

export const ONBOARDING_CSS = `
.pf-gold{color:var(--gold)}
.pf-welcome{padding:30px 28px}
.pf-mail-icon{font-size:38px;color:var(--gold);margin:18px 0 10px}
.pf-email-address{font-size:18px;color:var(--platinum);overflow-wrap:anywhere;margin:10px 0 20px;font-weight:600}
.pf-guide{border:1px solid var(--gold-dim);border-radius:12px;background:rgba(243,199,106,.05);padding:18px 20px;margin:18px 0}
.pf-guide summary,.pf-guide h3{font-size:15px;font-weight:700;color:var(--gold);margin:0;line-height:1.5}
.pf-guide summary{cursor:pointer}
.pf-guide-copy{font-size:13.5px;line-height:1.65;color:var(--ash);margin:10px 0}
.pf-guide-list{list-style:none;counter-reset:guide;padding:0;margin:20px 0;display:grid;gap:18px}
.pf-guide-list li{counter-increment:guide;position:relative;padding-left:40px;font-size:13.5px;line-height:1.6}
.pf-guide-list li:before{content:counter(guide);position:absolute;left:0;top:0;width:27px;height:27px;display:grid;place-items:center;border:1px solid var(--gold-dim);border-radius:50%;color:var(--gold);font-weight:600}
.pf-guide-list strong{display:block;color:var(--platinum)}
.pf-guide-list li>span{display:block;color:var(--ash);margin-top:3px}
.pf-guide-links{display:flex;align-items:center;flex-wrap:wrap;gap:12px 18px;font-size:12.5px;margin-top:18px}
.pf-guide-links .btn{padding:10px 14px;font-size:12.5px}
.pf-permissions{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:12px;color:var(--green);border-top:1px solid var(--steel-soft);padding-top:14px}
.pf-tutorial-progress{display:flex;gap:8px;margin-bottom:24px}
.pf-tutorial-progress button{flex:1;display:flex;gap:8px;align-items:center;border:0;border-bottom:2px solid var(--steel-soft);padding:0 0 12px;background:none;color:var(--ash);text-align:left;font:inherit;font-size:12px;cursor:pointer}
.pf-tutorial-progress button[aria-current]{color:var(--gold);border-color:var(--gold)}
.pf-tutorial-progress button:disabled{opacity:.5;cursor:default}
.pf-tutorial-progress button>span{border:1px solid currentColor;border-radius:50%;width:23px;height:23px;display:grid;place-items:center;flex-shrink:0}
.pf-tutorial-title{font-family:var(--font-display, "Bricolage Grotesque"), sans-serif;font-size:22px;line-height:1.3;margin:12px 0;color:var(--platinum)}
.pf-tutorial-choice{gap:4px;font-size:13px}
.pf-tutorial-choice[aria-pressed="true"]{border-color:var(--gold);background:rgba(243,199,106,.07);box-shadow:inset 0 0 0 1px var(--gold-dim)}
.pf-get-started{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;padding:18px 22px;margin-top:24px;border-color:var(--gold-dim);background:rgba(243,199,106,.04)}
.pf-get-started .pf-actions{margin:0}
.pf-tutorial-target{outline:2px solid var(--gold-dim);outline-offset:8px;border-radius:12px;scroll-margin-top:110px}
.pf-tutorial-hint{padding:12px 16px;margin-bottom:16px;border:1px solid var(--gold-dim);border-radius:8px;font-size:13px;line-height:1.6;color:var(--platinum);background:rgba(243,199,106,.06)}
.pf :is(button,a):focus-visible{outline:2px solid var(--gold);outline-offset:4px}
@media(max-width:600px){.pf-welcome{padding:24px 18px}.pf-guide{padding:16px}.pf-tutorial-progress button{font-size:10px;gap:5px}.pf-get-started{padding:16px}.pf-get-started .pf-actions{width:100%}.pf-tutorial-target{outline-offset:4px}}
`;

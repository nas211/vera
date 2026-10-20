import nodemailer from "nodemailer";
import type SMTPPool from "nodemailer/lib/smtp-pool";
import { Resend } from "resend";

// The one place this site sends mail from.
//
// Provider order is SMTP first, Resend second — deliberately the same order as
// why-partners/mailer.py, so both halves of the site behave identically and
// moving to a domain sender is an env change rather than a code change.
//
// Nothing here throws into a request. Every caller is on a path where the
// durable record is already written, so an outage must degrade to a logged
// line and never to an error a visitor sees. It IS logged, though: the
// waitlist sent nothing for two days and the swallowed exception is the reason
// nobody found out.
const HOST = (process.env.SMTP_HOST ?? "").trim();
const PORT = Number(process.env.SMTP_PORT ?? 587);
const USER = (process.env.SMTP_USER ?? "").trim();
const PASS = (process.env.SMTP_PASS ?? "").trim();
const KEY = (process.env.RESEND_API_KEY ?? "").trim();

const FROM =
  (process.env.MAIL_FROM ?? "").trim() ||
  (USER && `WHY TRADE <${USER}>`) ||
  "WHY TRADE <onboarding@resend.dev>";

const smtpReady = Boolean(HOST && USER && PASS);

export const configured = () => smtpReady || Boolean(KEY);

export type Mail = { to: string; subject: string; text: string; html?: string };

// One pooled transport for the process: Gmail throttles connection churn, and
// a waitlist burst is exactly when you would create one socket per signup.
let tx: nodemailer.Transporter<SMTPPool.SentMessageInfo> | null = null;
function transport() {
  if (!tx) {
    tx = nodemailer.createTransport({
      host: HOST,
      port: PORT,
      secure: PORT === 465,
      auth: { user: USER, pass: PASS },
      pool: true,
      maxConnections: 2,
    });
  }
  return tx;
}

export async function send(m: Mail): Promise<boolean> {
  if (smtpReady) {
    try {
      await transport().sendMail({ from: FROM, to: m.to, subject: m.subject, text: m.text, html: m.html });
      return true;
    } catch (e) {
      console.error(`[mail] smtp failed -> ${m.to}: ${(e as Error).message}`);
    }
  }
  if (KEY) {
    try {
      const r = await new Resend(KEY).emails.send({
        from: FROM,
        to: m.to,
        subject: m.subject,
        text: m.text,
        ...(m.html ? { html: m.html } : {}),
      });
      if (r.error) throw new Error(r.error.message);
      return true;
    } catch (e) {
      console.error(`[mail] resend failed -> ${m.to}: ${(e as Error).message}`);
    }
  }
  if (!configured()) {
    console.error(`[mail] no provider configured — dropped "${m.subject}" -> ${m.to}`);
  }
  return false;
}

/** Shared shell so every email off this site looks like the same sender.
 *  Inlined styles and a table-free layout: mail clients strip <style> blocks
 *  and flexbox is not safe to rely on in Outlook. */
export function wrap(title: string, bodyHtml: string): string {
  return `<div style="background:#0a0b0f;padding:32px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">
  <div style="max-width:520px;margin:0 auto;background:#11131a;border:1px solid #262a35;border-radius:14px;padding:32px">
    <div style="font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:700;letter-spacing:.05em;font-size:15px;color:#f2f4f8;margin-bottom:24px">WHY&nbsp;TRADE</div>
    <h1 style="font-size:21px;line-height:1.3;color:#f2f4f8;margin:0 0 16px">${title}</h1>
    ${bodyHtml}
    <div style="margin-top:28px;padding-top:18px;border-top:1px solid #262a35;font-size:12px;color:#6b7280">
      whytradingbot.com — nothing here is financial advice.
    </div>
  </div>
</div>`;
}

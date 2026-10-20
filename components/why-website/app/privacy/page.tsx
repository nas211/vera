import type { Metadata } from "next";
import { LegalPage } from "@/components/legal/LegalPage";

export const metadata: Metadata = { title: "Privacy Policy — WHY TRADE", robots: { index: true, follow: true } };

export default function Privacy() {
  return (
    <LegalPage eyebrow="Legal" title="Privacy Policy" updated="3 October 2026">
      <p>This Privacy Policy explains what <strong>Why Protocol LTD</strong> (“WHY”, “we”, “us”) collects when you use whytradingbot.com, the WHY Partner Portal, the Telegram bot and related services (together, the “Services”), why we collect it, and the choices you have.</p>

      <h2>1. What we collect</h2>
      <ul>
        <li><strong>Account data</strong> — name or alias, email address, and a one-way hash of your password when you create a Partner Portal account.</li>
        <li><strong>Exchange API credentials</strong> — if you choose to connect an exchange, the API key and secret you provide. These are encrypted at rest with a key stored separately from the database, are never displayed back to you, and are used only to read your account and, where you have enabled it, to transmit trade instructions. We refuse keys with withdrawal permissions.</li>
        <li><strong>Telegram data</strong> — your Telegram user ID and username when you interact with the bot.</li>
        <li><strong>Human support chats</strong> — messages you choose to send to Nad, his replies, timestamps and a random chat identifier. Your messages are relayed to Nad through Telegram, and his replies appear in the website chat. A secure cookie lets you return to your conversation.</li>
        <li><strong>Usage and technical data</strong> — IP address, browser type, pages visited, timestamps and error logs, used for security (for example rate-limiting sign-in attempts) and to keep the Services running.</li>
      </ul>
      <p>We do not collect payment card details on our own systems, and we never take custody of your funds or private keys.</p>

      <h2>2. How we use it</h2>
      <ul>
        <li>To provide and secure the Services, including verifying your email with a one-time code and protecting accounts against brute-force sign-in.</li>
        <li>To operate automated trading on your connected exchange only where you have explicitly enabled it.</li>
        <li>To show you performance information and send you service messages (verification codes, security notices).</li>
        <li>To comply with legal obligations and to prevent abuse or fraud.</li>
      </ul>
      <p>We do not sell personal data and we do not use it for third-party advertising.</p>

      <h2>3. Sharing</h2>
      <p>We share data only with processors that help us run the Services (hosting, email delivery for verification codes, the exchange you connect, Telegram) and where required by law. Each processor receives only what it needs.</p>

      <h2>4. Retention</h2>
      <p>Account data is kept while your account is active. Exchange credentials are deleted when you remove them from the portal or close your account. Security logs are kept for a limited period and then rotated.</p>
      <p>Human support messages are retained on our website systems for 30 days and then removed. Telegram receives the messages sent to Nad; copies in Telegram are subject to Telegram’s retention and the recipient’s chat history.</p>

      <h2>5. Your rights</h2>
      <p>You may access, correct or delete your account data, and remove connected exchange credentials at any time from the portal. To exercise any other right, or to close your account entirely, contact us via the Telegram bot <a href="https://t.me/Whytradingbot" target="_blank" rel="noopener">@Whytradingbot</a>.</p>

      <h2>6. Security</h2>
      <p>Passwords are hashed with bcrypt, sessions use secure HttpOnly cookies, exchange credentials are encrypted at rest, and all traffic to the Services is served over HTTPS. No system is perfectly secure; if we become aware of a breach affecting your data we will notify you as required by law.</p>

      <h2>7. Changes</h2>
      <p>We may update this policy from time to time. The date at the top shows the current version; material changes will be announced through the Services.</p>
    </LegalPage>
  );
}

# Nad's human support chat

The website's **Need a person? Chat with Nad** button opens a saved human chat.
The stdlib Python service stores messages in SQLite/WAL and relays notifications
to the verified `@nad_wv` private Telegram account. Replies to a notification
return to that notification's visitor chat; other Telegram users cannot answer.
Browser chat identity uses a random, hashed token in a Secure, HttpOnly,
SameSite=Strict cookie. Telegram credentials and the private owner ID stay server-side.

The selected existing bot is `@Gosoma_bot`. Its old Soma testing-agent receiver
was inactive at setup. The new service conflicts with `soma-bot.service` so both
cannot consume the same bot's updates. No trading service or exchange credentials
are used. A dedicated bot can be substituted by changing the protected bot token;
Nad must first start that bot and its configured private chat must match `@nad_wv`.
The bridge refuses to take over a bot with an existing webhook.

Reply to the specific **WHY support · Chat …** notification to answer that visitor.
Plain unrelated messages to the bot are ignored. Messages are text-only; a reply
with attachments gets an instruction to send text. Notifications use normal
Telegram notification delivery (subject to the recipient's Telegram settings).

Operator commands in the same bot chat:

- `/why_online`: show **Nad is online** for four hours.
- `/why_away`: show **Nad is away · leave a message**.
- `/why_support`: show current availability and reply instructions.

Availability is declared by Nad, not his personal Telegram last-seen status.
Online also requires a successful receiver poll within the last 90 seconds.
Replies stay in the website conversation across page reloads; messages are
removed from the website database after 30 days. Telegram keeps its own copies.
AI chat history is not automatically forwarded when a visitor switches to Nad.

Environment in `/etc/why-human-support/env` (root mode 0600):

- `WHY_HUMAN_BOT_TOKEN`: Telegram bot credential.
- `WHY_HUMAN_OWNER_ID`: verified private Telegram user/chat ID.
- `WHY_HUMAN_INTERNAL_SECRET`: at least 32 random characters; share this value
  with the website's `/etc/why-website/env`. Never use `NEXT_PUBLIC_`.
- Optional `WHY_HUMAN_DATABASE`, default `/var/lib/why-human-support/support.sqlite3`.
- Optional `WHY_HUMAN_INITIAL_ONLINE=1` declares availability for four hours on
  the first setup only; service restarts do not reset an existing availability setting.

`why-human-support.service` uses a DynamicUser, private state directory, a
read-only system filesystem and loopback port 8111. Next's `/api/helpdesk/human`
proxies to it, validates POST origins and bounded JSON input, and forwards only
the support cookie. No Telegram token appears in browser code or logs.

Visitor submits have a client idempotency key, Telegram updates are deduplicated,
and Telegram reply receipts map to the exact conversation. The poll offset and
all chat data survive restarts. If a notification send times out, it is marked
uncertain and is not blindly resent: a signed tag still routes Nad's reply if
the notification arrived. A received reply is committed before its acknowledgment.
Explicit Telegram rate-limit rejections are safe to retry after the provider's
delay. Visitor limits: 12 new chats/IP/day, five messages/chat/minute, 200
messages/IP/day, and 1,000 new chats/messages globally/day. IPs are hashed for
these counters; public conversation responses never contain other sessions.

Tests: `python3 -m unittest -v test_bridge.py` in this directory; existing
`tests/helpdesk-browser.cjs` and new `tests/human-chat-browser.cjs` with Playwright.
All automated Telegram calls are mocked. A real outbound notification plus Nad's
actual Telegram reply verifies the complete live round trip.

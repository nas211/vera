"""WHY visitor support -> Nad's Telegram -> the same visitor. Stdlib only."""
import hashlib
from contextlib import contextmanager
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import urllib.request
import urllib.error
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DAY = 86400
RETENTION = 30 * DAY
COOKIE = "why_human_session"
MAX_TEXT = 2000
SECRET_PATTERNS = [
    re.compile(r"\b(?:api[ _-]?(?:key|secret)|secret[ _-]?key|password|passphrase|access[ _-]?token|authorization)\s*(?:[:=]\s*|[\"']\s*:\s*)[\"']?[^\s\"']{6,}", re.I),
    re.compile(r"\b(?:sk-|gsk_|ghp_|gho_|eyJ)[a-zA-Z0-9_-]{12,}"),
    re.compile(r"\b(?=[a-zA-Z0-9_-]{32,}\b)(?=[a-zA-Z0-9_-]*\d)(?=[a-zA-Z0-9_-]*[a-zA-Z])[a-zA-Z0-9_-]{32,}\b"),
    re.compile(r"\b(?:seed|recovery|mnemonic)\s+(?:phrase|words)\s*[:=]", re.I),
]

def contains_credentials(text):
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)

class SupportError(Exception):
    def __init__(self, text, status=400):
        self.text, self.status = text, status

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                  role TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL, client_id TEXT NOT NULL, state TEXT NOT NULL, UNIQUE(session,client_id));
                CREATE INDEX IF NOT EXISTS session_messages ON messages(session,id);
                CREATE TABLE IF NOT EXISTS receipts(telegram_id INTEGER PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS handled(update_id INTEGER PRIMARY KEY, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rates(key TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS rate_key ON rates(key,created);
            """)
            # A process crash during send leaves an uncertain outcome. Do not resend
            # automatically; a reply can still route from the bot's signed tag.
            db.execute("UPDATE messages SET state='uncertain' WHERE state='sending'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def setting(db, key, default="0"):
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    @staticmethod
    def put(db, key, value):
        db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def presence(self):
        with self.connect() as db:
            now = time.time()
            receiver = now - float(self.setting(db, "receiver_seen")) < 90
            online = receiver and float(self.setting(db, "online_until")) > now
            return {"online": online, "operator": "Nad", "receiving": receiver}

    def set_online(self, value):
        with self.connect() as db:
            self.put(db, "online_until", time.time() + 4 * 3600 if value else 0)

    def authenticate(self, cookie):
        jar = SimpleCookie()
        try:
            jar.load(cookie or "")
            value = jar[COOKIE].value if COOKIE in jar else ""
            session, token = value.split(".", 1)
        except (ValueError, KeyError, CookieError):
            return None
        if not re.fullmatch(r"[a-f0-9]{32}", session) or not re.fullmatch(r"[a-f0-9]{64}", token):
            return None
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=? AND updated>?", (session, time.time() - RETENTION)).fetchone()
            if row and hmac.compare_digest(row["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
                return session
        return None

    @staticmethod
    def limit(db, key, seconds, count):
        now = time.time()
        total = db.execute("SELECT count(*) FROM rates WHERE key=? AND created>?", (key, now - seconds)).fetchone()[0]
        if total >= count:
            raise SupportError("Please wait before sending more messages. Your existing chat is still available.", 429)

    def open(self, cookie, ip):
        session = self.authenticate(cookie)
        if session:
            return session, None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.limit(db, "open:" + ip, DAY, 12)
            self.limit(db, "open-global", DAY, 1000)
            db.executemany("INSERT INTO rates VALUES(?,?)", [("open:" + ip, time.time()), ("open-global", time.time())])
            session, token = secrets.token_hex(16), secrets.token_hex(32)
            now = time.time()
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (session, hashlib.sha256(token.encode()).hexdigest(), now, now))
        value = f"{COOKIE}={session}.{token}; HttpOnly; Secure; SameSite=Strict; Path=/api/helpdesk/human; Max-Age={RETENTION}"
        return session, value

    def snapshot(self, session):
        result = self.presence()
        result["ready"] = bool(session)
        result["messages"] = []
        if session:
            with self.connect() as db:
                rows = db.execute("SELECT id,role,text,created,state,client_id FROM messages WHERE session=? ORDER BY id DESC LIMIT 100", (session,)).fetchall()
                result["messages"] = [dict(row) for row in reversed(rows)]
        return result

    def enqueue(self, session, text, client_id, ip):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise SupportError(f"Enter a message of up to {MAX_TEXT} characters.")
        text = text.strip()
        if contains_credentials(text):
            raise SupportError("Remove API keys, secrets, passwords and recovery phrases before sending.")
        if not isinstance(client_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", client_id):
            raise SupportError("Please retry your message.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id,text FROM messages WHERE session=? AND client_id=?", (session, client_id)).fetchone()
            if existing:
                if existing["text"] != text:
                    raise SupportError("This message was already submitted with different text.", 409)
                return existing["id"]
            self.limit(db, "send:" + session, 60, 5)
            self.limit(db, "send-ip:" + ip, DAY, 200)
            self.limit(db, "send-global", DAY, 1000)
            now = time.time()
            db.executemany("INSERT INTO rates VALUES(?,?)", [("send:" + session, now), ("send-ip:" + ip, now), ("send-global", now)])
            result = db.execute("INSERT INTO messages(session,role,text,created,client_id,state) VALUES(?,'visitor',?,?,?,'queued')", (session, text, now, client_id))
            db.execute("UPDATE sessions SET updated=? WHERE id=?", (now, session))
            return result.lastrowid

    def cleanup(self):
        with self.connect() as db:
            now = time.time()
            # Message retention is 30 days, even if a conversation stays active.
            db.execute("DELETE FROM messages WHERE created<?", (now - RETENTION,))
            db.execute("DELETE FROM sessions WHERE updated<?", (now - RETENTION,))
            db.execute("DELETE FROM handled WHERE created<?", (now - 7 * DAY,))
            db.execute("DELETE FROM rates WHERE created<?", (now - DAY,))

class RateLimited(Exception):
    def __init__(self, seconds):
        self.seconds = min(60, max(1, int(seconds)))

class Telegram:
    def __init__(self, token):
        self.token = token
        self.send_lock = threading.Lock()
        self.last_send = 0

    def call(self, method, payload):
        request = urllib.request.Request("https://api.telegram.org/bot" + self.token + "/" + method,
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 429:
                try:
                    data = json.loads(error.read(4096))
                    raise RateLimited(data.get("parameters", {}).get("retry_after", 5))
                except (ValueError, TypeError):
                    raise RateLimited(5)
            raise
        if not result.get("ok"):
            raise RuntimeError("telegram_failed")
        return result["result"]

    def send(self, chat, text, **extra):
        with self.send_lock:
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_send)))
            try:
                return self.call("sendMessage", {"chat_id": chat, "text": text, "link_preview_options": {"is_disabled": True}, **extra})
            finally:
                self.last_send = time.monotonic()

class Relay:
    def __init__(self, store, telegram, owner, bot_id, signing_key):
        self.store, self.telegram = store, telegram
        self.owner, self.bot_id, self.signing_key = int(owner), int(bot_id), signing_key

    def signature(self, session):
        return hmac.new(self.signing_key.encode(), session.encode(), hashlib.sha256).hexdigest()[:16]

    def notification(self, message):
        session = message["session"]
        return f"WHY support · Chat {session} · {self.signature(session)}\n\nVisitor:\n{message['text']}\n\nReply to THIS message to answer in their website chat.\n/why_online — available for 4 hours\n/why_away — away"

    def deliver_one(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM messages WHERE role='visitor' AND state='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return False
            message = dict(row)
            db.execute("UPDATE messages SET state='sending' WHERE id=?", (message["id"],))
        try:
            sent = self.telegram.send(self.owner, self.notification(message), reply_markup={"force_reply": True, "input_field_placeholder": "Reply to the visitor"})
            with self.store.connect() as db:
                db.execute("INSERT OR REPLACE INTO receipts VALUES(?,?)", (sent["message_id"], message["session"]))
                db.execute("UPDATE messages SET state='sent' WHERE id=?", (message["id"],))
        except RateLimited as error:
            with self.store.connect() as db:
                db.execute("UPDATE messages SET state='queued' WHERE id=?", (message["id"],))
            time.sleep(error.seconds)
        except Exception:
            # Telegram has no idempotency key for sendMessage. A timeout may have
            # delivered it; don't blindly resend and create duplicate alerts.
            with self.store.connect() as db:
                db.execute("UPDATE messages SET state='uncertain' WHERE id=?", (message["id"],))
        return True

    def session_for_reply(self, reply):
        if not isinstance(reply, dict) or reply.get("from", {}).get("id") != self.bot_id:
            return None
        with self.store.connect() as db:
            row = db.execute("SELECT r.session FROM receipts r JOIN sessions s ON s.id=r.session WHERE telegram_id=? AND s.updated>?", (reply.get("message_id"), time.time() - RETENTION)).fetchone()
            if row:
                return row[0]
            # Recover a reply even if Telegram delivered a notification but its
            # send response was lost, using the signed first line of that message.
            match = re.match(r"^WHY support · Chat ([a-f0-9]{32}) · ([a-f0-9]{16})\n", reply.get("text", ""))
            if match and hmac.compare_digest(match[2], self.signature(match[1])):
                row = db.execute("SELECT id FROM sessions WHERE id=? AND updated>?", (match[1], time.time() - RETENTION)).fetchone()
                return row[0] if row else None
        return None

    def handle(self, update):
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            return
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM handled WHERE update_id=?", (update_id,)).fetchone():
                return
        message = update.get("message", {})
        authorized = message.get("from", {}).get("id") == self.owner and message.get("chat", {}).get("id") == self.owner and message.get("chat", {}).get("type") == "private"
        reply_notice = None
        if authorized:
            text = message.get("text", "")
            command = text.split("@", 1)[0].strip().lower()
            if command in ("/why_online", "/why_away", "/why_support"):
                if command != "/why_support":
                    self.store.set_online(command == "/why_online")
                online = self.store.presence()["online"]
                reply_notice = ("WHY support: Nad is online for 4 hours." if online else "WHY support: Nad is away. Visitors can leave messages.") + "\nReply to a WHY support notification to answer that visitor.\n/why_online · /why_away"
            else:
                session = self.session_for_reply(message.get("reply_to_message"))
                if session:
                    if not text.strip():
                        reply_notice = "Please reply with text so it can appear in the visitor's chat."
                    elif len(text) > MAX_TEXT or contains_credentials(text):
                        reply_notice = "Reply not sent: use up to 2,000 characters and remove any keys, secrets or passwords."
                    else:
                        with self.store.connect() as db:
                            now = time.time()
                            db.execute("INSERT OR IGNORE INTO messages(session,role,text,created,client_id,state) VALUES(?,'nad',?,?,?,'sent')", (session, text.strip(), now, "telegram-" + str(message["message_id"])))
                            db.execute("UPDATE sessions SET updated=? WHERE id=?", (now, session))
                        reply_notice = "Sent to the visitor's WHY chat."
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO handled VALUES(?,?)", (update_id, time.time()))
            self.store.put(db, "telegram_offset", max(int(self.store.setting(db, "telegram_offset")), update_id + 1))
        if reply_notice:
            try:
                self.telegram.send(self.owner, reply_notice)
            except Exception:
                pass  # A lost acknowledgment never loses the committed reply.

    def receive(self):
        while True:
            try:
                with self.store.connect() as db:
                    offset = int(self.store.setting(db, "telegram_offset"))
                updates = self.telegram.call("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message"]})
                with self.store.connect() as db:
                    self.store.put(db, "receiver_seen", time.time())
                for update in updates:
                    self.handle(update)
            except Exception:
                time.sleep(5)

    def deliver(self):
        next_cleanup = 0
        while True:
            try:
                if time.time() > next_cleanup:
                    self.store.cleanup()
                    next_cleanup = time.time() + 3600
                if not self.deliver_one():
                    time.sleep(1)
            except Exception:
                time.sleep(2)

def make_handler(store, internal_secret):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log visitor text, cookies or Telegram URLs/tokens.

        def respond(self, data, status=200, cookie=None):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return hmac.compare_digest(self.headers.get("X-Why-Human-Secret", ""), internal_secret)

        def do_GET(self):
            if not self.authorized():
                return self.respond({"error": "Unauthorized"}, 403)
            if self.path != "/chat":
                return self.respond({"error": "Not found"}, 404)
            session = store.authenticate(self.headers.get("Cookie"))
            return self.respond(store.snapshot(session))

        def do_POST(self):
            if not self.authorized():
                return self.respond({"error": "Unauthorized"}, 403)
            if self.path != "/chat":
                return self.respond({"error": "Not found"}, 404)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 16384:
                    raise SupportError("Please shorten your message.", 413)
                self.connection.settimeout(10)
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise SupportError("Please enter a valid message.")
                cookie = self.headers.get("Cookie", "")
                ip = hashlib.sha256(self.headers.get("X-Real-IP", "unknown").encode()).hexdigest()
                if body.get("action") == "open":
                    session, new_cookie = store.open(cookie, ip)
                    return self.respond(store.snapshot(session), cookie=new_cookie)
                session = store.authenticate(cookie)
                if not session:
                    raise SupportError("Your chat has expired. Please reopen it.", 401)
                if body.get("action") != "send":
                    raise SupportError("Unknown action.")
                store.enqueue(session, body.get("text"), body.get("clientId"), ip)
                return self.respond(store.snapshot(session))
            except SupportError as error:
                return self.respond({"error": error.text}, error.status)
            except (ValueError, TypeError, TimeoutError):
                return self.respond({"error": "Please enter a valid message."}, 400)
            except Exception:
                return self.respond({"error": "Human support is temporarily unavailable. Please retry."}, 503)
    return Handler

def main():
    os.umask(0o077)
    token = os.environ["WHY_HUMAN_BOT_TOKEN"]
    secret = os.environ["WHY_HUMAN_INTERNAL_SECRET"]
    owner = int(os.environ["WHY_HUMAN_OWNER_ID"])
    if len(secret) < 32:
        raise SystemExit("A secure internal support secret is required.")
    telegram = Telegram(token)
    bot = telegram.call("getMe", {})
    chat = telegram.call("getChat", {"chat_id": owner})
    if chat.get("type") != "private" or chat.get("username", "").lower() != "nad_wv":
        raise SystemExit("Telegram support owner must be the verified @nad_wv private chat.")
    if telegram.call("getWebhookInfo", {}).get("url"):
        raise SystemExit("This bot already has a webhook; support cannot take over its receiver.")
    store = Store(os.environ.get("WHY_HUMAN_DATABASE", "/var/lib/why-human-support/support.sqlite3"))
    with store.connect() as db:
        if Store.setting(db, "online_until", "") == "":
            Store.put(db, "online_until", time.time() + 4 * 3600 if os.environ.get("WHY_HUMAN_INITIAL_ONLINE") == "1" else 0)
    relay = Relay(store, telegram, owner, bot["id"], secret)
    threading.Thread(target=relay.receive, daemon=True).start()
    threading.Thread(target=relay.deliver, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("WHY_HUMAN_PORT", "8111"))), make_handler(store, secret)).serve_forever()

if __name__ == "__main__":
    main()

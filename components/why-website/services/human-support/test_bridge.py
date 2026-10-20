import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from bridge import Store, Relay, SupportError, make_handler, RETENTION

class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, chat, text, **extra):
        if self.fail:
            raise TimeoutError("fixture timeout")
        result = {"message_id": 1000 + len(self.sent), "text": text, "from": {"id": 222}}
        self.sent.append(result)
        return result

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "support.sqlite3"
        self.store = Store(self.path)
        self.telegram = FakeTelegram()
        self.relay = Relay(self.store, self.telegram, 111, 222, "test-signing-key-only")
        self.a, self.cookie_a = self.store.open("", "fixture-a")
        self.b, self.cookie_b = self.store.open("", "fixture-b")

    def enqueue(self, session, text="Can you help me connect?", client="fixture-client-id-1234"):
        return self.store.enqueue(session, text, client, "fixture-ip")

    def reply(self, notification, text="Open Live and check the connection status.", update_id=900, owner=111):
        return {"update_id": update_id, "message": {"message_id": update_id + 2000, "from": {"id": owner}, "chat": {"id": owner, "type": "private"}, "text": text, "reply_to_message": notification}}

    def test_full_two_visitor_reply_routing(self):
        self.enqueue(self.a)
        self.enqueue(self.b, "Where is my balance?", "fixture-client-id-5678")
        self.assertTrue(self.relay.deliver_one())
        self.assertTrue(self.relay.deliver_one())
        self.relay.handle(self.reply(self.telegram.sent[0], "Answer for A"))
        self.relay.handle(self.reply(self.telegram.sent[1], "Answer for B", update_id=901))
        self.assertEqual([m["text"] for m in self.store.snapshot(self.a)["messages"]], ["Can you help me connect?", "Answer for A"])
        self.assertEqual([m["text"] for m in self.store.snapshot(self.b)["messages"]], ["Where is my balance?", "Answer for B"])

    def test_owner_and_bot_identity_are_required(self):
        self.enqueue(self.a); self.relay.deliver_one()
        self.relay.handle(self.reply(self.telegram.sent[0], owner=333))
        forged = dict(self.telegram.sent[0], **{"from": {"id": 999}})
        self.relay.handle(self.reply(forged, update_id=901))
        self.assertEqual(len(self.store.snapshot(self.a)["messages"]), 1)

    def test_cookie_authentication_and_forgery(self):
        self.assertEqual(self.store.authenticate(self.cookie_a), self.a)
        self.assertEqual(self.store.authenticate(self.cookie_b), self.b)
        forged = self.cookie_b.replace(self.b, self.a)
        self.assertIsNone(self.store.authenticate(forged))
        self.assertIsNone(self.store.authenticate("why_human_session=not-a-session"))

    def test_concurrent_browser_retries_make_one_notification(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.enqueue(self.a), range(4)))
        self.assertEqual(len(set(ids)), 1)
        self.assertTrue(self.relay.deliver_one())
        self.assertFalse(self.relay.deliver_one())
        self.assertEqual(len(self.telegram.sent), 1)
        with self.assertRaises(SupportError):
            self.enqueue(self.a, "changed text")

    def test_telegram_redelivery_and_process_restart_do_not_duplicate_reply(self):
        self.enqueue(self.a); self.relay.deliver_one()
        update = self.reply(self.telegram.sent[0])
        self.relay.handle(update)
        restarted = Relay(Store(self.path), self.telegram, 111, 222, "test-signing-key-only")
        restarted.handle(update)
        self.assertEqual(len(restarted.store.snapshot(self.a)["messages"]), 2)
        with restarted.store.connect() as db:
            self.assertEqual(int(Store.setting(db, "telegram_offset")), 901)

    def test_uncertain_notification_is_not_resent_and_signed_reply_recovers(self):
        self.enqueue(self.a)
        with self.store.connect() as db:
            message = dict(db.execute("SELECT * FROM messages WHERE session=?", (self.a,)).fetchone())
        text = self.relay.notification(message)
        self.telegram.fail = True
        self.relay.deliver_one()
        self.assertEqual(self.store.snapshot(self.a)["messages"][0]["state"], "uncertain")
        self.assertFalse(self.relay.deliver_one())
        self.telegram.fail = False
        self.relay.handle(self.reply({"message_id": 55, "from": {"id": 222}, "text": text}))
        self.assertEqual(self.store.snapshot(self.a)["messages"][-1]["role"], "nad")

    def test_invalid_signed_tag_is_ignored(self):
        self.relay.handle(self.reply({"message_id": 55, "from": {"id": 222}, "text": f"WHY support · Chat {self.a} · 0000000000000000\n"}))
        self.assertEqual(self.store.snapshot(self.a)["messages"], [])

    def test_keys_are_blocked_before_storage_and_owner_reply(self):
        for text in ["api_key=fixture1234567890", "sk-fixture1234567890", "seed phrase: words"]:
            with self.assertRaises(SupportError):
                self.enqueue(self.a, text)
        self.enqueue(self.a); self.relay.deliver_one()
        self.relay.handle(self.reply(self.telegram.sent[0], "password=fixturepassword"))
        self.assertEqual(len(self.store.snapshot(self.a)["messages"]), 1)
        self.assertIn("Reply not sent", self.telegram.sent[-1]["text"])

    def test_presence_requires_availability_and_a_healthy_receiver(self):
        self.store.set_online(True)
        self.assertFalse(self.store.presence()["online"])
        with self.store.connect() as db:
            self.store.put(db, "receiver_seen", time.time())
        self.assertTrue(self.store.presence()["online"])
        self.relay.handle({"update_id": 777, "message": {"message_id": 778, "from": {"id": 111}, "chat": {"id": 111, "type": "private"}, "text": "/why_away"}})
        self.assertFalse(self.store.presence()["online"])

    def test_message_and_session_retention(self):
        self.enqueue(self.a)
        with self.store.connect() as db:
            db.execute("UPDATE messages SET created=?", (time.time() - RETENTION - 1,))
            db.execute("UPDATE sessions SET updated=? WHERE id=?", (time.time() - RETENTION - 1, self.a))
        self.assertIsNone(self.store.authenticate(self.cookie_a))
        self.store.cleanup()
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)

    def test_per_chat_burst_limit(self):
        for i in range(5):
            self.enqueue(self.a, client=f"fixture-client-id-{i:04}")
        with self.assertRaises(SupportError) as caught:
            self.enqueue(self.a, client="fixture-client-id-9999")
        self.assertEqual(caught.exception.status, 429)

    def test_http_boundary_protects_saved_chats_and_sets_secure_cookie(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store, "fixture-internal-secret"))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_address[1]) + "/chat"
        def call(body=None, secret="fixture-internal-secret", cookie=""):
            req = urllib.request.Request(base, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json", "X-Why-Human-Secret": secret, "Cookie": cookie})
            return urllib.request.urlopen(req)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            call(secret="forged")
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        with call({"action": "open"}) as response:
            cookie = response.headers["Set-Cookie"]
            self.assertTrue(json.load(response)["ready"])
            for flag in ["Secure", "HttpOnly", "SameSite=Strict"]:
                self.assertIn(flag, cookie)
        with call({"action": "send", "text": "Hello Nad", "clientId": "fixture-request-1234"}, cookie=cookie) as response:
            self.assertEqual(json.load(response)["messages"][0]["text"], "Hello Nad")
        with call(cookie=self.cookie_b) as response:
            self.assertEqual(json.load(response)["messages"], [])

if __name__ == "__main__":
    unittest.main()
